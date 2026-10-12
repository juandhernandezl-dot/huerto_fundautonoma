"""
Cliente del modelo de lenguaje (Capítulo 8, §8.4 — "el directorio del
cliente de modelo de lenguaje ... con su manejo de fallos y su degradación
elegante"). Dos backends intercambiables por LLM_BACKEND: "ollama"
(contenedor, API nativa -- por defecto) y "openai" (LM Studio u otro
servidor compatible con OpenAI en el host). También es el único punto de
lectura/escritura del historial corto de conversación por chat --
comandos.py y consultas.py no tocan _history directamente, pasan por
record_turn().
"""
import logging
from collections import defaultdict, deque
from typing import Deque, Dict, List, Tuple

import httpx
from openai import OpenAI

import config
import prompts
import telemetria

log = logging.getLogger(__name__)

if config.LLM_BACKEND not in ("ollama", "openai"):
    # Falla al importar, no en la primera pregunta: un .env mal escrito se
    # ve de inmediato en `docker compose logs bot`, no horas después.
    raise ValueError(f"LLM_BACKEND inválido: {config.LLM_BACKEND!r} (usa 'ollama' u 'openai')")

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
    fusionados), porque la plantilla Jinja de Qwen3.5 exige que, si hay
    mensaje system, sea único y esté en la posición 0.

    Nota 2: el "thinking mode" de Qwen3.5 se desactiva en cada backend a su
    manera (ver _llamar_ollama / _llamar_openai). Sin eso, el modelo puede
    gastar todo el presupuesto de tokens razonando internamente y devolver
    content vacío, lo que además revienta reply_text() de Telegram (no
    acepta mensajes vacíos).
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

    if config.LLM_BACKEND == "ollama":
        reply, finish_reason = _llamar_ollama(messages, user_text)
    else:
        reply, finish_reason = _llamar_openai(messages, user_text)

    if not reply:
        # Red de seguridad: si el modelo sigue devolviendo vacío, nunca se
        # manda texto vacío a Telegram.
        log.warning(
            "El LLM devolvió contenido vacío (backend=%s, finish_reason=%s); "
            "revisar si el modo de razonamiento (thinking) sigue activo.",
            config.LLM_BACKEND, finish_reason,
        )
        reply = prompts.RESPUESTA_LLM_VACIO

    record_turn(chat_id, user_text, reply)
    return reply


def _llamar_ollama(messages: List[dict], user_text: str) -> Tuple[str, str]:
    """Backend por defecto: contenedor huerto-ollama, API NATIVA /api/chat.

    No se usa el endpoint compatible con OpenAI de Ollama porque NO propaga
    "think" al modelo (verificado en prueba_agente/benchmark.py: ignora tanto
    'think' como 'enable_thinking' vía extra_body). Con la API nativa,
    "think": false desactiva el razonamiento de Qwen3.5 de forma confiable,
    así que aquí no hace falta el sufijo "/no_think" de la ruta OpenAI.
    """
    payload = {
        "model": config.LLM_MODEL,
        "messages": [*messages, {"role": "user", "content": user_text}],
        "think": False,
        "stream": False,
        "keep_alive": config.OLLAMA_KEEP_ALIVE,
        "options": {
            "temperature": 0.7,
            "top_p": 0.8,
            "num_predict": 300,  # equivalente a max_tokens
            "num_ctx": config.OLLAMA_NUM_CTX,
        },
    }
    resp = httpx.post(
        f"{config.OLLAMA_BASE_URL.rstrip('/')}/api/chat",
        json=payload,
        timeout=config.REQUEST_TIMEOUT_S,
    )
    if resp.status_code == 404:
        # Caso típico tras un despliegue nuevo: el contenedor está vivo pero
        # el modelo aún no se descargó (o LLM_MODEL tiene el nombre de LM
        # Studio, "qwen3.5-4b", en vez del de Ollama, "qwen3.5:4b").
        log.error("Ollama respondió 404 para el modelo %r: %s", config.LLM_MODEL, resp.text[:200])
    resp.raise_for_status()
    data = resp.json()
    reply = (data.get("message", {}).get("content") or "").strip()
    return reply, data.get("done_reason", "")


def _llamar_openai(messages: List[dict], user_text: str) -> Tuple[str, str]:
    """Backend alternativo: servidor compatible con OpenAI (LM Studio en el
    host). Se conserva para poder comparar contra el statu quo y como
    respaldo; se activa con LLM_BACKEND=openai.

    Se desactiva el "thinking mode" de Qwen3.5 por dos capas independientes
    -- enable_thinking=False vía extra_body, y "/no_think" al final del
    turno del usuario -- porque la primera capa sola no se respetó en el
    100% de las llamadas observadas con LM Studio.
    """
    response = client.chat.completions.create(
        model=config.LLM_MODEL,
        messages=[*messages, {"role": "user", "content": f"{user_text}\n\n/no_think"}],
        temperature=0.7,
        top_p=0.8,
        max_tokens=300,
        timeout=config.REQUEST_TIMEOUT_S,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    choice = response.choices[0]
    return (choice.message.content or "").strip(), choice.finish_reason or ""