"""
Prueba de humo: valida que llm_client.ask_llm() arma bien la petición y
procesa la respuesta, usando un servidor HTTP falso en vez de LM Studio
real. No usa Telegram ni requiere token real.
"""
import io
import json
import math
import os
import struct
import sys
import threading
import wave as wave_module
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

# Los módulos del proyecto viven un nivel arriba de tests/ -- se agrega
# esa carpeta al principio de sys.path para poder importarlos sin
# necesidad de instalar el proyecto como paquete.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Variables de entorno ANTES de importar config.py (config.py las lee al
# importarse, y todos los demás módulos importan config).
os.environ["TELEGRAM_BOT_TOKEN"] = "dummy-token-para-test"
os.environ["LLM_BASE_URL"] = "http://localhost:8765/v1"
os.environ["LLM_MODEL"] = "qwen3.5-4b"


class FakeLMStudioHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        self.received_messages = body.get("messages", [])

        fake_reply = (
            "¡Hola! Le cuento que regamos la zona uno para que esté bien "
            "jugosa y la otra ya está lista, todo en orden."
        )
        response = {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": fake_reply},
                    "finish_reason": "stop",
                }
            ],
        }
        payload = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        pass  # silenciar logs del servidor de prueba


def main():
    server = HTTPServer(("localhost", 8765), FakeLMStudioHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Se importan aquí, ya con las env vars de prueba listas.
    import logging_config
    logging_config.setup_logging()  # ejercita también el logging a archivo

    import config
    import llm_client
    import telemetria
    import voz

    print("--- Turno 1 ---")
    reply1 = llm_client.ask_llm(chat_id=1, user_text="¿Cómo está la zona 1?")
    print("Respuesta:", reply1)
    assert "zona uno" in reply1.lower() or "jugosa" in reply1.lower()

    print("\n--- Turno 2 (verifica que el historial se acumula) ---")
    reply2 = llm_client.ask_llm(chat_id=1, user_text="¿Y la zona 2?")
    print("Respuesta:", reply2)
    history_len = len(llm_client._history[1])
    print(f"Mensajes en historial tras 2 turnos: {history_len}")
    assert history_len == 4, "Se esperaban 4 mensajes (2 turnos user+assistant)"

    print("\n--- Formateo de telemetría (con documento sintético, igual al real) ---")
    doc_reciente = {
        "timestamp": datetime.now(timezone.utc) - timedelta(minutes=5),
        "zona1_humedad": 28, "zona2_humedad": 55, "zona3_humedad": 40,
        "temp_ambiente": 26, "hum_ambiente": 60, "bomba": 1,
        "valvula1": 1, "valvula2": 0, "valvula3": 0,
        "can1_online": 1, "can2_online": 1, "can3_online": 1,
    }
    texto = telemetria.format_telemetry_for_llm(doc_reciente)
    print("Formateado:", texto)
    assert "28" in texto and "encendida" in texto, "Faltan datos esperados en el formateo"
    assert "válvula zona 1 abierta" in texto, "Falta el estado de la válvula 1"
    assert "válvula zona 2 cerrada" in texto, "Falta el estado de la válvula 2"
    assert "zona 1: en línea" in texto, "Falta declarar CAN zona 1 en línea"

    doc_con_zona_offline = {**doc_reciente, "can2_online": 0}
    texto2 = telemetria.format_telemetry_for_llm(doc_con_zona_offline)
    print("Con zona offline:", texto2)
    assert "zona 2: sin conexión" in texto2, "No detectó la zona 2 offline"
    assert "zona 1: en línea" in texto2, "Zona 1 no debería verse afectada"

    texto_sin_datos = telemetria.format_telemetry_for_llm(None)
    print("Sin ningún documento:", texto_sin_datos)
    assert "No hay datos" in texto_sin_datos

    print("\n--- Resiliencia: forzando que Mongo no responda (puerto inexistente) ---")
    config.MONGO_URI = "mongodb://localhost:1/"
    telemetria._mongo_client = None  # descarta el cliente cacheado, si ya se creó
    resultado = telemetria.get_latest_telemetry()
    assert resultado is None, "Se esperaba None: el puerto 1 nunca debería responder"
    print("get_latest_telemetry() devolvió None sin lanzar excepción ✅ (así se comportará")
    print("también en producción si el ESP32/Mongo no están disponibles todavía).")

    print("\n--- ask_llm() ahora con telemetría inyectada (aunque Mongo esté caído) ---")
    reply3 = llm_client.ask_llm(chat_id=2, user_text="¿Cómo está el huerto?")
    print("Respuesta:", reply3)
    assert reply3, "ask_llm no debería fallar aunque Mongo esté caído"

    print("\n--- Resumen histórico: cálculo de tiempo de riego (_calcular_tiempo_riego) ---")
    ahora = datetime.now(timezone.utc)
    eventos_test = [
        {"timestamp": ahora - timedelta(hours=10), "tipo": "riego_on"},
        {"timestamp": ahora - timedelta(hours=9, minutes=45), "tipo": "riego_off"},  # 15 min
        {"timestamp": ahora - timedelta(hours=5), "tipo": "riego_on"},
        {"timestamp": ahora - timedelta(hours=4, minutes=30), "tipo": "riego_off"},  # 30 min
    ]
    minutos, activaciones = telemetria._calcular_tiempo_riego(eventos_test)
    print(f"2 ciclos completos: {minutos:.0f} min, {activaciones} activaciones")
    assert abs(minutos - 45) < 0.1 and activaciones == 2

    eventos_regando_ahora = eventos_test + [{"timestamp": ahora - timedelta(minutes=10), "tipo": "riego_on"}]
    minutos2, act2 = telemetria._calcular_tiempo_riego(eventos_regando_ahora)
    print(f"Con riego activo sin apagar (caso borde): {minutos2:.1f} min, {act2} activaciones")
    assert 54 < minutos2 < 56 and act2 == 3, "El riego en curso debe contar hasta ahora"

    resumen = {
        "promedios": {"zona1_prom": 32.5, "zona2_prom": 55.1, "zona3_prom": 44.8, "n": 100},
        "minutos_riego": 45, "activaciones": 2,
    }
    texto_resumen = telemetria.format_daily_summary_for_llm(resumen)
    print("Formateado:", texto_resumen)
    assert "45" in texto_resumen and "32" in texto_resumen

    print("\n--- Detección determinista: acciones (comandos.py) y consultas (consultas.py) ---")
    import comandos
    import consultas

    accion = comandos.detectar_accion("abre la válvula de la zona 1")
    assert accion == {"tipo": "abrir_valvula", "zona": "1"}
    print("Acción detectada:", accion, "->", comandos.responder_accion_no_disponible(accion))

    assert comandos.detectar_accion("¿la bomba está encendida?") is None, \
        "una pregunta informativa no debe detectarse como acción"

    resultado_determinista = consultas.resolver_consulta_determinista("dime el estado actual del huerto")
    assert resultado_determinista is not None
    print("Consulta resuelta sin LLM:", resultado_determinista)

    print("\n--- Conversión de audio WAV -> OGG/Opus (usada para responder con voz) ---")
    sample_rate = 22050
    wav_buf = io.BytesIO()
    with wave_module.open(wav_buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        frames = bytearray()
        for i in range(sample_rate):
            val = int(3000 * math.sin(2 * math.pi * 440 * i / sample_rate))
            frames += struct.pack("<h", val)
        w.writeframes(bytes(frames))

    ogg_bytes = voz.wav_bytes_to_ogg_opus(wav_buf.getvalue())
    assert ogg_bytes[:4] == b"OggS", "La salida no tiene la firma OGG esperada"
    print(f"WAV sintético ({len(wav_buf.getvalue())} bytes) -> OGG/Opus ({len(ogg_bytes)} bytes) ✅")

    print(
        "\nNota: no se prueban aquí Whisper ni Piper de extremo a extremo porque "
        "requieren descargar modelos desde huggingface.co, inalcanzable en este "
        "entorno. Esa parte se valida en tu máquina siguiendo el README."
    )

    print("\n✅ Smoke test OK: llm_client.ask_llm arma la petición, llama al servidor, "
          "guarda historial, comandos.py/consultas.py detectan sin invocar al LLM, y "
          "la conversión de audio para notas de voz funciona.")
    server.shutdown()

    os._exit(0)


if __name__ == "__main__":
    main()
