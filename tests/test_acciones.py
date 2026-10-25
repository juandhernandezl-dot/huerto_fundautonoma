"""
Prueba de las acciones físicas del bot (comandos.py) contra un Node-RED
falso. No requiere Node-RED, MQTT ni el ESP32.

Verifica las reglas del consejo del 20 oct 2026:
  - la detección no adivina: zona pegada a "válvula"/"zona", ambigüedad
    rechazada, negación antes de abrir rechazada;
  - abrir / encender / pasar a manual piden SÍ; cerrar / apagar no;
  - solo "confirmado" de Node-RED suena a éxito.
"""
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PUERTO = 8766
os.environ["TELEGRAM_BOT_TOKEN"] = "dummy-token-para-test"
os.environ["NODERED_ACCION_URL"] = f"http://127.0.0.1:{PUERTO}/huerto/api/accion"
os.environ["ACCION_TIMEOUT_S"] = "1"
os.environ["CONFIRMACION_TTL_S"] = "1"

import comandos  # noqa: E402
import consultas  # noqa: E402
import prompts  # noqa: E402
import telemetria  # noqa: E402

# Los casos de falla provocan a propósito errores de conexión y timeouts que
# comandos.py registra con traceback; aquí asustan sin aportar. Los fallos de
# esta prueba salen como AssertionError, no como logs.
logging.disable(logging.CRITICAL)

RECIBIDO = []
RESPUESTA = {"codigo": 200, "cuerpo": {"estado": "confirmado"}, "demora": 0}


class FakeNodeRed(BaseHTTPRequestHandler):
    def do_POST(self):
        largo = int(self.headers.get("Content-Length", 0))
        RECIBIDO.append(json.loads(self.rfile.read(largo)))
        time.sleep(RESPUESTA["demora"])
        datos = json.dumps(RESPUESTA["cuerpo"]).encode()
        try:
            self.send_response(RESPUESTA["codigo"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(datos)))
            self.end_headers()
            self.wfile.write(datos)
        except BrokenPipeError:
            pass  # el bot ya colgó por timeout: es el caso que se prueba

    def log_message(self, *args):
        pass


def responder(codigo, cuerpo, demora=0):
    RESPUESTA.update(codigo=codigo, cuerpo=cuerpo, demora=demora)


def probar_deteccion():
    casos = {
        # verbos que antes se escapaban
        "Activa la válvula 1": {"tipo": "abrir_valvula", "zona": "1"},
        "Enciende la válvula dos": {"tipo": "abrir_valvula", "zona": "2"},
        "Prende la válvula 2": {"tipo": "abrir_valvula", "zona": "2"},
        "quiero prender la bomba": {"tipo": "encender_bomba"},
        "Activa modo manual": {"tipo": "cambiar_modo", "modo": "manual"},
        "Activa control RL": {"tipo": "cambiar_modo", "modo": "ia"},
        "pon el modo automático": {"tipo": "cambiar_modo", "modo": "automático"},
        "vuelve a automático": {"tipo": "cambiar_modo", "modo": "automático"},
        "regresa al modo automático": {"tipo": "cambiar_modo", "modo": "automático"},
        "ponlo en manual": {"tipo": "cambiar_modo", "modo": "manual"},
        "cambia a manual": {"tipo": "cambiar_modo", "modo": "manual"},
        "no lo pases a manual": {"tipo": "ambigua"},
        # zona = número pegado a válvula/zona, no el primero del mensaje
        "en 1 minuto abre la válvula 3": {"tipo": "abrir_valvula", "zona": "3"},
        "abre la válvula de la zona uno": {"tipo": "abrir_valvula", "zona": "1"},
        "cierra la segunda válvula": {"tipo": "cerrar_valvula", "zona": "2"},
        "apaga la válvula número 3": {"tipo": "cerrar_valvula", "zona": "3"},
        "abre la válvula": {"tipo": "abrir_valvula", "zona": None},
        # ambigüedad: se rechaza, no se adivina
        "abre la válvula 1 y la válvula 2": {"tipo": "ambigua"},
        "abre la válvula 7": {"tipo": "ambigua"},
        # coordinadas, plurales y negaciones (detectadas en la prueba real)
        "abre la válvula 1 y la 2": {"tipo": "ambigua"},
        "abre las válvulas 1 y 2": {"tipo": "ambigua"},
        "abre la válvula 1, 2 y 3": {"tipo": "ambigua"},
        "cierra la válvula 2 y 3": {"tipo": "ambigua"},
        "no abras la válvula 1": {"tipo": "ambigua"},
        "no cierres la válvula 1": {"tipo": "ambigua"},
        "no prendas la bomba": {"tipo": "ambigua"},
        "abre la válvula 1 y espera 2 minutos": {"tipo": "abrir_valvula", "zona": "1"},
        "cierra la válvula 1 y abre la válvula 2": {"tipo": "ambigua"},
        "no, mejor no abras... bueno sí, abre la válvula 1": {"tipo": "ambigua"},
        "apaga la bomba y cierra la válvula 1": {"tipo": "ambigua"},
    }
    for frase, esperado in casos.items():
        obtenido = comandos.detectar_accion(frase)
        assert obtenido == esperado, f"{frase!r}: esperado {esperado}, obtenido {obtenido}"

    # preguntas de estado: NO son acciones, siguen yendo a consultas.py
    for pregunta in ["¿la bomba está encendida?", "¿la válvula 1 está abierta?",
                     "¿está prendida la bomba?", "¿cómo está la zona 1?",
                     "¿está en modo automático?", "¿en qué modo está?"]:
        assert comandos.detectar_accion(pregunta) is None, pregunta
    assert consultas.resolver_consulta_determinista("¿la bomba está encendida?") is not None
    print(f"Detección: {len(casos)} frases de orden y las preguntas de estado OK")


def main():
    probar_deteccion()

    # Sin servidor: la orden no salió.
    r = comandos.ejecutar_accion({"tipo": "apagar_bomba"})
    assert r == prompts.RESPUESTA_ACCION_SIN_CONEXION, r

    server = HTTPServer(("127.0.0.1", PUERTO), FakeNodeRed)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    chat = 42

    # Abrir: primero pregunta, NO envía nada hasta el SÍ
    n = len(RECIBIDO)
    r = comandos.procesar_accion(chat, comandos.detectar_accion("abre la válvula de la zona 2"))
    assert "zona 2" in r and "SÍ" in r, r
    assert len(RECIBIDO) == n, "abrir no debe enviarse antes del SÍ"

    # SÍ (como lo transcribe Whisper) -> envía, y dice la hora de cierre
    responder(200, {"estado": "confirmado", "cierra_a": time.time() * 1000 + 20 * 60 * 1000})
    r = comandos.resolver_confirmacion(chat, "Sí.")
    assert RECIBIDO[-1] == {"tipo": "abrir_valvula", "zona": 2}, RECIBIDO[-1]
    assert r.startswith("Listo") and "se cierra sola a las" in r, r

    # NO -> no envía
    n = len(RECIBIDO)
    comandos.procesar_accion(chat, {"tipo": "encender_bomba"})
    assert comandos.resolver_confirmacion(chat, "no") == prompts.RESPUESTA_CONFIRMACION_CANCELADA
    assert len(RECIBIDO) == n

    # Otra cosa -> descarta la pendiente y el mensaje sigue su flujo normal
    comandos.procesar_accion(chat, {"tipo": "encender_bomba"})
    assert comandos.resolver_confirmacion(chat, "¿cómo está la zona 1?") is None
    assert comandos.resolver_confirmacion(chat, "sí") is None, "la pendiente ya se descartó"
    assert len(RECIBIDO) == n

    # SÍ tardío -> vencida, no envía
    comandos.procesar_accion(chat, {"tipo": "cambiar_modo", "modo": "manual"})
    time.sleep(1.2)
    assert comandos.resolver_confirmacion(chat, "sí") == prompts.RESPUESTA_CONFIRMACION_VENCIDA
    assert len(RECIBIDO) == n

    # Pasar a manual: pregunta; al confirmar envía "manual" y dice la hora de vuelta a auto
    comandos.procesar_accion(chat, {"tipo": "cambiar_modo", "modo": "manual"})
    responder(202, {"estado": "enviado", "vence_a": time.time() * 1000 + 30 * 60 * 1000})
    r = comandos.resolver_confirmacion(chat, "dale")
    assert RECIBIDO[-1] == {"tipo": "cambiar_modo", "modo": "manual"}, RECIBIDO[-1]
    assert "no puedo confirmarlo" in r and "vuelve solo a automático" in r, r

    # Cerrar, apagar y volver a automático: directo, sin preguntar
    responder(200, {"estado": "confirmado"})
    r = comandos.procesar_accion(chat, comandos.detectar_accion("cierra la válvula 3"))
    assert RECIBIDO[-1] == {"tipo": "cerrar_valvula", "zona": 3} and r.startswith("Listo"), r
    responder(202, {"estado": "enviado"})
    comandos.procesar_accion(chat, comandos.detectar_accion("cambia a modo automático"))
    assert RECIBIDO[-1] == {"tipo": "cambiar_modo", "modo": "auto"}, RECIBIDO[-1]

    # Casos que NO deben llegar a Node-RED
    n = len(RECIBIDO)
    assert comandos.procesar_accion(chat, {"tipo": "ambigua"}) == prompts.RESPUESTA_ACCION_AMBIGUA
    assert comandos.procesar_accion(chat, {"tipo": "abrir_valvula", "zona": None}) == prompts.RESPUESTA_ACCION_SIN_ZONA
    assert "en construcción" in comandos.procesar_accion(chat, {"tipo": "cambiar_modo", "modo": "ia"})
    assert len(RECIBIDO) == n, "una acción ambigua o no soportada no debe enviarse"

    # Ningún estado distinto de "confirmado" puede sonar a éxito
    for codigo, estado in [(504, "sin_confirmar"), (409, "requiere_manual"),
                           (409, "nodo_desconectado"), (409, "sin_valvula_abierta"),
                           (409, "sin_telemetria"), (409, "ocupado"),
                           (400, "invalido"), (403, "prohibido"), (500, "")]:
        responder(codigo, {"estado": estado})
        r = comandos.ejecutar_accion({"tipo": "apagar_bomba"})
        assert not r.startswith("Listo"), (estado, r)

    responder(500, "no es json")
    assert comandos.ejecutar_accion({"tipo": "apagar_bomba"}) == prompts.RESPUESTA_ACCION_INCIERTA

    # Timeout del lado del bot: resultado incierto, no "no se hizo"
    responder(200, {"estado": "confirmado"}, demora=2)
    assert comandos.ejecutar_accion({"tipo": "apagar_bomba"}) == prompts.RESPUESTA_ACCION_INCIERTA
    time.sleep(1.5)

    # --- Estado tras una orden confirmada (Mongo guarda 1 doc/min) ---------
    # El caso real: "Listo, abrí la válvula 1" y 15 s después "¿está abierta?"
    # respondía "cerrada" porque el último documento era anterior a la orden.
    def doc(segundos_atras, **campos):
        base = {"timestamp": datetime.now(timezone.utc) - timedelta(seconds=segundos_atras),
                "zona1_humedad": 40, "zona2_humedad": 50, "zona3_humedad": 45,
                "valvula1": 0, "valvula2": 0, "valvula3": 0, "bomba": 0,
                "can1_online": 1, "can2_online": 1, "can3_online": 1}
        base.update(campos)
        return base

    EVENTOS = []

    class FakeEventos:
        def find(self, filtro):
            desde = filtro["timestamp"]["$gt"]
            tipos = filtro["tipo"]["$in"]
            self.res = [e for e in EVENTOS if e["tipo"] in tipos and e["timestamp"] > desde]
            return self
        def sort(self, *_):
            return sorted(self.res, key=lambda e: e["timestamp"])

    class FakeMongo:
        def __init__(self, d): self.d = d
        def __getitem__(self, nombre):
            return FakeEventos() if nombre == telemetria.config.EVENTOS_COLLECTION else self
        def find_one(self, **_): return self.d

    def con_mongo(d):
        telemetria.get_mongo_client = lambda: FakeMongo(d)

    def evento(segundos_atras, tipo, **campos):
        EVENTOS.append({"timestamp": datetime.now(timezone.utc) - timedelta(seconds=segundos_atras),
                        "tipo": tipo, **campos})

    telemetria._ordenes.clear()
    con_mongo(doc(40))
    r = consultas.resolver_consulta_determinista("¿cómo está la zona 1?")
    assert "válvula cerrada" in r and "Hace 40 segundos" in r, r          # sin órdenes: Mongo manda
    assert "Se guarda un dato por minuto" in r, r

    responder(200, {"estado": "confirmado", "campo": "valvula1", "valor": 1})
    assert comandos.ejecutar_accion({"tipo": "abrir_valvula", "zona": 1}).startswith("Listo")
    r = consultas.resolver_consulta_determinista("¿cómo está la zona 1?")  # doc de hace 40 s < orden
    assert "válvula abierta" in r and "Por tu orden confirmada" in r, r
    r = consultas.resolver_consulta_determinista("¿cómo está la zona 2?")  # otra zona: no se toca
    assert "válvula cerrada" in r and "Por tu orden" not in r, r
    r = consultas.resolver_consulta_determinista("dime el estado actual del huerto")
    assert "válvula zona 1 abierta" in r and "válvula zona 2 cerrada" in r and "Por tu orden" in r, r

    con_mongo(doc(0))                                                      # entra un doc posterior a la orden
    r = consultas.resolver_consulta_determinista("¿cómo está la zona 1?")
    assert "válvula cerrada" in r and "Por tu orden" not in r, r           # Mongo vuelve a mandar

    con_mongo(doc(300))                                                    # sin docs nuevos: la orden caduca
    telemetria._ordenes["valvula1"] = (1, datetime.now(timezone.utc) - timedelta(seconds=telemetria.ORDEN_VIGENCIA_S + 5))
    r = consultas.resolver_consulta_determinista("¿cómo está la zona 1?")
    assert "válvula cerrada" in r and "Hace 5 minutos" in r and "Por tu orden" not in r, r

    # Una orden que NO se confirmó no cambia lo que se dice
    telemetria._ordenes.clear()
    con_mongo(doc(40))
    responder(504, {"estado": "sin_confirmar", "campo": "valvula1", "valor": 1})
    comandos.ejecutar_accion({"tipo": "abrir_valvula", "zona": 1})
    assert "válvula cerrada" in consultas.resolver_consulta_determinista("¿cómo está la zona 1?")

    # Cierre confirmado: la bomba apagada por orden se dice apagada
    responder(200, {"estado": "confirmado", "campo": "bomba", "valor": 0})
    con_mongo(doc(40, bomba=1))
    comandos.ejecutar_accion({"tipo": "apagar_bomba"})
    r = consultas.resolver_consulta_determinista("dime el estado actual del huerto")
    assert "bomba de agua (motor principal) apagada" in r and "la bomba está apagada" in r, r

    # --- Cambios registrados en `eventos` (dashboard, llave a mano) --------
    telemetria._ordenes.clear()
    EVENTOS.clear()
    con_mongo(doc(40))
    evento(10, "valvula_abierta", zona=2)                    # nadie lo pidió por el bot
    r = consultas.resolver_consulta_determinista("¿cómo está la zona 2?")
    assert "válvula abierta" in r and "Según el último cambio registrado" in r, r
    assert "válvula cerrada" in consultas.resolver_consulta_determinista("¿cómo está la zona 1?")
    evento(5, "valvula_cerrada", zona=2)                     # el último cambio manda
    assert "válvula cerrada" in consultas.resolver_consulta_determinista("¿cómo está la zona 2?")
    EVENTOS.clear()
    evento(60, "valvula_abierta", zona=3)                    # evento ANTERIOR al doc: Mongo manda
    assert "válvula cerrada" in consultas.resolver_consulta_determinista("¿cómo está la zona 3?")
    EVENTOS.clear()
    evento(10, "riego_on")                                   # bomba: la escribe el flujo existente
    assert "bomba de agua (motor principal) encendida" in consultas.resolver_consulta_determinista("estado actual")
    EVENTOS.clear()
    evento(10, "valvula_abierta", zona=9)                    # basura: se ignora
    evento(10, "otro_tipo", zona=1)
    assert "Según el último" not in consultas.resolver_consulta_determinista("estado actual")
    # una orden más nueva que el evento gana, y al revés
    evento(30, "valvula_abierta", zona=1)
    responder(200, {"estado": "confirmado", "campo": "valvula1", "valor": 0})
    comandos.ejecutar_accion({"tipo": "cerrar_valvula", "zona": 1})
    r = consultas.resolver_consulta_determinista("¿cómo está la zona 1?")
    assert "válvula cerrada" in r and "Por tu orden confirmada" in r, r
    # Mongo caído al leer eventos: no rompe, sigue con el documento
    telemetria._ordenes.clear()
    class Roto(FakeMongo):
        def __getitem__(self, nombre):
            if nombre == telemetria.config.EVENTOS_COLLECTION:
                raise telemetria.PyMongoError("caído")
            return self
    telemetria.get_mongo_client = lambda: Roto(doc(40))
    assert "válvula cerrada" in consultas.resolver_consulta_determinista("¿cómo está la zona 1?")

    server.shutdown()
    print("✅ test_acciones OK: detección sin adivinar, SÍ/NO para abrir agua, "
          "y solo 'confirmado' suena a éxito.")
    os._exit(0)


if __name__ == "__main__":
    main()
