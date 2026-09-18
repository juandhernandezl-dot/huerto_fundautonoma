"""
Configuración centralizada: variables de entorno y parámetros de
comportamiento (Capítulo 8, §8.4 — "archivo de configuración junto con
las variables de entorno: separación entre parámetros de comportamiento
y credenciales"). Único punto de lectura de os.environ en todo el
proyecto; el resto de los módulos importan este módulo, nunca leen
os.environ directamente.
"""
import os

# --------------------------------------------------------------------------
# Telegram / LLM
# --------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]  # obligatorio, sin default
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:1234/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen3.5-4b")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "lm-studio")  # LM Studio ignora el valor
MAX_HISTORY_TURNS = int(os.environ.get("MAX_HISTORY_TURNS", "4"))
REQUEST_TIMEOUT_S = int(os.environ.get("REQUEST_TIMEOUT_S", "60"))

# --------------------------------------------------------------------------
# Voz (STT: faster-whisper / TTS: Piper)
# --------------------------------------------------------------------------
WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "small")
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")  # int8 = rápido en CPU
WHISPER_LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "es")
# Ruta al modelo .onnx de Piper (el .onnx.json debe estar en la misma carpeta).
PIPER_VOICE_PATH = os.environ.get("PIPER_VOICE_PATH", "")

# --------------------------------------------------------------------------
# MongoDB (telemetría real del huerto)
# --------------------------------------------------------------------------
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.environ.get("MONGO_DB", "huerto")
MONGO_COLLECTION = os.environ.get("MONGO_COLLECTION", "telemetria")
EVENTOS_COLLECTION = os.environ.get("EVENTOS_COLLECTION", "eventos")
# Ventana de tiempo que cubre el "resumen histórico" (tiempo de riego,
# humedad promedio). 24h = rolling window, no "calendario" (más simple).
HISTORIAL_HORAS = int(os.environ.get("HISTORIAL_HORAS", "24"))
# Si el dato más reciente es más viejo que esto, se avisa que está
# desactualizado en vez de presentarlo como si fuera en vivo.
TELEMETRY_STALE_MINUTES = int(os.environ.get("TELEMETRY_STALE_MINUTES", "60"))
