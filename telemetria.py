"""
Acceso a telemetría (Capítulo 8, §8.4 — "módulo de acceso a telemetría:
encapsula las consultas a las colecciones de telemetría y eventos y el
formateo del estado para inyección en el prompt"). Usado tanto por
llm_client.ask_llm() (como contexto del prompt) como por
consultas.resolver_consulta_determinista() (Decisión 5, respuesta exacta
sin invocar al modelo).
"""
import logging
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient
from pymongo.errors import PyMongoError

import config

log = logging.getLogger(__name__)

_mongo_client: MongoClient | None = None


def get_mongo_client() -> MongoClient:
    global _mongo_client
    if _mongo_client is None:
        # Timeout corto: si Mongo no responde, el bot no debe quedarse
        # colgado esperando — mejor seguir sin datos que trabar la respuesta.
        _mongo_client = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=2000)
    return _mongo_client


def get_latest_telemetry() -> dict | None:
    """Consulta el documento más reciente de telemetria. Devuelve None si
    Mongo no responde o si la colección está vacía (nunca debe romper el bot).
    """
    try:
        client = get_mongo_client()
        return client[config.MONGO_DB][config.MONGO_COLLECTION].find_one(sort=[("timestamp", -1)])
    except PyMongoError as e:
        log.warning("No se pudo leer telemetria de MongoDB: %s", e)
        return None


def format_telemetry_for_llm(doc: dict | None) -> str:
    """Convierte el documento crudo de Mongo en una descripción en texto que
    el LLM (o la respuesta determinista de consultas.py) pueda usar."""
    if doc is None:
        return "No hay datos de sensores disponibles en este momento."

    edad_minutos = (datetime.now(timezone.utc) - doc["timestamp"].replace(tzinfo=timezone.utc)) \
        .total_seconds() / 60

    def estado_can(v) -> str:
        return "en línea" if v == 1 else "sin conexión"

    partes = [
        f"Hace {edad_minutos:.0f} minutos:",
        f"zona 1 humedad {doc.get('zona1_humedad')}%,",
        f"zona 2 humedad {doc.get('zona2_humedad')}%,",
        f"zona 3 humedad {doc.get('zona3_humedad')}%,",
        f"temperatura {doc.get('temp_ambiente')}°C,",
        f"humedad ambiente {doc.get('hum_ambiente')}%,",
        f"bomba de agua (motor principal) {'encendida' if doc.get('bomba') == 1 else 'apagada'},",
        f"válvula zona 1 {'abierta' if doc.get('valvula1') == 1 else 'cerrada'},",
        f"válvula zona 2 {'abierta' if doc.get('valvula2') == 1 else 'cerrada'},",
        f"válvula zona 3 {'abierta' if doc.get('valvula3') == 1 else 'cerrada'}.",
        "(la bomba y las válvulas son componentes distintos, no los confundas)",
        f"Conexión CAN zona 1: {estado_can(doc.get('can1_online'))},",
        f"conexión CAN zona 2: {estado_can(doc.get('can2_online'))},",
        f"conexión CAN zona 3: {estado_can(doc.get('can3_online'))}.",
    ]
    if edad_minutos > config.TELEMETRY_STALE_MINUTES:
        partes.append(
            f"AVISO: este dato tiene más de {config.TELEMETRY_STALE_MINUTES} minutos, "
            "podría estar desactualizado."
        )

    return " ".join(partes)


def _calcular_tiempo_riego(eventos: list[dict]) -> tuple[float, int]:
    """Reproduce la misma lógica que el nodo 'tiempo riego' de Node-RED, pero
    en Python sobre la colección eventos (en vez de inferirlo de sensores).

    Empareja cada riego_on con el riego_off que le sigue y suma duraciones.
    Si el último evento es un riego_on sin su riego_off correspondiente
    (todavía regando ahora mismo), cuenta desde ahí hasta el momento actual.
    """
    inicio_riego = None
    total_segundos = 0.0
    activaciones = 0

    for ev in eventos:
        if ev["tipo"] == "riego_on":
            inicio_riego = ev["timestamp"]
            activaciones += 1
        elif ev["tipo"] == "riego_off" and inicio_riego is not None:
            total_segundos += (ev["timestamp"] - inicio_riego).total_seconds()
            inicio_riego = None

    if inicio_riego is not None:
        ahora = datetime.now(timezone.utc)
        total_segundos += (ahora - inicio_riego.replace(tzinfo=timezone.utc)).total_seconds()

    return total_segundos / 60, activaciones


def get_daily_summary() -> dict | None:
    """Consulta un resumen de las últimas config.HISTORIAL_HORAS: humedad
    promedio por zona (de telemetria) y tiempo total de riego (de eventos).
    Devuelve None si Mongo no responde o no hay datos en esa ventana.
    """
    try:
        client = get_mongo_client()
        desde = datetime.now(timezone.utc) - timedelta(hours=config.HISTORIAL_HORAS)

        pipeline = [
            {"$match": {"timestamp": {"$gte": desde}}},
            {"$group": {
                "_id": None,
                "zona1_prom": {"$avg": "$zona1_humedad"},
                "zona2_prom": {"$avg": "$zona2_humedad"},
                "zona3_prom": {"$avg": "$zona3_humedad"},
                "n": {"$sum": 1},
            }},
        ]
        resultado = list(client[config.MONGO_DB][config.MONGO_COLLECTION].aggregate(pipeline))
        promedios = resultado[0] if resultado else None

        eventos = list(
            client[config.MONGO_DB][config.EVENTOS_COLLECTION]
            .find({"timestamp": {"$gte": desde}})
            .sort("timestamp", 1)
        )
        minutos_riego, activaciones = _calcular_tiempo_riego(eventos)

        if promedios is None and not eventos:
            return None

        return {"promedios": promedios, "minutos_riego": minutos_riego, "activaciones": activaciones}
    except PyMongoError as e:
        log.warning("No se pudo leer resumen histórico de MongoDB: %s", e)
        return None


def format_daily_summary_for_llm(resumen: dict | None) -> str:
    """Convierte el resumen histórico en texto para el contexto del LLM."""
    if resumen is None:
        return f"No hay historial de las últimas {config.HISTORIAL_HORAS} horas disponible."

    partes = [
        f"Resumen de las últimas {config.HISTORIAL_HORAS} horas: se regó un total de "
        f"{resumen['minutos_riego']:.0f} minutos, en {resumen['activaciones']} activación(es)."
    ]

    prom = resumen.get("promedios")
    if prom:
        partes.append(
            f"Humedad promedio: zona 1 {prom['zona1_prom']:.0f}%, "
            f"zona 2 {prom['zona2_prom']:.0f}%, zona 3 {prom['zona3_prom']:.0f}%."
        )

    return " ".join(partes)
