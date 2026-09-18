"""
Configuración de logging (Capítulo 8, §8.4 — "el directorio de
registros: necesario para la fase de pruebas con la comunidad"). Consola
+ archivo rotativo en logs/bot.log, para poder revisar qué pasó después
de una sesión de pruebas sin depender del scrollback de la terminal.
"""
import logging
import os
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def setup_logging() -> None:
    """Debe llamarse UNA vez, antes de importar cualquier otro módulo del
    proyecto -- así, avisos de import (ej. el chequeo de ffmpeg en voz.py)
    también quedan en el archivo, no solo en consola."""
    os.makedirs(LOG_DIR, exist_ok=True)
    formato = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    consola = logging.StreamHandler()
    consola.setFormatter(formato)

    archivo = RotatingFileHandler(
        os.path.join(LOG_DIR, "bot.log"),
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    archivo.setFormatter(formato)

    logging.basicConfig(level=logging.INFO, handlers=[consola, archivo])
