"""Decodificación de cuerpos HTTP textuales con charset explícito o declarado."""
from __future__ import annotations

import codecs
from email.message import Message

from bs4 import UnicodeDammit
from charset_normalizer import from_bytes

from src.monitor.logger import get_logger

logger = get_logger("utils.http_encoding")


def decode_http_body(data: bytes, content_type: str) -> str:
    """Prioriza BOM, charset HTTP y meta HTML; avisa si es necesario reemplazar bytes."""
    message = Message()
    message["content-type"] = content_type or "application/octet-stream"
    encoding = message.get_content_charset()
    if data.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
    elif data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encoding = "utf-32"
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    elif not encoding and message.get_content_type() in ("text/html", "application/xhtml+xml"):
        encoding = UnicodeDammit(data[:65536], is_html=True).declared_html_encoding
    if not encoding:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            best = from_bytes(data[:65536]).best()
            encoding = best.encoding if best else "utf-8"
    try:
        return data.decode(encoding)
    except LookupError:
        logger.warning("Charset HTTP o HTML desconocido: %s; se intenta UTF-8.", encoding)
        encoding = "utf-8"
    except UnicodeDecodeError:
        logger.warning("Bytes incompatibles con el charset %s; se reemplazarán caracteres inválidos.", encoding)
        return data.decode(encoding, errors="replace")
    try:
        return data.decode(encoding)
    except UnicodeDecodeError:
        logger.warning("Respuesta no válida en UTF-8; se reemplazarán caracteres inválidos.")
        return data.decode(encoding, errors="replace")
