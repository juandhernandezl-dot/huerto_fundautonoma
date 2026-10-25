"""
Acceso a telemetría (Capítulo 8, §8.4 — "módulo de acceso a telemetría:
encapsula las consultas a las colecciones de telemetría y eventos y el
formateo del estado para inyección en el prompt"). Usado tanto por
llm_client.ask_llm() (como contexto del prompt) como por
consultas.resolver_consulta_determinista() (Decisión 5, respuesta exacta
sin invocar al modelo).
"""
import logging
import threading
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


# --- Órdenes recién confirmadas -------------------------------------------
# Mongo recibe como máximo un documento por minuto; una orden confirmada
# por Node-RED (telemetría en vivo) puede ser más nueva que el último
# documento. Mientras eso ocurra, la respuesta de estado refleja la orden y
# lo dice. Lo mismo vale para los cambios que Node-RED registra en `eventos`
# (dashboard, llave a mano). En cuanto entra un documento posterior, Mongo
# vuelve a mandar; una orden sin documento posterior caduca (ORDEN_VIGENCIA_S).
# Es memoria del proceso del bot: si se reinicia, se pierde y se responde
# solo con Mongo (que es lo correcto después de un minuto).
ORDEN_VIGENCIA_S = 120
AVISO_GUARDADO = (
    "Se guarda un dato por minuto: si algo cambió hace menos de un minuto, "
    "puede no verse todavía."
)
_NOMBRE_ACTUADOR = {
    "valvula1": "la válvula de la zona 1", "valvula2": "la válvula de la zona 2",
    "valvula3": "la válvula de la zona 3", "bomba": "la bomba",
}
_ordenes: dict[str, tuple[int, datetime]] = {}
_ordenes_lock = threading.Lock()


def registrar_orden(campo: str, valor: int) -> None:
    """Llamar solo cuando Node-RED respondió 'confirmado' para esa orden."""
    if campo in _NOMBRE_ACTUADOR:
        with _ordenes_lock:
            _ordenes[campo] = (int(valor), datetime.now(timezone.utc))


def _con_utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


_TIPO_EVENTO = {  # tipo en la colección eventos -> (campo, valor)
    "valvula_abierta": ("valvula", 1), "valvula_cerrada": ("valvula", 0),
    "riego_on": ("bomba", 1), "riego_off": ("bomba", 0),
}


def _eventos_desde(ts_doc: datetime) -> dict[str, tuple[int, datetime]]:
    """Último cambio de cada actuador registrado en `eventos` DESPUÉS del
    documento de telemetria. Node-RED escribe ese evento al instante en que
    ve el cambio (venga de Telegram, del dashboard o de una llave a mano),
    sin esperar al guardado de 1 doc/min. Si Mongo falla, devuelve {}."""
    try:
        col = get_mongo_client()[config.MONGO_DB][config.EVENTOS_COLLECTION]
        cursor = col.find({"tipo": {"$in": list(_TIPO_EVENTO)}, "timestamp": {"$gt": ts_doc}}) \
                    .sort("timestamp", 1)
        salida = {}
        for ev in cursor:
            base, valor = _TIPO_EVENTO.get(ev.get("tipo"), (None, None))
            if base == "valvula" and ev.get("zona") in (1, 2, 3):
                salida[f"valvula{ev['zona']}"] = (valor, _con_utc(ev["timestamp"]))
            elif base == "bomba":
                salida["bomba"] = (valor, _con_utc(ev["timestamp"]))
        return salida
    except PyMongoError as e:
        log.warning("No se pudieron leer eventos de MongoDB: %s", e)
        return {}


def aplicar_ordenes(doc: dict | None) -> dict | None:
    """Copia de doc con el estado de los actuadores que cambiaron DESPUÉS del
    timestamp del documento: por una orden confirmada de este bot, o por un
    evento registrado en Mongo (lo más nuevo gana). Deja en doc['_ordenes']
    {campo: (instante, 'orden'|'evento')} para poder avisarlo en el texto.
    Con doc=None devuelve None."""
    if doc is None:
        return None
    ahora = datetime.now(timezone.utc)
    ts_doc = _con_utc(doc["timestamp"])
    candidatos = {}
    with _ordenes_lock:
        for campo, (valor, t) in list(_ordenes.items()):
            if (ahora - t).total_seconds() > ORDEN_VIGENCIA_S:
                del _ordenes[campo]
            elif t > ts_doc:
                candidatos[campo] = (valor, t, "orden")
    for campo, (valor, t) in _eventos_desde(ts_doc).items():
        if campo not in candidatos or t > candidatos[campo][1]:
            candidatos[campo] = (valor, t, "evento")
    if not candidatos:
        return doc
    salida = dict(doc)
    salida["_ordenes"] = {}
    for campo, (valor, t, fuente) in candidatos.items():
        salida[campo] = valor
        salida["_ordenes"][campo] = (t, fuente)
    return salida


def edad_texto(ts: datetime) -> str:
    """'Hace 40 segundos' / 'Hace 5 minutos'. Con menos de minuto y medio
    se dan segundos: 'Hace 0 minutos' junto a un estado que acaba de
    cambiar confundía más de lo que informaba."""
    seg = max(0, int((datetime.now(timezone.utc) - _con_utc(ts)).total_seconds()))
    if seg < 90:
        return f"Hace {seg} segundos"
    return f"Hace {seg // 60} minutos"


def notas_ordenes(doc: dict, campos=None) -> list[str]:
    """Una frase por actuador cuyo estado viene de una orden reciente y no
    de Mongo. campos=None incluye todos."""
    ahora = datetime.now(timezone.utc)
    notas = []
    for campo, (t, fuente) in (doc.get("_ordenes") or {}).items():
        if campos is not None and campo not in campos:
            continue
        if doc.get(campo) == 1:
            estado = "encendida" if campo == "bomba" else "abierta"
        else:
            estado = "apagada" if campo == "bomba" else "cerrada"
        seg = max(0, int((ahora - t).total_seconds()))
        origen = "Por tu orden confirmada" if fuente == "orden" else "Según el último cambio registrado"
        notas.append(
            f"{origen} hace {seg} segundos, {_NOMBRE_ACTUADOR[campo]} "
            f"está {estado} (lo guardado en la base todavía es anterior)."
        )
    return notas


def get_latest_telemetry() -> dict | None:
    """Consulta el documento más reciente de telemetria. Devuelve None si
    Mongo no responde o si la colección está vacía (nunca debe romper el bot).
    Si hay una orden confirmada más nueva que ese documento, el estado de
    ese actuador sale de la orden (ver aplicar_ordenes).
    """
    try:
        client = get_mongo_client()
        doc = client[config.MONGO_DB][config.MONGO_COLLECTION].find_one(sort=[("timestamp", -1)])
    except PyMongoError as e:
        log.warning("No se pudo leer telemetria de MongoDB: %s", e)
        return None
    return aplicar_ordenes(doc)


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
        f"{edad_texto(doc['timestamp'])}:",
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
    partes.extend(notas_ordenes(doc))
    partes.append(AVISO_GUARDADO)
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
