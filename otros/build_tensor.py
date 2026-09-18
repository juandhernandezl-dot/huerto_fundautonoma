"""
Construye el tensor de entrenamiento (X, y) para la LSTM a partir de la
colección "telemetria" en MongoDB.

Uso con datos reales:
    python3 build_tensor.py --mongo-uri mongodb://localhost:27017 \
        --start 2026-07-01 --end 2026-07-09 --out dataset.npz

Uso de prueba (sin Mongo, genera datos sintéticos con un hueco a propósito):
    python3 build_tensor.py --demo --out dataset_demo.npz

Principios de Karpathy aplicados:
- Simplicity First: normalización con límites físicos fijos (0-100% humedad,
  0-50°C temp), sin depender de scikit-learn ni persistir un objeto "scaler".
- Surgical Changes: este script SOLO arma el tensor. No entrena la LSTM
  (eso es la Opción B, que sigue pendiente) ni toca Node-RED.
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta
from typing import List, Tuple

import numpy as np
import pandas as pd

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger("build_tensor")

# --------------------------------------------------------------------------
# Columnas del tensor, en un orden fijo (el mismo orden se usará al entrenar
# y al hacer inferencia con la LSTM más adelante).
# --------------------------------------------------------------------------
HUMEDAD_COLS = ["zona1_humedad", "zona2_humedad", "zona3_humedad"]
CONTINUAS_COLS = HUMEDAD_COLS + ["temp_ambiente", "hum_ambiente"]
BINARIAS_COLS = ["valvula1", "valvula2", "valvula3", "bomba"]
# hour_sin/hour_cos se agregan después (no vienen de Mongo, se derivan del timestamp)
FEATURE_COLS = CONTINUAS_COLS + BINARIAS_COLS + ["hour_sin", "hour_cos"]

# Límites físicos fijos para normalizar a [0, 1] sin necesitar un scaler ajustado.
# Documentados aquí para que el entrenamiento y la inferencia usen siempre
# los mismos límites (si un sensor se satura fuera de rango, hay que revisar
# el sensor, no ampliar el límite silenciosamente).
LIMITES_FISICOS = {
    "zona1_humedad": (0.0, 100.0),
    "zona2_humedad": (0.0, 100.0),
    "zona3_humedad": (0.0, 100.0),
    "temp_ambiente": (0.0, 50.0),
    "hum_ambiente": (0.0, 100.0),
}


# --------------------------------------------------------------------------
# 1. Obtener los datos (de Mongo real, o sintéticos para pruebas)
# --------------------------------------------------------------------------
def fetch_telemetry_from_mongo(
    mongo_uri: str, db_name: str, collection: str, start: datetime, end: datetime
) -> pd.DataFrame:
    from pymongo import MongoClient  # import local: no es necesario en modo --demo

    client = MongoClient(mongo_uri)
    cursor = client[db_name][collection].find(
        {"timestamp": {"$gte": start, "$lte": end}}
    ).sort("timestamp", 1)
    df = pd.DataFrame(list(cursor))
    if df.empty:
        raise ValueError(
            f"No hay documentos en {db_name}.{collection} entre {start} y {end}"
        )
    df = df.set_index("timestamp")
    return df


def generate_demo_data(n_hours: int = 48, gap_hours: int = 3) -> pd.DataFrame:
    """Genera telemetría sintética con un hueco de `gap_hours` a propósito,
    para probar que el pipeline maneja datos faltantes correctamente.
    """
    rng = np.random.default_rng(seed=42)
    timestamps = pd.date_range("2026-07-01", periods=n_hours * 12, freq="5min")

    t = np.arange(len(timestamps))
    zona1 = 50 + 15 * np.sin(2 * np.pi * t / (12 * 24)) + rng.normal(0, 2, len(t))
    zona2 = 55 + 10 * np.sin(2 * np.pi * t / (12 * 24) + 1) + rng.normal(0, 2, len(t))
    zona3 = 45 + 12 * np.sin(2 * np.pi * t / (12 * 24) + 2) + rng.normal(0, 2, len(t))
    temp = 24 + 5 * np.sin(2 * np.pi * t / (12 * 24)) + rng.normal(0, 0.5, len(t))
    hum_amb = 60 + 10 * np.sin(2 * np.pi * t / (12 * 24) + 1) + rng.normal(0, 1, len(t))
    bomba = (zona1 < 40).astype(int)

    df = pd.DataFrame(
        {
            "zona1_humedad": np.clip(zona1, 0, 100),
            "zona2_humedad": np.clip(zona2, 0, 100),
            "zona3_humedad": np.clip(zona3, 0, 100),
            "temp_ambiente": temp,
            "hum_ambiente": np.clip(hum_amb, 0, 100),
            "valvula1": bomba,
            "valvula2": np.zeros(len(t), dtype=int),
            "valvula3": np.zeros(len(t), dtype=int),
            "bomba": bomba,
        },
        index=timestamps,
    )

    # Hueco a propósito (simula el ESP32 desconectado unas horas)
    gap_start = len(df) // 2
    gap_end = gap_start + gap_hours * 12
    df = df.drop(df.index[gap_start:gap_end])

    log.info(
        "Datos demo generados: %d filas, con un hueco simulado de %dh en la mitad.",
        len(df), gap_hours,
    )
    return df


# --------------------------------------------------------------------------
# 2. Resamplear a una malla de tiempo fija + rellenar huecos cortos
# --------------------------------------------------------------------------
def resample_and_fill(
    df: pd.DataFrame, interval: str, max_gap_periods: int
) -> pd.DataFrame:
    agg = {col: "mean" for col in CONTINUAS_COLS if col in df.columns}
    agg.update({col: "last" for col in BINARIAS_COLS if col in df.columns})

    resampled = df.resample(interval).agg(agg)

    # Relleno hacia adelante SOLO para huecos cortos (sensor lento a reportar).
    # Huecos más largos que max_gap_periods se dejan en NaN a propósito: son
    # una desconexión real (ver can*_online) y no deben inventarse datos.
    filled = resampled.ffill(limit=max_gap_periods)

    n_nan_restantes = filled[HUMEDAD_COLS].isna().any(axis=1).sum()
    log.info(
        "Resampleado a '%s': %d filas (%d con huecos sin rellenar, quedarán excluidas).",
        interval, len(filled), n_nan_restantes,
    )
    return filled


# --------------------------------------------------------------------------
# 3. Features derivadas + normalización
# --------------------------------------------------------------------------
def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    hour_frac = df.index.hour + df.index.minute / 60.0
    df["hour_sin"] = np.sin(2 * np.pi * hour_frac / 24.0)
    df["hour_cos"] = np.cos(2 * np.pi * hour_frac / 24.0)
    return df


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col, (lo, hi) in LIMITES_FISICOS.items():
        if col in df.columns:
            df[col] = (df[col] - lo) / (hi - lo)
    # BINARIAS_COLS ya están en [0,1]; hour_sin/hour_cos ya están en [-1,1].
    return df


# --------------------------------------------------------------------------
# 4. Ventanas deslizantes para la LSTM
# --------------------------------------------------------------------------
def build_windows(
    df: pd.DataFrame, window_size: int
) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """Arma (X, y):
      X[i] = features de los `window_size` pasos anteriores.
      y[i] = humedad de las 3 zonas en el paso SIGUIENTE (lo que la LSTM predice).
    Ventanas que contienen NaN (huecos sin rellenar) se descartan.
    """
    feature_cols = [c for c in FEATURE_COLS if c in df.columns]
    data = df[feature_cols].to_numpy(dtype=np.float32)
    target_idx = [feature_cols.index(c) for c in HUMEDAD_COLS]

    X, y = [], []
    n = len(data)
    for i in range(n - window_size):
        window = data[i : i + window_size]
        target = data[i + window_size, target_idx]
        if np.isnan(window).any() or np.isnan(target).any():
            continue
        X.append(window)
        y.append(target)

    X = np.array(X, dtype=np.float32)
    y = np.array(y, dtype=np.float32)
    log.info(
        "Ventanas construidas: X=%s, y=%s (de %d filas de entrada, ventana=%d pasos).",
        X.shape, y.shape, n, window_size,
    )
    return X, y, feature_cols


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="Usar datos sintéticos en vez de Mongo real")
    parser.add_argument("--mongo-uri", default="mongodb://localhost:27017")
    parser.add_argument("--db", default="huerto")
    parser.add_argument("--collection", default="telemetria")
    parser.add_argument("--start", type=str, help="YYYY-MM-DD")
    parser.add_argument("--end", type=str, help="YYYY-MM-DD")
    parser.add_argument("--resample", default="30min", help="Intervalo fijo, ej. '30min'")
    parser.add_argument("--window", type=int, default=12, help="Pasos de historia (12 x 30min = 6h)")
    parser.add_argument("--max-gap-fill", type=int, default=2, help="Máximo de pasos a rellenar por gap")
    parser.add_argument("--out", default="dataset.npz")
    args = parser.parse_args()

    if args.demo:
        df = generate_demo_data()
    else:
        if not args.start or not args.end:
            parser.error("--start y --end son obligatorios sin --demo")
        start = datetime.fromisoformat(args.start)
        end = datetime.fromisoformat(args.end) + timedelta(days=1)
        df = fetch_telemetry_from_mongo(args.mongo_uri, args.db, args.collection, start, end)

    df = resample_and_fill(df, args.resample, args.max_gap_fill)
    df = add_time_features(df)
    df = normalize(df)
    X, y, feature_cols = build_windows(df, args.window)

    if len(X) == 0:
        log.warning("No se generó ninguna ventana completa. ¿Hay suficientes datos contiguos?")
        return

    np.savez_compressed(
        args.out, X=X, y=y, feature_names=np.array(feature_cols), target_names=np.array(HUMEDAD_COLS)
    )
    log.info("Guardado: %s (X=%s, y=%s)", args.out, X.shape, y.shape)


if __name__ == "__main__":
    main()
