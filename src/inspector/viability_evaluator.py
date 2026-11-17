"""
Módulo 2 — Evaluador de viabilidad técnica de extracción.

Modelo de puntuación transparente (0–100):
- Base inicial: 100 puntos.
- Deducciones por barreras y restricciones observadas:
  * SSRF bloqueado / Sitio inaccesible por HTTP: -60
  * Acceso bloqueado (HTTP 403 / 503):           -70
  * CAPTCHA detectado (HTML o DOM):              -65
  * Autenticación requerida (401 / login):       -40
  * Crawling restringido por robots.txt:         -35
  * Rate limiting activo (429 / headers):        -20
  * Scroll infinito (mayor complejidad):         -10 (nota técnica)

Clasificación resultante:
- >= 70 : viable (solo si no existe barrera crítica bloqueante)
- 40–69 : parcialmente_viable
- 10–39 : limitada
- <  10 : no_viable

IMPORTANTE: La viabilidad técnica mide únicamente complejidad y acceso
observable. NO representa autorización legal ni cumplimiento de Términos
de Servicio. Cuando exista una barrera crítica (bloqueo, CAPTCHA, SSRF,
HTTP 401/403/429/503 o robots.txt restrictivo), NO se recomienda
extracción automatizada.
"""

from __future__ import annotations

from typing import Any

from src.monitor.logger import get_logger

logger = get_logger("inspector.viability")

_LEGAL_DISCLAIMER = (
    "AVISO LEGAL: La viabilidad técnica evalúa únicamente factibilidad técnica "
    "observable y NO constituye autorización legal. Antes de extraer datos "
    "debe verificarse el cumplimiento de Términos de Servicio, robots.txt, "
    "privacidad y permisos aplicables."
)


class ViabilityEvaluator:
    """
    Evalúa la viabilidad técnica de extracción de un sitio
    diferenciándola explícitamente de la autorización legal.
    """

    def evaluate(
        self,
        http_result: dict[str, Any],
        html_analysis: dict[str, Any],
        dom_result: dict[str, Any],
        protection: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Evalúa viabilidad técnica y determina si existen estrategias aplicables.
        """
        result: dict[str, Any] = {
            "technical_feasibility": "no_evaluado",
            "feasibility_score": 0,
            "score_breakdown": [],
            "blocked_by_barrier": False,
            "recommended_strategies": [],
            "limitations": [],
            "notes": [_LEGAL_DISCLAIMER],
            "legal_authorization_note": _LEGAL_DISCLAIMER,
        }

        score = 100
        breakdown: list[str] = ["Base inicial: 100"]
        strategies: list[str] = []
        limitations: list[str] = []
        notes: list[str] = [_LEGAL_DISCLAIMER]

        http_accessible = bool(http_result.get("access_ok", False))
        ssrf_blocked = bool(protection.get("ssrf_blocked") or http_result.get("ssrf_blocked"))
        captcha = bool(protection.get("captcha_detected"))
        access_blocked = bool(protection.get("access_blocked"))
        auth_required = bool(protection.get("auth_required"))
        rate_limiting = bool(protection.get("rate_limiting"))
        robots_disallowed = protection.get("robots_crawl_allowed") is False

        if ssrf_blocked:
            score -= 100
            breakdown.append("Política anti-SSRF activada: -100")
            limitations.append("Solicitud bloqueada por política de seguridad anti-SSRF.")

        if not http_accessible and not ssrf_blocked:
            score -= 60
            breakdown.append("Sitio inaccesible o respuesta HTTP de error: -60")
            limitations.append("Sitio no accesible correctamente por HTTP.")

        if access_blocked and not ssrf_blocked:
            score -= 70
            breakdown.append("Acceso bloqueado (403/503): -70")
            limitations.append(
                f"Acceso bloqueado: {protection.get('block_reason', 'Sin detalles')}"
            )

        if captcha:
            score -= 65
            breakdown.append("CAPTCHA detectado: -65")
            limitations.append(
                "CAPTCHA detectado — extracción automatizada improcedente (no se evade)."
            )

        if auth_required:
            score -= 40
            breakdown.append("Autenticación requerida: -40")
            limitations.append(
                "Autenticación requerida — solo viable mediante autorización legítima."
            )

        if rate_limiting:
            score -= 20
            breakdown.append("Rate limiting detectado: -20")
            limitations.append(
                "Rate limiting activo — restringe la frecuencia de solicitudes."
            )

        if robots_disallowed:
            score -= 35
            breakdown.append("Restricción en robots.txt: -35")
            limitations.append("Crawling restringido por robots.txt.")

        has_hard_barrier = any([
            ssrf_blocked,
            not http_accessible,
            access_blocked,
            captcha,
            robots_disallowed,
            bool(protection.get("blocking_barrier")),
        ])
        result["blocked_by_barrier"] = has_hard_barrier

        # Solo recomendar estrategias si no hay barrera bloqueante ni autenticación pendiente
        if not has_hard_barrier and not auth_required:
            content_format = str(http_result.get("content_format", "html"))
            render_type = html_analysis.get("render_type", "static")

            if content_format in ("json", "xml", "rss_atom", "csv"):
                strategies.append(
                    f"Consumo directo de formato estructurado ({content_format.upper()})"
                )
                notes.append(
                    f"El recurso entrega directamente datos en formato {content_format.upper()}."
                )
            elif render_type == "static":
                strategies.append("requests + BeautifulSoup (HTML estático)")
                notes.append(
                    "Contenido principalmente estático en la respuesta HTTP inicial."
                )
            elif render_type in ("dynamic_probable", "spa_probable"):
                strategies.append("Selenium o Playwright (contenido dinámico/SPA)")
                notes.append(
                    "Se requiere ejecución de JavaScript para renderizar el contenido."
                )
            elif render_type == "mixed_probable":
                strategies.append("requests + BeautifulSoup (contenido estático)")
                strategies.append("Selenium (secciones dinámicas identificadas)")

            if dom_result.get("rest_api_detected"):
                strategies.append("API REST directa (revisar endpoints observados)")
                notes.append(
                    "Se observaron endpoints probables de API de datos; verificar autorización."
                )

            if dom_result.get("graphql_detected"):
                strategies.append("API GraphQL (requiere análisis de esquema autorizado)")

            if html_analysis.get("pagination", {}).get("detected"):
                notes.append("Paginación detectada — requiere manejo de navegación por páginas.")

            if dom_result.get("infinite_scroll_confirmed"):
                score -= 10
                breakdown.append("Scroll infinito confirmado: -10")
                limitations.append(
                    "Scroll infinito — requiere simulación de scroll controlado."
                )
                strategies.append("Selenium con simulación de scroll infinito")
        else:
            if dom_result.get("infinite_scroll_confirmed"):
                limitations.append(
                    "Scroll infinito detectado (operación detenida por barreras)."
                )
            notes.append(
                "No se recomiendan estrategias de extracción automatizada debido a "
                "barreras técnicas, de acceso o restricciones identificadas."
            )

        score = max(0, min(100, score))
        result["feasibility_score"] = score
        result["score_breakdown"] = breakdown

        if ssrf_blocked or access_blocked:
            result["technical_feasibility"] = "no_viable"
        elif score >= 70 and not has_hard_barrier:
            result["technical_feasibility"] = "viable"
        elif score >= 40:
            result["technical_feasibility"] = "parcialmente_viable"
        elif score >= 10:
            result["technical_feasibility"] = "limitada"
        else:
            result["technical_feasibility"] = "no_viable"

        result["recommended_strategies"] = list(dict.fromkeys(strategies))
        result["limitations"] = limitations
        result["notes"] = notes

        logger.info(
            "Viabilidad técnica: %s (score=%d, barrera=%s)",
            result["technical_feasibility"],
            score,
            has_hard_barrier,
        )
        return result
