"""
Módulo 2 — Orquestador del sistema de inspección y diagnóstico.

Coordina todos los inspectores y analistas del Módulo 2:
1. HttpInspector — análisis HTTP con control anti-SSRF y robots.txt.
2. HtmlAnalyzer — análisis HTML estático (probabilístico).
3. ProtectionDetector (pre-check) — detiene ejecución ante barreras críticas.
4. DomInspector — análisis DOM dinámico con Selenium (confirmación).
5. ProtectionDetector (consolidado) — incidencias estructuradas.
6. ViabilityEvaluator — evaluación técnica separada de la legal.

Genera el diagnóstico consolidado y sanitizado que alimenta el perfil
y el reporte final.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable

from src.config.settings import AnalysisTarget, ProjectConfig, get_config
from src.inspector.dom_inspector import DomInspector
from src.inspector.html_analyzer import HtmlAnalyzer
from src.inspector.http_inspector import HttpInspector
from src.inspector.protection_detector import ProtectionDetector
from src.inspector.viability_evaluator import ViabilityEvaluator
from src.monitor.logger import get_logger
from src.utils.security import sanitize_for_persistence, sanitize_url

logger = get_logger("inspector.orchestrator")


class WebInspector:
    """
    Orquestador principal del sistema de inspección web.

    Uso:
        inspector = WebInspector()
        diagnosis = inspector.inspect(target)
    """

    def __init__(self, config: ProjectConfig | None = None) -> None:
        self._config = config or get_config()

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def inspect(
        self,
        target: AnalysisTarget,
        use_selenium: bool = True,
        previous_profile: dict[str, Any] | None = None,
        sample_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """
        Ejecuta el diagnóstico completo de un sitio web.

        Args:
            target: Objetivo de análisis con URL validada.
            use_selenium: Si True, incluye análisis DOM dinámico cuando aplique.
            previous_profile: Perfil previo reutilizable si el sitio es conocido.

        Returns:
            Diccionario de diagnóstico consolidado y sanitizado.
        """
        start_total = time.perf_counter()
        if target.auth_mode != "none":
            use_selenium = False
        safe_url = target.safe_url
        logger.info("Iniciando diagnóstico de: %s", safe_url)

        steps_executed: list[str] = []
        diagnosis: dict[str, Any] = {
            "url": safe_url,
            "final_url": safe_url,
            "domain": target.domain,
            "label": target.label,
            "http_auth_mode": target.auth_mode,
            "site_identification": {
                "domain": target.domain,
                "label": target.label,
                "title": "",
                "final_url": safe_url,
            },
            "diagnosis_timestamp": datetime.now().isoformat(),
            "access_status": "desconocido",
            "selenium_status": "pendiente",
            "http": {},
            "html_analysis": {},
            "technologies": {},
            "acquisition_mechanisms": {},
            "network": {},
            "pagination": {},
            "interactions": {},
            "candidate_selectors": {},
            "protection": {},
            "viability": {},
            "confidence_summary": {
                "confirmed": [],
                "probable": [],
                "unidentified": [],
            },
            "traceability": {},
            "errors": [],
        }

        if previous_profile:
            diagnosis["traceability"]["previous_profile_reused"] = True
            diagnosis["traceability"]["previous_analysis_count"] = previous_profile.get(
                "analysis_count", 0
            )
            if not target.label and previous_profile.get("label"):
                diagnosis["label"] = previous_profile["label"]
                diagnosis["site_identification"]["label"] = previous_profile["label"]

        # ---- Paso 1: Inspección HTTP ----
        steps_executed.append("http_inspection")
        http_inspector = HttpInspector(
            self._config.request,
            allow_private_network=target.allow_private_network,
            auth_mode=target.auth_mode,
        )
        try:
            http_result = http_inspector.inspect(
                target.url, check_robots=target.respect_robots_txt
            )
        except Exception as exc:
            diagnosis["errors"].append(f"Error en inspección HTTP: {exc}")
            logger.exception("Error crítico en inspección HTTP")
            http_result = {
                "url": safe_url,
                "http_connected": False,
                "access_ok": False,
                "connection_error": True,
                "error": str(exc),
            }
        finally:
            http_inspector.close()

        full_body = str(http_result.pop("_raw_body", "") or "")
        http_result.pop("html_preview", None)

        diagnosis["http"] = http_result
        diagnosis["final_url"] = http_result.get("final_url", safe_url)
        diagnosis["site_identification"]["final_url"] = diagnosis["final_url"]
        diagnosis["access_status"] = self._resolve_access_status(http_result)

        if http_result.get("error"):
            diagnosis["errors"].append(http_result["error"])

        if not http_result.get("access_ok"):
            logger.warning(
                "Sitio inaccesible o con respuesta de restricción (%s). Diagnóstico detenido antes de Selenium.",
                diagnosis["access_status"],
            )
            diagnosis["selenium_status"] = "halted_http_error"
            diagnosis["protection"] = ProtectionDetector().analyze(http_result, {}, {})
            diagnosis["viability"] = ViabilityEvaluator().evaluate(
                http_result, {}, {}, diagnosis["protection"]
            )
            self._finalize_metadata(
                diagnosis, {}, {}, steps_executed, start_total
            )
            return sanitize_for_persistence(diagnosis)

        # ---- Paso 2: Análisis HTML estático (solo si el formato es HTML) ----
        content_format = str(http_result.get("content_format", "html"))
        html_analysis: dict[str, Any] = {}
        if content_format == "html":
            steps_executed.append("html_static_analysis")
            try:
                analyzer = HtmlAnalyzer(full_body, base_url=target.url)
                html_analysis = analyzer.analyze()
                diagnosis["site_identification"]["title"] = html_analysis.get("title", "")
            except Exception as exc:
                diagnosis["errors"].append(f"Error en análisis HTML: {exc}")
                logger.exception("Error en análisis HTML")
        else:
            steps_executed.append(f"structured_format_{content_format}")
            html_analysis = {
                "render_type": f"direct_{content_format}",
                "static_html_size": http_result.get("bytes_received", 0),
                "static_text_size": 0,
            }

        diagnosis["html_analysis"] = html_analysis

        # ---- Paso 3: Verificación previa de barreras antes de Selenium ----
        pre_protection = ProtectionDetector().analyze(http_result, html_analysis, {})
        dom_result: dict[str, Any] = {}

        if pre_protection.get("blocking_barrier"):
            diagnosis["selenium_status"] = "halted_by_protection"
            logger.warning(
                "Barrera crítica detectada en inspección inicial; Selenium omitido por seguridad/política."
            )
            dom_result = {
                "selenium_available": False,
                "halted_by_protection": True,
            }
        elif content_format != "html":
            diagnosis["selenium_status"] = "skipped_non_html"
            dom_result = {
                "selenium_available": False,
                "skipped_non_html": True,
            }
        elif use_selenium:
            steps_executed.append("selenium_dom_inspection")
            try:
                with DomInspector(
                    self._config.selenium,
                    allow_private_network=target.allow_private_network,
                    wait_after_load_s=target.parameters.wait_after_load_s,
                ) as dom_inspector:
                    dom_result = dom_inspector.inspect(
                        target.url,
                        static_html=full_body,
                        static_text_size=int(html_analysis.get("static_text_size", 0) or 0),
                    )
                if dom_result.get("error"):
                    diagnosis["errors"].append(f"DOM inspector: {dom_result['error']}")
                    diagnosis["selenium_status"] = (
                        "halted_ssrf" if dom_result.get("ssrf_blocked") else "error"
                    )
                else:
                    diagnosis["selenium_status"] = "executed"
            except Exception as exc:
                diagnosis["errors"].append(
                    f"Error en DOM inspector (Selenium): {exc}. "
                    "Continuando con análisis estático."
                )
                logger.warning("Selenium no disponible: %s", exc)
                diagnosis["selenium_status"] = "error"
                dom_result = {"selenium_available": False, "error": str(exc)}
        else:
            diagnosis["selenium_status"] = "skipped_by_user"
            dom_result = {"selenium_available": False, "skipped": True}

        # Propagar métricas dinámicas a html_analysis para coherencia del reporte
        if dom_result.get("selenium_available"):
            diagnosis["html_analysis"]["dynamic_dom_size"] = dom_result.get("dynamic_dom_size")
            diagnosis["html_analysis"]["dynamic_text_size"] = dom_result.get("dynamic_text_size")
            diagnosis["html_analysis"]["dom_elements_count"] = dom_result.get("dom_elements_count")
            diagnosis["html_analysis"]["dom_difference"] = dom_result.get("dom_difference")
            diagnosis["html_analysis"]["text_difference"] = dom_result.get("text_difference")
            diagnosis["html_analysis"]["dom_significantly_different"] = dom_result.get(
                "dom_significantly_different", False
            )
            diagnosis["html_analysis"]["js_generated_content"] = dom_result.get(
                "js_generated_content", False
            )
            diagnosis["html_analysis"]["time_dependent_content"] = dom_result.get(
                "time_dependent_content", False
            )
            diagnosis["html_analysis"]["page_load_time_s"] = dom_result.get("page_load_time_s")

        # ---- Paso 4: Consolidar tecnologías ----
        diagnosis["technologies"] = self._consolidate_technologies(
            html_analysis, dom_result
        )

        # ---- Paso 5: Consolidar información de red ----
        diagnosis["network"] = {
            "rest_api_detected": dom_result.get("rest_api_detected", False),
            "graphql_network_hint": bool(dom_result.get("graphql_detected", False)),
            "graphql_inline_hint": bool((html_analysis.get("inline_api_calls") or {}).get("graphql_in_inline_js")),
            "xhr_fetch_observed": bool(dom_result.get("xhr_fetch_detected", False)),
            "xhr_fetch_inline_hint": bool((html_analysis.get("inline_api_calls") or {}).get("fetch_in_inline_js")
                                          or (html_analysis.get("inline_api_calls") or {}).get("xhr_ajax_in_inline_js")),
            "graphql_detected": dom_result.get("graphql_detected", False)
            or bool((html_analysis.get("inline_api_calls") or {}).get("graphql_in_inline_js")),
            "xhr_fetch_detected": dom_result.get("xhr_fetch_detected", False)
            or bool((html_analysis.get("inline_api_calls") or {}).get("fetch_in_inline_js"))
            or bool((html_analysis.get("inline_api_calls") or {}).get("xhr_ajax_in_inline_js")),
            "websocket_detected": dom_result.get("websocket_detected", False)
            or bool((html_analysis.get("time_dependent_hints") or {}).get("uses_websocket")),
            "http_methods_observed": dom_result.get("http_methods_observed", []),
            "content_types_observed": dom_result.get("content_types_observed", []),
            "endpoints_observed": dom_result.get("endpoints_observed", []),
            "network_requests": dom_result.get("network_requests", []),
        }

        # ---- Paso 6: Consolidar paginación ----
        diagnosis["pagination"] = self._consolidate_pagination(
            html_analysis, dom_result
        )

        # ---- Paso 7: Detectar protecciones consolidadas ----
        steps_executed.append("protection_detection")
        try:
            diagnosis["protection"] = ProtectionDetector().analyze(
                http_result, html_analysis, dom_result
            )
        except Exception as exc:
            diagnosis["errors"].append(f"Error en detección de protecciones: {exc}")
            logger.exception("Error en detección de protecciones")
            diagnosis["protection"] = {}

        # ---- Paso 8: Evaluar viabilidad ----
        steps_executed.append("viability_evaluation")
        try:
            diagnosis["viability"] = ViabilityEvaluator().evaluate(
                http_result, html_analysis, dom_result, diagnosis["protection"]
            )
        except Exception as exc:
            diagnosis["errors"].append(f"Error en evaluación de viabilidad: {exc}")
            logger.exception("Error en evaluación de viabilidad")
            diagnosis["viability"] = {}

        self._finalize_metadata(
            diagnosis, html_analysis, dom_result, steps_executed, start_total
        )

        # Solo la interfaz recibe la muestra temporal; nunca se incluye en el
        # diagnóstico persistido. Ante barreras o robots indeterminado no se envía.
        protection = diagnosis.get("protection") or {}
        if (sample_callback is not None and content_format == "html" and full_body
                and protection.get("robots_crawl_allowed") is True
                and not protection.get("blocking_barrier")
                and not protection.get("auth_required")
                and not (diagnosis.get("viability") or {}).get("blocked_by_barrier")):
            try:
                sample_callback(full_body, diagnosis)
            except Exception:
                logger.warning("No se pudo preparar la vista previa de selección")

        logger.info(
            "Diagnóstico completado: %s — Viabilidad: %s",
            safe_url,
            diagnosis["viability"].get("technical_feasibility", "N/A"),
        )
        return sanitize_for_persistence(diagnosis)

    # ------------------------------------------------------------------
    # Consolidación y trazabilidad
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_access_status(http_result: dict[str, Any]) -> str:
        if http_result.get("ssrf_blocked"):
            return "bloqueado_ssrf"
        if http_result.get("access_ok"):
            return "accesible"
        if http_result.get("http_connected"):
            status = http_result.get("status_code")
            return f"restringido_http_{status}"
        return "inaccesible"

    def _consolidate_technologies(
        self,
        html: dict[str, Any],
        dom: dict[str, Any],
    ) -> dict[str, Any]:
        """Consolida tecnologías distinguiendo confirmado (DOM) vs probable (HTML)."""
        js_fw_html = html.get("js_frameworks", {}) or {}
        confirmed_dom = set(dom.get("js_frameworks_confirmed", []))
        probable_dom = set(dom.get("js_frameworks_probable", []))

        confirmed: list[str] = sorted(confirmed_dom)
        probable: list[str] = []

        for fw in js_fw_html:
            if fw not in confirmed_dom and fw not in probable:
                probable.append(fw)
        for fw in probable_dom:
            if fw not in confirmed_dom and fw not in probable:
                probable.append(fw)

        return {
            "js_frameworks_confirmed": confirmed,
            "js_frameworks_probable": probable,
            "cms_probable": html.get("cms_probable") or self._detect_cms(html),
            "cms": html.get("cms_probable") or self._detect_cms(html),
            "meta_generator": html.get("meta_generator"),
            "render_type": html.get("render_type", "no_identificado"),
            "has_jquery": "jQuery" in confirmed or "jQuery" in probable,
        }

    def _detect_cms(self, html: dict[str, Any]) -> str | None:
        """Detecta CMS probable a partir de firmas identificadas."""
        frameworks = html.get("js_frameworks", {}) or {}
        for fw in ("WordPress", "Drupal", "Joomla", "Shopify"):
            if fw in frameworks:
                return fw
        return None

    def _consolidate_pagination(
        self,
        html: dict[str, Any],
        dom: dict[str, Any],
    ) -> dict[str, Any]:
        """Consolida la información de paginación de HTML y DOM."""
        html_pagination = html.get("pagination", {}) or {}
        dom_pagination = dom.get("pagination_dom", {}) or {}
        load_more = html.get("load_more", {}) or {}
        infinite_html = html.get("infinite_scroll_hints", {}) or {}
        infinite_dom = bool(dom.get("infinite_scroll_confirmed", False))

        dom_selectors = dom_pagination.get("selectors", []) or []
        html_selectors = html_pagination.get("selectors", []) or []
        combined_selectors = list(dict.fromkeys(dom_selectors + html_selectors))

        return {
            "traditional_pagination": bool(html_pagination.get("detected", False)),
            "dynamic_pagination": bool(dom_pagination.get("detected", False)),
            "load_more_button": bool(load_more.get("detected", False)),
            "infinite_scroll": infinite_dom or bool(infinite_html.get("detected", False)),
            "infinite_scroll_confidence": (
                "confirmado"
                if infinite_dom
                else ("probable" if infinite_html.get("detected") else "no_identificado")
            ),
            "pagination_selector": combined_selectors[0] if combined_selectors else None,
            "all_pagination_selectors": combined_selectors,
            "has_rel_next": bool(html_pagination.get("has_rel_next", False)),
        }

    def _finalize_metadata(
        self,
        diagnosis: dict[str, Any],
        html: dict[str, Any],
        dom: dict[str, Any],
        steps_executed: list[str],
        start_total: float,
    ) -> None:
        """Completa adquisición, interacciones, selectores, confianza y trazabilidad."""
        http_res = diagnosis.get("http", {})
        inline_api = html.get("inline_api_calls", {}) or {}

        diagnosis["acquisition_mechanisms"] = {
            "content_format": http_res.get("content_format", "desconocido"),
            "format_details": http_res.get("format_details", {}),
            "render_type": html.get("render_type", "no_identificado"),
            "js_generated_content": bool(dom.get("js_generated_content", False)),
            "time_dependent_content": bool(
                dom.get("time_dependent_content")
                or (html.get("time_dependent_hints") or {}).get("uses_set_interval")
                or (html.get("time_dependent_hints") or {}).get("uses_eventsource_sse")
            ),
            "timed_text_growth_heuristic": bool(dom.get("time_dependent_content", False)),
            "time_dependent_hints": dict(html.get("time_dependent_hints") or {}),
            "inline_api_calls": inline_api,
            "embedded_data_formats": html.get("embedded_data_formats", {}),
        }

        static_inter = html.get("interaction_indicators", {}) or {}
        dom_inter = dom.get("interactive_elements_dom", {}) or {}
        diagnosis["interactions"] = {
            "requires_interaction_probable": bool(
                static_inter.get("requires_interaction_probable", False)
            ),
            "user_event_listener_hints_count": static_inter.get("user_event_listener_hints_count", 0),
            "tabs_count": dom_inter.get("tabs", static_inter.get("tabs_count", 0)),
            "accordions_count": dom_inter.get(
                "accordions", static_inter.get("accordions_count", 0)
            ),
            "dropdown_filters_count": dom_inter.get(
                "selects", static_inter.get("dropdown_filters_count", 0)
            ),
            "modals_count": static_inter.get("modals_count", 0),
            "buttons_count": dom_inter.get("buttons", 0),
        }

        cand = dict(html.get("candidate_selectors", {}) or {})
        cand["pagination"] = diagnosis.get("pagination", {}).get("all_pagination_selectors", [])
        diagnosis["candidate_selectors"] = cand

        diagnosis["confidence_summary"] = self._build_confidence_summary(
            diagnosis, dom
        )

        diagnosis["traceability"] = {
            **diagnosis.get("traceability", {}),
            "total_duration_s": round(time.perf_counter() - start_total, 3),
            "steps_executed": steps_executed,
            "selenium_status": diagnosis.get("selenium_status"),
            "source_url": sanitize_url(diagnosis.get("url", "")),
        }

    @staticmethod
    def _build_confidence_summary(
        diagnosis: dict[str, Any], dom: dict[str, Any]
    ) -> dict[str, list[str]]:
        """Clasifica los hallazgos en Confirmado, Probable y No identificado."""
        confirmed: list[str] = []
        probable: list[str] = []
        unidentified: list[str] = []

        http_res = diagnosis.get("http", {})
        tech = diagnosis.get("technologies", {})
        net = diagnosis.get("network", {})
        pag = diagnosis.get("pagination", {})
        prot = diagnosis.get("protection", {})

        if http_res.get("http_connected"):
            confirmed.append(f"HTTP {http_res.get('status_code')} ({http_res.get('content_format')})")
        else:
            unidentified.append("Respuesta HTTP exitosa (fallo de conexión o bloqueo)")

        for fw in tech.get("js_frameworks_confirmed", []):
            confirmed.append(f"Framework JS: {fw}")
        for fw in tech.get("js_frameworks_probable", []):
            probable.append(f"Framework/tecnología JS: {fw}")
        if not tech.get("js_frameworks_confirmed") and not tech.get("js_frameworks_probable"):
            unidentified.append("Frameworks JavaScript")

        if tech.get("cms_probable"):
            probable.append(f"CMS: {tech['cms_probable']}")
        else:
            unidentified.append("CMS")

        if dom.get("selenium_available"):
            if dom.get("js_generated_content"):
                confirmed.append("Contenido generado/modificado por JavaScript en DOM")
            if dom.get("infinite_scroll_confirmed"):
                confirmed.append("Scroll infinito")
            if net.get("xhr_fetch_detected"):
                confirmed.append("Solicitudes XHR/Fetch observadas")
            if net.get("rest_api_detected"):
                probable.append("API REST con intercambio de datos")
            if net.get("graphql_detected"):
                probable.append("Endpoint GraphQL")
        else:
            unidentified.append("Comportamiento DOM dinámico y red en ejecución (Selenium no ejecutado)")

        if pag.get("traditional_pagination"):
            confirmed.append("Elementos de paginación en HTML")
        elif pag.get("dynamic_pagination"):
            confirmed.append("Paginación detectada en DOM dinámico")
        else:
            unidentified.append("Paginación")

        if prot.get("captcha_detected"):
            if prot.get("captcha_confidence") == "confirmado":
                confirmed.append(f"CAPTCHA ({prot.get('captcha_type')})")
            else:
                probable.append(f"CAPTCHA ({prot.get('captcha_type')})")

        if prot.get("waf_detected"):
            confirmed.append(f"WAF/CDN ({prot.get('waf_type')})")

        return {
            "confirmed": confirmed,
            "probable": probable,
            "unidentified": unidentified,
        }
