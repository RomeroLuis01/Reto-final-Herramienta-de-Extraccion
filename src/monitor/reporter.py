"""
Módulo 7 — Generación de reportes de diagnóstico.

Serializa y persiste los reportes generados tras cada inspección
en formato JSON sanitizado y texto plano legible, cubriendo todos los
apartados técnicos y distinguiendo información confirmada, probable
y no identificada.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from src.monitor.logger import get_logger
from src.utils.security import sanitize_for_persistence

logger = get_logger("monitor.reporter")


class DiagnosticReporter:
    """
    Genera y persiste reportes sanitizados de diagnóstico web.

    Los reportes se almacenan en el directorio configurado como:
    - <dominio>_<timestamp>.json  — datos estructurados sanitizados
    - <dominio>_<timestamp>.txt   — reporte técnico legible
    """

    def __init__(self, reports_dir: Path) -> None:
        self.reports_dir = reports_dir
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def generate(self, diagnosis: dict[str, Any]) -> dict[str, Path]:
        """
        Genera y guarda un reporte sanitizado a partir del diagnóstico.

        Returns:
            Diccionario con rutas {'json': Path, 'txt': Path}.
        """
        clean_data = sanitize_for_persistence(diagnosis)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        domain = self._safe_domain(str(clean_data.get("url", "unknown")))
        base_name = f"{domain}_{timestamp}"

        json_path = self.reports_dir / f"{base_name}.json"
        txt_path = self.reports_dir / f"{base_name}.txt"

        # Evitar colisión si se generan varios reportes en el mismo segundo
        suffix = 1
        while json_path.exists() or txt_path.exists():
            json_path = self.reports_dir / f"{base_name}_{suffix}.json"
            txt_path = self.reports_dir / f"{base_name}_{suffix}.txt"
            suffix += 1

        self._save_json(clean_data, json_path)
        self._save_text(clean_data, txt_path)

        logger.info("Reporte guardado: %s", json_path.name)
        return {"json": json_path, "txt": txt_path}

    # ------------------------------------------------------------------
    # Serialización
    # ------------------------------------------------------------------

    def _save_json(self, data: dict[str, Any], path: Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    def _save_text(self, data: dict[str, Any], path: Path) -> None:
        lines = self._build_text_report(data)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    # ------------------------------------------------------------------
    # Formato texto legible (17 secciones requeridas + niveles de certeza)
    # ------------------------------------------------------------------

    def _build_text_report(self, d: dict[str, Any]) -> list[str]:  # noqa: PLR0912
        sep = "=" * 74
        thin = "-" * 74
        lines: list[str] = []

        def h(title: str) -> None:
            lines.extend(["", sep, f"  {title}", sep])

        def sub(title: str) -> None:
            lines.extend(["", f"  -- {title} --", thin])

        def kv(key: str, value: Any, indent: int = 4) -> None:
            prefix = " " * indent
            if isinstance(value, list):
                if not value:
                    lines.append(f"{prefix}{key}: Ninguno / No identificado")
                    return
                lines.append(f"{prefix}{key}:")
                for item in value:
                    lines.append(f"{prefix}  · {item}")
            elif isinstance(value, dict):
                if not value:
                    lines.append(f"{prefix}{key}: No disponible")
                    return
                lines.append(f"{prefix}{key}:")
                for k, v in value.items():
                    lines.append(f"{prefix}  {k}: {v}")
            else:
                val_str = "No identificado" if value is None or value == "" else str(value)
                lines.append(f"{prefix}{key}: {val_str}")

        site_id = d.get("site_identification", {}) or {}
        http = d.get("http", {}) or {}
        html = d.get("html_analysis", {}) or {}
        tech = d.get("technologies", {}) or {}
        acq = d.get("acquisition_mechanisms", {}) or {}
        network = d.get("network", {}) or {}
        pagination = d.get("pagination", {}) or {}
        interactions = d.get("interactions", {}) or {}
        prot = d.get("protection", {}) or {}
        via = d.get("viability", {}) or {}
        conf = d.get("confidence_summary", {}) or {}
        trace = d.get("traceability", {}) or {}

        # 1-4. Encabezado, Identificación, URL, Fecha/Hora, Estado de acceso
        h("REPORTE DE DIAGNÓSTICO WEB")
        kv("1. Identificación del sitio", site_id.get("label") or site_id.get("title") or site_id.get("domain") or "No identificado")
        kv("   Dominio", site_id.get("domain") or d.get("domain"))
        kv("   Título HTML", site_id.get("title") or html.get("title"))
        kv("2. URL analizada", d.get("url"))
        kv("   URL final", d.get("final_url") or http.get("final_url"))
        kv("3. Fecha y hora del diagnóstico", d.get("diagnosis_timestamp"))
        kv("4. Estado de acceso", d.get("access_status"))

        sub("DETALLE DE ACCESO HTTP")
        kv("Conectividad HTTP", http.get("http_connected"))
        kv("Acceso válido (2xx/3xx)", http.get("access_ok"))
        kv("Código de estado", http.get("status_code"))
        kv("Tiempo de respuesta (s)", http.get("response_time_s"))
        kv("Tipo de contenido", http.get("content_type"))
        kv("Formato detectado", http.get("content_format"))
        kv("Servidor", http.get("server"))
        kv("Redirecciones", http.get("redirects", []))
        kv("HTTPS", http.get("is_https"))
        kv("Cookies detectadas (cantidad)", http.get("cookies_count"))
        kv("Cookies (solo nombres)", http.get("cookies_names", []))

        # 5. Estructura HTML y DOM
        sub("5. ESTRUCTURA HTML Y DOM IDENTIFICADA")
        kv("Renderizado (clasificación)", html.get("render_type"))
        kv("Tamaño HTML estático (bytes)", html.get("static_html_size"))
        kv("Texto visible estático (chars)", html.get("static_text_size"))
        kv("Tamaño DOM dinámico (bytes)", html.get("dynamic_dom_size"))
        kv("Texto visible dinámico (chars)", html.get("dynamic_text_size"))
        kv("Diferencia DOM", html.get("dom_difference"))
        kv("Diferencia texto visible", html.get("text_difference"))
        kv("Contenido generado por JS", html.get("js_generated_content"))
        kv("Dependiente del tiempo", html.get("time_dependent_content"))
        kv("Tiempo carga Selenium (s)", html.get("page_load_time_s"))
        kv("Metaetiquetas", html.get("meta_tags_count"))
        kv("Scripts detectados", html.get("scripts_count"))
        kv("Formularios", html.get("forms_count"))
        kv("Tablas", html.get("tables_count"))

        # 6. Tecnologías detectadas
        sub("6. TECNOLOGÍAS DETECTADAS (CONFIRMADO vs PROBABLE)")
        kv("Frameworks JS [CONFIRMADO]", tech.get("js_frameworks_confirmed", []))
        kv("Frameworks JS [PROBABLE]", tech.get("js_frameworks_probable", []))
        kv("CMS [PROBABLE]", tech.get("cms_probable") or tech.get("cms"))
        kv("Meta-generador", tech.get("meta_generator"))

        # 7. Mecanismos de adquisición
        sub("7. MECANISMOS DE ADQUISICIÓN DE INFORMACIÓN")
        kv("Formato de datos principal", acq.get("content_format"))
        kv("Detalles de formato", acq.get("format_details"))
        kv("Llamadas API en scripts inline", acq.get("inline_api_calls"))
        kv("Formatos embebidos/enlazados", acq.get("embedded_data_formats"))

        # 8. APIs y solicitudes observadas
        sub("8. APIs Y SOLICITUDES DE RED OBSERVADAS")
        kv("API REST probable", network.get("rest_api_detected"))
        kv("API GraphQL probable", network.get("graphql_detected"))
        kv("Solicitudes XHR/Fetch", network.get("xhr_fetch_detected"))
        kv("WebSockets", network.get("websocket_detected"))
        kv("Métodos HTTP observados", network.get("http_methods_observed", []))
        kv("Content-Types observados", network.get("content_types_observed", []))
        kv("Endpoints relevantes", network.get("endpoints_observed", []))

        # 9-10. Métodos de carga y paginación
        sub("9-10. MÉTODOS DE CARGA Y PAGINACIÓN")
        kv("Scroll infinito", pagination.get("infinite_scroll"))
        kv("Confianza scroll infinito", pagination.get("infinite_scroll_confidence"))
        kv("Paginación tradicional", pagination.get("traditional_pagination"))
        kv("Paginación dinámica", pagination.get("dynamic_pagination"))
        kv("Botón 'cargar más'", pagination.get("load_more_button"))
        kv("Selector de paginación", pagination.get("pagination_selector"))

        # 11. Interacciones requeridas
        sub("11. INTERACCIONES REQUERIDAS")
        kv("Interacción probable requerida", interactions.get("requires_interaction_probable"))
        kv("Pestañas (tabs)", interactions.get("tabs_count"))
        kv("Acordeones / desplegables", interactions.get("accordions_count"))
        kv("Filtros select", interactions.get("dropdown_filters_count"))
        kv("Modales", interactions.get("modals_count"))
        kv("Botones en DOM", interactions.get("buttons_count"))

        # 12-13. Protecciones y restricciones
        sub("12. MECANISMOS DE PROTECCIÓN")
        kv("CAPTCHA detectado", prot.get("captcha_detected"))
        kv("Tipo de CAPTCHA", prot.get("captcha_type"))
        kv("Confianza CAPTCHA", prot.get("captcha_confidence"))
        kv("Autenticación requerida", prot.get("auth_required"))
        kv("Tipo de autenticación", prot.get("auth_type"))
        kv("Gestión de sesiones", prot.get("session_management_detected"))
        kv("Cookies de sesión (nombres)", prot.get("session_cookies", []))
        kv("Rate limiting", prot.get("rate_limiting"))
        kv("WAF / CDN", prot.get("waf_type") if prot.get("waf_detected") else False)
        kv("robots.txt estado", prot.get("robots_txt_status"))
        kv("robots.txt permite rastreo", prot.get("robots_crawl_allowed"))

        sub("13. RESTRICCIONES IDENTIFICADAS")
        kv("Barrera bloqueante activa", prot.get("blocking_barrier"))
        kv("Restricciones temporales", prot.get("temporal_restrictions", []))
        kv("Lista de restricciones", prot.get("restrictions", []))

        # 14-16. Viabilidad, estrategias, limitaciones
        sub("14-16. VIABILIDAD TÉCNICA, ESTRATEGIAS Y LIMITACIONES")
        kv("14. Viabilidad técnica", via.get("technical_feasibility"))
        kv("    Puntuación (0-100)", via.get("feasibility_score"))
        kv("    Desglose de puntuación", via.get("score_breakdown", []))
        kv("15. Estrategias adecuadas", via.get("recommended_strategies", []))
        kv("16. Limitaciones detectadas", via.get("limitations", []))
        kv("    Aviso legal", via.get("legal_authorization_note"))

        # Nivel de certeza consolidado
        sub("CLASIFICACIÓN DE CERTEZA DEL DIAGNÓSTICO")
        kv("Información CONFIRMADA", conf.get("confirmed", []))
        kv("Información PROBABLE", conf.get("probable", []))
        kv("Información NO IDENTIFICADA", conf.get("unidentified", []))

        # 17. Errores e incidencias + Trazabilidad
        sub("17. ERRORES, INCIDENCIAS Y TRAZABILIDAD")
        incidents = prot.get("incidents", [])
        if incidents:
            lines.append("    Incidencias de protección:")
            for inc in incidents:
                lines.append(
                    f"      - [{inc.get('severity', 'N/A')}] {inc.get('type')}: "
                    f"{inc.get('description')} (Evidencia: {inc.get('evidence')}) "
                    f"-> Acción: {inc.get('action')}"
                )
        else:
            lines.append("    Incidencias de protección: Ninguna")

        errors = d.get("errors", [])
        if errors:
            lines.append("    Errores registrados:")
            for err in errors:
                lines.append(f"      ⚠ {err}")
        else:
            lines.append("    Errores registrados: Sin errores.")

        kv("Trazabilidad", trace)
        lines.extend(["", sep, "  FIN DEL REPORTE", sep, ""])
        return lines

    @staticmethod
    def _safe_domain(url: str) -> str:
        """Convierte una URL en un nombre seguro para archivo (sin credenciales)."""
        try:
            netloc = urlparse(url).netloc or url
            host_port = netloc.rsplit("@", 1)[-1]
            safe = host_port.replace(".", "_").replace(":", "_").replace("/", "_")
            return safe[:50] or "unknown"
        except Exception:
            return "unknown"
