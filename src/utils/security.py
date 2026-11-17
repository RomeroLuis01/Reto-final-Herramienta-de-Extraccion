"""
Utilidades de seguridad — Validación SSRF y sanitización de secretos.

Proporciona:
1. Validación estricta de URLs y prevención de Server-Side Request Forgery (SSRF):
   - Permite únicamente esquemas http y https.
   - Rechaza localhost, loopback, direcciones privadas, link-local,
     multicast, reservadas, sin especificar y metadatos de nube.
   - Resuelve DNS y valida todas las direcciones IP resultantes.
   - Revalida destinos en redirecciones.
2. Sanitización centralizada de URLs, cabeceras HTTP y estructuras
   para evitar exponer credenciales, cookies de sesión o tokens en logs,
   perfiles y reportes.

Limitaciones documentadas:
- Aunque se valida la resolución DNS justo antes de cada solicitud y
  redirección, existe una ventana teórica de DNS rebinding (TOCTOU) entre
  la verificación previa y la apertura del socket por urllib3/Chrome,
  ya que no se desactiva la verificación TLS ni se reemplaza el
  resolutor interno del navegador.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from copy import deepcopy
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

SCHEMES_ALLOWED = frozenset({"http", "https"})

_BLOCKED_HOSTNAMES = frozenset({
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata.google.internal",
    "metadata.goog",
    "metadata",
    "169.254.169.254",
})

_BLOCKED_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".localdomain",
)

_DOMAIN_PATTERN = re.compile(
    r"^(?:[a-zA-Z0-9]"
    r"(?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+"
    r"[a-zA-Z]{2,}$"
)

_SENSITIVE_QUERY_KEYS = frozenset({
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "api_key",
    "apikey",
    "key",
    "secret",
    "client_secret",
    "password",
    "passwd",
    "pwd",
    "auth",
    "authorization",
    "session",
    "sessionid",
    "sid",
    "jwt",
    "signature",
    "sig",
    "code",
})

_SENSITIVE_HEADER_KEYS = frozenset({
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "x-csrf-token",
})

_SENSITIVE_DICT_KEYS = frozenset({
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "cookies",
    "set-cookie",
    "html_preview",
    "dynamic_dom_html",
    "_raw_body",
})


def _extract_hostname(netloc: str) -> str:
    """Extrae el host sin userinfo ni puerto."""
    host_port = netloc.rsplit("@", 1)[-1]
    if host_port.startswith("[") and "]" in host_port:
        return host_port[1:host_port.index("]")]
    return host_port.split(":", 1)[0]


def _parse_ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """
    Intenta interpretar el host como IP literal (incluyendo formatos
    numéricos enteros o IPv4 mapeadas en IPv6).
    """
    clean = host.strip("[]")
    try:
        return ipaddress.ip_address(clean)
    except ValueError:
        pass

    # Detectar formatos decimales/hexadecimales tipo 2130706433 o 0x7f000001
    if re.fullmatch(r"\d+", clean) or re.fullmatch(r"0x[0-9a-fA-F]+", clean):
        try:
            val = int(clean, 0)
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
        except ValueError:
            pass
    return None


def is_ip_publicly_routable(ip_str: str) -> tuple[bool, str]:
    """
    Verifica si una dirección IP es públicamente enrutable.

    Rechaza explícitamente direcciones loopback, privadas, link-local,
    multicast, reservadas, CGNAT (100.64.0.0/10) y sin especificar.
    """
    try:
        ip_obj = ipaddress.ip_address(ip_str.strip("[]"))
    except ValueError:
        return False, f"Dirección IP no válida: '{ip_str}'."

    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
        ip_obj = ip_obj.ipv4_mapped

    if ip_obj.is_loopback:
        return False, f"Dirección loopback bloqueada por SSRF: {ip_obj}"
    if ip_obj.is_private:
        return False, f"Dirección privada bloqueada por SSRF: {ip_obj}"
    if ip_obj.is_link_local:
        return False, f"Dirección link-local/metadatos bloqueada por SSRF: {ip_obj}"
    if ip_obj.is_multicast:
        return False, f"Dirección multicast bloqueada por SSRF: {ip_obj}"
    if ip_obj.is_reserved:
        return False, f"Dirección reservada bloqueada por SSRF: {ip_obj}"
    if ip_obj.is_unspecified:
        return False, f"Dirección no especificada bloqueada por SSRF: {ip_obj}"
    if isinstance(ip_obj, ipaddress.IPv4Address):
        cgnat = ipaddress.IPv4Network("100.64.0.0/10")
        if ip_obj in cgnat:
            return False, f"Dirección CGNAT interna bloqueada por SSRF: {ip_obj}"
    if not ip_obj.is_global:
        return False, f"Dirección no pública bloqueada por SSRF: {ip_obj}"

    return True, "IP pública válida."


def validate_url_security(
    url: str,
    *,
    resolve_dns: bool = False,
    allow_private_network: bool = False,
) -> tuple[bool, str]:
    """
    Valida sintaxis y seguridad SSRF de una URL.

    Args:
        url: URL a validar.
        resolve_dns: Si True, resuelve DNS y valida cada IP obtenida.
        allow_private_network: Solo para pruebas controladas con servidores
            locales. Debe permanecer False para entradas de usuario.

    Returns:
        Tupla (es_segura, mensaje).
    """
    if not url or not url.strip():
        return False, "La URL no puede estar vacía."

    url = url.strip()

    try:
        parsed = urlparse(url)
    except Exception as exc:
        return False, f"Error al parsear la URL: {exc}"

    scheme = parsed.scheme.lower()
    if scheme not in SCHEMES_ALLOWED:
        return (
            False,
            f"Esquema no permitido: '{parsed.scheme}'. "
            f"Solo se permiten: {', '.join(sorted(SCHEMES_ALLOWED))}.",
        )

    if not parsed.netloc:
        return False, "La URL no contiene un dominio válido."

    hostname = _extract_hostname(parsed.netloc)
    if not hostname:
        return False, "La URL no contiene un host válido."

    host_lower = hostname.lower().rstrip(".")

    if not allow_private_network:
        if host_lower in _BLOCKED_HOSTNAMES or any(
            host_lower.endswith(suffix) for suffix in _BLOCKED_HOST_SUFFIXES
        ):
            return (
                False,
                f"Host bloqueado por política anti-SSRF: '{hostname}'.",
            )

    ip_literal = _parse_ip_literal(hostname)
    if ip_literal is not None:
        if not allow_private_network:
            ok, msg = is_ip_publicly_routable(str(ip_literal))
            if not ok:
                return False, msg
    else:
        if not _DOMAIN_PATTERN.match(host_lower) and not (
            allow_private_network and host_lower == "localhost"
        ):
            return False, f"Formato de dominio no válido: '{hostname}'."

    if resolve_dns and not allow_private_network:
        port = parsed.port or (443 if scheme == "https" else 80)
        try:
            addr_infos = socket.getaddrinfo(
                hostname, port, proto=socket.IPPROTO_TCP
            )
        except socket.gaierror as exc:
            return False, f"No se pudo resolver DNS para '{hostname}': {exc}"
        except Exception as exc:
            return False, f"Error de resolución DNS para '{hostname}': {exc}"

        if not addr_infos:
            return False, f"DNS no devolvió direcciones para '{hostname}'."

        for info in addr_infos:
            resolved_ip = info[4][0]
            ok, msg = is_ip_publicly_routable(resolved_ip)
            if not ok:
                return (
                    False,
                    f"Destino DNS bloqueado por SSRF ({hostname} -> {resolved_ip}): {msg}",
                )

    return True, "URL válida."


def sanitize_url(url: str) -> str:
    """
    Oculta credenciales incrustadas (user:pass@) y parámetros de consulta
    sensibles antes de registrar URLs en logs, reportes o perfiles.
    """
    if not url:
        return url
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc
        if "@" in netloc:
            _, host_part = netloc.rsplit("@", 1)
            netloc = f"***:***@{host_part}"

        query_items = parse_qsl(parsed.query, keep_blank_values=True)
        sanitized_query = [
            (k, "***" if k.lower() in _SENSITIVE_QUERY_KEYS else v)
            for k, v in query_items
        ]
        return urlunparse((
            parsed.scheme,
            netloc,
            parsed.path,
            parsed.params,
            urlencode(sanitized_query),
            parsed.fragment,
        ))
    except Exception:
        return url


def sanitize_headers(headers: dict[str, Any]) -> dict[str, Any]:
    """
    Redacta valores sensibles de cabeceras HTTP manteniendo trazabilidad
    de presencia sin exponer secretos ni valores de cookies de sesión.
    """
    sanitized: dict[str, Any] = {}
    for k, v in headers.items():
        if v is None:
            sanitized[k] = None
        elif str(k).lower() in _SENSITIVE_HEADER_KEYS:
            sanitized[k] = "[REDACTED]"
        else:
            sanitized[k] = v
    return sanitized


def sanitize_for_persistence(data: Any) -> Any:
    """
    Elimina o redacta campos sensibles de diccionarios antes de guardarlos
    en perfiles o reportes.
    """
    if isinstance(data, dict):
        cleaned: dict[str, Any] = {}
        for k, v in data.items():
            key_lower = str(k).lower()
            if key_lower in _SENSITIVE_DICT_KEYS:
                if key_lower in ("html_preview", "dynamic_dom_html", "_raw_body"):
                    continue  # No persistir HTML crudo ni cuerpo interno
                cleaned[k] = "[REDACTED]"
            elif key_lower in ("url", "final_url"):
                cleaned[k] = sanitize_url(str(v)) if v else v
            elif key_lower in ("redirects", "endpoints_observed") and isinstance(v, list):
                cleaned[k] = [sanitize_url(str(item)) for item in v]
            elif key_lower == "security_headers" and isinstance(v, dict):
                cleaned[k] = sanitize_headers(v)
            else:
                cleaned[k] = sanitize_for_persistence(v)
        return cleaned
    if isinstance(data, list):
        return [sanitize_for_persistence(item) for item in data]
    return data

