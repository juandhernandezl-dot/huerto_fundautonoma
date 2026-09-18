"""
Módulo de voz (Capítulo 8, §8.4 — "un módulo de voz, que agrupa la
transcripción con faster-whisper y la síntesis con Piper"): entrada de
notas de voz de Telegram (.ogg/Opus) -> faster-whisper -> texto, y
respuesta -> Piper -> WAV -> ffmpeg -> .ogg/Opus -> Telegram.
"""
import io
import logging
import shutil
import subprocess
import wave

from faster_whisper import WhisperModel
from piper import PiperVoice

import config
import prompts

log = logging.getLogger(__name__)

if shutil.which("ffmpeg") is None:
    log.warning(
        "ffmpeg no está instalado (sudo apt install ffmpeg): las respuestas "
        "de voz no se podrán convertir al formato que exige Telegram."
    )

# Los modelos de voz se cargan de forma perezosa (solo la primera vez que se
# reciba una nota de voz), no al arrancar. Así:
#  1) el bot sigue arrancando rápido si todavía no se usa voz,
#  2) el modo texto (ya validado en producción) no depende de ellos,
#  3) no se descarga ~500MB de Whisper si nunca llega un audio.
_whisper_model: WhisperModel | None = None
_piper_voice: PiperVoice | None = None


def get_whisper_model() -> WhisperModel:
    global _whisper_model
    if _whisper_model is None:
        log.info("Cargando modelo de voz a texto (faster-whisper, %s)...", config.WHISPER_MODEL_SIZE)
        _whisper_model = WhisperModel(
            config.WHISPER_MODEL_SIZE, device="cpu", compute_type=config.WHISPER_COMPUTE_TYPE
        )
    return _whisper_model


def get_piper_voice() -> PiperVoice:
    global _piper_voice
    if _piper_voice is None:
        if not config.PIPER_VOICE_PATH:
            raise RuntimeError("PIPER_VOICE_PATH no está configurado en .env")
        log.info("Cargando voz de Piper desde %s...", config.PIPER_VOICE_PATH)
        _piper_voice = PiperVoice.load(config.PIPER_VOICE_PATH)
    return _piper_voice


def transcribe_audio(audio_path: str) -> str:
    """Transcribe un archivo de audio (cualquier formato que ffmpeg soporte,
    incluido el .oga/Opus de las notas de voz de Telegram) a texto en español.
    Usa prompts.WHISPER_INITIAL_PROMPT para sesgar el reconocimiento hacia
    el vocabulario del huerto (zona, válvula, riego, bomba, etc.).
    """
    segments, _info = get_whisper_model().transcribe(
        audio_path,
        language=config.WHISPER_LANGUAGE,
        beam_size=1,
        initial_prompt=prompts.WHISPER_INITIAL_PROMPT,
    )
    # segments es un generador: hay que consumirlo para que se genere el texto.
    return " ".join(segment.text for segment in segments).strip()


def wav_bytes_to_ogg_opus(wav_bytes: bytes) -> bytes:
    """Convierte WAV a .ogg/Opus, el único formato que Telegram acepta para que
    un mensaje se muestre como nota de voz (send_voice) en vez de como archivo.
    """
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", "pipe:0",
            "-c:a", "libopus",
            "-b:a", "32k",
            "-ar", "48000",
            "-f", "ogg",
            "pipe:1",
        ],
        input=wav_bytes,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg falló: {proc.stderr.decode(errors='ignore')[-300:]}")
    return proc.stdout


def synthesize_speech_ogg(text: str) -> bytes:
    """Convierte texto a una nota de voz .ogg/Opus lista para enviar por Telegram
    (Piper genera WAV; wav_bytes_to_ogg_opus hace la conversión final)."""
    voice = get_piper_voice()

    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        voice.synthesize_wav(text, wav_file)

    return wav_bytes_to_ogg_opus(wav_buffer.getvalue())
