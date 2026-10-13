"""
Configuración de logging (Capítulo 8, §8.4 — "el directorio de
registros: necesario para la fase de pruebas con la comunidad"). Consola
+ archivo rotativo en logs/bot.log, para poder revisar qué pasó después
de una sesión de pruebas sin depender del scrollback de la terminal.
"""
import logging
import os
import re
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

# El token de Telegram aparece en logs de tres formas: en la URL de la API
# (.../bot<ID>:<SECRETO>/getUpdates), en la URL de descarga de archivos
# (.../file/bot<ID>%3A<SECRETO>/voice/..., con ":" codificado como %3A) y en
# el texto de telegram.error.InvalidToken (sin prefijo "bot"). El patrón
# reconoce la forma del token (ID numérico, ":" o "%3A", 30+ caracteres),
# no el contexto, para cubrir todas y cualquier otra que aparezca.
_TOKEN_TELEGRAM = re.compile(r"(?:bot)?\d{6,}(?::|%3[Aa])[A-Za-z0-9_-]{30,}")


class _FormatoSeguro(logging.Formatter):
    """Igual que Formatter, pero tapa el token de Telegram en el texto ya
    formateado (mensaje y traceback), venga del logger que venga."""

    def format(self, record: logging.LogRecord) -> str:
        return _TOKEN_TELEGRAM.sub("<TOKEN>", super().format(record))


def setup_logging() -> None:
    """Debe llamarse UNA vez, antes de importar cualquier otro módulo del
    proyecto -- así, avisos de import (ej. el chequeo de ffmpeg en voz.py)
    también quedan en el archivo, no solo en consola."""
    os.makedirs(LOG_DIR, exist_ok=True)
    formato = _FormatoSeguro("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

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
