# Bot de Telegram — Huerto Fundautonoma (MVP lean + voz)

Bot que conecta Telegram con el LLM local (Qwen3.5-4B vía LM Studio),
ya elegido tras la evaluación a ciegas con usuarios. Sin frameworks de agentes.
Soporta texto y notas de voz (faster-whisper para escuchar, Piper para hablar).

## Qué hace y qué no hace (a propósito)

✅ Recibe mensajes de **texto** en Telegram → LLM local → responde en texto.
✅ Recibe **notas de voz** → las transcribe (faster-whisper) → LLM local →
   responde con **una nota de voz** (Piper), y con texto si la síntesis falla.
✅ Consulta la **telemetría real** del huerto en MongoDB (colección
   `telemetria`, la de la Opción A) en cada mensaje, y se la pasa al LLM como
   contexto — así puede responder con datos verdaderos, no inventados.
✅ Mantiene un historial corto por conversación (en memoria, se pierde al reiniciar).
✅ Maneja el caso de que LM Studio, MongoDB o la voz no estén disponibles,
   sin romperse (cae a texto / dice que no hay datos).

❌ No usa Hermes Agent ni LangChain — si este MVP se queda corto, esa es la migración de fase 2.
❌ No predice el futuro (eso es la LSTM+MPC, Opción B, en pausa hasta tener
   más datos reales y definir alcance con la fundación) — solo narra el
   estado *actual* de los sensores, no lo que va a pasar.

## 1. Crear el bot en Telegram (una sola vez)

1. Abre Telegram y busca **@BotFather**.
2. Envíale `/newbot`, dale un nombre y un username (debe terminar en `bot`, ej. `huerto_fundautonoma_bot`).
3. BotFather te entrega un **token** (algo como `123456789:ABCdefGhIJKlmNoPQRstuVwxYZ`). Guárdalo.

## 2. Preparar el entorno

Primero, una dependencia de sistema (no de Python) que convierte el audio al
formato que Telegram exige para notas de voz:

```bash
sudo apt install ffmpeg
```

Luego el entorno de Python:

```bash
cd huerto-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

> Nota: `faster-whisper` y `piper-tts` son librerías más pesadas que las del
> MVP de solo texto. La primera vez que llegue una nota de voz, el bot
> descargará el modelo de Whisper (~500 MB para "small") desde Hugging Face;
> tarda un momento y luego queda en caché.

### Descargar una voz de Piper (para las respuestas en audio)

```bash
mkdir -p piper-voices
cd piper-voices
python -m piper.download_voices es_MX-claude-high
cd ..
```

Esto descarga `es_MX-claude-high.onnx` y su `.onnx.json` a la carpeta
`piper-voices/`. Elegí español de México (acento latino neutro, más cercano
al colombiano que el de España) y calidad "high". Si tu PC donado es muy
lento, puedes buscar una variante `-medium` o `-low` del mismo estilo en
<https://rhasspy.github.io/piper-samples/> (ahí puedes escuchar muestras
antes de decidir).

Si dejas `PIPER_VOICE_PATH` vacío en tu `.env`, el bot sigue funcionando:
transcribe la voz del usuario igual, pero responde en texto en vez de audio.

Copia la plantilla de variables de entorno y complétala con tu token:

```bash
cp .env.example .env
# edita .env y pega tu TELEGRAM_BOT_TOKEN
```

Cárgalas en la terminal antes de correr el bot:

```bash
export $(grep -v '^#' .env | xargs)
```

## 3. Encender el LLM local (LM Studio)

1. Abre LM Studio, carga **qwen3.5-4b** (el modelo ganador de las pruebas).
2. Ve a la pestaña **Local Server** (icono `<->`) y actívalo.
3. Confirma que el nombre del modelo mostrado ahí coincide con `LLM_MODEL` en tu `.env`.
   Por defecto sirve en `http://localhost:1234/v1` (ya es el default del bot).

## 4. Correr el bot

```bash
python3 bot.py
```

Deberías ver en la terminal:
```
[INFO] huerto-bot: Bot iniciado. Escuchando mensajes de Telegram...
```

Abre Telegram, busca tu bot por su username, y mándale `/start` y luego cualquier mensaje.

## 5. Probar la lógica sin Telegram (ya incluido y verificado)

`test_smoke.py` simula el servidor de LM Studio para probar que `ask_llm()`
arma bien la petición y guarda el historial, y además verifica la conversión
de audio WAV → OGG/Opus (el formato que exige Telegram), todo sin necesitar
token real ni LM Studio encendido:

```bash
python3 test_smoke.py
```

> Esta prueba **no** cubre Whisper ni Piper de extremo a extremo (transcribir
> o sintetizar audio real), porque eso requiere los modelos descargados. Para
> probar la voz de verdad, el camino es correr el bot y mandarle una nota de
> voz real por Telegram — no hay atajo que la reemplace.

## Probar la voz una vez corriendo

1. Corre `python3 bot.py` como en el paso 4.
2. En Telegram, en vez de escribir, **manda una nota de voz** preguntando
   algo, por ejemplo grabando: *"¿Cómo está la zona 1?"*
3. Deberías recibir de vuelta otra nota de voz con la respuesta.
4. Si en cambio recibes texto, revisa los logs: lo más común es que
   `PIPER_VOICE_PATH` esté vacío o mal escrito, o que falte `ffmpeg`.

## Siguiente paso natural

El bot ya narra el estado *actual* del huerto (última lectura de `telemetria`).
Lo que falta es que además narre lo que *va a pasar* — eso es la Opción B
(LSTM + MPC), pausada hasta acumular más datos reales y definir bien el
alcance con la fundación. Cuando esa pieza exista, este mismo bot podría
incluir también la predicción en el contexto que le pasa al LLM, sin
cambiar la arquitectura.
