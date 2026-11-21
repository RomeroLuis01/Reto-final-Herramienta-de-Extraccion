"""Módulo 3 — Motor de extracción con requests (HTML/JSON/XML/CSV estático)."""

from __future__ import annotations

import time
from typing import Any

from src.config.settings import ProjectConfig
from src.extractor._http_utils import (
    HttpClient,
    detect_content_format,
    find_next_url,
    parse_body,
)
from src.extractor.base import BaseExtractor, ExtractionBatch, PageSnapshot
from src.monitor.logger import get_logger
from src.utils.security import sanitize_url

logger = get_logger("extractor.requests")


class RequestsExtractor(BaseExtractor):
    """Obtiene páginas mediante peticiones HTTP controladas (sin JavaScript)."""

    method = "requests"

    def __init__(
        self,
        config: ProjectConfig | None = None,
        *,
        allow_private_network: bool = False,
    ) -> None:
        super().__init__(config, allow_private_network=allow_private_network)
        self._client: HttpClient | None = None

    def extract(self, url: str, config: dict[str, Any]) -> ExtractionBatch:
        """Descarga la URL y sigue la paginación traditional detectada."""
        self._check_barrier(config)
        max_pages, _max_records, delay = self._limits(config)
        diagnosis = config.get("diagnosis") or {}
        pagination = diagnosis.get("pagination") or {}
        next_selector = str(
            config.get("next_page_selector") or pagination.get("pagination_selector") or ""
        )

        batch = ExtractionBatch(method=self.method)
        batch.metadata["max_pages"] = max_pages
        if pagination.get("infinite_scroll"):
            batch.metadata["warning"] = (
                "El sitio usa scroll infinito; se extrajo únicamente el HTML inicial. "
                "Use --strategy selenium para cargar todo el contenido."
            )

        self._client = HttpClient(
            self.config.request,
            allow_private_network=self.allow_private_network,
            auth_mode=config.get("auth_mode", "none"),
            auth_origin=url,
        )
        current_url = url
        visited: set[str] = set()

        try:
            for page_number in range(1, max_pages + 1):
                if not current_url or current_url in visited:
                    break
                visited.add(current_url)

                response, body, final_url, redirects = self._client.get(current_url)
                content_format = detect_content_format(
                    response.headers.get("Content-Type", ""), body
                )
                batch.pages.append(
                    PageSnapshot(
                        url=sanitize_url(current_url),
                        html=body if content_format == "html" else "",
                        data=parse_body(body, content_format),
                        content_format=content_format,
                        status_code=response.status_code,
                        final_url=final_url,
                        method=self.method,
                        page_number=page_number,
                        metadata={
                            "redirects": redirects,
                            "content_type": response.headers.get("Content-Type", ""),
                        },
                    )
                )
                logger.info(
                    "Página %d extraída con requests (%s): %s",
                    page_number,
                    content_format,
                    sanitize_url(current_url),
                )

                if content_format != "html":
                    break

                next_url = find_next_url(body, final_url, next_selector)
                if not next_url or next_url in visited:
                    break
                current_url = next_url
                if delay and page_number < max_pages:
                    time.sleep(delay)
        except ValueError as exc:
            # Errores anti-SSRF: se reportan y no se continúa.
            batch.errors.append(str(exc))
            logger.warning("Extracción requests bloqueada: %s", exc)
        except Exception as exc:  #requests.RequestException y errores de decode
            batch.errors.append(f"Error al extraer con requests: {exc}")
            logger.exception("Error en RequestsExtractor")
        finally:
            self._client.close()
            self._client = None

        return batch

    def is_applicable(self, diagnosis: dict[str, Any]) -> bool:
        """Aplica a sitios accesibles con formatos estáticos o sin JS crítico."""
        if self._barrier_message({"diagnosis": diagnosis}):
            return False
        if diagnosis.get("access_status") not in (None, "", "accesible"):
            return False
        acquisition = diagnosis.get("acquisition_mechanisms") or {}
        fmt = str(acquisition.get("content_format") or "html").lower()
        return fmt in ("html", "json", "xml", "csv", "texto")
