"""Credenciales HTTP explícitas, limitadas al origen del objetivo."""
from __future__ import annotations

import base64
import os
from urllib.parse import urlsplit


def origin(url: str) -> tuple[str, str | None, int | None]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    return scheme, parts.hostname, parts.port or (443 if scheme == "https" else 80)


def authorization(mode: str, target_url: str, request_url: str) -> dict[str, str]:
    """Construye el encabezado solo para el origen autorizado, sin registrarlo."""
    if not mode or mode == "none" or origin(target_url) != origin(request_url):
        return {}
    if mode == "basic":
        username = os.getenv("EXTRACTION_AUTH_USERNAME")
        password = os.getenv("EXTRACTION_AUTH_PASSWORD")
        if username is None or password is None:
            raise ValueError("Defina EXTRACTION_AUTH_USERNAME y EXTRACTION_AUTH_PASSWORD.")
        if ":" in username:
            raise ValueError("El nombre de usuario Basic no puede contener dos puntos.")
        encoded = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}
    if mode == "bearer":
        token = os.getenv("EXTRACTION_AUTH_TOKEN")
        if not token:
            raise ValueError("Defina EXTRACTION_AUTH_TOKEN.")
        if any(ord(char) < 33 or ord(char) > 126 for char in token):
            raise ValueError("El token contiene caracteres no válidos para HTTP.")
        return {"Authorization": f"Bearer {token}"}
    raise ValueError("Modo de autenticación desconocido.")
