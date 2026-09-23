"""
Resolutor determinista de consultas informativas (Decisión 5, Capítulo 8
§8.6 — "módulo de resolución determinista de consultas informativas,
hermano del detector de comandos"). Patrón "fallback determinista" de
ai.pdf (RAG industrial): si la pregunta admite una respuesta exacta
calculable, se responde por consulta directa y el LLM NO se invoca en
absoluto. Alucinación matemáticamente imposible en estas respuestas: no
hay generación de lenguaje de por medio, solo aritmética sobre datos
reales.

Reutiliza telemetria.get_daily_summary()/get_latest_telemetry() y sus
formateadores — no duplica lógica de agregación. Cubre cuatro patrones;
todo lo demás sigue yendo al LLM (ver docstring de
resolver_consulta_determinista para el detalle de qué queda fuera).
"""
import re
from datetime import datetime, timezone

import config
import telemetria
from texto import normalizar

_FORMAS_RIEGO = ("riego", "regando", "regado", "regada", "rego", "regaron", "regue")
_CUES_RIEGO_HOY = re.compile(
    r"\b(hoy|cuanto|cuanta|cuantos|cuantas|total|veces|activaciones|minutos|horas)\b"
)

_CUES_HUMEDAD_PROMEDIO = re.compile(r"\b(promedio|hoy|ha estado|han estado)\b")

_FRASES_ESTADO_ACTUAL = (
    "estado actual", "estado del huerto", "como esta el huerto",
    "que tal el huerto", "esta el huerto", "conexion can",
)
_PALABRAS_ESTADO_ACTUAL = re.compile(
    r"\b(can|valvula|bomba|regando|prendida|apagada|encendida|abierta|cerrada)\b"
)

# Patrón 4 (nuevo): pregunta sobre una zona en particular (ej. "¿cómo
# está la zona 1?", "zona uno", "cómo va la zona 2"). Cubre dígito o
# número escrito -- las notas de voz transcritas ya se han visto llegar
# en ambas formas (ver transcripciones de prueba). "una" se acepta como
# variante de "uno" por errores comunes de transcripción de Whisper.
_ZONA_NUM = {"1": 1, "2": 2, "3": 3, "uno": 1, "una": 1, "dos": 2, "tres": 3}
_CUE_ZONA = re.compile(r"\bzona\s*(1|2|3|uno|una|dos|tres)\b")


def _es_pregunta_riego_hoy(texto: str) -> bool:
    tiene_riego = any(forma in texto for forma in _FORMAS_RIEGO)
    return tiene_riego and bool(_CUES_RIEGO_HOY.search(texto))


def _es_pregunta_humedad_promedio(texto: str) -> bool:
    return "humedad" in texto and bool(_CUES_HUMEDAD_PROMEDIO.search(texto))


def _es_pregunta_estado_actual(texto: str) -> bool:
    if any(frase in texto for frase in _FRASES_ESTADO_ACTUAL):
        return True
    return bool(_PALABRAS_ESTADO_ACTUAL.search(texto))


def _extraer_zona(texto: str) -> int | None:
    m = _CUE_ZONA.search(texto)
    if not m:
        return None
    return _ZONA_NUM.get(m.group(1))


def _formatear_zona(doc: dict, zona: int) -> str:
    """Extrae y formatea solo los campos de la zona pedida -- misma
    fuente de datos que format_telemetry_for_llm, pero sin mezclar las
    otras dos zonas en la respuesta.
    """
    ts = doc["timestamp"]
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    minutos = int((datetime.now(timezone.utc) - ts).total_seconds() // 60)

    humedad = doc.get(f"zona{zona}_humedad")
    valvula = doc.get(f"valvula{zona}")
    can_online = doc.get(f"can{zona}_online")

    valvula_txt = "abierta" if valvula == 1 else "cerrada"
    can_txt = "en línea" if can_online == 1 else "sin conexión"

    return (
        f"Hace {minutos} minutos: zona {zona} humedad {humedad}%, "
        f"válvula {valvula_txt}, conexión CAN: {can_txt}."
    )


def resolver_consulta_determinista(user_text: str) -> str | None:
    """Devuelve la respuesta ya lista para enviar, o None si el mensaje no
    coincide con ninguno de los cuatro patrones cubiertos (en ese caso el
    flujo sigue normal hacia llm_client.ask_llm()).

    Cubre:
      1) Tiempo de riego / activaciones de hoy (ej. "¿cuánto ha regado
         hoy?", "qué ha pasado hoy en cuanto al riego").
      2) Humedad promedio por zona, histórica (ej. "humedad promedio",
         "¿cómo ha estado la humedad?").
      3) Una zona en particular, estado actual (ej. "¿cómo está la zona
         1?", "zona 2", "cómo va la zona tres") -- humedad, válvula y
         conexión CAN de ESA zona únicamente, no de las otras dos.
      4) Estado actual del huerto completo: válvulas, bomba, conexión CAN
         (ej. "estado actual", "¿la bomba está encendida?", "¿alguna
         válvula está abierta?", "¿está regando ahora?").

    NO cubre (sigue yendo al LLM):
      - Humedad de una zona AHORA MISMO sin "hoy"/"promedio"/"ha estado"
        Y sin la palabra "zona" (ej. "dime la humedad" a secas, ambiguo
        sobre cuál zona) -- el patrón 2 es sobre el promedio histórico;
        para una zona puntual ahora mismo, usa el patrón 3 nombrando la
        zona explícitamente.
      - Cualquier mensaje que combine varios temas o use lenguaje ambiguo
        que no calce con las frases/palabras clave anteriores.
    """
    texto = normalizar(user_text)

    if _es_pregunta_riego_hoy(texto):
        return telemetria.format_daily_summary_for_llm(telemetria.get_daily_summary())

    if _es_pregunta_humedad_promedio(texto):
        resumen = telemetria.get_daily_summary()
        if resumen is None or not resumen.get("promedios"):
            return (
                f"No tengo el promedio de humedad de las últimas "
                f"{config.HISTORIAL_HORAS} horas disponible en este momento."
            )
        prom = resumen["promedios"]
        return (
            f"En las últimas {config.HISTORIAL_HORAS} horas, la humedad promedio fue: "
            f"zona 1 {prom['zona1_prom']:.0f}%, zona 2 {prom['zona2_prom']:.0f}%, "
            f"zona 3 {prom['zona3_prom']:.0f}%."
        )

    zona = _extraer_zona(texto)
    if zona is not None:
        doc = telemetria.get_latest_telemetry()
        if doc is None:
            return "No hay datos de sensores disponibles en este momento."
        return _formatear_zona(doc, zona)

    if _es_pregunta_estado_actual(texto):
        return telemetria.format_telemetry_for_llm(telemetria.get_latest_telemetry())

    return None