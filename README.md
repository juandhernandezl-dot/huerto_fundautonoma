# Bot de Telegram — Huerto Fundautonoma (MVP lean + voz)

Bot que conecta Telegram con el LLM local (Qwen3.5-4B servido por Ollama en un
contenedor Docker; LM Studio en el host queda como backend alternativo),
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
✅ Maneja el caso de que el servidor del modelo (Ollama), MongoDB o la voz no estén disponibles,
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

## 3. El LLM local (Ollama en contenedor)

Desde la rama `agente-ollama`, el modelo ya no depende de LM Studio: corre en
el contenedor `huerto-ollama`, **solo en CPU**, y se levanta con el resto del
stack (Mongo, Node-RED, bot y autoheal):

```bash
docker compose up -d --build
```

- La primera vez, el servicio `ollama-pull` descarga `qwen3.5:4b` (~3.3 GB) al
  volumen `ollama_data`. Sigue el progreso con `docker compose logs -f ollama-pull`
  (repite "pulling manifest" mientras descarga; al terminar el contenedor queda
  en "Exited (0)", es normal). Mientras no termine, el bot responde su mensaje
  de fallo en vez de colgarse.
- Ollama escucha en `127.0.0.1:11435` (no 11434, para no chocar con un Ollama
  nativo). Se usa su API **nativa** `/api/chat` porque es la única que respeta
  `think: false` en Qwen3.5.
- Prueba directa del modelo, sin el bot:
```bash
  curl -s localhost:11435/api/chat -d '{"model":"qwen3.5:4b","think":false,"stream":false,"messages":[{"role":"user","content":"Hola"}]}'
```
- En `.env` el modelo se llama `qwen3.5:4b` (con dos puntos). Con
  `qwen3.5-4b` (el nombre de LM Studio) Ollama responde 404.
- Por qué solo CPU: la GPU del PC de desarrollo (RTX 3050, 4 GB) no se pudo
  usar desde el contenedor (ver `prueba_agente`), y el despliegue final
  (Raspberry Pi 5 / PC donado) no tiene GPU NVIDIA.
- Para volver a LM Studio en el host: `LLM_BACKEND=openai` y `LLM_MODEL=qwen3.5-4b`
  en `.env`, y `docker compose restart bot`.

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

`tests/test_smoke.py` simula el servidor del modelo (Ollama nativo y
compatible con OpenAI) para probar que `ask_llm()` arma bien la petición en
ambos backends y guarda el historial, y además verifica la conversión
de audio WAV → OGG/Opus (el formato que exige Telegram), todo sin necesitar
token real ni servidor de modelo encendido:

```bash
python3 tests/test_smoke.py
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

## Acciones físicas (válvulas, bomba, modo)

El bot puede abrir/cerrar válvulas, encender/apagar la bomba y cambiar el
modo, pero **nunca publica MQTT**: hace POST a una API de Node-RED
(`/huerto/api/accion`, solo desde la propia máquina), y el nodo
**Supervisor riego** (`otros/nodered-supervisor-riego.js`) valida, publica a
través de los switches del dashboard y confirma con la telemetría real.

Reglas (consejo del 20 oct 2026, `acciones-agente/council-transcript.md` en el proyecto):

- Abrir válvula, encender bomba o pasar a manual piden **SÍ/NO** repitiendo
  la orden. Cerrar, apagar o volver a automático se ejecutan directo.
- Órdenes ambiguas (dos zonas, zona 4, dos acciones, "no" antes de abrir)
  se rechazan; nunca se adivina.
- Abrir agua es un préstamo: en manual, cada válvula o la bomba se apaga
  sola a los **20 min** (venga del bot o del dashboard; repetir "abre" no
  reinicia el plazo), la bomba sin ninguna válvula abierta se apaga a los
  **30 s**, y el manual vuelve solo a automático a los **30 min** (o al vencer
  la última válvula). El bot le dice al cuidador la hora exacta.
- Al arrancar Node-RED, o si la telemetría se corta más de 5 s (ESP32 o
  broker reiniciados), todo va a 0 y a automático. Ningún tópico usa retain.

**Límite conocido (no lo cierra el software del PC):** si caen el PC o el
broker, el `reconnect()` bloqueante de `Receptor.ino` congela el ESP32 con el
relé de la bomba como estaba. Hasta el cambio de firmware, la protección es
el **corte físico rotulado** junto a la bomba.

### Configurar Node-RED (a mano, en http://localhost:1880/)

1. Función **control modo**: primera línea `flow.set("modo_riego", msg.payload);`
2. Nodo **inject**: repetir cada 5 s, `msg.topic` = `tick`.
3. Nodo **http in**: `POST`, URL `/huerto/api/accion`.
4. Nodo **function** "Supervisor riego", **Outputs = 6**, con el contenido
   de `otros/nodered-supervisor-riego.js`.
5. Entradas del supervisor: el **http in**, el **inject** y la salida del
   nodo **json** que parsea `huerto/general`.
6. Salidas: 1 → **http response** nuevo; 2 → switch "Bomba Manual";
   3 → "Modo Manual"; 4/5/6 → "ValvulaZona1/2/3". Deploy.

`HUECO_TELEMETRIA_MS` (5 s) debe ser mayor que 3 periodos de publicación del
ESP32. Si el firmware Wi-Fi publica más lento, súbelo o el supervisor
aplicará el estado seguro sin parar.

### Probar sin ESP32 (todo dentro del PC)

Paso a paso, con `docker compose exec`, en [`tests/PRUEBAS_ACCIONES.md`](tests/PRUEBAS_ACCIONES.md):
pruebas de lógica, ESP32 simulado (`tests/simulador_esp32.py`), chat por
terminal sin Telegram (`tests/chat_consola.py`), topes, fallas (ESP32,
Node-RED y Mosquitto caídos) y limpieza de los datos de prueba en Mongo.

### Deshacer

- Código: `git revert <commit>` (o, antes de aplicar, `git branch respaldo-antes-acciones`).
- Node-RED: borra los nodos nuevos y la línea de "control modo", o restaura
  tu respaldo con el contenedor detenido:
  `docker compose stop node-red && cp ~/respaldo/flows.json ~/.node-red/flows.json && docker compose start node-red`.
  Node-RED además guarda `.flows.json.backup` (el estado anterior al último Deploy) en la misma carpeta.

### Estado después de una orden (Mongo guarda 1 documento por minuto)

Las consultas de estado siguen leyendo Mongo. Como `telemetria` recibe como
máximo un documento por minuto, una orden recién confirmada por Node-RED puede
ser más nueva que el último documento. Mientras eso pase, `telemetria.py`
muestra el estado de esa válvula/bomba según la orden y lo dice ("Por tu orden
confirmada hace N segundos… lo guardado en la base todavía es anterior"). En
cuanto entra un documento posterior a la orden, Mongo vuelve a mandar; si no
entra ninguno, la orden caduca a los 120 s (`ORDEN_VIGENCIA_S`). Solo cuenta lo
que Node-RED respondió `confirmado`. La edad se da en segundos si es menor a
minuto y medio, y toda respuesta de estado avisa que se guarda un dato por
minuto. Lo que NO cubre: un cambio hecho a mano o desde el dashboard dentro del
último minuto; el aviso lo menciona. La memoria de órdenes vive en el proceso
del bot (si reinicia, se responde solo con Mongo).

### Eventos de válvula (colección `eventos`)

Para que el bot también vea los cambios que no hizo él (dashboard, llave a
mano) sin subir la frecuencia de `telemetria`, Node-RED escribe un evento en
`eventos` en cuanto una válvula cambia: `{timestamp, tipo: "valvula_abierta"|
"valvula_cerrada", zona}`. La bomba ya la cubre `detectar_cambio_riego`
(`riego_on`/`riego_off`). El bot lee los eventos posteriores al último
documento y los superpone igual que una orden confirmada ("Según el último
cambio registrado hace N segundos…"). `telemetria` no se toca, así que la LSTM
no cambia; `_calcular_tiempo_riego` solo mira `riego_on/off` y ignora los tipos
nuevos. Código del nodo: `otros/nodered-detectar-cambio-valvulas.js`.

Paso a paso en http://localhost:1880/ (flujo "datos"):
1. Arrastra un nodo **function**, nómbralo `detectar_cambio_valvulas`, pega el
   contenido de `otros/nodered-detectar-cambio-valvulas.js`, Done.
2. Cablea la **salida del nodo `datos`** a la entrada del nuevo nodo (la misma
   salida que ya va a `detectar_cambio_riego` y al delay).
3. Cablea la salida del nuevo nodo a la entrada de **`envolver payload eventos`**
   (el mismo nodo al que ya llega `detectar_cambio_riego`).
4. Deploy. La primera lectura tras cada reinicio de Node-RED solo fija la
   referencia; no genera evento.

## Siguiente paso natural

El bot ya narra el estado *actual* del huerto (última lectura de `telemetria`).
Lo que falta es que además narre lo que *va a pasar* — eso es la Opción B
(LSTM + MPC), pausada hasta acumular más datos reales y definir bien el
alcance con la fundación. Cuando esa pieza exista, este mismo bot podría
incluir también la predicción en el contexto que le pasa al LLM, sin
cambiar la arquitectura.