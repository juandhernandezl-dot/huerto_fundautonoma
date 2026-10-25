// === Contenido del nodo function "detectar_cambio_valvulas" en Node-RED ===
//
// Registra en la colección `eventos` cada vez que una válvula se abre o se
// cierra, venga el cambio de Telegram, del dashboard o de una llave a mano.
// Se conecta en paralelo a "detectar_cambio_riego" (la salida del nodo
// "datos") y su salida va al MISMO nodo "envolver payload eventos" que ya
// usa "detectar_cambio_riego"; no hay nodo mongodb nuevo.
//
// Evalúa cada mensaje de telemetría (no pasa por el delay de 1/min), por eso
// el evento queda escrito al instante y el bot lo ve antes que el siguiente
// documento de `telemetria`. No toca `telemetria`: la LSTM no se entera.
//
// Documento que escribe:  { timestamp, tipo: "valvula_abierta"|"valvula_cerrada", zona: 1|2|3 }
// La bomba ya la cubre "detectar_cambio_riego" (riego_on / riego_off).
//
// Un solo nodo, 1 salida. Al reiniciar Node-RED la primera lectura solo fija
// el estado de referencia (igual que "detectar_cambio_riego").

const t = msg.payload;
const actual = [1, 2, 3].map(z => Number(t["valvula" + z]));
if (actual.some(v => v !== 0 && v !== 1)) return null;   // lectura incompleta o inválida

const anterior = flow.get("valvulas_anterior");
flow.set("valvulas_anterior", actual);
if (!anterior) return null;                               // primera lectura: sin transición

const ahora = new Date();
const salida = [];
actual.forEach((v, i) => {
    if (v !== anterior[i]) {
        salida.push({ payload: {
            timestamp: ahora,
            tipo: v === 1 ? "valvula_abierta" : "valvula_cerrada",
            zona: i + 1
        } });
    }
});
return salida.length ? [salida] : null;                   // un mensaje por válvula que cambió
