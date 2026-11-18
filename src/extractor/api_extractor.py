"""Módulo 3 — Motor de extracción de APIs REST/JSON autorizadas (Avance 2)."""

from __future__ import annotations

import time
from typing import Any

from src.config.settings import ProjectConfig
from src.extractor._http_utils import HttpClient
from src.extractor.base import BaseExtractor, ExtractionBatch, PageSnapshot
from src.monitor.logger import get_logger
from src.utils.security import sanitize_url

logger = get_logger("extractor.api")

_DATA_KEYS = ("data", "results", "items", "records", "products", "posts", "features")


class ApiExtractor(BaseExtractor):
    """Consume endpoints JSON detectados y autorizados en el diagnóstico."""

    method = "api"

    def __init__(
        self,
        config: ProjectConfig | None = None,
        *,
        allow_private_network: bool = False,
    ) -> None:
        super().__init__(config, allow_private_network=allow_private_network)
        self._client: HttpClient | None = None

    def extract(self, url: str, config: dict[str, Any]) -> ExtractionBatch:
        """Obtiene datos de los endpoints configurados con paginación controlada."""
        self._check_barrier(config)
        max_pages, _max_records, delay = self._limits(config)

        endpoints = self._resolve_endpoints(url, config)
        if not endpoints:
            return ExtractionBatch(
                method=self.method,
                errors=["No hay endpoints API autorizados disponibles para extraer."],
            )

        page_param = str(config.get("api_page_param") or "page")
        page_start = int(config.get("api_page_start", 1) or 1)
        base_params = dict(config.get("api_params") or {})
        batch = ExtractionBatch(method=self.method)
        batch.metadata["endpoints"] = [sanitize_url(e) for e in endpoints]

        self._client = HttpClient(
            self.config.request,
            allow_private_network=self.allow_private_network,
        )
        page_number = 0

        try:
            for endpoint in endpoints:
                page = page_start
                while page_number < max_pages:
                    params = dict(base_params)
                    if page_param:
                        params[page_param] = page
                    response, body, final_url, redirects = self._client.get(
                        endpoint, params=params
                    )
                    data = self._json_data(body)
                    if data is None:
                        batch.errors.append(
                            f"Respuesta JSON no interpretable en {sanitize_url(endpoint)}"
                        )
                        break

                    if isinstance(data, list) and not data:
                        break
                    if isinstance(data, dict) and not data:
                        break

                    page_number += 1
                    batch.pages.append(
                        PageSnapshot(
                            url=sanitize_url(endpoint),
                            html="",
                            data=data,
                            content_format="json",
                            status_code=response.status_code,
                            final_url=final_url,
                            method=self.method,
                            page_number=page_number,
                            metadata={
                                "params": {
                                    k: ("***" if "token" in k.lower() or "key" in k.lower() else v)
                                    for k, v in params.items()
                                },
                                "redirects": redirects,
                            },
                        )
                    )
                    logger.info(
                        "Endpoint extraído (%s): %s",
                        page_number,
                        sanitize_url(endpoint),
                    )
                    if not page_param or not self._looks_paginated(data):
                        break
                    page += 1
                    if delay:
                        time.sleep(delay)
                if page_number >= max_pages:
                    break
        except ValueError as exc:
            batch.errors.append(str(exc))
            logger.warning("Extracción API bloqueada: %s", exc)
        except Exception as exc:
            batch.errors.append(f"Error al extraer desde API: {exc}")
            logger.exception("Error en ApiExtractor")
        finally:
            self._client.close()
            self._client = None

        return batch

    def is_applicable(self, diagnosis: dict[str, Any]) -> bool:
        """Aplica cuando hay API REST detectada o endpoints observados."""
        if self._barrier_message({"diagnosis": diagnosis}):
            return False
        network = diagnosis.get("network") or {}
        has_api = bool(network.get("rest_api_detected")) or bool(
            network.get("endpoints_observed")
        )
        strategies = " ".join(
            str(s).lower()
            for s in (diagnosis.get("viability") or {}).get("recommended_strategies", [])
        )
        return has_api and ("api" in strategies or "json" in strategies)

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_endpoints(url: str, config: dict[str, Any]) -> list[str]:
        endpoints: list[str] = []
        explicit = config.get("endpoints") or config.get("api_endpoints") or []
        if isinstance(explicit, str):
            endpoints.append(explicit)
        elif isinstance(explicit, list):
            endpoints.extend(str(e) for e in explicit if e)
        if not endpoints:
            diagnosis = config.get("diagnosis") or {}
            endpoints.extend(
                str(e)
                for e in (diagnosis.get("network") or {}).get("endpoints_observed", [])
                if e
            )
        # La URL objetivo puede ser directamente un endpoint JSON.
        if not endpoints and config.get("content_format") in ("json", "api"):
            endpoints.append(url)
        return list(dict.fromkeys(endpoints))

    @staticmethod
    def _json_data(body: str) -> Any:
        import json

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in _DATA_KEYS:
                if isinstance(payload.get(key), list):
                    return payload[key]
        return payload

    @staticmethod
    def _looks_paginated(data: Any) -> bool:
        """Heurística conservadora para seguir paginando solo si hay más datos."""
        if isinstance(data, list):
            return len(data) > 0
        if isinstance(data, dict):
            for key in ("next", "has_more", "hasMore", "total_pages", "totalPages"):
                if key in data:
                    value = data[key]
                    if isinstance(value, bool):
                        return value
                    if isinstance(value, (int, float)):
                        return value > 1
                    return bool(value)
            return False
        return False
