"""
Detección determinista de acciones (Fase 3; formalizado en el Capítulo 8,
§8.4, como "módulo de detección determinista de comandos... el
guardarraíl arquitectónico central del sistema").

Corre ANTES del LLM. Reconoce que el usuario pidió una acción física
(abrir/cerrar válvula, encender/apagar bomba, cambiar de modo) y la envía
a Node-RED por HTTP (POST a config.NODERED_ACCION_URL). Node-RED es quien
valida (telemetría viva, modo manual, nodo conectado), publica en riego/*
por MQTT, espera la confirmación real en la telemetría (patrón launch +
ack) y aplica los topes de tiempo. Este módulo NO importa ningún cliente
MQTT: el bot no tiene ruta de escritura directa hacia el ESP32.

Justificación de diseño (evaluada contra la arquitectura de ai.pdf, el
paper de microservicios CIP): el paper separa un "Deterministic
Supervisor" -- el único componente con ruta de escritura validada hacia
el actuador -- de un LLM Assistant puramente consultivo, sin autoridad de
escritura. Esa propiedad de seguridad es verificable inspeccionando el
grafo de componentes, no depende de que el modelo "decida bien". Aquí se
aplica la misma idea: este módulo es el único código que reconoce una
intención de acción, y su única salida es la API de Node-RED. Solo un
"confirmado" de Node-RED produce un mensaje de éxito.

Reglas de seguridad (consejo del 20 oct 2026, acciones-agente/
council-transcript.md en el proyecto):
  - Lo que abre agua o quita el control automático (abrir válvula,
    encender bomba, pasar a manual) pide confirmación SÍ/NO repitiendo la
    orden en palabras. Lo que la corta (cerrar, apagar, volver a
    automático) se ejecuta directo: cerrar nunca debe demorarse.
  - Ante ambigüedad (dos zonas, zona inexistente, dos acciones distintas,
    una negación antes del verbo) se rechaza y se pide repetir. Nunca se
    adivina.
"""
import logging
import re
import threading
import time

import httpx

import config
import prompts
import telemetria
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
    "rl": "ia",
}

# Verbos por intención. Solo formas imperativas/infinitivas: participios
# ("abierta", "encendida", "prendida") son preguntas de estado y deben
# seguir llegando a consultas.py.
_V_ABRIR = r"(?:abr(?:e|a|as|ir|iste|amos)|abrela|activ(?:a|e|es|ar)|enciend(?:e|a|as)|encender|prend(?:e|a|as|er))"
_V_CERRAR = r"(?:cierr(?:a|e|es)|cerrar|cierrela|apag(?:a|ue|ues|ar)|desactiv(?:a|e|es|ar))"
_V_MODO = r"(?:cambi(?:a|e|es|ar)|pon(?:er|ga|gas)?|pas(?:a|e|es|ar)|dej(?:a|e|es|ar)|activ(?:a|e|es|ar))(?:l[oa])?"
_V_VOLVER = r"(?:vuelv(?:e|a|er)|regres(?:a|e|ar)|devuelv(?:e|a))"

_PATRONES = [
    ("abrir_valvula", re.compile(rf"\b{_V_ABRIR}\b.*\bvalvula")),
    ("cerrar_valvula", re.compile(rf"\b{_V_CERRAR}\b.*\bvalvula")),
    ("encender_bomba", re.compile(rf"\b{_V_ABRIR}\b.*\bbomba")),
    ("apagar_bomba", re.compile(rf"\b{_V_CERRAR}\b.*\bbomba")),
    # con "modo"/"control" (incluye "control RL") o, sin esas palabras, con
    # "automático"/"manual" a secas: "vuelve a automático", "ponlo en manual".
    ("cambiar_modo", re.compile(
        rf"\b{_V_MODO}\b.*\b(?:modo|control)\b|\b(?:{_V_MODO}|{_V_VOLVER})\b.*\b(?:automatico|manual)\b")),
]
_TIPOS_QUE_ABREN = {"abrir_valvula", "encender_bomba"}

# Zona solo cuando el número va pegado a "válvula"/"zona" (o el ordinal va
# delante): "en 1 minuto abre la válvula 3" debe dar zona 3, no 1.
_ZONA_TRAS = re.compile(
    r"\b(?:valvulas?|zonas?)\s+(?:de\s+la\s+zona\s+|numero\s+|nro\.?\s*|n\.?\s*|#\s*)?(\d+|uno|una|dos|tres)\b"
)
# Zonas coordinadas tras la primera: "la válvula 1 y la 2", "válvulas 1, 2 y 3".
# Cualquier número así encontrado cuenta como otra zona: si no es 1-3 o hay
# más de una, la orden se rechaza en vez de abrir solo la primera.
_ZONA_COORD = re.compile(
    r"\s*(?:,|\by\b|\be\b|\bcon\b)\s*(?:(?:la|las)\s+)?(?:(?:valvula|zona)s?\s+)?(\d+|uno|una|dos|tres)\b"
)
_ZONA_ORDINAL = re.compile(r"\b(primera|segunda|tercera)\s+(?:valvula|zona)\b")

_AFIRMATIVOS = {"si", "dale", "claro", "ok", "okay", "confirmo", "hagale", "correcto", "listo", "bueno"}
_NEGATIVOS = {"no", "cancela", "cancelar", "cancele", "espera", "espere", "mejor"}

# Confirmaciones pendientes por chat: chat_id -> (accion, vence_en_monotonic).
# En memoria a propósito: tras un reinicio del bot no debe quedar ninguna
# orden física "colgada" esperando un SÍ.
_pendientes: dict = {}
_lock = threading.Lock()


def _extraer_zonas(texto: str) -> set[str]:
    crudas = _ZONA_ORDINAL.findall(texto)
    for m in _ZONA_TRAS.finditer(texto):
        crudas.append(m.group(1))
        fin = m.end()
        while (c := _ZONA_COORD.match(texto, fin)):
            crudas.append(c.group(1))
            fin = c.end()
    return {_ZONA_PALABRAS.get(z, "invalida") for z in crudas}


def _extraer_modo(texto: str) -> str | None:
    for palabra, modo in _MODOS_VALIDOS.items():
        if re.search(rf"\b{palabra}\b", texto):
            return modo
    return None


def detectar_accion(texto_usuario: str) -> dict | None:
    """Devuelve un dict describiendo la acción detectada, o None si el
    mensaje no corresponde a ninguna acción (sigue hacia consultas.py /
    llm_client.ask_llm()). Un mensaje que parece una orden pero es ambiguo
    devuelve {"tipo": "ambigua"}: nunca debe llegar al LLM, que podría
    responder como si se hubiera hecho algo."""
    texto = normalizar(texto_usuario)

    coincidencias = [(tipo, patron.search(texto)) for tipo, patron in _PATRONES]
    coincidencias = [(tipo, m) for tipo, m in coincidencias if m]
    if not coincidencias:
        return None

    tipos = {tipo for tipo, _ in coincidencias}
    if len(tipos) > 1:
        return {"tipo": "ambigua"}
    tipo, m = coincidencias[0]

    # "no abras", "no quiero que prendas...": una negación antes del verbo
    # en una orden que abre agua se rechaza en vez de interpretarse.
    if re.search(r"\bno\b", texto[: m.start()]):
        return {"tipo": "ambigua"}

    if tipo in ("abrir_valvula", "cerrar_valvula"):
        zonas = _extraer_zonas(texto)
        if len(zonas) > 1 or "invalida" in zonas:
            return {"tipo": "ambigua"}
        return {"tipo": tipo, "zona": next(iter(zonas), None)}
    if tipo == "cambiar_modo":
        return {"tipo": tipo, "modo": _extraer_modo(texto)}
    return {"tipo": tipo}


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


def _requiere_confirmacion(accion: dict) -> bool:
    return accion["tipo"] in _TIPOS_QUE_ABREN or (
        accion["tipo"] == "cambiar_modo" and accion.get("modo") == "manual"
    )


def procesar_accion(chat_id: int, accion: dict) -> str:
    """Punto de entrada desde bot.py para una acción recién detectada.
    Valida, y o bien la ejecuta (acciones que cortan agua) o bien la deja
    pendiente de un SÍ (acciones que abren agua o pasan a manual)."""
    tipo = accion["tipo"]
    if tipo == "ambigua":
        return prompts.RESPUESTA_ACCION_AMBIGUA
    if tipo in ("abrir_valvula", "cerrar_valvula") and accion.get("zona") is None:
        return prompts.RESPUESTA_ACCION_SIN_ZONA
    if tipo == "cambiar_modo" and accion.get("modo") not in ("manual", "automático"):
        # "ia" (control RL) o modo no reconocido: el firmware del receptor
        # solo entiende "manual" y "auto".
        return prompts.respuesta_accion_no_disponible(_describir_accion(accion))

    if _requiere_confirmacion(accion):
        with _lock:
            _pendientes[chat_id] = (accion, time.monotonic() + config.CONFIRMACION_TTL_S)
        return prompts.pregunta_confirmacion(tipo, _describir_accion(accion))
    return ejecutar_accion(accion)


def resolver_confirmacion(chat_id: int, texto_usuario: str) -> str | None:
    """Si el chat tenía una orden esperando confirmación, la resuelve con
    este mensaje. Devuelve None cuando el mensaje no es una respuesta a esa
    pregunta: la orden pendiente se descarta (cualquier otra cosa equivale
    a no confirmar) y el mensaje sigue el flujo normal."""
    with _lock:
        pendiente = _pendientes.pop(chat_id, None)
    if pendiente is None:
        return None
    accion, vence = pendiente

    palabras = re.findall(r"[a-z]+", normalizar(texto_usuario))
    primera = palabras[0] if palabras else ""
    if primera in _NEGATIVOS:
        return prompts.RESPUESTA_CONFIRMACION_CANCELADA
    if primera not in _AFIRMATIVOS:
        return None
    if time.monotonic() > vence:
        return prompts.RESPUESTA_CONFIRMACION_VENCIDA
    return ejecutar_accion(accion)


def ejecutar_accion(accion: dict) -> str:
    """Envía la acción a la API de Node-RED y devuelve el texto para el
    usuario. Nunca lanza excepción: cualquier fallo se traduce en una
    respuesta honesta que no afirma que algo haya pasado."""
    descripcion = _describir_accion(accion)
    tipo = accion["tipo"]

    cuerpo = {"tipo": tipo}
    if accion.get("zona") is not None:
        cuerpo["zona"] = int(accion["zona"])
    if tipo == "cambiar_modo":
        cuerpo["modo"] = "auto" if accion["modo"] == "automático" else "manual"

    try:
        resp = httpx.post(config.NODERED_ACCION_URL, json=cuerpo, timeout=config.ACCION_TIMEOUT_S)
    except httpx.ConnectError:
        # No hubo conexión: la orden con certeza no salió.
        log.warning("Node-RED no disponible para la acción %s", cuerpo)
        return prompts.RESPUESTA_ACCION_SIN_CONEXION
    except httpx.HTTPError:
        # Timeout u otro corte a mitad de camino: la orden pudo salir o no.
        log.exception("Respuesta incierta de Node-RED para la acción %s", cuerpo)
        return prompts.RESPUESTA_ACCION_INCIERTA

    try:
        datos = resp.json()
    except ValueError:
        datos = None
    if not isinstance(datos, dict):
        datos = {}
    log.info("Acción %s -> HTTP %s, respuesta=%r", cuerpo, resp.status_code, datos)
    if datos.get("estado") == "confirmado":
        telemetria.registrar_orden(datos.get("campo"), datos.get("valor"))
    return prompts.respuesta_accion(
        datos.get("estado", ""), descripcion,
        cierra_a=datos.get("cierra_a"), vence_a=datos.get("vence_a"),
    )
