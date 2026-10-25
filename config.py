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
# Backend del LLM: "ollama" (contenedor huerto-ollama, API nativa /api/chat)
# u "openai" (cualquier servidor compatible con OpenAI, p.ej. LM Studio en
# el host). OJO: el nombre del modelo cambia según el backend -- Ollama lo
# llama "qwen3.5:4b", LM Studio "qwen3.5-4b".
LLM_BACKEND = os.environ.get("LLM_BACKEND", "ollama").strip().lower()
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11435")
# Cuánto tiempo deja Ollama el modelo cargado en RAM tras cada llamada.
# Largo a propósito: en CPU (PC donado, Raspberry Pi 5) recargar los ~3 GB
# del modelo en cada pregunta costaría más que la respuesta misma.
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "24h")
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "4096"))
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:1234/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen3.5:4b" if LLM_BACKEND == "ollama" else "qwen3.5-4b")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "lm-studio")  # solo backend "openai"; LM Studio ignora el valor
MAX_HISTORY_TURNS = int(os.environ.get("MAX_HISTORY_TURNS", "4"))
REQUEST_TIMEOUT_S = int(os.environ.get("REQUEST_TIMEOUT_S", "60"))

# --------------------------------------------------------------------------
# Acciones sobre actuadores (comandos.py -> API HTTP de Node-RED -> MQTT)
# --------------------------------------------------------------------------
# El bot no habla MQTT: Node-RED es la única ruta de escritura hacia el
# ESP32 (valida modo, publica en riego/* y espera la confirmación en
# huerto/general). 127.0.0.1 a propósito: el endpoint rechaza cualquier
# petición que no venga de la propia máquina.
NODERED_ACCION_URL = os.environ.get("NODERED_ACCION_URL", "http://127.0.0.1:1880/huerto/api/accion")
# Debe ser MAYOR que el tiempo de espera de confirmación configurado en el
# nodo de Node-RED (10 s); si no, el bot cortaría antes de saber el resultado.
ACCION_TIMEOUT_S = int(os.environ.get("ACCION_TIMEOUT_S", "20"))
# Cuánto tiempo espera el bot un SÍ antes de descartar una orden que abre
# agua o pasa a manual. Corto a propósito: un "sí" de hace 10 minutos ya
# no responde a la pregunta que el cuidador tiene en mente.
CONFIRMACION_TTL_S = int(os.environ.get("CONFIRMACION_TTL_S", "60"))

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