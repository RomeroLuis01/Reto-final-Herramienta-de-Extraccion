"""
Módulo 2 — Inspector HTTP.

Realiza el análisis de acceso HTTP: estado, cabeceras, cookies (solo metadatos),
tiempos de respuesta, redirecciones controladas con validación anti-SSRF,
límite de tamaño de respuesta, detección de formato y verificación de robots.txt.
"""

from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from charset_normalizer import from_bytes
from requests.exceptions import (
    ConnectionError as ReqConnectionError,
    SSLError,
    Timeout,
)

from src.config.settings import RequestConfig, get_config
from src.monitor.logger import get_logger
from src.utils.security import sanitize_headers, sanitize_url, validate_url_security
from src.utils.auth import authorization, origin

logger = get_logger("inspector.http")

_SESSION_COOKIE_PATTERNS = (
    "session",
    "sessid",
    "phpsessid",
    "jsessionid",
    "asp.net_sessionid",
    "connect.sid",
    "csrftoken",
    "xsrf",
    "_csrf",
    "sid",
    "auth",
)

_MAX_ROBOTS_BYTES = 512 * 1024  # 512 KB


class HttpInspector:
    """
    Realiza la inspección HTTP de un sitio web con controles anti-SSRF.

    Responsabilidades:
    - Validar URL y resolución DNS antes de cada solicitud y redirección.
    - Verificar acceso y estado HTTP sin confundir errores HTTP con fallos de red.
    - Analizar cabeceras de seguridad, rate limiting y WAF sin almacenar secretos.
    - Extraer metadatos de cookies y gestión de sesiones (sin valores).
    - Limitar tiempos de espera, redirecciones y tamaño de descarga.
    - Verificar robots.txt usando la sesión configurada.
    """

    def __init__(
        self,
        config: RequestConfig | None = None,
        *,
        allow_private_network: bool = False,
        auth_mode: str = "none",
    ) -> None:
        self._cfg = config or get_config().request
        self._allow_private_network = allow_private_network
        self._auth_mode = auth_mode
        self._session = self._build_session()

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(self._cfg.headers)
        return session

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def inspect(self, url: str, check_robots: bool = True) -> dict[str, Any]:
        """
        Inspecciona la URL a nivel HTTP.

        Args:
            url: URL a inspeccionar.
            check_robots: Si se debe verificar robots.txt.

        Returns:
            Diccionario con resultados de la inspección HTTP.
        """
        safe_input_url = sanitize_url(url)
        result: dict[str, Any] = {
            "url": safe_input_url,
            "http_connected": False,
            "access_ok": False,
            "status_code": None,
            "http_error_category": None,
            "connection_error": False,
            "error_type": None,
            "ssrf_blocked": False,
            "response_time_s": None,
            "content_type": None,
            "content_format": "desconocido",
            "format_details": {},
            "content_length": None,
            "bytes_received": 0,
            "response_truncated": False,
            "server": None,
            "is_https": url.lower().startswith("https://"),
            "tls_verified": True,
            "redirects": [],
            "redirects_count": 0,
            "final_url": safe_input_url,
            "cookies_count": 0,
            "cookies_names": [],
            "cookie_flags": {},
            "session_cookies_detected": [],
            "security_headers": {},
            "robots_txt": {},
            "error": None,
        }

        # Validación anti-SSRF previa a cualquier conexión
        safe, reason = validate_url_security(
            url,
            resolve_dns=not self._allow_private_network,
            allow_private_network=self._allow_private_network,
        )
        if not safe:
            result["ssrf_blocked"] = True
            result["error_type"] = "ssrf_blocked"
            result["error"] = f"Solicitud bloqueada por política anti-SSRF: {reason}"
            logger.warning("SSRF bloqueado (%s): %s", safe_input_url, reason)
            return result

        fetch_out = self._fetch_with_retry_and_redirects(url, result)
        if fetch_out is None:
            return result

        response, body_bytes, redirects = fetch_out

        # Servidor respondió por HTTP (incluso si el estado es 4xx/5xx)
        result["http_connected"] = True
        result["status_code"] = response.status_code
        result["access_ok"] = 200 <= response.status_code < 400
        result["content_type"] = response.headers.get("Content-Type", "")
        result["content_length"] = response.headers.get("Content-Length")
        result["bytes_received"] = len(body_bytes)
        result["server"] = response.headers.get("Server", "No revelado")
        result["final_url"] = sanitize_url(response.url)
        result["is_https"] = response.url.lower().startswith("https://")
        result["redirects"] = [sanitize_url(r) for r in redirects]
        result["redirects_count"] = len(redirects)

        cookie_info = self._extract_cookie_metadata(response)
        result["cookies_count"] = cookie_info["cookies_count"]
        result["cookies_names"] = cookie_info["cookies_names"]
        result["cookie_flags"] = cookie_info["cookie_flags"]
        result["session_cookies_detected"] = cookie_info["session_cookies_detected"]

        result["security_headers"] = self._extract_security_headers(response)

        encoding = response.encoding
        if not encoding:
            best = from_bytes(body_bytes[:65536]).best()
            encoding = best.encoding if best else "utf-8"
        try:
            body_text = body_bytes.decode(encoding, errors="replace")
        except (LookupError, UnicodeDecodeError):
            body_text = body_bytes.decode("utf-8", errors="replace")
        result["html_preview"] = body_text[:5000]
        result["_raw_body"] = body_text

        content_format, format_details = self._detect_content_format(
            result["content_type"], body_text
        )
        result["content_format"] = content_format
        result["format_details"] = format_details

        # Clasificar códigos de estado HTTP (distinto de errores de conexión)
        self._classify_http_status(response.status_code, result, safe_input_url)

        if check_robots and not result["ssrf_blocked"]:
            result["robots_txt"] = self._check_robots_txt(response.url)

        logger.info(
            "HTTP %s (%s) — %s (%.2fs, %d bytes)",
            result["status_code"],
            "accesible" if result["access_ok"] else "restringido/error",
            result["final_url"],
            result["response_time_s"] or 0.0,
            result["bytes_received"],
        )
        return result

    # ------------------------------------------------------------------
    # Solicitud con reintentos y validación hop-by-hop de redirecciones
    # ------------------------------------------------------------------

    def _fetch_with_retry_and_redirects(
        self, url: str, result: dict[str, Any]
    ) -> tuple[requests.Response, bytes, list[str]] | None:
        """
        Ejecuta la solicitud HTTP validando SSRF en cada salto de redirección
        y limitando el tamaño máximo descargado.
        """
        for attempt in range(1, self._cfg.max_retries + 1):
            try:
                start = time.perf_counter()
                current_url = url
                redirects: list[str] = []

                for _hop in range(self._cfg.max_redirects + 1):
                    if self._auth_mode != "none" and origin(url) != origin(current_url):
                        result["error_type"] = "cross_origin_auth_redirect"
                        result["error"] = "Redirección fuera del origen bloqueada durante autenticación."
                        return None
                    # Revalidar URL + DNS en cada salto antes de conectar
                    safe, reason = validate_url_security(
                        current_url,
                        resolve_dns=not self._allow_private_network,
                        allow_private_network=self._allow_private_network,
                    )
                    if not safe:
                        result["ssrf_blocked"] = True
                        result["error_type"] = "ssrf_blocked"
                        result["redirects"] = [sanitize_url(r) for r in redirects]
                        result["error"] = (
                            f"Redirección bloqueada por política anti-SSRF: {reason}"
                        )
                        logger.warning("Redirección SSRF bloqueada: %s", reason)
                        return None

                    resp = self._session.get(
                        current_url,
                        headers=authorization(self._auth_mode, url, current_url),
                        timeout=self._cfg.timeout,
                        allow_redirects=False,
                        stream=True,
                        verify=True,
                    )
                    try:
                        if resp.is_redirect or (
                            300 <= resp.status_code < 400 and "Location" in resp.headers
                        ):
                            location = resp.headers.get("Location", "")
                            next_url = urljoin(current_url, location)
                            redirects.append(current_url)
                            current_url = next_url
                            if len(redirects) > self._cfg.max_redirects:
                                result["error_type"] = "too_many_redirects"
                                result["redirects"] = [sanitize_url(r) for r in redirects]
                                result["error"] = (
                                    f"Demasiadas redirecciones (>{self._cfg.max_redirects})."
                                )
                                logger.error(
                                    "Demasiadas redirecciones en %s", sanitize_url(url)
                                )
                                return None
                            continue

                        body_bytes, truncated = self._read_limited_body(
                            resp, self._cfg.max_response_bytes
                        )
                        result["response_truncated"] = truncated
                        result["response_time_s"] = round(
                            time.perf_counter() - start, 3
                        )
                        return resp, body_bytes, redirects
                    finally:
                        resp.close()

            except SSLError as exc:
                result["connection_error"] = True
                result["error_type"] = "ssl_error"
                result["error"] = f"Error SSL/TLS: {exc}"
                logger.error("Error SSL en %s: %s", sanitize_url(url), exc)
                return None

            except Timeout as exc:
                result["connection_error"] = True
                result["error_type"] = "timeout"
                result["error"] = f"Timeout de conexión (intento {attempt}): {exc}"
                logger.warning(
                    "Timeout intento %d/%d: %s",
                    attempt,
                    self._cfg.max_retries,
                    sanitize_url(url),
                )

            except ReqConnectionError as exc:
                result["connection_error"] = True
                result["error_type"] = "connection_error"
                result["error"] = f"Error de conexión (intento {attempt}): {exc}"
                logger.warning(
                    "Error de conexión intento %d/%d: %s",
                    attempt,
                    self._cfg.max_retries,
                    sanitize_url(url),
                )

            except Exception as exc:
                result["connection_error"] = True
                result["error_type"] = "unexpected_error"
                result["error"] = f"Error inesperado: {exc}"
                logger.exception("Error inesperado al acceder a %s", sanitize_url(url))
                return None

            if attempt < self._cfg.max_retries:
                time.sleep(self._cfg.retry_delay)

        return None

    @staticmethod
    def _read_limited_body(
        response: requests.Response, max_bytes: int
    ) -> tuple[bytes, bool]:
        """Lee el cuerpo de respuesta respetando el límite máximo de bytes."""
        chunks: list[bytes] = []
        total = 0
        truncated = False
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            remaining = max_bytes - total
            if remaining <= 0:
                truncated = True
                break
            if len(chunk) > remaining:
                chunks.append(chunk[:remaining])
                total += remaining
                truncated = True
                break
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks), truncated

    # ------------------------------------------------------------------
    # Clasificación HTTP / Cookies / Cabeceras / Formatos
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_http_status(
        status_code: int, result: dict[str, Any], safe_url: str
    ) -> None:
        """Clasifica respuestas HTTP de restricción o error sin confundir con red."""
        if status_code == 401:
            result["auth_required"] = True
            result["http_error_category"] = "auth_required"
            result["error_type"] = "http_401"
            logger.warning("Autenticación requerida (401): %s", safe_url)
        elif status_code == 403:
            result["access_blocked"] = True
            result["http_error_category"] = "access_blocked"
            result["error_type"] = "http_403"
            logger.warning("Acceso bloqueado (403): %s", safe_url)
        elif status_code == 404:
            result["http_error_category"] = "not_found"
            result["error_type"] = "http_404"
            logger.warning("Recurso no encontrado (404): %s", safe_url)
        elif status_code == 429:
            result["rate_limited"] = True
            result["http_error_category"] = "rate_limited"
            result["error_type"] = "http_429"
            logger.warning("Rate limiting detectado (429): %s", safe_url)
        elif status_code >= 500:
            result["server_error"] = True
            result["http_error_category"] = "server_error"
            result["error_type"] = f"http_{status_code}"
            logger.warning("Error de servidor (%s): %s", status_code, safe_url)
        elif status_code >= 400:
            result["http_error_category"] = "client_error"
            result["error_type"] = f"http_{status_code}"

    @staticmethod
    def _extract_cookie_metadata(response: requests.Response) -> dict[str, Any]:
        """
        Extrae únicamente metadatos de cookies (nombres y banderas),
        NUNCA los valores de sesión ni tokens.
        """
        names: list[str] = []
        flags: dict[str, dict[str, Any]] = {}
        session_cookies: list[str] = []

        for cookie in response.cookies:
            name = cookie.name
            names.append(name)
            rest = getattr(cookie, "_rest", {}) or {}
            rest_lower = {str(k).lower(): v for k, v in rest.items()}
            flags[name] = {
                "secure": bool(cookie.secure),
                "httponly": "httponly" in rest_lower,
                "samesite": rest_lower.get("samesite"),
            }
            if any(pat in name.lower() for pat in _SESSION_COOKIE_PATTERNS):
                session_cookies.append(name)

        return {
            "cookies_count": len(names),
            "cookies_names": names,
            "cookie_flags": flags,
            "session_cookies_detected": session_cookies,
        }

    def _extract_security_headers(
        self, response: requests.Response
    ) -> dict[str, str | None]:
        """
        Extrae cabeceras de seguridad, rate limiting y WAF redactando valores
        sensibles como Set-Cookie o WWW-Authenticate con tokens.
        """
        headers_of_interest = [
            "Strict-Transport-Security",
            "Content-Security-Policy",
            "X-Frame-Options",
            "X-Content-Type-Options",
            "X-XSS-Protection",
            "Referrer-Policy",
            "Permissions-Policy",
            "Cache-Control",
            "Set-Cookie",
            "WWW-Authenticate",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
            "X-Rate-Limit",
            "Retry-After",
            "Cf-Ray",
            "Cf-Mitigated",
            "X-Sucuri-ID",
            "X-Iinfo",
            "X-Amz-Cf-Id",
            "X-Akamai-Transformed",
            "X-CDN",
            "X-Cache",
            "X-Powered-By",
        ]
        raw = {h: response.headers.get(h) for h in headers_of_interest}
        return sanitize_headers(raw)

    @staticmethod
    def _detect_content_format(
        content_type: str, body_text: str
    ) -> tuple[str, dict[str, Any]]:
        """Identifica el formato principal de datos entregado por la respuesta."""
        ct = (content_type or "").lower()
        stripped = body_text.lstrip()

        if "application/json" in ct or "+json" in ct:
            details: dict[str, Any] = {"source": "content-type"}
            try:
                parsed = json.loads(body_text)
                details["valid_json"] = True
                if isinstance(parsed, dict):
                    details["root_type"] = "object"
                    details["keys_count"] = len(parsed)
                elif isinstance(parsed, list):
                    details["root_type"] = "array"
                    details["items_count"] = len(parsed)
            except Exception:
                details["valid_json"] = False
            return "json", details

        if "rss" in ct or "atom" in ct:
            return "rss_atom", {"source": "content-type"}

        if "xml" in ct:
            details = {"source": "content-type"}
            try:
                root = ET.fromstring(body_text)
                tag = root.tag.lower()
                if tag in ("rss", "feed") or tag.endswith("}feed"):
                    return "rss_atom", {"source": "xml-root", "root_tag": root.tag}
                details["root_tag"] = root.tag
            except Exception:
                pass
            return "xml", details

        if "text/csv" in ct or "application/csv" in ct:
            lines = [ln for ln in body_text.splitlines() if ln.strip()]
            cols = len(lines[0].split(",")) if lines else 0
            return "csv", {"source": "content-type", "lines_sample": len(lines), "columns_estimate": cols}

        if "application/pdf" in ct:
            return "pdf", {"source": "content-type"}

        if "text/html" in ct or "application/xhtml" in ct or stripped.lower().startswith("<!doctype html") or stripped.lower().startswith("<html"):
            return "html", {"source": "content-type/markup"}

        if stripped.startswith("{") or stripped.startswith("["):
            try:
                json.loads(body_text)
                return "json", {"source": "body-sniffing", "valid_json": True}
            except Exception:
                pass

        if stripped.startswith("<"):
            return "xml", {"source": "body-sniffing"}

        if "text/plain" in ct:
            return "text", {"source": "content-type"}

        return "other", {"content_type": content_type}

    # ------------------------------------------------------------------
    # robots.txt (con timeout, User-Agent y validación SSRF)
    # ------------------------------------------------------------------

    def _check_robots_txt(self, url: str) -> dict[str, Any]:
        """
        Verifica las restricciones de robots.txt usando la sesión configurada
        con timeout, límite de tamaño y validación anti-SSRF.
        """
        parsed = urlparse(url)
        robots_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", "/robots.txt")
        safe_robots_url = sanitize_url(robots_url)

        result: dict[str, Any] = {
            "url": safe_robots_url,
            "accessible": False,
            "crawl_allowed": None,
            "disallowed_paths": [],
            "crawl_delay": None,
            "sitemaps": [],
        }

        safe, reason = validate_url_security(
            robots_url,
            resolve_dns=not self._allow_private_network,
            allow_private_network=self._allow_private_network,
        )
        if not safe:
            result["error"] = f"robots.txt bloqueado por SSRF: {reason}"
            return result

        try:
            resp = self._session.get(
                robots_url,
                timeout=self._cfg.timeout,
                allow_redirects=False,
                stream=True,
                verify=True,
            )
            try:
                if resp.status_code != 200:
                    result["status_code"] = resp.status_code
                    return result

                raw_bytes, _ = self._read_limited_body(resp, _MAX_ROBOTS_BYTES)
                lines = raw_bytes.decode("utf-8", errors="replace").splitlines()
            finally:
                resp.close()

            rp = RobotFileParser()
            rp.parse(lines)

            result["accessible"] = True
            result["crawl_allowed"] = rp.can_fetch("*", url)
            result["crawl_delay"] = rp.crawl_delay("*")

            disallowed: list[str] = []
            sitemaps: list[str] = []
            in_wildcard = False
            for raw_line in lines:
                line = raw_line.split("#", 1)[0].strip()
                if not line or ":" not in line:
                    continue
                key, val = line.split(":", 1)
                key_l = key.strip().lower()
                val_s = val.strip()
                if key_l == "user-agent":
                    in_wildcard = val_s == "*"
                elif key_l == "disallow" and in_wildcard and val_s:
                    disallowed.append(val_s)
                elif key_l == "sitemap" and val_s:
                    sitemaps.append(sanitize_url(val_s))

            result["disallowed_paths"] = disallowed[:50]
            if not sitemaps and hasattr(rp, "site_maps") and rp.site_maps():
                sitemaps = [sanitize_url(s) for s in rp.site_maps()]
            result["sitemaps"] = sitemaps[:20]

            logger.info(
                "robots.txt: acceso %s para %s",
                "permitido" if result["crawl_allowed"] else "RESTRINGIDO",
                sanitize_url(url),
            )

        except Exception as exc:
            result["error"] = str(exc)
            logger.debug("No se pudo leer robots.txt de %s: %s", safe_robots_url, exc)

        return result

    def close(self) -> None:
        """Cierra la sesión HTTP."""
        self._session.close()
