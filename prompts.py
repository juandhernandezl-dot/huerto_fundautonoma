"""
Prompts y plantillas de texto (Capítulo 8, §8.4 — "el directorio de
prompts: el prompt de sistema, el vocabulario de dominio inyectado en la
transcripción y las plantillas de respuesta son artefactos que ya se
iteran y que hoy conviven con el código de aplicación").
"""

# Prompt validado en las pruebas a ciegas con Qwen3.5-4B (ver reporte de evaluación)
SYSTEM_PROMPT = (
    "Eres el asistente del huerto de Fundautonoma. Respondes siempre en "
    "español colombiano, de forma clara y amable, como si hablaras con un "
    "adulto mayor sin conocimientos técnicos. Máximo 3 oraciones. "
    "No inventes datos que no te den; si no tienes información suficiente, dilo. "
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
