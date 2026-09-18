"""
Siembra 48 horas de datos históricos COHERENTES en telemetria y eventos,
para probar la nueva capacidad del bot de responder sobre el historial
(tiempo de riego hoy, humedad promedio, etc.)

A diferencia de mongo_seed_test.js (que sembraba valores fijos, sin ciclo),
esto genera una sola línea de tiempo simulada de la que se derivan AMBAS
colecciones, para que sean consistentes entre sí: si telemetria dice que la
bomba estuvo prendida de 2pm a 2:15pm, eventos debe tener exactamente un
riego_on y un riego_off en esos instantes.

Uso:
    python3 seed_historical_data.py            # inserta 48h de datos
    python3 seed_historical_data.py --clear    # borra los de prueba anterior
    python3 seed_historical_data.py --hours 72 # cambiar el rango

Todos los documentos se marcan con "prueba_historica": true para poder
borrarlos después sin tocar datos reales:
    mongosh huerto --eval 'db.telemetria.deleteMany({prueba_historica:true}); db.eventos.deleteMany({prueba_historica:true})'
"""

import argparse
from datetime import datetime, timedelta, timezone

import numpy as np


def generar_serie(horas: int, paso_min: int = 5, seed: int = 7):
    """Genera telemetria + eventos de una sola línea de tiempo coherente.

    Lógica de riego: histéresis simple sobre zona 1 (ON < 30%, OFF >= 50%),
    igual al umbral por defecto que ya usa el flow de Node-RED. La válvula 1
    y la bomba se mueven juntas (simplificación: en esta simulación solo la
    zona 1 necesita riego). Se simula una caída de conexión CAN en la zona 2
    durante 2 horas, a la mitad del período, para poder probar ese caso
    también en el historial.
    """
    rng = np.random.default_rng(seed)
    ahora = datetime.now(timezone.utc)
    inicio = ahora - timedelta(hours=horas)
    n = int(horas * 60 / paso_min)

    docs_telemetria = []
    docs_eventos = []

    bomba = 0
    zona1 = 45.0
    can2_offline_desde = n // 2
    can2_offline_hasta = can2_offline_desde + int(120 / paso_min)  # 2 horas

    for i in range(n):
        t = inicio + timedelta(minutes=paso_min * i)
        hora_frac = t.hour + t.minute / 60.0

        # Secado más rápido de día (mayor evapotranspiración) que de noche
        tasa_secado = 0.15 + 0.10 * max(0.0, np.sin(2 * np.pi * (hora_frac - 6) / 24))
        zona1 -= tasa_secado
        if bomba == 1:
            zona1 += 1.2  # el riego sube la humedad rápido mientras está activo
        zona1 = float(np.clip(zona1 + rng.normal(0, 0.3), 5, 90))

        nuevo_estado = bomba
        if zona1 < 30 and bomba == 0:
            nuevo_estado = 1
        elif zona1 >= 50 and bomba == 1:
            nuevo_estado = 0

        if nuevo_estado != bomba:
            tipo = "riego_on" if nuevo_estado == 1 else "riego_off"
            docs_eventos.append({
                "timestamp": t, "tipo": tipo, "prueba_historica": True,
            })
            bomba = nuevo_estado

        zona2 = float(np.clip(55 + 8 * np.sin(2 * np.pi * hora_frac / 24 + 1) + rng.normal(0, 1.5), 20, 90))
        zona3 = float(np.clip(45 + 9 * np.sin(2 * np.pi * hora_frac / 24 + 2) + rng.normal(0, 1.5), 20, 90))
        temp = float(24 + 5 * np.sin(2 * np.pi * hora_frac / 24) + rng.normal(0, 0.4))
        hum_amb = float(np.clip(60 + 10 * np.sin(2 * np.pi * hora_frac / 24 + 1) + rng.normal(0, 1), 20, 95))

        can2_online = 0 if can2_offline_desde <= i < can2_offline_hasta else 1

        docs_telemetria.append({
            "timestamp": t,
            "zona1_humedad": round(zona1, 1),
            "zona2_humedad": round(zona2, 1),
            "zona3_humedad": round(zona3, 1),
            "temp_ambiente": round(temp, 1),
            "hum_ambiente": round(hum_amb, 1),
            "valvula1": bomba, "valvula2": 0, "valvula3": 0,
            "bomba": bomba,
            "can1_online": 1, "can2_online": can2_online, "can3_online": 1,
            "prueba_historica": True,
        })

    return docs_telemetria, docs_eventos


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--mongo-uri", default="mongodb://localhost:27017")
    parser.add_argument("--db", default="huerto")
    parser.add_argument("--clear", action="store_true", help="Solo borrar datos de prueba histórica anteriores")
    args = parser.parse_args()

    from pymongo import MongoClient
    client = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=3000)
    db = client[args.db]

    if args.clear:
        r1 = db.telemetria.delete_many({"prueba_historica": True})
        r2 = db.eventos.delete_many({"prueba_historica": True})
        print(f"Borrados: {r1.deleted_count} de telemetria, {r2.deleted_count} de eventos.")
        return

    docs_telemetria, docs_eventos = generar_serie(args.hours)

    if docs_telemetria:
        db.telemetria.insert_many(docs_telemetria)
    if docs_eventos:
        db.eventos.insert_many(docs_eventos)

    minutos_totales = sum(
        (docs_eventos[i + 1]["timestamp"] - docs_eventos[i]["timestamp"]).total_seconds() / 60
        for i in range(len(docs_eventos) - 1)
        if docs_eventos[i]["tipo"] == "riego_on" and docs_eventos[i + 1]["tipo"] == "riego_off"
    )

    print(f"Insertados {len(docs_telemetria)} documentos en telemetria "
          f"({args.hours}h, cada 5 min).")
    print(f"Insertados {len(docs_eventos)} eventos de riego "
          f"({len(docs_eventos)//2} activaciones aprox).")
    print(f"Tiempo total de riego en la serie generada: {minutos_totales:.0f} minutos.")
    print("Para borrarlos después: python3 seed_historical_data.py --clear")


if __name__ == "__main__":
    main()
