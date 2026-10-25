"""
Simulador del ESP32 receptor + nodos de zona, para probar las acciones del
bot SIN hardware: Mosquitto real, Node-RED real, el bot real (o la consola).

Imita lo que hacen Receptor.ino y emisores.ino:
  - arranca en AUTOMÁTICO con todo apagado;
  - los comandos de bomba/válvula se GUARDAN siempre, pero solo se APLICAN
    en modo manual (así es el firmware: el callback guarda sin mirar el modo);
  - en automático cada zona riega por histéresis (abre bajo ON, cierra sobre OFF)
    y la bomba sigue a las válvulas;
  - publica huerto/general con las mismas claves del firmware.

Lo que NO imita: el CAN, el DHT22 ni el bloqueo de reconnect(). El apagón
del PC o del broker se prueba con el cronómetro y el corte físico, no aquí.

Uso (pip install paho-mqtt):
    python3 tests/simulador_esp32.py            # broker en localhost:1883
Comandos por teclado mientras corre:
    estado          muestra modo, válvulas, bomba y humedades
    hum 1 20        pone la humedad de la zona 1 en 20 %
    corte           el ESP32 "se apaga": deja de publicar y de escuchar
    vuelta          el ESP32 "reinicia": vuelve en automático, todo apagado
    q               salir
"""
import argparse
import json
import sys
import threading
import time

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("Falta paho-mqtt:  pip install paho-mqtt")

TOPICS = ["riego/modo", "riego/bomba", "riego/valvula1", "riego/valvula2",
          "riego/valvula3", "riego/umbrales"]


class Huerto:
    def __init__(self, humedad, on, off):
        self.humedad_inicial = [float(h) for h in humedad]
        self.on, self.off = on, off
        self.lock = threading.Lock()
        self.encendido = True
        self.reiniciar()

    def reiniciar(self):
        with self.lock:
            self.hum = list(self.humedad_inicial)
            self.modo_manual = False
            self.cmd_bomba = 0
            self.cmd_valvula = [0, 0, 0]
            self.riego_activo = [False] * 3
            self.valvula = [0, 0, 0]
            self.bomba = 0

    def mensaje(self, topic, texto):
        """Callback del firmware: guarda siempre; el modo decide si se aplica."""
        if not self.encendido:
            return
        with self.lock:
            if topic == "riego/modo":
                if texto == "manual":
                    self.modo_manual = True
                elif texto == "auto":
                    self.modo_manual = False
            elif topic == "riego/bomba":
                self.cmd_bomba = 1 if texto == "1" else 0
            elif topic.startswith("riego/valvula"):
                try:
                    self.cmd_valvula[int(topic[-1]) - 1] = int(texto)
                except ValueError:
                    self.cmd_valvula[int(topic[-1]) - 1] = 0  # String.toInt()

    def paso(self, dt):
        with self.lock:
            for i in range(3):
                if self.modo_manual:
                    self.valvula[i] = 1 if self.cmd_valvula[i] else 0
                else:
                    if not self.riego_activo[i] and self.hum[i] < self.on:
                        self.riego_activo[i] = True
                    if self.riego_activo[i] and self.hum[i] > self.off:
                        self.riego_activo[i] = False
                    self.valvula[i] = 1 if self.riego_activo[i] else 0
                # regar sube la humedad rápido (para ver el ciclo); el suelo se seca lento
                self.hum[i] = min(100.0, max(0.0, self.hum[i] + (1.0 if self.valvula[i] else -0.02) * dt))
            self.bomba = self.cmd_bomba if self.modo_manual else int(any(self.valvula))

    def telemetria(self):
        with self.lock:
            h, v = [int(x) for x in self.hum], self.valvula
            return json.dumps({
                "nodo1": h[0], "nodo2": h[1], "nodo3": h[2],
                "valvula1": v[0], "valvula2": v[1], "valvula3": v[2],
                "can1": 1, "can2": 1, "can3": 1,
                "on1": self.on, "off1": self.off, "on2": self.on, "off2": self.off,
                "on3": self.on, "off3": self.off,
                "bomba": self.bomba, "temp": 26.0, "humAmb": 60.0})

    def resumen(self):
        with self.lock:
            return (f"modo={'MANUAL' if self.modo_manual else 'auto'} "
                    f"válvulas={self.valvula} bomba={self.bomba} "
                    f"humedad={[int(x) for x in self.hum]}")


def hora():
    return time.strftime("%H:%M:%S")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--broker", default="localhost")
    ap.add_argument("--puerto", type=int, default=1883)
    ap.add_argument("--humedad", default="45,55,40", help="humedad inicial de las 3 zonas")
    ap.add_argument("--on", type=int, default=30, help="umbral que abre (automático)")
    ap.add_argument("--off", type=int, default=50, help="umbral que cierra (automático)")
    ap.add_argument("--periodo", type=float, default=0.2, help="segundos entre publicaciones")
    a = ap.parse_args()

    huerto = Huerto(a.humedad.split(","), a.on, a.off)
    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ESP32_Receptor_SIM") \
        if hasattr(mqtt, "CallbackAPIVersion") else mqtt.Client("ESP32_Receptor_SIM")

    def on_connect(c, *args):
        for t in TOPICS:
            c.subscribe(t)
        print(f"[{hora()}] conectado al broker {a.broker}:{a.puerto}")

    def on_message(c, _u, m):
        texto = m.payload.decode(errors="replace")
        if huerto.encendido:
            print(f"[{hora()}] <- {m.topic} = {texto!r}" + ("" if huerto.modo_manual or m.topic == "riego/modo"
                  or m.topic == "riego/umbrales" else "   (guardado; en automático no se aplica)"))
        huerto.mensaje(m.topic, texto)

    cliente.on_connect, cliente.on_message = on_connect, on_message
    cliente.connect(a.broker, a.puerto, keepalive=30)
    cliente.loop_start()

    def teclado():
        for linea in sys.stdin:
            p = linea.split()
            if not p:
                continue
            if p[0] == "estado":
                print(f"[{hora()}] {'APAGADO ' if not huerto.encendido else ''}{huerto.resumen()}")
            elif p[0] == "hum" and len(p) == 3:
                with huerto.lock:
                    huerto.hum[int(p[1]) - 1] = float(p[2])
            elif p[0] == "corte":
                huerto.encendido = False
                print(f"[{hora()}] ESP32 apagado: no publica ni escucha")
            elif p[0] == "vuelta":
                huerto.reiniciar()
                huerto.encendido = True
                print(f"[{hora()}] ESP32 reiniciado: automático, todo apagado")
            elif p[0] == "q":
                print("saliendo")
                import os
                os._exit(0)

    threading.Thread(target=teclado, daemon=True).start()

    anterior = None
    ultimo = time.time()
    while True:
        time.sleep(a.periodo)
        ahora = time.time()
        if huerto.encendido:
            huerto.paso(ahora - ultimo)
            cliente.publish("huerto/general", huerto.telemetria())
            actual = huerto.resumen().rsplit(" humedad", 1)[0]
            if actual != anterior:
                print(f"[{hora()}] {actual}")
                anterior = actual
        ultimo = ahora


if __name__ == "__main__":
    main()
