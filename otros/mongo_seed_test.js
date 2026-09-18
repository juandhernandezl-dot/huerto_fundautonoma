// Inserta telemetría sintética con fechas retroactivas, SOLO para probar
// build_tensor.py contra Mongo real sin esperar horas de datos de verdad.
// Ejecutar con: mongosh huerto mongo_seed_test.js
// Borra estos documentos de prueba con: db.telemetria.deleteMany({prueba: true})

const ahora = new Date();
const docs = [];
for (let i = 0; i < 12 * 8; i++) {  // 8 horas cada 5 min
    const t = new Date(ahora.getTime() - (12 * 8 - i) * 5 * 60 * 1000);
    docs.push({
        timestamp: t,
        zona1_humedad: 50 + 10 * Math.sin(i / 20),
        zona2_humedad: 55 + 8 * Math.sin(i / 20 + 1),
        zona3_humedad: 45 + 9 * Math.sin(i / 20 + 2),
        temp_ambiente: 24 + 3 * Math.sin(i / 40),
        hum_ambiente: 60,
        valvula1: 0, valvula2: 0, valvula3: 0, bomba: 0,
        can1_online: 1, can2_online: 1, can3_online: 1,
        prueba: true
    });
}
db.telemetria.insertMany(docs);
print("Insertados " + docs.length + " documentos de prueba en telemetria.");
