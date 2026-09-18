// Configuración inicial de la base de datos "huerto" en MongoDB.
// Ejecutar una sola vez con:
//   mongosh huerto mongo_setup.js
//
// Crea dos colecciones con responsabilidades separadas:
//   - telemetria: lecturas periódicas de sensores (consumida por la LSTM)
//   - eventos:    cambios discretos de estado (modo, umbrales, riego on/off)

// --- telemetria: Time Series Collection nativa de MongoDB ---
// Las time series collections requieren declarar el campo de tiempo al crear
// la colección (no se puede agregar después). Si ya existe una colección
// "sensores" vieja, esto NO la toca ni la borra: es un nombre nuevo.
db.createCollection("telemetria", {
  timeseries: {
    timeField: "timestamp",
    granularity: "minutes"
  }
});

// Índice adicional útil si luego filtras por rango de fechas específico
// (las time series ya indexan timestamp automáticamente, esto es un extra
// por si acaso se necesitan consultas más complejas más adelante).
db.telemetria.createIndex({ timestamp: 1 });

// --- eventos: colección normal (no es una serie de tiempo regular) ---
db.createCollection("eventos");
db.eventos.createIndex({ timestamp: 1 });
db.eventos.createIndex({ tipo: 1 });

print("Colecciones 'telemetria' (time series) y 'eventos' creadas en la base 'huerto'.");
print("Colecciones existentes:");
db.getCollectionNames().forEach(name => print(" - " + name));
