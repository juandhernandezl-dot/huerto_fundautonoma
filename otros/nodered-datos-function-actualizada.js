// === Reemplazar el contenido de la función "datos" en Node-RED ===
//
// Antes: guardaba fecha/hora_local/zona1-3/bomba/riego en la colección
// "sensores", mezclando telemetría con estado de control.
//
// Ahora: separa la telemetría física (lo que consumirá la LSTM) de los
// eventos de control (modo, riego on/off), y usa nombres de campo
// consistentes con el esquema de la colección "telemetria".
//
// IMPORTANTE: además de pegar este código, hay que cambiar en el nodo
// "mongodb out" de este flujo la colección de "sensores" a "telemetria".

let d = msg.payload; // JSON ya parseado desde el ESP32 (nodo1, nodo2, ... can3)

msg.payload = {
    timestamp: new Date(),
    zona1_humedad: d.nodo1,
    zona2_humedad: d.nodo2,
    zona3_humedad: d.nodo3,
    temp_ambiente: d.temp,
    hum_ambiente: d.humAmb,
    valvula1: d.valvula1,
    valvula2: d.valvula2,
    valvula3: d.valvula3,
    bomba: d.bomba,
    can1_online: d.can1,
    can2_online: d.can2,
    can3_online: d.can3
};

return msg;


// === OPCIONAL: detectar transiciones de riego para la colección "eventos" ===
//
// Si quieres registrar cuándo se prende/apaga la bomba como un evento
// discreto (útil para la consulta de "tiempo de riego" que ya tienes en el
// flujo), agrega una función nueva conectada en paralelo a "datos", con
// este código. Necesita un nodo "mongodb out" propio apuntando a "eventos".
//
// function "detectar cambio riego":
/*
let bombaActual = msg.payload.bomba;
let bombaAnterior = flow.get("bomba_anterior");

if (bombaAnterior === undefined) {
    flow.set("bomba_anterior", bombaActual);
    return null; // primera lectura, no hay transición que registrar
}

if (bombaActual !== bombaAnterior) {
    flow.set("bomba_anterior", bombaActual);
    msg.payload = {
        timestamp: new Date(),
        tipo: bombaActual === 1 ? "riego_on" : "riego_off"
    };
    return msg;
}

return null; // sin cambio, no se emite evento
*/
