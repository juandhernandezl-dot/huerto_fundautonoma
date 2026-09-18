"""
Cliente del modelo de lenguaje (Capítulo 8, §8.4 — "el directorio del
cliente de modelo de lenguaje: el cliente contra el endpoint compatible
con la API de OpenAI, con su manejo de fallos y su degradación
elegante"). También es el único punto de lectura/escritura del historial
corto de conversación por chat -- comandos.py y consultas.py no tocan
_history directamente, pasan por record_turn().
"""
import logging
from collections import defaultdict, deque
from typing import Deque, Dict

from openai import OpenAI

import config
import prompts
import telemetria

log = logging.getLogger(__name__)

client = OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY)

# Historial corto por chat (en memoria; se pierde al reiniciar el bot).
# Suficiente para dar continuidad a la conversación sin añadir una base de
# datos todavía.
_history: Dict[int, Deque[dict]] = defaultdict(
    lambda: deque(maxlen=config.MAX_HISTORY_TURNS * 2)
)


def record_turn(chat_id: int, user_text: str, reply: str) -> None:
    """Registra un turno user/assistant en el historial corto del chat.
    Único punto de escritura de _history: las tres ramas de
    bot.process_user_message() (acción detectada, consulta determinista,
    LLM) pasan por aquí, para que el historial no se fragmente entre
    módulos."""
    _history[chat_id].append({"role": "user", "content": user_text})
    _history[chat_id].append({"role": "assistant", "content": reply})


def ask_llm(chat_id: int, user_text: str) -> str:
    """Envía el mensaje del usuario + historial corto + telemetría actual +
    resumen histórico al LLM local. El contexto se refresca en cada llamada
    y NO se guarda en el historial de conversación (para que el siguiente
    turno vea siempre el dato más nuevo).

    Nota 1: se envía un único mensaje "system" (prompt + telemetría + resumen
    fusionados), porque la plantilla Jinja de Qwen3.5 en LM Studio exige que,
    si hay mensaje system, sea único y esté en la posición 0.

    Nota 2: se desactiva el "thinking mode" de Qwen3.5 por dos capas
    independientes -- enable_thinking=False vía extra_body, y "/no_think"
    al final del turno del usuario -- porque la primera capa sola no se
    respetó en el 100% de las llamadas observadas en producción. Sin
    ninguna de las dos, el modelo puede gastar todo max_tokens razonando
    internamente y devolver content vacío (finish_reason="length"), lo que
    además revienta reply_text() de Telegram (no acepta mensajes vacíos).
    """
    telemetry_context = telemetria.format_telemetry_for_llm(telemetria.get_latest_telemetry())
    daily_context = telemetria.format_daily_summary_for_llm(telemetria.get_daily_summary())

    messages = [
        {
            "role": "system",
            "content": "\n\n".join([
                prompts.SYSTEM_PROMPT,
                f"Datos actuales del huerto: {telemetry_context}",
                daily_context,
            ]),
        },
    ]
    messages.extend(_history[chat_id])
    messages.append({"role": "user", "content": f"{user_text}\n\n/no_think"})

    response = client.chat.completions.create(
        model=config.LLM_MODEL,
        messages=messages,
        temperature=0.7,
        top_p=0.8,
        max_tokens=300,
        timeout=config.REQUEST_TIMEOUT_S,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    choice = response.choices[0]
    reply = (choice.message.content or "").strip()

    if not reply:
        # Red de seguridad: si el modelo sigue devolviendo vacío, nunca se
        # manda texto vacío a Telegram.
        log.warning(
            "El LLM devolvió contenido vacío (finish_reason=%s); "
            "revisar si enable_thinking sigue activo en LM Studio.",
            choice.finish_reason,
        )
        reply = prompts.RESPUESTA_LLM_VACIO

    record_turn(chat_id, user_text, reply)
    return reply

