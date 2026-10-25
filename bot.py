"""
Bot de Telegram para el huerto de Fundautonoma.

Módulo de entrada: wiring de Telegram y orquestación entre comandos.py
(Fase 3), consultas.py (Decisión 5) y llm_client.py. La lógica de cada
guardarraíl vive en su propio módulo -- ver Capítulo 8, §8.4, para la
justificación de esta separación y §8.4.2 para por qué todos estos
módulos comparten un solo contenedor (mismo ciclo de vida: se actualizan
juntos, se reinician juntos y fallan juntos desde el punto de vista del
cuidador).

Principios de Karpathy aplicados:
- Simplicity First: sin frameworks de agentes (LangChain/CrewAI/Hermes),
  solo Telegram + una llamada HTTP al LLM.
- Surgical Changes: un módulo por responsabilidad, sin abstracciones
  especulativas (ver §8.4.1: se descartó el patrón de RAG documental
  porque este agente no tiene corpus que vectorizar).
- Goal-Driven: el criterio de éxito es "el cuidador recibe una respuesta
  clara y correcta en <20s", no una receta paso a paso.

Fix 1: process_user_message(), voz.transcribe_audio() y
voz.synthesize_speech_ogg() son funciones síncronas y bloqueantes (HTTP
al LLM, transcripción, síntesis). Se llaman a través de asyncio.to_thread
para no congelar el event loop mientras corren.

Fix 2 (este archivo): Whisper y Piper se precargan en main(), ANTES de
app.run_polling(), en vez de cargarse perezosamente en el primer mensaje
de voz. Motivo verificado en producción: la primera carga de Whisper
puede disparar una descarga de ~500MB desde Hugging Face, y esa descarga
corre en un hilo de asyncio.to_thread que NO se puede cancelar a la
fuerza -- si alguien apaga el bot (Ctrl+C) mientras esa descarga sigue en
curso, el proceso queda colgado en el apagado esperando a que ese hilo
termine, y Ctrl+C repetidos durante esa espera terminan en un crash feo
("Aborted (core dumped)"), no en un cierre limpio. Precargar aquí mueve
ese costo a un momento sin tráfico concurrente y sin ningún hilo de fondo
que el cierre tenga que esperar -- y de paso falla rápido si algo está
mal configurado (ej. PIPER_VOICE_PATH ausente), en vez de fallar en
silencio en el primer uso real, sin tumbar el resto del bot si falla.
"""
from __future__ import annotations

# logging_config se importa y se configura ANTES que el resto de los
# módulos del proyecto, para que cualquier aviso emitido al importarlos
# (ej. el chequeo de ffmpeg en voz.py) también quede en logs/bot.log.
import logging_config
logging_config.setup_logging()

import asyncio
import io
import logging
import os
import tempfile

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import comandos
import config
import consultas
import llm_client
import prompts
import voz

log = logging.getLogger(__name__)


def process_user_message(chat_id: int, user_text: str) -> str:
    """Punto único de TRES etapas para cualquier mensaje ya convertido a
    texto (llega por Telegram como texto, o como nota de voz ya
    transcrita). handle_text() y handle_voice() llaman ambos aquí (a
    través de asyncio.to_thread, ver nota de módulo), para que el
    comportamiento no diverja entre canales de entrada.

    Orden (cada etapa solo corre si la anterior no resolvió el mensaje):
      0) Respuesta SÍ/NO a una orden física pendiente (comandos.py).
      1) Detección determinista de ACCIONES (comandos.py) -- pedidos de
         escribir hacia un actuador. Nunca llega al LLM.
      2) Resolutor determinista de CONSULTAS informativas (consultas.py,
         Decisión 5) -- preguntas con respuesta exacta calculable. Nunca
         llega al LLM.
      3) LLM (llm_client.ask_llm) -- todo lo que no coincidió con 1) ni 2).

    Esta función es, en sí misma, el "Deterministic Supervisor" del
    sistema (ver ai.pdf): el LLM solo se invoca cuando ninguna de las dos
    etapas deterministas resolvió el mensaje, nunca antes.
    """
    # 0) Respuesta SÍ/NO a una orden física pendiente de confirmación.
    reply_confirmacion = comandos.resolver_confirmacion(chat_id, user_text)
    if reply_confirmacion is not None:
        log.info("Confirmación de acción resuelta | mensaje: %r", user_text)
        llm_client.record_turn(chat_id, user_text, reply_confirmacion)
        return reply_confirmacion

    accion = comandos.detectar_accion(user_text)
    if accion is not None:
        log.info("Acción detectada: %s | mensaje: %r", accion, user_text)
        reply = comandos.procesar_accion(chat_id, accion)
        llm_client.record_turn(chat_id, user_text, reply)
        return reply

    reply_determinista = consultas.resolver_consulta_determinista(user_text)
    if reply_determinista is not None:
        log.info("Consulta resuelta sin LLM (Decisión 5) | mensaje: %r", user_text)
        llm_client.record_turn(chat_id, user_text, reply_determinista)
        return reply_determinista

    return llm_client.ask_llm(chat_id, user_text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "¡Hola! Soy el asistente del huerto de Fundautonoma. "
        "Pregúntame lo que quieras sobre el riego o las plantas."
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_text = update.message.text

    try:
        reply = await asyncio.to_thread(process_user_message, chat_id, user_text)
    except Exception:
        log.exception("Fallo al consultar el LLM local (¿está LM Studio encendido?)")
        reply = prompts.RESPUESTA_LLM_FALLO

    await update.message.reply_text(reply)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    tmp_path = None

    try:
        with tempfile.NamedTemporaryFile(suffix=".oga", delete=False) as tmp:
            tmp_path = tmp.name

        tg_file = await update.message.voice.get_file()
        await tg_file.download_to_drive(tmp_path)

        # Con el modelo ya precargado por _precargar_modelos_de_voz(),
        # esta llamada ya no descarga nada -- solo transcribe.
        user_text = await asyncio.to_thread(voz.transcribe_audio, tmp_path)
        if not user_text:
            await update.message.reply_text(prompts.RESPUESTA_VOZ_NO_ENTENDIDA)
            return

        reply = await asyncio.to_thread(process_user_message, chat_id, user_text)

    except Exception:
        log.exception("Fallo al procesar la nota de voz")
        await update.message.reply_text(prompts.RESPUESTA_VOZ_FALLO)
        return
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    try:
        ogg_bytes = await asyncio.to_thread(voz.synthesize_speech_ogg, reply)
        await update.message.reply_voice(voice=io.BytesIO(ogg_bytes))
    except Exception:
        log.exception("Fallo la síntesis de voz; respondo en texto como respaldo")
        await update.message.reply_text(reply)


def _precargar_modelos_de_voz() -> None:
    """Carga (y, la primera vez, descarga) Whisper y Piper ANTES de
    empezar a recibir tráfico de Telegram -- ver la nota de fix 2 en el
    docstring del módulo para el porqué. Cada modelo se intenta por
    separado: si Piper falla (ej. PIPER_VOICE_PATH mal configurado), el
    bot igual arranca y sigue funcionando en texto y en transcripción,
    solo sin síntesis de voz -- misma filosofía de degradación elegante
    que ya aplica el resto del sistema ante Mongo o LM Studio caídos.
    """
    try:
        voz.get_whisper_model()
    except Exception:
        log.exception(
            "No se pudo precargar Whisper -- las notas de voz van a fallar "
            "hasta que esto se resuelva, pero el resto del bot sigue funcionando."
        )

    try:
        voz.get_piper_voice()
    except Exception:
        log.exception(
            "No se pudo precargar Piper -- las respuestas de voz se van a "
            "enviar como texto de respaldo hasta que esto se resuelva."
        )


def main() -> None:
    log.info("Precargando modelos de voz (Whisper + Piper) antes de iniciar Telegram...")
    try:
        _precargar_modelos_de_voz()
    except KeyboardInterrupt:
        log.warning(
            "Precarga de modelos de voz cancelada por el usuario (Ctrl+C) "
            "antes de terminar. El bot no llegó a iniciar -- ningún usuario "
            "de Telegram estuvo esperando respuesta mientras tanto. Vuelve a "
            "correr 'python3 bot.py' (si la descarga alcanzó a avanzar, "
            "huggingface_hub reutiliza lo que ya bajó en vez de empezar de "
            "cero) y espera a que termine sin volver a presionar Ctrl+C."
        )
        return

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    log.info("Bot iniciado. Escuchando mensajes de Telegram...")
    app.run_polling()


if __name__ == "__main__":
    main()