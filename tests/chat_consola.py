"""
Habla con el bot desde la terminal, sin Telegram: el mismo camino que un
mensaje de texto (SÍ/NO pendiente -> acciones -> consultas -> LLM), con la
API real de Node-RED. Sirve para probar las acciones físicas contra el
simulador (tests/simulador_esp32.py) o contra el ESP32 de verdad.

    python3 tests/chat_consola.py

Una frase por línea; Ctrl+D o "salir" termina. Lo que no sea una orden ni
una consulta exacta va al LLM (si Ollama no está, responde el mensaje de fallo).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if not os.environ.get("TELEGRAM_BOT_TOKEN"):
    os.environ["TELEGRAM_BOT_TOKEN"] = "consola-sin-telegram"  # no se usa

import bot  # noqa: E402

CHAT_ID = 1


def main():
    print("Chat de consola (sin Telegram). Prueba: «abre la válvula 1», «sí», «cierra la válvula 1».")
    while True:
        try:
            texto = input("tú> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if texto.lower() in {"salir", "q"}:
            break
        if texto:
            print("bot>", bot.process_user_message(CHAT_ID, texto))


if __name__ == "__main__":
    main()
