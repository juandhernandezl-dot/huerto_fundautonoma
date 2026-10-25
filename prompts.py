"""
Prompts y plantillas de texto (Capítulo 8, §8.4 — "el directorio de
prompts: el prompt de sistema, el vocabulario de dominio inyectado en la
transcripción y las plantillas de respuesta son artefactos que ya se
iteran y que hoy conviven con el código de aplicación").
"""
from datetime import datetime, timedelta, timezone

# Prompt validado en las pruebas a ciegas con Qwen3.5-4B (ver reporte de evaluación)
SYSTEM_PROMPT = (
    "Eres el asistente del huerto de Fundautonoma. Respondes siempre en "
    "español colombiano, de forma clara y amable, como si hablaras con un "
    "adulto mayor sin conocimientos técnicos. Máximo 3 oraciones. "
    "No inventes datos que no te den; si no tienes información suficiente, dilo. "
    "Los datos de sensores que recibes en cada mensaje son siempre los más "
    "recientes disponibles ahora mismo -- si dicen que no hay datos "
    "disponibles, tu respuesta debe decirlo también, aunque en mensajes "
    "anteriores de esta misma conversación hayas mencionado otros valores: "
    "esos ya pueden estar desactualizados, no los repitas como si fueran "
    "el estado actual. "
    "Recibirás dos bloques de datos: el estado ACTUAL de los sensores, y un "
    "RESUMEN de las últimas horas (tiempo de riego, promedios). Usa cada uno "
    "según corresponda a la pregunta (ahora vs. historial/hoy), y solo si la "
    "pregunta es sobre el huerto; si pregunta otra cosa, ignora ambos."
)

# Vocabulario del dominio para sesgar la transcripción de Whisper.
WHISPER_INITIAL_PROMPT = (
    "Vocabulario del huerto: zona uno, zona dos, zona tres, válvula, "
    "válvulas, riego, bomba, humedad, sensor, conexión CAN, modo manual, "
    "modo automático."
)

# El LLM devolvió contenido vacío (thinking mode no desactivado, corte por
# longitud, etc.) — nunca se manda texto vacío a Telegram.
RESPUESTA_LLM_VACIO = (
    "Uy, no logré terminar de pensar la respuesta. "
    "¿Puedes preguntarme de nuevo, por favor?"
)

# ask_llm() lanzó una excepción (LM Studio apagado, timeout, etc.)
RESPUESTA_LLM_FALLO = (
    "Uy, en este momento no puedo pensar bien la respuesta. "
    "Por favor intenta de nuevo en un momentico."
)

RESPUESTA_VOZ_NO_ENTENDIDA = (
    "No logré entender el audio. ¿Puedes intentar de nuevo, por favor?"
)

RESPUESTA_VOZ_FALLO = (
    "Uy, no pude procesar el audio. Intenta de nuevo o escribe tu pregunta."
)


def respuesta_accion_no_disponible(descripcion: str) -> str:
    """Plantilla genérica para una acción detectada que todavía no se
    puede ejecutar. comandos.py arma la 'descripcion' específica
    (ej. "abrir la válvula de la zona 1") y esta función solo envuelve
    el mensaje en el tono establecido para el cuidador."""
    return (
        f"Por ahora todavía no puedo {descripcion} por ti — esa parte "
        "está en construcción. Mientras tanto, alguien puede hacerlo "
        "directamente desde el tablero del huerto."
    )


# Respuestas a una acción enviada a Node-RED, según el "estado" que
# devuelve su API. Regla: solo "confirmado" dice que algo pasó; el resto
# dice exactamente lo que se sabe (indicador "cero confirmaciones sin
# respaldo").
_RESPUESTAS_ACCION = {
    "confirmado": "Listo, ya pude {d}. El sistema del huerto lo confirmó.",
    "enviado": (
        "Ya envié la orden para {d}. El sistema no me reporta el modo, así "
        "que no puedo confirmarlo; puede revisarlo en el tablero del huerto."
    ),
    "sin_confirmar": (
        "Envié la orden para {d}, pero el sistema no la confirmó a tiempo. "
        "No puedo asegurar que se haya hecho; revise el tablero o pregúnteme "
        "el estado en un momento."
    ),
    "requiere_manual": (
        "No puedo {d} porque el huerto está en modo automático. Si quiere "
        "hacerlo a mano, primero dígame «cambia a modo manual»."
    ),
    "nodo_desconectado": (
        "No puedo {d} porque esa zona está desconectada del sistema en este "
        "momento. No se hizo ningún cambio."
    ),
    "sin_valvula_abierta": (
        "No puedo {d} porque no hay ninguna válvula abierta, y la bomba "
        "trabajando con todo cerrado se puede dañar. Primero abra una válvula."
    ),
    "sin_telemetria": (
        "No puedo {d} porque el sistema de riego no está reportando datos en "
        "este momento. No se hizo ningún cambio; revise que esté encendido."
    ),
    "ocupado": (
        "Todavía estoy esperando la confirmación de la orden anterior. "
        "Intente de nuevo en unos segundos, por favor."
    ),
}

RESPUESTA_ACCION_SIN_CONEXION = (
    "No pude comunicarme con el sistema de riego, así que no se hizo ningún "
    "cambio. Puede intentarlo de nuevo o hacerlo desde el tablero del huerto."
)

# Timeout del lado del bot: la orden pudo haberse publicado o no.
RESPUESTA_ACCION_INCIERTA = (
    "El sistema de riego tardó demasiado en responder y no sé si la orden se "
    "aplicó. Revise el tablero o pregúnteme el estado en un momento."
)

RESPUESTA_ACCION_SIN_ZONA = (
    "¿De cuál zona es la válvula? Dígame, por ejemplo, «abre la válvula de "
    "la zona 1»."
)

RESPUESTA_ACCION_AMBIGUA = (
    "No estoy seguro de qué quiere que haga, así que no hice nada. Dígame "
    "una sola orden, por ejemplo «abre la válvula de la zona 1» o «cierra "
    "la válvula de la zona 2». Las zonas son 1, 2 y 3."
)

RESPUESTA_CONFIRMACION_CANCELADA = "Listo, no hice nada."

RESPUESTA_CONFIRMACION_VENCIDA = (
    "Esa orden ya se venció y no la hice. Si todavía la necesita, "
    "pídamela otra vez."
)

# Colombia no tiene horario de verano: UTC-5 fijo, sin depender de tzdata
# en el contenedor.
_HORA_COLOMBIA = timezone(timedelta(hours=-5))


def _hora(epoch_ms) -> str | None:
    try:
        momento = datetime.fromtimestamp(float(epoch_ms) / 1000, _HORA_COLOMBIA)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return momento.strftime("%I:%M %p").lstrip("0").replace("AM", "a. m.").replace("PM", "p. m.")


def pregunta_confirmacion(tipo: str, descripcion: str) -> str:
    """Eco de la orden antes de ejecutarla: si la detección se equivocó de
    zona, el cuidador lo ve aquí, antes de que se moje nada."""
    if tipo == "cambiar_modo":
        return (
            "¿Paso el huerto a modo manual? Mientras esté en manual, el riego "
            "automático queda pausado (vuelve solo a automático después de un "
            "rato). Responda SÍ o NO."
        )
    return f"¿Quiere que vaya a {descripcion}? Responda SÍ o NO."


def respuesta_accion(estado: str, descripcion: str, cierra_a=None, vence_a=None) -> str:
    """Envuelve el resultado de la API de Node-RED en el tono del cuidador.
    Un estado desconocido nunca se presenta como éxito. Si Node-RED informó
    a qué hora se cierra sola la válvula/bomba o vence el modo manual, se
    dice la hora: el cuidador no tiene que saber qué es un temporizador."""
    plantilla = _RESPUESTAS_ACCION.get(estado)
    if plantilla is None:
        return RESPUESTA_ACCION_INCIERTA
    texto = plantilla.format(d=descripcion)

    hora_cierre = _hora(cierra_a) if cierra_a is not None else None
    if estado == "confirmado" and hora_cierre:
        verbo = "se cierra sola" if "válvula" in descripcion else "se apaga sola"
        texto += f" Por seguridad {verbo} a las {hora_cierre} si nadie lo hace antes."
    hora_vence = _hora(vence_a) if vence_a is not None else None
    if estado == "enviado" and hora_vence:
        texto += f" Si queda en manual, vuelve solo a automático a las {hora_vence}{'' if hora_vence.endswith('.') else '.'}"
    return texto
