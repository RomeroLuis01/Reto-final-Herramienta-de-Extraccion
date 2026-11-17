"""
Módulo 7 — Sistema de logging y monitoreo (Avance 1).

Configura el logger centralizado del proyecto con soporte para
archivo y consola. Todos los módulos deben obtener su logger
a través de get_logger() en lugar de usar logging.getLogger() directamente.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from colorama import Fore, Style, init as colorama_init

colorama_init(autoreset=True)

# Formato de fecha/hora compacto
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

# Nivel por defecto si no se configura desde settings
_DEFAULT_LEVEL = logging.INFO

# Referencia al handler de archivo para evitar duplicados
_file_handler_registered: bool = False


# ---------------------------------------------------------------------------
# Formatter con colores para consola
# ---------------------------------------------------------------------------


class _ColorFormatter(logging.Formatter):
    """Formatter que añade colores según el nivel de log en la consola."""

    _COLORS: dict[int, str] = {
        logging.DEBUG: Fore.CYAN,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.MAGENTA + Style.BRIGHT,
    }

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        color = self._COLORS.get(record.levelno, "")
        record.levelname = f"{color}{record.levelname:<8}{Style.RESET_ALL}"
        return super().format(record)


# ---------------------------------------------------------------------------
# Configuración del logger raíz del proyecto
# ---------------------------------------------------------------------------


def setup_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
) -> None:
    """
    Configura el logger raíz del proyecto.

    Debe llamarse una única vez al iniciar la aplicación.

    Args:
        level: Nivel de log (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_file: Ruta del archivo de log. Si es None no se escribe a archivo.
    """
    global _file_handler_registered

    numeric_level = getattr(logging, level.upper(), _DEFAULT_LEVEL)

    root_logger = logging.getLogger("src")
    root_logger.setLevel(numeric_level)

    # Evitar duplicar handlers si se llama más de una vez
    if root_logger.handlers:
        return

    # --- Handler de consola ---
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_fmt = _ColorFormatter(
        fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt=_DATE_FMT,
    )
    console_handler.setFormatter(console_fmt)
    root_logger.addHandler(console_handler)

    # --- Handler de archivo ---
    if log_file and not _file_handler_registered:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)  # El archivo guarda todo
        file_fmt = logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
            datefmt=_DATE_FMT,
        )
        file_handler.setFormatter(file_fmt)
        root_logger.addHandler(file_handler)
        _file_handler_registered = True


def get_logger(name: str) -> logging.Logger:
    """
    Retorna un logger hijo del logger raíz del proyecto.

    Args:
        name: Nombre del módulo o componente (ej. 'inspector.http').

    Returns:
        Logger configurado.
    """
    return logging.getLogger(f"src.{name}")

