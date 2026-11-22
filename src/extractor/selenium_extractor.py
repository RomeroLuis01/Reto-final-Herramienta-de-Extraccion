"""Módulo 3 — Motor de extracción de DOM dinámico con Selenium (Avance 2)."""

from __future__ import annotations

import hashlib
from typing import Any

from src.config.settings import ProjectConfig
from src.extractor._http_utils import find_next_url
from src.extractor.base import BaseExtractor, ExtractionBatch, PageSnapshot
from src.inspector.dom_inspector import DomInspector
from src.monitor.logger import get_logger
from src.utils.security import sanitize_url

logger = get_logger("extractor.selenium")


class SeleniumExtractor(BaseExtractor):
    """Extrae contenido renderizado por JavaScript, scroll infinito y 'cargar más'."""

    method = "selenium"

    def __init__(
        self,
        config: ProjectConfig | None = None,
        *,
        allow_private_network: bool = False,
        wait_after_load_s: float | None = None,
    ) -> None:
        super().__init__(config, allow_private_network=allow_private_network)
        self._wait_after_load_s = wait_after_load_s

    def extract(self, url: str, config: dict[str, Any]) -> ExtractionBatch:
        """Navega con Chrome headless y captura el DOM tras cada interacción."""
        self._check_barrier(config)
        max_pages, _max_records, delay = self._limits(config)
        diagnosis = config.get("diagnosis") or {}
        pagination = diagnosis.get("pagination") or {}

        infinite_scroll = bool(
            config.get("infinite_scroll", pagination.get("infinite_scroll", False))
        )
        load_more_selector = str(
            config.get("load_more_selector") or ""
        )
        next_selector = str(
            config.get("next_page_selector") or pagination.get("pagination_selector") or ""
        )

        batch = ExtractionBatch(method=self.method)
        batch.metadata.update(
            {
                "infinite_scroll": infinite_scroll,
                "load_more_selector": load_more_selector,
                "next_page_selector": next_selector,
            }
        )

        mechanism = ("scroll_infinito" if infinite_scroll else
                     "cargar_mas" if load_more_selector else
                     "paginacion" if next_selector else "ninguno")
        batch.metadata["mechanism"] = mechanism
        batch.metadata["stopped_reason"] = "sin determinar"
        interaction = "carga_inicial"

        wait = self._wait_after_load_s
        if wait is None:
            wait = float(config.get("wait_after_load_s", 2.0) or 2.0)

        try:
            with DomInspector(
                self.config.selenium,
                allow_private_network=self.allow_private_network,
                wait_after_load_s=wait,
            ) as dom:
                inspection = dom.inspect(url, static_html="", static_text_size=0)
                if inspection.get("error") or inspection.get("ssrf_blocked"):
                    batch.errors.append(
                        str(inspection.get("error") or "Navegación Selenium bloqueada")
                    )
                    return batch
                batch.metadata["diagnosis_dom"] = {
                    "js_generated_content": inspection.get("js_generated_content", False),
                    "infinite_scroll_confirmed": inspection.get(
                        "infinite_scroll_confirmed", False
                    ),
                }

                current_url = inspection.get("final_url") or sanitize_url(url)
                seen_html: set[str] = set()

                for page_number in range(1, max_pages + 1):
                    html = dom.get_rendered_html()
                    if not html:
                        batch.errors.append("Selenium no devolvió HTML renderizado.")
                        batch.metadata["stopped_reason"] = "sin HTML renderizado"
                        break
                    fingerprint = hashlib.sha256(html.encode("utf-8")).hexdigest()
                    if fingerprint in seen_html:
                        batch.metadata["stopped_reason"] = "contenido sin cambios"
                        break
                    seen_html.add(fingerprint)

                    batch.pages.append(
                        PageSnapshot(
                            url=sanitize_url(current_url),
                            html=html,
                            data=None,
                            content_format="html",
                            status_code=None,
                            final_url=sanitize_url(current_url),
                            method=self.method,
                            page_number=page_number,
                            metadata={"interaction": interaction},
                        )
                    )
                    logger.info(
                        "Página %d extraída con Selenium: %s",
                        page_number,
                        sanitize_url(current_url),
                    )
                    if page_number >= max_pages:
                        batch.metadata["stopped_reason"] = "límite de capturas alcanzado"
                        break

                    advance = self._advance(
                        dom,
                        html,
                        current_url,
                        infinite_scroll=infinite_scroll,
                        load_more_selector=load_more_selector,
                        next_selector=next_selector,
                        delay=delay,
                    )
                    if not advance.get("ok"):
                        batch.metadata["stopped_reason"] = advance.get("reason", "sin más contenido")
                        break
                    interaction = mechanism
                    if advance.get("url"):
                        current_url = advance["url"]
        except Exception as exc:
            batch.metadata["stopped_reason"] = "error durante adquisición"
            batch.errors.append(f"Error al extraer con Selenium: {exc}")
            logger.exception("Error en SeleniumExtractor")

        return batch

    def is_applicable(self, diagnosis: dict[str, Any]) -> bool:
        """Aplica a sitios con contenido dinámico o paginación por interacción."""
        if self._barrier_message({"diagnosis": diagnosis}):
            return False
        pagination = diagnosis.get("pagination") or {}
        acquisition = diagnosis.get("acquisition_mechanisms") or {}
        technologies = diagnosis.get("technologies") or {}
        return bool(
            pagination.get("infinite_scroll")
            or pagination.get("load_more_button")
            or acquisition.get("js_generated_content")
            or technologies.get("render_type")
            in ("dynamic_probable", "spa_probable", "mixed_probable")
            or diagnosis.get("selenium_status") == "executed"
        )

    # ------------------------------------------------------------------
    # Avance de paginación
    # ------------------------------------------------------------------

    @staticmethod
    def _advance(
        dom: DomInspector,
        html: str,
        current_url: str,
        *,
        infinite_scroll: bool,
        load_more_selector: str,
        next_selector: str,
        delay: float,
    ) -> dict[str, Any]:
        if infinite_scroll:
            grew = dom.scroll_to_bottom(wait_s=max(0.3, delay or 0.6))
            return {
                "ok": grew,
                "reason": "scroll infinito: no apareció más contenido",
            }

        if load_more_selector:
            clicked = dom.click_css(load_more_selector, wait_s=max(0.3, delay or 0.6))
            return {
                "ok": clicked,
                "reason": "botón 'cargar más' no disponible",
            }

        if next_selector:
            next_url = find_next_url(html, current_url, next_selector)
            if not next_url:
                return {"ok": False, "reason": "no hay enlace siguiente"}
            navigation = dom.navigate(next_url)
            if not navigation.get("ok"):
                return {"ok": False, "reason": navigation.get("error")}
            return {"ok": True, "url": navigation["final_url"]}

        return {"ok": False, "reason": "sin mecanismo de paginación configurado"}
