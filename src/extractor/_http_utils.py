"""Utilidades HTTP compartidas por los motores de extracción (Avance 2)."""

from __future__ import annotations

import csv
import io
import json
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

import requests
from bs4 import BeautifulSoup
from charset_normalizer import from_bytes

from src.config.settings import RequestConfig
from src.monitor.logger import get_logger
from src.utils.security import sanitize_url, validate_url_security
from src.utils.auth import authorization, origin

logger = get_logger("extractor.http_utils")


def make_soup(html: str) -> BeautifulSoup:
    """Construye BeautifulSoup usando lxml con fallback a html.parser."""
    try:
        return BeautifulSoup(html or "", "lxml")
    except Exception:  # pragma: no cover - solo si lxml no está disponible
        return BeautifulSoup(html or "", "html.parser")


def detect_content_format(content_type: str, body: str = "") -> str:
    """Detecta html, json, xml, csv o texto a partir de Content-Type/contenido."""
    ct = (content_type or "").lower()
    if "json" in ct or "+json" in ct:
        return "json"
    if "xml" in ct:
        return "xml"
    if "csv" in ct or "text/tab-separated-values" in ct:
        return "csv"
    if "html" in ct:
        return "html"
    stripped = (body or "").lstrip()
    if stripped.startswith(("{", "[")):
        return "json"
    if stripped.startswith("<?xml") or stripped.startswith("<rss"):
        return "xml"
    if "<html" in stripped[:2000].lower():
        return "html"
    return "texto"


def parse_body(body: str, content_format: str) -> Any:
    """Convierte el cuerpo a datos estructurados cuando aplica."""
    if content_format == "json":
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            logger.warning("JSON inválido en la respuesta: %s", exc)
            return None
    if content_format == "csv":
        try:
            return list(csv.DictReader(io.StringIO(body)))
        except csv.Error as exc:  # pragma: no cover - csv robusto por defecto
            logger.warning("CSV inválido en la respuesta: %s", exc)
            return []
    if content_format == "xml":
        try:
            root = ET.fromstring(body)
            return [elem.attrib for elem in root] if len(root) else root.attrib
        except ET.ParseError as exc:
            logger.warning("XML inválido en la respuesta: %s", exc)
            return None
    return None


class HttpClient:
    """Cliente HTTP con anti-SSRF, redirecciones controladas y límite de descarga."""

    def __init__(
        self,
        config: RequestConfig,
        *,
        allow_private_network: bool = False,
        auth_mode: str = "none",
        auth_origin: str = "",
    ) -> None:
        self._cfg = config
        self._allow_private_network = allow_private_network
        self._auth_mode = auth_mode
        self._auth_origin = auth_origin
        self._session = requests.Session()
        self._session.headers.update(config.headers)

    def close(self) -> None:
        """Cierra la sesión subyacente."""
        self._session.close()

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> tuple[requests.Response, str, str, list[str]]:
        """
        Descarga una URL con validación anti-SSRF en cada salto.

        Returns:
            (response, cuerpo_decodificado, url_final, redirecciones_sanitizadas)
        """
        last_exc: Exception | None = None
        for attempt in range(max(1, self._cfg.max_retries)):
            try:
                return self._get_once(url, params=params)
            except requests.RequestException as exc:
                last_exc = exc
                if attempt + 1 < max(1, self._cfg.max_retries):
                    logger.debug(
                        "Reintento %d/%d tras error HTTP: %s",
                        attempt + 1,
                        self._cfg.max_retries,
                        exc,
                    )
                    time.sleep(max(0.0, self._cfg.retry_delay))
        assert last_exc is not None
        raise last_exc

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _validate(self, url: str) -> None:
        safe, reason = validate_url_security(
            url,
            resolve_dns=not self._allow_private_network,
            allow_private_network=self._allow_private_network,
        )
        if not safe:
            raise ValueError(f"URL bloqueada por anti-SSRF: {reason}")

    def _get_once(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> tuple[requests.Response, str, str, list[str]]:
        self._validate(url)
        current_url = url
        redirects: list[str] = []
        response: requests.Response | None = None

        for _hop in range(max(1, self._cfg.max_redirects) + 1):
            if self._auth_mode != "none" and origin(self._auth_origin) != origin(current_url):
                raise ValueError("Destino fuera del origen bloqueado durante autenticación.")
            self._validate(current_url)
            response = self._session.get(
                current_url,
                headers=authorization(self._auth_mode, self._auth_origin, current_url),
                params=params if not redirects else None,
                timeout=self._cfg.timeout,
                allow_redirects=False,
                stream=True,
                verify=True,
            )
            location = response.headers.get("Location")
            if response.status_code in (301, 302, 303, 307, 308) and location:
                current_url = requests.compat.urljoin(current_url, location)
                redirects.append(sanitize_url(current_url))
                response.close()
                continue
            break
        else:  # pragma: no cover - límite de saltos
            raise requests.TooManyRedirects(
                f"Se superaron {self._cfg.max_redirects} redirecciones."
            )

        assert response is not None
        if response.status_code in (401, 403, 429, 503):
            status = response.status_code
            response.close()
            raise ValueError(f"Extracción detenida por respuesta HTTP {status}.")
        body_bytes = self._read_limited(response)
        encoding = response.encoding
        if not encoding:
            best = from_bytes(body_bytes[:65536]).best()
            encoding = best.encoding if best else "utf-8"
        try:
            body = body_bytes.decode(encoding, errors="replace")
        except (LookupError, UnicodeDecodeError):
            body = body_bytes.decode("utf-8", errors="replace")

        final_url = sanitize_url(response.url or current_url)
        self._validate_final_url(response.url or current_url)
        return response, body, final_url, redirects

    def _read_limited(self, response: requests.Response) -> bytes:
        """Lee como máximo max_response_bytes para no saturar memoria."""
        chunks: list[bytes] = []
        total = 0
        max_bytes = self._cfg.max_response_bytes
        for chunk in response.iter_content(chunk_size=65536):
            remaining = max_bytes - total
            if remaining <= 0:
                logger.warning(
                    "Respuesta truncada a %d bytes (%s)", max_bytes, sanitize_url(response.url)
                )
                break
            chunks.append(chunk[:remaining])
            total += len(chunk[:remaining])
        response.close()
        return b"".join(chunks)

    def _validate_final_url(self, url: str) -> None:
        """Revalida la URL final por si hubo redirección a destino interno."""
        safe, reason = validate_url_security(
            url,
            resolve_dns=not self._allow_private_network,
            allow_private_network=self._allow_private_network,
        )
        if not safe:
            raise ValueError(f"URL final bloqueada por anti-SSRF: {reason}")


_CSS_NEXT_HINTS = (
    "a[rel='next']",
    "a.next",
    ".pagination a.next",
    ".pager a.next",
    "a[title*='next' i]",
    "a[aria-label*='next' i]",
)


def find_next_url(html: str, current_url: str, selector: str = "") -> str | None:
    """Localiza la URL de la página siguiente en HTML estático."""
    soup = make_soup(html)
    candidates: list[Any] = []
    if selector:
        try:
            candidates.extend(soup.select(selector))
        except Exception:
            logger.debug("Selector de paginación inválido: %s", selector)
    for hint in _CSS_NEXT_HINTS:
        try:
            candidates.extend(soup.select(hint))
        except Exception:  # pragma: no cover
            continue

    for element in candidates:
        href = element.get("href") if getattr(element, "get", None) else None
        if not href:
            continue
        href = str(href)
        if href.startswith(("javascript:", "#", "mailto:")):
            continue
        from urllib.parse import urljoin

        return urljoin(current_url, href)

    text_hints = ("siguiente", "next", "›", "»")
    for link in soup.find_all("a", href=True):
        label = f"{link.get_text(' ', strip=True)} {link.get('aria-label', '')}".lower()
        if any(hint in label for hint in text_hints):
            return str(requests.compat.urljoin(current_url, str(link["href"])))
    return None


def clean_text(value: str) -> str:
    """Normaliza espacios en blanco para usar en reglas de paginación."""
    return re.sub(r"\s+", " ", value or "").strip()
