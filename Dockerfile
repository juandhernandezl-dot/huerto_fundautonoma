# A propósito NO copia bot.py/config.py/etc. aquí -- el código se monta
# como bind mount en docker-compose.yml (volumes: - .:/app). Así:
#   - Editar un .py no exige rebuild, solo `docker compose restart bot`.
#   - logs/ y piper-voices/ sobreviven a un rebuild de imagen, porque
#     viven en el host (mismo mount).
# Convención tomada del Dockerfile ya probado de motor_agent/docker/.
#
# El modelo de Whisper, en cambio, SÍ vive horneado dentro de la imagen
# (ver ARG WHISPER_MODEL_SIZE más abajo) -- ya sufrimos la descarga lenta
# de ~500MB en caliente dos veces en este proyecto. Hornearlo en el
# build (que corre una sola vez, no cada arranque de contenedor) saca
# esa descarga del camino crítico para siempre.
FROM python:3.10-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Solo requirements.txt entra a la imagen -- así el layer de pip install
# (el más lento) solo se invalida cuando cambian las dependencias, no
# cada vez que se edita un .py.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# hf_xet (el backend acelerado de descargas de Hugging Face que
# faster-whisper usa por debajo) tiene un bug conocido: se cuelga
# indefinidamente en ciertas redes, sin error visible. Forzamos el
# fallback al HTTP clásico de huggingface_hub -- más lento pero
# confiable. Puede explicar parte de la lentitud ya vista con este
# mismo modelo fuera de Docker.
ENV HF_HUB_DISABLE_XET=1

# /opt/hf_cache, NO el default (~/.cache/huggingface) -- mismo motivo
# que en motor: /app se reemplaza por completo con el bind mount del
# host en tiempo de ejecución, así que cualquier cosa horneada bajo una
# ruta que termine tapada por ese mount desaparecería. /opt queda fuera
# de lo que se monta, así que sobrevive.
ENV HF_HOME="/opt/hf_cache"

# Hornea el modelo de Whisper en la imagen -- así la primera nota de voz
# real no depende de la red en tiempo de ejecución. Debe coincidir con
# WHISPER_MODEL_SIZE/WHISPER_COMPUTE_TYPE en tu .env real -- si no
# coinciden, bot.py pide en tiempo de ejecución un modelo distinto al
# horneado aquí, y lo vuelve a descargar en caliente (sigue
# funcionando, solo pierde la ventaja de tenerlo ya listo).
ARG WHISPER_MODEL_SIZE=small
ARG WHISPER_COMPUTE_TYPE=int8
RUN python3 -c "from faster_whisper import WhisperModel; WhisperModel('$WHISPER_MODEL_SIZE', device='cpu', compute_type='$WHISPER_COMPUTE_TYPE')"

# Dependencias SOLO de las pruebas (simulador del ESP32). Va al final para no
# invalidar la capa que hornea el modelo de Whisper. Aquí se actualiza pip y
# se silencian los avisos de "root"/"nueva versión" de pip en `docker compose exec`.
ENV PIP_ROOT_USER_ACTION=ignore \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements-test.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements-test.txt

CMD ["python3", "bot.py"]