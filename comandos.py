"""
Detección determinista de acciones (Fase 3; formalizado en el Capítulo 8,
§8.4, como "módulo de detección determinista de comandos... el
guardarraíl arquitectónico central del sistema").

Corre ANTES del LLM. Reconoce que el usuario pidió una acción física
(abrir/cerrar válvula, encender/apagar bomba, cambiar de modo) y responde
honestamente que todavía no se puede ejecutar — NO publica nada a
MQTT/Node-RED (esa superficie no se abre hasta que se retome esa parte
del sistema). El punto de conexión futuro del patrón launch+ack queda
marcado explícitamente en responder_accion_no_disponible().

Justificación de diseño (evaluada contra la arquitectura de ai.pdf, el
paper de microservicios CIP): el paper separa un "Deterministic
Supervisor" -- el único componente con ruta de escritura validada hacia
el actuador -- de un LLM Assistant puramente consultivo, sin autoridad de
escritura. Esa propiedad de seguridad es verificable inspeccionando el
grafo de componentes, no depende de que el modelo "decida bien". Aquí se
aplica la misma idea: este módulo es el único código que reconoce una
intención de acción, y no importa ningún cliente MQTT ni conoce Node-RED
-- esa ruta de escritura no existe todavía en el grafo del sistema, a
propósito.

Limitación conocida (a refinar con frases reales de las pruebas con la
comunidad, ver bitácora motor->huerto, punto "colisión de palabras
clave"): _extraer_zona() busca el primer número (dígito o en palabras) en
todo el mensaje, no solo cerca de "válvula". Bajo riesgo hoy porque no
hay más detectores deterministas con los que colisionar.

Colisión conocida y NO corregida a propósito (fuera de alcance de la
sesión que la encontró): "prender" en infinitivo ("quiero prender la
bomba") no dispara _ENCENDER_BOMBA (solo "prende"/"enciende"/"activa"),
así que ese mensaje cae en consultas.py, que sí tiene "bomba" en sus
palabras clave de estado actual -- el resultado es que el bot cuenta el
estado de la bomba en vez de reconocer que le pidieron encenderla.
"""
import logging
import re

from prompts import respuesta_accion_no_disponible
from texto import normalizar

log = logging.getLogger(__name__)

_ZONA_PALABRAS = {
    "1": "1", "uno": "1", "una": "1", "primera": "1",
    "2": "2", "dos": "2", "segunda": "2",
    "3": "3", "tres": "3", "tercera": "3",
}

_MODOS_VALIDOS = {
    "manual": "manual",
    "automatico": "automático",
    "auto": "automático",
    "ia": "ia",
    "inteligente": "ia",
}

_ABRIR_VALVULA = re.compile(r"\babr(?:e|ir|iste|amos)?\b.*\bvalvula")
_CERRAR_VALVULA = re.compile(r"\b(?:cierr(?:a|e|as)?|cerrar)\b.*\bvalvula")
_ENCENDER_BOMBA = re.compile(r"\b(?:encien(?:de|da)?|prende|activa)\b.*\bbomba")
_APAGAR_BOMBA = re.compile(r"\b(?:apaga|desactiva)\b.*\bbomba")
_CAMBIAR_MODO = re.compile(r"\b(?:cambia|cambiar|pon|poner)\b.*\bmodo")


def _extraer_zona(texto_normalizado: str) -> str | None:
    m = re.search(r"\b(1|2|3|uno|una|primera|dos|segunda|tres|tercera)\b", texto_normalizado)
    return _ZONA_PALABRAS.get(m.group(1)) if m else None


def _extraer_modo(texto_normalizado: str) -> str | None:
    for palabra, modo in _MODOS_VALIDOS.items():
        if re.search(rf"\b{palabra}\b", texto_normalizado):
            return modo
    return None


def detectar_accion(texto_usuario: str) -> dict | None:
    """Devuelve un dict describiendo la acción detectada, o None si el
    mensaje no corresponde a ninguna acción reconocida (en ese caso sigue
    el flujo normal hacia consultas.py / llm_client.ask_llm())."""
    texto = normalizar(texto_usuario)

    if _ABRIR_VALVULA.search(texto):
        return {"tipo": "abrir_valvula", "zona": _extraer_zona(texto)}
    if _CERRAR_VALVULA.search(texto):
        return {"tipo": "cerrar_valvula", "zona": _extraer_zona(texto)}
    if _ENCENDER_BOMBA.search(texto):
        return {"tipo": "encender_bomba"}
    if _APAGAR_BOMBA.search(texto):
        return {"tipo": "apagar_bomba"}
    if _CAMBIAR_MODO.search(texto):
        return {"tipo": "cambiar_modo", "modo": _extraer_modo(texto)}

    return None


def _describir_accion(accion: dict) -> str:
    tipo = accion["tipo"]
    if tipo == "abrir_valvula":
        zona = accion.get("zona")
        return f"abrir la válvula de la zona {zona}" if zona else "abrir esa válvula"
    if tipo == "cerrar_valvula":
        zona = accion.get("zona")
        return f"cerrar la válvula de la zona {zona}" if zona else "cerrar esa válvula"
    if tipo == "encender_bomba":
        return "encender la bomba"
    if tipo == "apagar_bomba":
        return "apagar la bomba"
    if tipo == "cambiar_modo":
        modo = accion.get("modo")
        return f"cambiar el huerto a modo {modo}" if modo else "cambiar el modo del huerto"
    return "hacer ese cambio"


def responder_accion_no_disponible(accion: dict) -> str:
    """Respuesta honesta para una acción detectada. NO publica nada a
    MQTT/Node-RED todavía -- a propósito.

    Punto de conexión futuro (patrón launch + ack, ver bitácora de
    transferencia motor->huerto): cuando se retome Node-RED, esta función
    es el lugar exacto a reemplazar por:
      1) publicar la intención (ej. tópico riego/...), igual que ya hace
         hoy el canal manual del dashboard;
      2) esperar confirmación real (telemetría reflejando el nuevo estado,
         o un evento explícito) con timeout;
      3) solo entonces devolver un mensaje de éxito -- nunca antes, para
         no repetir el bug de motor donde el LLM decía "listo" sin que
         hubiera pasado nada.
    Mientras el punto 2 no exista, esta función debe seguir devolviendo
    el mensaje honesto de prompts.respuesta_accion_no_disponible(), y
    este módulo no debe importar ningún cliente MQTT.
    """
    return respuesta_accion_no_disponible(_describir_accion(accion))
