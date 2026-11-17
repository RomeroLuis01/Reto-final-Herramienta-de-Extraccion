"""
Módulo 2 — Detector de protecciones y bloqueos.

Analiza la información recopilada por los inspectores HTTP, HTML y DOM
para identificar y clasificar mecanismos de protección, sesiones y restricciones.

REGLA FUNDAMENTAL: Este módulo identifica y documenta las protecciones.
NO implementa ninguna técnica para evadir CAPTCHAs, autenticaciones
ni controles de acceso.
"""

from __future__ import annotations

from typing import Any

from src.monitor.logger import get_logger

logger = get_logger("inspector.protection")


class ProtectionDetector:
    """
    Clasifica los mecanismos de protección identificados durante
    la inspección HTTP, HTML estática y DOM dinámica.
    """

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def analyze(
        self,
        http_result: dict[str, Any],
        html_analysis: dict[str, Any],
        dom_result: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Consolida y clasifica todos los mecanismos de protección.

        Returns:
            Diccionario con clasificación de protecciones e incidencias.
        """
        result: dict[str, Any] = {
            # CAPTCHA
            "captcha_detected": False,
            "captcha_type": None,
            "captcha_confidence": None,
            "captcha_evidence": [],
            # Autenticación y sesiones
            "auth_required": False,
            "auth_type": None,
            "auth_evidence": [],
            "session_management_detected": False,
            "session_cookies": [],
            "csrf_detected": False,
            # Rate limiting y restricciones temporales
            "rate_limiting": False,
            "rate_limit_evidence": [],
            "temporal_restrictions": [],
            # Bloqueo de acceso / SSRF
            "access_blocked": False,
            "block_reason": None,
            "ssrf_blocked": bool(http_result.get("ssrf_blocked") or dom_result.get("ssrf_blocked")),
            # WAF / CDN
            "waf_detected": False,
            "waf_type": None,
            "waf_evidence": [],
            # Robots.txt
            "robots_txt_status": "no_verificado",
            "robots_crawl_allowed": None,
            # Indicador de barrera que impide continuar
            "blocking_barrier": False,
            # Resumen de restricciones e incidencias
            "restrictions": [],
            "incidents": [],
        }

        self._check_ssrf(http_result, dom_result, result)
        self._check_http_status(http_result, result)
        self._check_rate_limiting(http_result, html_analysis, result)
        self._check_waf(http_result, result)
        self._check_captcha(html_analysis, dom_result, result)
        self._check_auth_and_sessions(http_result, html_analysis, dom_result, result)
        self._check_robots(http_result, result)
        self._determine_blocking_barrier(http_result, result)
        self._generate_incidents(result)

        logger.info(
            "Protecciones: CAPTCHA=%s, auth=%s, WAF=%s, rate_limit=%s, bloqueado=%s",
            result["captcha_detected"],
            result["auth_required"],
            result["waf_detected"],
            result["rate_limiting"],
            result["access_blocked"],
        )
        return result

    # ------------------------------------------------------------------
    # Verificaciones individuales
    # ------------------------------------------------------------------

    def _check_ssrf(
        self, http: dict[str, Any], dom: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Registra bloqueos de seguridad anti-SSRF."""
        if result["ssrf_blocked"]:
            reason = http.get("error") or dom.get("error") or "Destino interno o no autorizado"
            result["access_blocked"] = True
            result["block_reason"] = reason
            result["restrictions"].append("Destino bloqueado por política anti-SSRF")

    def _check_http_status(
        self, http: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Detecta bloqueos por código de estado HTTP."""
        status = http.get("status_code")

        if status == 403:
            result["access_blocked"] = True
            result["block_reason"] = "HTTP 403 Forbidden — acceso denegado."
            result["restrictions"].append("Acceso bloqueado (HTTP 403)")
            logger.warning("Acceso bloqueado: HTTP 403")

        elif status == 401:
            result["auth_required"] = True
            result["auth_type"] = "HTTP 401 Unauthorized"
            result["auth_evidence"].append("status_code=401")
            result["restrictions"].append("Autenticación requerida (HTTP 401)")
            logger.warning("Autenticación requerida: HTTP 401")

        elif status == 429:
            result["rate_limiting"] = True
            result["rate_limit_evidence"].append("status_code=429")
            result["temporal_restrictions"].append("HTTP 429 Too Many Requests")
            result["restrictions"].append("Rate limiting activo (HTTP 429)")
            logger.warning("Rate limiting detectado: HTTP 429")

        elif status == 503:
            result["access_blocked"] = True
            result["block_reason"] = "HTTP 503 — servicio temporalmente no disponible."
            result["temporal_restrictions"].append("HTTP 503 Service Unavailable")
            result["restrictions"].append("Servicio temporalmente no disponible (HTTP 503)")

    def _check_rate_limiting(
        self,
        http: dict[str, Any],
        html: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Detecta cabeceras de rate limiting y restricciones temporales."""
        headers = http.get("security_headers", {}) or {}
        headers_lower = {str(k).lower(): v for k, v in headers.items()}

        rl_headers = (
            "x-ratelimit-limit",
            "x-ratelimit-remaining",
            "x-ratelimit-reset",
            "retry-after",
            "x-rate-limit",
        )
        for h in rl_headers:
            if headers_lower.get(h):
                result["rate_limiting"] = True
                ev = f"header:{h}={headers_lower[h]}"
                if ev not in result["rate_limit_evidence"]:
                    result["rate_limit_evidence"].append(ev)
                if h in ("retry-after", "x-ratelimit-reset"):
                    result["temporal_restrictions"].append(ev)
                result["restrictions"].append(f"Rate limit header detectado: {h}")

        if (html.get("time_dependent_hints") or {}).get("has_meta_refresh"):
            result["temporal_restrictions"].append("meta http-equiv=refresh")

    def _check_waf(
        self, http: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Detecta Web Application Firewalls y CDN de seguridad mediante evidencia."""
        headers = http.get("security_headers", {}) or {}
        headers_lower = {str(k).lower(): str(v or "") for k, v in headers.items()}
        server = (http.get("server") or "").lower()

        waf_checks: list[tuple[str, bool, str]] = [
            (
                "Cloudflare",
                bool(
                    headers_lower.get("cf-ray")
                    or headers_lower.get("cf-mitigated")
                    or "cloudflare" in server
                ),
                "Cf-Ray / Cf-Mitigated / Server: cloudflare",
            ),
            (
                "Imperva",
                bool(
                    headers_lower.get("x-iinfo")
                    or "imperva" in server
                    or "incapsula" in server
                    or "incapsula" in headers_lower.get("x-cdn", "").lower()
                ),
                "X-Iinfo / X-CDN / Server Imperva",
            ),
            (
                "Akamai",
                bool(
                    headers_lower.get("x-akamai-transformed")
                    or "akamai" in server
                    or "akamaighost" in server
                ),
                "X-Akamai-Transformed / Server Akamai",
            ),
            (
                "Sucuri",
                bool(headers_lower.get("x-sucuri-id") or "sucuri" in server),
                "X-Sucuri-ID / Server Sucuri",
            ),
            (
                "ModSecurity",
                "mod_security" in server or "modsecurity" in server,
                "Server ModSecurity",
            ),
            (
                "AWS WAF / CloudFront",
                bool(
                    headers_lower.get("x-amz-cf-id")
                    or "awselb" in server
                    or "cloudfront" in server
                ),
                "X-Amz-Cf-Id / CloudFront",
            ),
        ]

        detected_wafs: list[str] = []
        for name, matched, evidence in waf_checks:
            if matched:
                detected_wafs.append(name)
                result["waf_evidence"].append(evidence)
                result["restrictions"].append(f"WAF/CDN detectado: {name}")

        if detected_wafs:
            result["waf_detected"] = True
            result["waf_type"] = ", ".join(detected_wafs)
            logger.info("WAF/CDN detectado: %s", result["waf_type"])

    def _check_captcha(
        self,
        html: dict[str, Any],
        dom: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """
        Detecta CAPTCHAs mediante evidencia del HTML estático y el DOM.
        NOTA: Solo se identifica. No se intenta resolver ni evadir.
        """
        captcha_html = html.get("captcha_indicators", [])
        captcha_dom = dom.get("captcha_detected", False)
        captcha_type_dom = dom.get("captcha_type")

        if captcha_html:
            result["captcha_detected"] = True
            result["captcha_confidence"] = "probable"
            known_types = [c["indicator"] for c in captcha_html]
            result["captcha_evidence"].extend(known_types[:5])
            result["captcha_type"] = self._classify_captcha_label(known_types)
            result["restrictions"].append(
                f"CAPTCHA probable en HTML ({', '.join(known_types[:3])})"
            )
            logger.warning("CAPTCHA probable detectado en HTML.")

        if captcha_dom:
            result["captcha_detected"] = True
            result["captcha_confidence"] = "confirmado"
            result["captcha_type"] = captcha_type_dom or result["captcha_type"]
            result["captcha_evidence"].append(f"dom:{result['captcha_type']}")
            result["restrictions"].append(
                f"CAPTCHA confirmado en DOM: {result['captcha_type']}"
            )
            logger.warning("CAPTCHA confirmado en DOM: %s", result["captcha_type"])

    @staticmethod
    def _classify_captcha_label(indicators: list[str]) -> str:
        joined = " ".join(indicators).lower()
        if "recaptcha" in joined:
            return "reCAPTCHA (probable en HTML)"
        if "hcaptcha" in joined or "h-captcha" in joined:
            return "hCaptcha (probable en HTML)"
        if "turnstile" in joined:
            return "Cloudflare Turnstile (probable en HTML)"
        return f"Indicadores en HTML: {', '.join(indicators[:3])}"

    def _check_auth_and_sessions(
        self,
        http: dict[str, Any],
        html: dict[str, Any],
        dom: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Detecta requerimientos de autenticación y gestión de sesiones."""
        www_auth = (http.get("security_headers") or {}).get("WWW-Authenticate")
        if www_auth:
            result["auth_required"] = True
            result["auth_type"] = f"WWW-Authenticate: {www_auth}"
            result["auth_evidence"].append("header:WWW-Authenticate")

        auth_html = html.get("auth_indicators", {}) or {}
        if auth_html.get("has_login_form"):
            result["auth_required"] = True
            result["auth_type"] = result["auth_type"] or "Formulario de login"
            result["auth_evidence"].append("html:login_form")
            result["restrictions"].append("Formulario de autenticación detectado")

        if dom.get("auth_wall_detected"):
            result["auth_required"] = True
            result["auth_type"] = result["auth_type"] or "Muro de autenticación en DOM"
            result["auth_evidence"].append("dom:auth_wall")
            result["restrictions"].append("Muro de autenticación detectado en DOM")

        # Gestión de sesiones y CSRF
        session_cookies = http.get("session_cookies_detected", []) or []
        if session_cookies or http.get("cookies_count", 0) > 0:
            result["session_management_detected"] = bool(session_cookies)
            result["session_cookies"] = list(session_cookies)

        forms = html.get("forms", []) or []
        if any(f.get("has_csrf_field") for f in forms):
            result["csrf_detected"] = True

    def _check_robots(
        self, http: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Registra el estado de robots.txt."""
        robots = http.get("robots_txt", {}) or {}

        if not robots.get("accessible"):
            result["robots_txt_status"] = "no_accesible"
            result["robots_crawl_allowed"] = None
        else:
            allowed = robots.get("crawl_allowed")
            result["robots_txt_status"] = "accesible"
            result["robots_crawl_allowed"] = allowed
            if allowed is False:
                result["restrictions"].append("Crawling restringido por robots.txt")
                logger.warning("Crawling restringido por robots.txt.")

    @staticmethod
    def _determine_blocking_barrier(
        http: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Determina si existe una barrera que impide continuar operaciones automatizadas."""
        status = http.get("status_code")
        result["blocking_barrier"] = any([
            result["ssrf_blocked"],
            result["access_blocked"],
            result["captcha_detected"],
            status in (401, 403, 429, 503),
            result["robots_crawl_allowed"] is False,
        ])

    # ------------------------------------------------------------------
    # Generación de incidencias estructuradas
    # ------------------------------------------------------------------

    def _generate_incidents(self, result: dict[str, Any]) -> None:
        """Genera incidencias con tipo, evidencia, severidad, descripción y acción."""
        incidents: list[dict[str, Any]] = []

        if result["ssrf_blocked"]:
            incidents.append({
                "type": "SSRF_BLOQUEADO",
                "evidence": result["block_reason"] or "URL o DNS interno rechazado",
                "severity": "ALTA",
                "description": "La URL o una redirección apunta a un esquema/host/IP no permitido.",
                "action": "Detener operación inmediatamente. Validar que el objetivo sea público.",
            })

        if result["captcha_detected"]:
            incidents.append({
                "type": "CAPTCHA",
                "evidence": ", ".join(result["captcha_evidence"]) or result["captcha_type"],
                "severity": "ALTA",
                "description": (
                    f"CAPTCHA detectado ({result['captcha_type']}, "
                    f"confianza: {result['captcha_confidence']}). "
                    "No se implementan ni ejecutan evasiones."
                ),
                "action": "Detener operación automatizada. Informar al usuario.",
            })

        if result["access_blocked"] and not result["ssrf_blocked"]:
            incidents.append({
                "type": "ACCESO_BLOQUEADO",
                "evidence": result["block_reason"] or "HTTP 403/503",
                "severity": "ALTA",
                "description": result["block_reason"] or "Acceso denegado por el servidor.",
                "action": "Detener operación. Verificar permisos y autorización del sitio.",
            })

        if result["auth_required"]:
            incidents.append({
                "type": "AUTENTICACION_REQUERIDA",
                "evidence": ", ".join(result["auth_evidence"]) or result["auth_type"],
                "severity": "MEDIA",
                "description": f"Autenticación requerida: {result['auth_type']}.",
                "action": "Utilizar únicamente credenciales o APIs legítimas autorizadas.",
            })

        if result["rate_limiting"]:
            incidents.append({
                "type": "RATE_LIMITING",
                "evidence": ", ".join(result["rate_limit_evidence"]) or "HTTP 429 / headers",
                "severity": "MEDIA",
                "description": "El servidor aplica limitación de tasa o restricción temporal.",
                "action": "Detener solicitudes repetidas y respetar Retry-After / crawl-delay.",
            })

        if result["waf_detected"]:
            incidents.append({
                "type": "WAF_CDN",
                "evidence": ", ".join(result["waf_evidence"]) or result["waf_type"],
                "severity": "BAJA",
                "description": f"Presencia de WAF/CDN ({result['waf_type']}).",
                "action": "Respetar límites de acceso y términos de servicio del sitio.",
            })

        if result["robots_crawl_allowed"] is False:
            incidents.append({
                "type": "ROBOTS_TXT_RESTRICCION",
                "evidence": "robots.txt Disallow para URL objetivo",
                "severity": "ALTA",
                "description": "El archivo robots.txt restringe el rastreo automatizado.",
                "action": "Detener rastreo automatizado conforme a robots.txt.",
            })

        result["incidents"] = incidents
