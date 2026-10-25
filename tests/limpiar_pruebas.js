// Borra de Mongo lo que generó una prueba (simulador, chat de consola).
// Se corre en el HOST con mongosh, desde ~/Documents/huerto-bot:
//
//   # 1) Copia de seguridad (siempre, antes de borrar):
//   docker compose exec mongo mongodump --port 27019 --db huerto --archive=/data/db/respaldo_$(date +%F_%H%M).archive
//
//   # 2) Solo lo posterior al inicio de la prueba (el valor de /tmp/inicio_pruebas):
//   DESDE=$(cat /tmp/inicio_pruebas) mongosh --port 27019 huerto --quiet --file tests/limpiar_pruebas.js
//   #    o con una fecha a mano (UTC):  DESDE=2026-10-25T04:30:00Z mongosh ...
//
//   # 3) TODO el historial (telemetria y eventos quedan vacías; no se puede deshacer
//   #    salvo con el respaldo del paso 1):
//   BORRAR_TODO=si mongosh --port 27019 huerto --quiet --file tests/limpiar_pruebas.js
//
// Con el ESP32 real apagado durante la prueba, todo lo posterior a DESDE es de
// la prueba. Si el real estuvo encendido, también se borra lo suyo de esa ventana.
//
// `telemetria` es una colección de series de tiempo: según la versión de MongoDB
// el borrado con filtro por fecha puede no estar permitido. En ese caso el script
// la reconstruye conservando lo anterior a DESDE (queda una copia en
// telemetria_conservada, que borras tú cuando verifiques).

const TODO = process.env.BORRAR_TODO === "si";
const desdeTxt = process.env.DESDE;
if (!TODO && !desdeTxt) {
  print("Falta DESDE=<fecha ISO UTC> (p. ej. $(cat /tmp/inicio_pruebas)) o BORRAR_TODO=si. No se borró nada.");
  quit(1);
}
const desde = TODO ? null : new Date(desdeTxt);
if (!TODO && isNaN(desde.getTime())) {
  print("DESDE no es una fecha válida: " + desdeTxt + ". No se borró nada.");
  quit(1);
}

const filtro = TODO ? {} : { timestamp: { $gte: desde } };
print(TODO ? "Modo: BORRAR TODO" : "Modo: borrar desde " + desde.toISOString());
print("Antes:   telemetria=" + db.telemetria.countDocuments({}) + "  eventos=" + db.eventos.countDocuments({}));
print("A borrar: telemetria=" + db.telemetria.countDocuments(filtro) + "  eventos=" + db.eventos.countDocuments(filtro));

// eventos: colección normal, el filtro por fecha siempre funciona.
print("eventos borrados: " + db.eventos.deleteMany(filtro).deletedCount);

// telemetria: primero el camino simple.
try {
  print("telemetria borrada: " + db.telemetria.deleteMany(filtro).deletedCount);
} catch (e) {
  print("MongoDB no permite ese borrado en la serie de tiempo (" + e.codeName + "). Reconstruyendo...");
  const previo = db.telemetria.countDocuments({});
  const conservar = TODO ? [] : db.telemetria.find({ timestamp: { $lt: desde } }).toArray();
  db.telemetria_conservada.drop();
  if (conservar.length) db.telemetria_conservada.insertMany(conservar);
  if (db.telemetria_conservada.countDocuments({}) !== conservar.length) {
    print("La copia no coincide; se aborta SIN tocar telemetria.");
    quit(2);
  }
  db.telemetria.drop();
  db.createCollection("telemetria", { timeseries: { timeField: "timestamp", granularity: "minutes" } });
  db.telemetria.createIndex({ timestamp: 1 });
  if (conservar.length) db.telemetria.insertMany(conservar.map(d => { delete d._id; return d; }));
  print("telemetria reconstruida: de " + previo + " a " + db.telemetria.countDocuments({}) +
        " documentos. Copia en telemetria_conservada (bórrala con db.telemetria_conservada.drop() cuando verifiques).");
}
print("Después: telemetria=" + db.telemetria.countDocuments({}) + "  eventos=" + db.eventos.countDocuments({}));
