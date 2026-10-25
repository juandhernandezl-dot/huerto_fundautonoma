// === Contenido del nodo function "Supervisor riego" en Node-RED ===
//
// Única ruta de escritura del bot hacia el ESP32 y supervisor de seguridad
// de TODO el modo manual, venga la orden del bot o del dashboard (consejo
// del 20 oct 2026, acciones-agente/council-transcript.md en el proyecto).
//
// Principio: abrir agua es un préstamo con vencimiento; el estado al que
// todo vuelve solo es "cerrado y en automático".
//   - Cada válvula o la bomba encendida en manual se apaga sola a los
//     MAX_ABIERTO_MS. El plazo arranca cuando la telemetría la ve encendida,
//     así que cubre igual al bot y al dashboard, y repetir "abre" no lo
//     reinicia.
//   - La bomba encendida en manual sin ninguna válvula abierta se apaga a
//     los BOMBA_SIN_VALVULA_MS.
//   - El modo manual vuelve solo a automático a los MAX_MANUAL_MS de
//     entrar, o cuando vence la última válvula abierta si es más tarde.
//   - Arranque y huecos de telemetría (Node-RED reiniciado, ESP32
//     reiniciado, broker caído): en cuanto vuelve la telemetría se manda
//     todo a 0 y modo automático, que es en lo que arranca el ESP32. Así
//     Node-RED nunca "cree" que sigue en manual cuando el ESP32 ya no lo
//     está. Nada se publica con retain ni se re-publica periódicamente.
//
// LÍMITE QUE ESTE NODO NO PUEDE CERRAR: si cae el PC, Node-RED o el broker,
// el ESP32 queda bloqueado en reconnect() con el relé de la bomba en su
// último estado. Eso solo lo cierran el corte físico y el cambio de firmware.
//
// ENTRADAS (tres cables llegan a este mismo nodo):
//   - "http in" POST /huerto/api/accion          -> trae msg.req
//   - nodo "json" que parsea huerto/general       -> telemetría (objeto)
//   - "inject" cada 5 s con msg.topic = "tick"    -> revisa vencimientos
//
// SALIDAS (configurar el nodo con 6 salidas, en este orden):
//   1 -> "http response"
//   2 -> switch "Bomba Manual"   (riego/bomba)
//   3 -> switch "Modo Manual"    (riego/modo)
//   4 -> switch "ValvulaZona1"   (riego/valvula1)
//   5 -> switch "ValvulaZona2"   (riego/valvula2)
//   6 -> switch "ValvulaZona3"   (riego/valvula3)
//
// Depende de flow.get("modo_riego"), que escribe la función "control modo"
// (una línea agregada allí).

const MAX_ABIERTO_MS = 20 * 60 * 1000;       // tope por válvula y bomba
const MAX_MANUAL_MS = 30 * 60 * 1000;        // tope del modo manual
const BOMBA_SIN_VALVULA_MS = 30 * 1000;      // bomba contra todo cerrado
const HUECO_TELEMETRIA_MS = 5000;            // > 3 periodos de publicación del ESP32; ver README
const ACK_MS = 10000;                        // el bot espera 20 s (ACCION_TIMEOUT_S)

const LOCAL = ["127.0.0.1", "::1", "::ffff:127.0.0.1"];
const VALVULAS = ["valvula1", "valvula2", "valvula3"];
const ACTUADORES = ["valvula1", "valvula2", "valvula3", "bomba"];
const SALIDA = { bomba: 1, valvula1: 3, valvula2: 4, valvula3: 5 };

const ahora = Date.now();
const modo = flow.get("modo_riego") || "auto";

function vacio() { return [null, null, null, null, null, null]; }

function responder(m, codigo, cuerpo) {
    m.statusCode = codigo;
    m.payload = cuerpo;
    const o = vacio();
    o[0] = m;
    return o;
}

function liberarPendiente(cuerpo) {
    const p = context.get("pendiente");
    if (!p) return null;
    clearTimeout(p.timer);
    context.set("pendiente", undefined);
    return responder(p.msg, cuerpo.estado === "confirmado" ? 200 : 504, cuerpo);
}

// Todo a 0 y a automático. Las salidas pasan por los switches del
// dashboard, así que el tablero queda mostrando lo mismo que se ordenó.
function estadoSeguro(o, motivo) {
    for (const a of ACTUADORES) o[SALIDA[a]] = { payload: 0 };
    o[2] = { payload: "auto" };
    context.set("plazos", {});
    context.set("manual_vence", undefined);
    context.set("bomba_sola_desde", undefined);
    node.warn("Estado seguro aplicado: " + motivo);
    return o;
}

// ---------------------------------------------------------------- tick
if (msg.topic === "tick") {
    if (modo !== "manual") {
        context.set("manual_vence", undefined);
        return null;
    }
    const ultima = context.get("ultima_ts");
    if (!ultima || ahora - ultima > HUECO_TELEMETRIA_MS) return null; // sin ESP32 no hay a quién ordenar

    const plazos = context.get("plazos") || {};
    let vence = context.get("manual_vence");
    if (vence === undefined) {               // se entró a manual desde el dashboard
        vence = ahora + MAX_MANUAL_MS;
        context.set("manual_vence", vence);
    }
    const ultimoPlazo = Math.max(0, ...Object.values(plazos));
    if (ahora >= Math.max(vence, ultimoPlazo)) {
        return estadoSeguro(vacio(), "venció el modo manual");
    }

    const o = vacio();
    let hay = false;
    for (const a of ACTUADORES) {
        if (plazos[a] && ahora >= plazos[a]) { o[SALIDA[a]] = { payload: 0 }; hay = true; }
    }
    const solaDesde = context.get("bomba_sola_desde");
    if (solaDesde && ahora - solaDesde >= BOMBA_SIN_VALVULA_MS) { o[1] = { payload: 0 }; hay = true; }
    // Se repite el 0 en cada tick hasta que la telemetría lo refleje: un
    // QoS 0 perdido no deja la válvula abierta.
    return hay ? o : null;
}

// ---------------------------------------------------------- telemetría
if (!msg.req) {
    const t = msg.payload;
    if (typeof t !== "object" || t === null) return null;

    const anterior = context.get("ultima_ts");
    context.set("ultima", t);
    context.set("ultima_ts", ahora);

    if (!anterior || ahora - anterior > HUECO_TELEMETRIA_MS) {
        // Arranque de Node-RED o vuelta del ESP32/broker: no se sabe en qué
        // modo quedó el ESP32 (arranca en auto), así que se fija el seguro.
        const o = estadoSeguro(vacio(), anterior ? "hueco de telemetría" : "arranque");
        const r = liberarPendiente({ estado: "sin_confirmar" });
        if (r) o[0] = r[0];
        return o;
    }

    const plazos = context.get("plazos") || {};
    if (modo === "manual") {
        for (const a of ACTUADORES) {
            if (Number(t[a]) === 1) { if (!plazos[a]) plazos[a] = ahora + MAX_ABIERTO_MS; }
            else delete plazos[a];
        }
        const algunaValvula = VALVULAS.some(v => Number(t[v]) === 1);
        if (Number(t.bomba) === 1 && !algunaValvula) {
            if (!context.get("bomba_sola_desde")) context.set("bomba_sola_desde", ahora);
        } else {
            context.set("bomba_sola_desde", undefined);
        }
    } else {
        for (const a of ACTUADORES) delete plazos[a];
        context.set("bomba_sola_desde", undefined);
    }
    context.set("plazos", plazos);

    const p = context.get("pendiente");
    if (p && Number(t[p.campo]) === p.valor) {
        const cuerpo = { estado: "confirmado", campo: p.campo, valor: p.valor };
        if (p.valor === 1 && plazos[p.campo]) cuerpo.cierra_a = plazos[p.campo];
        return liberarPendiente(cuerpo);
    }
    return null;
}

// ------------------------------------------------- petición HTTP del bot
if (!LOCAL.includes(msg.req.ip)) {
    return responder(msg, 403, { estado: "prohibido" });
}
if (context.get("pendiente")) {
    return responder(msg, 409, { estado: "ocupado" });
}
const ultima = context.get("ultima_ts");
if (!ultima || ahora - ultima > HUECO_TELEMETRIA_MS) {
    // Sin ESP32 escuchando, la orden se perdería (QoS 0, sin retain).
    return responder(msg, 409, { estado: "sin_telemetria" });
}

const b = (typeof msg.payload === "object" && msg.payload) ? msg.payload : {};
const salidas = vacio();

if (b.tipo === "cambiar_modo") {
    if (b.modo !== "manual" && b.modo !== "auto") {
        return responder(msg, 400, { estado: "invalido" });
    }
    // Antes de cambiar de modo, los cuatro switches a 0. Si no, un switch que
    // quedó en ON en el dashboard re-publica ese ON al habilitarse (y el
    // firmware guarda siempre el último comando), y al entrar en manual
    // abriría una válvula que nadie pidió. Se envía antes del cambio de modo.
    const ceros = vacio();
    for (const a of ACTUADORES) ceros[SALIDA[a]] = { payload: 0 };
    node.send(ceros);

    const cuerpo = { estado: "enviado", modo: b.modo };
    if (b.modo === "manual") {
        context.set("manual_vence", ahora + MAX_MANUAL_MS);
        cuerpo.vence_a = ahora + MAX_MANUAL_MS;
    }
    // La telemetría no reporta el modo: no hay ack posible, se dice "enviado".
    salidas[2] = { payload: b.modo };
    salidas[0] = responder(msg, 202, cuerpo)[0];
    return salidas;
}

let campo, valor, salida;
if (b.tipo === "abrir_valvula" || b.tipo === "cerrar_valvula") {
    const z = Number(b.zona);
    if (![1, 2, 3].includes(z)) return responder(msg, 400, { estado: "invalido" });
    campo = "valvula" + z;
    valor = b.tipo === "abrir_valvula" ? 1 : 0;
    salida = SALIDA[campo];
} else if (b.tipo === "encender_bomba" || b.tipo === "apagar_bomba") {
    campo = "bomba";
    valor = b.tipo === "encender_bomba" ? 1 : 0;
    salida = SALIDA.bomba;
} else {
    return responder(msg, 400, { estado: "invalido" });
}

// El receptor ignora riego/bomba y riego/valvulaN fuera de modo manual.
if (modo !== "manual") {
    return responder(msg, 409, { estado: "requiere_manual" });
}
const u = context.get("ultima") || {};
if (campo !== "bomba" && valor === 1 && Number(u["can" + campo.slice(-1)]) === 0) {
    return responder(msg, 409, { estado: "nodo_desconectado" });
}
if (campo === "bomba" && valor === 1 && !VALVULAS.some(v => Number(u[v]) === 1)) {
    return responder(msg, 409, { estado: "sin_valvula_abierta" });
}

const timer = setTimeout(() => {
    const p = context.get("pendiente");
    if (p && p.timer === timer) {
        node.send(liberarPendiente({ estado: "sin_confirmar", campo: p.campo, valor: p.valor }));
    }
}, ACK_MS);
context.set("pendiente", { msg: msg, campo: campo, valor: valor, timer: timer });

salidas[salida] = { payload: valor };
return salidas;
