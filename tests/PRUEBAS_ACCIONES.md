# Comandos de prueba — Acciones del agente (válvulas, bomba, modo)

Mismo estilo que la guía del stack contenerizado: todo con `docker compose exec`
desde `~/Documents/huerto-bot`. Mosquitto sigue en el host.

Se usan **3 terminales** (A, B, C). Cada paso dice cuál.

## 0. Antes de empezar

- Confirma que tienes los archivos de esta guía (si alguno falta, te falta aplicar un parche):

```bash
cd ~/Documents/huerto-bot
ls tests/simulador_esp32.py tests/chat_consola.py tests/limpiar_pruebas.js requirements-test.txt
```

- **Apaga el ESP32 real** (o desconéctalo del Wi-Fi). El simulador publica en
  `huerto/general`; dos "ESP32" a la vez mezclarían telemetrías, y el simulador
  escribiría datos falsos en tu Mongo mientras el real también escribe.
- Marca la hora de inicio, para limpiar al final solo lo que generó la prueba:

```bash
date -u +%Y-%m-%dT%H:%M:%SZ > /tmp/inicio_pruebas && cat /tmp/inicio_pruebas
```

## 1. Arrancar los servicios (terminal A)

```bash
sudo systemctl start mosquitto
cd ~/Documents/huerto-bot
docker compose up -d
docker compose restart bot      # el código está montado en vivo: así toma los .py nuevos
docker compose ps               # autoheal, huerto-mongo, huerto-node-red, huerto-bot: Up (healthy)
```

Comprueba que tu Node-RED tiene el cambio de seguridad en "Supervisor riego"
(debe imprimir `1`):

```bash
grep -c "const ceros = vacio()" /home/reds7/.node-red/flows.json
```

## 2. Pruebas de lógica (sin MQTT, sin ESP32)

```bash
docker compose exec bot python3 tests/test_acciones.py
docker compose exec bot python3 tests/test_smoke.py
```

La última línea de cada una debe empezar con ✅.

## 3. ESP32 simulado

**Terminal A** (se queda corriendo; es interactivo):

```bash
docker compose exec bot python3 tests/simulador_esp32.py
```

`paho-mqtt` ya viene en la imagen (`requirements-test.txt`). Si te dice
`Falta paho-mqtt`, tu imagen es anterior a ese archivo: reconstruye una vez con
`docker compose build bot && docker compose up -d bot` (reutiliza las capas, no
vuelve a descargar Whisper).

Debe mostrar `conectado al broker localhost:1883` y `modo=auto válvulas=[0, 0, 0] bomba=0`.
Comandos que acepta mientras corre: `estado`, `hum 1 20`, `corte`, `vuelta`, `q`.

**Terminal B** — comprobar que Node-RED ve la telemetría y aplicó el estado seguro:

```bash
mosquitto_sub -h localhost -t huerto/general -C 2
docker compose logs --tail 20 node-red | grep -i "estado seguro"
```

Esperado: dos JSON con `"bomba":0` y una línea `Estado seguro aplicado: arranque`.

## 4. Hablar con el bot sin Telegram (terminal C)

```bash
docker compose exec bot python3 tests/chat_consola.py
```

Escribe una frase por línea. Qué esperar (y qué ver en la terminal A):

| Tú | Bot | Simulador (terminal A) |
|---|---|---|
| `abre la válvula 1` | pregunta SÍ/NO | nada |
| `sí` | dice que hace falta modo manual | nada (estás en automático) |
| `pon el modo manual` | pregunta SÍ/NO | nada |
| `sí` | "ya envié la orden… no puedo confirmarlo… vuelve solo a automático a las HH:MM" | `riego/modo = 'manual'` y `modo=MANUAL` |
| `abre la válvula 1` → `sí` | "Listo… se cierra sola a las HH:MM" | `válvulas=[1, 0, 0]` |
| `¿cómo está la zona 1?` (a los pocos segundos) | "Hace N segundos… válvula abierta. Por tu orden confirmada hace N segundos, la válvula de la zona 1 está abierta (lo guardado en la base todavía es anterior)." y el aviso de que se guarda un dato por minuto | — |
| `enciende la bomba` → `sí` | "Listo… se apaga sola a las HH:MM" | `bomba=1` |
| `cierra la válvula 1` | "Listo" **sin preguntar** | `válvulas=[0, 0, 0]` |
| (no hagas nada ~35 s) | — | la bomba queda sola y se apaga: `bomba=0` |
| `vuelve a automático` | directo | `modo=auto` |

Después de ~2 min sin nuevas órdenes (o en cuanto Mongo guarde un documento
posterior a la orden), la misma pregunta responde solo con Mongo, sin la frase
"Por tu orden…". Mira lo guardado con:
`mongosh --port 27019 huerto --eval "db.telemetria.find().sort({timestamp:-1}).limit(1)"`.

También prueba que **no adivina**: `abre la válvula 1 y la 2`, `abre la válvula 7`,
`no abras la válvula 1`. Las tres deben responder que no entendió, y el
simulador no debe recibir nada.

### 4b. Eventos de válvula (Node-RED) y estado sin esperar a Mongo

Requiere haber creado el nodo **detectar_cambio_valvulas** (ver README, sección
"Eventos de válvula"). Con el simulador y el chat corriendo:

1. En el chat: `pon el modo manual` → `sí`, `abre la válvula 2` → `sí`.
2. En otra terminal, mira el evento (con `.toArray()`; sin él `--eval` no imprime nada):

```bash
mongosh --port 27019 huerto --quiet --eval 'printjson(db.eventos.find({tipo:/valvula/}).sort({timestamp:-1}).limit(3).toArray())'
```

   Debe haber un `valvula_abierta` con `zona: 2`, aunque `telemetria` aún no tenga documento nuevo.
3. **Cambio que no viene del bot**: en http://localhost:1880/ui activa *ValvulaZona3*.
   En el chat, a los segundos: `¿cómo está la zona 3?` → "…válvula abierta. Según el
   último cambio registrado hace N segundos, la válvula de la zona 3 está abierta…".
   (Si el último documento de Mongo ya es posterior al cambio, esa frase no aparece: es lo correcto.)
4. `cierra la válvula 2` y apaga *ValvulaZona3* en el dashboard: aparecen dos `valvula_cerrada`.
   Al terminar, `vuelve a automático`.
5. Comprobar que `telemetria` sigue a un documento por minuto:

```bash
mongosh --port 27019 huerto --quiet --eval 'printjson(db.telemetria.find({},{_id:0,timestamp:1}).sort({timestamp:-1}).limit(4).toArray())'
```

Escribe las frases del chat en el chat (`chat_consola.py`), no en la terminal del
sistema, y no pegues las líneas con ``` ni con comillas invertidas.

## 5. Topes (opcional, para no esperar 20 minutos)

En http://localhost:1880/ abre el nodo **Supervisor riego** y cambia
**temporalmente** estas tres líneas; Deploy:

```js
const MAX_ABIERTO_MS = 20 * 1000;
const MAX_MANUAL_MS = 30 * 1000;
const BOMBA_SIN_VALVULA_MS = 8 * 1000;
```

Pasa a manual, abre la válvula 1 y enciende la bomba (como en el paso 4).
Esperado: a los ~25 s válvula y bomba se apagan solas, y a los ~35 s el
simulador muestra `modo=auto`. **Devuelve las constantes originales
(`20 * 60 * 1000`, `30 * 60 * 1000`, `30 * 1000`) y Deploy.**

## 6. Fallas

**ESP32 que se corta** — en la terminal A del simulador:

```
corte
```

Espera 8 s y en el chat: `abre la válvula 1` → `sí`. Debe decir que no hay
telemetría. Luego en el simulador `vuelta` y:

```bash
docker compose logs --tail 20 node-red | grep -i "estado seguro"
```

Esperado: `Estado seguro aplicado: hueco de telemetría`.

**Node-RED caído**:

```bash
docker compose stop node-red
```

En el chat: `apaga la bomba` → debe decir que no pudo comunicarse y que la orden
no salió (nunca "Listo"). Luego `docker compose start node-red`.

**Mosquitto caído**:

```bash
sudo systemctl stop mosquitto
```

Pasados 8 s, cualquier orden del chat debe responder "sin telemetría". Luego
`sudo systemctl start mosquitto`: el simulador reconecta solo y Node-RED
registra `Estado seguro aplicado: hueco de telemetría`.

**Switch del dashboard que quedó encendido** (la falla que se corrigió):
en http://localhost:1880/ui activa *Modo Manual*, luego *ValvulaZona1*, luego
apaga *Modo Manual* (la válvula queda en ON en pantalla). En el chat:
`pon el modo manual` → `sí`. El simulador debe mostrar `válvulas=[0, 0, 0]`;
si mostrara `[1, 0, 0]`, falta el cambio de la sección 1.

## 7. Telegram

El bot real ya corre en el contenedor. Con el simulador encendido:

```bash
docker compose logs -f bot
```

y repite el guion del paso 4 desde el teléfono (texto o nota de voz). Un SÍ
tarda como máximo 60 s en caducar.

## 8. Limpiar y apagar

En el simulador: `q`. La prueba deja datos falsos en Mongo: el simulador publica
humedades y válvulas que Node-RED guarda como si fueran reales (se ven como saltos
en el histórico del dashboard). Se limpia con `tests/limpiar_pruebas.js`, desde
`~/Documents/huerto-bot` y en el host (necesita `mongosh`). Escribe cada comando
tal cual, sin las líneas de ``` .

**Paso 1 — respaldo (siempre):**

```bash
docker compose exec mongo mongodump --port 27019 --db huerto --archive=/data/db/respaldo_$(date +%F_%H%M).archive
```

**Paso 2a — vaciar TODO** (`telemetria` y `eventos`; úsalo mientras no haya datos reales):

```bash
BORRAR_TODO=si mongosh --port 27019 huerto --quiet --file tests/limpiar_pruebas.js
```

**Paso 2b — borrar solo lo posterior al inicio de la prueba** (cuando ya haya datos reales
antes). `DESDE` va en UTC, la hora que muestra el simulador:

```bash
DESDE=2026-10-25T04:30:00Z mongosh --port 27019 huerto --quiet --file tests/limpiar_pruebas.js
```

El script imprime cuántos documentos hay antes, cuántos borra y cuántos quedan. Esperado
tras 2a: `Después: telemetria=0  eventos=0`. Con el ESP32 real encendido, `telemetria`
vuelve a llenarse sola a un documento por minuto.

Si tu MongoDB no permite borrar por fecha en la colección de series de tiempo (solo
pasa en 2b), el script la reconstruye conservando lo anterior y deja una copia en
`telemetria_conservada`; bórrala tú cuando verifiques con
`mongosh --port 27019 huerto --quiet --eval 'db.telemetria_conservada.drop()'`.

Para volver atrás con el respaldo del paso 1 (restaura sobre lo que haya):

```bash
docker compose exec mongo mongorestore --port 27019 --drop --archive=/data/db/respaldo_AAAA-MM-DD_HHMM.archive
```

Si no vas a seguir: `docker compose down`.

## 9. Pasar al ESP32 real

Todo lo anterior prueba el software (bot, Node-RED, MQTT). El simulador **no
imita** el CAN, los relés ni el bloqueo del firmware cuando se cae el broker, así
que esta parte se hace aparte y por etapas. Si una etapa falla, no pases a la siguiente.

**Antes de encender (con el simulador apagado: `q`):**
- Confirma que el firmware cargado en el receptor y en los nodos es el que usas.
  Está sin verificar que el mensaje de modo por CAN coincida entre `receptor.ino` y
  `emisores.ino` (el receptor lo envía con 1 byte y el nodo espera [nodo, modo]).
  Si tu receptor ignora el cambio de modo, las órdenes del bot no tendrán efecto
  físico aunque Node-RED las confirme. Pruébalo en la etapa 2.
- Ten a mano el **corte físico** (interruptor de la bomba o de la alimentación) y
  rotúlalo. Si cae el PC, Node-RED o el broker, el ESP32 se queda esperando
  reconexión con los relés en su último estado: eso solo lo corta el interruptor.
- Limpia los datos falsos (sección 8) para que el histórico empiece con datos reales.

**Etapa 1 — solo telemetría (sin tocar nada físico).** Enciende el ESP32 real.

```bash
mosquitto_sub -h localhost -t huerto/general -C 2
docker compose logs --tail 20 node-red | grep -i "estado seguro"
```

Esperado: dos JSON con tus humedades reales y `"bomba":0`, y `Estado seguro aplicado: arranque`.
En el chat: `¿cómo está la zona 1?` debe mostrar la humedad real con "Hace N segundos".
Un minuto después debe haber un documento nuevo en `telemetria`.

**Etapa 2 — modo y una válvula, con la bomba desconectada y sin agua.**
En el chat: `pon el modo manual` → `sí`; `abre la válvula 1` → `sí`.
Esperado: la electroválvula de la zona 1 se oye o se ve abrir, y la respuesta dice
"Listo… se cierra sola a las HH:MM". Si dice "no pude confirmar", la orden no
llegó al hardware: no sigas. Después `cierra la válvula 1` y `vuelve a automático`:
la válvula cierra y el huerto vuelve a regar por umbrales.

**Etapa 3 — bomba.** Con agua y alguien presente: manual, abre una válvula y
`enciende la bomba`. Comprueba que `enciende la bomba` **sin** válvula abierta la
rechaza. Cierra todo y `vuelve a automático`.

**Etapa 4 — los topes con el hardware real.** Repite la sección 5 (constantes cortas)
con una válvula y la bomba reales y alguien vigilando, y devuelve las constantes originales.

**Etapa 5 — fallas.** Con la bomba desconectada y una válvula abierta desde el bot:
apaga el ESP32 → el bot responde que no hay telemetría y al volver aplica el estado
seguro. No pruebes caída de Mosquitto con la bomba encendida: el ESP32 quedaría con
el relé como estaba hasta que vuelva el broker.

**Qué sigue fuera de esta guía** (no está resuelto): firmware sin bloqueo en
`reconnect()`, cierre local por tiempo en el ESP32, y relés en apagado al arrancar.
Hasta entonces el único corte garantizado ante caída del PC es el interruptor físico.
