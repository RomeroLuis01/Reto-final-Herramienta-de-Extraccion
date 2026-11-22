"""Verificación manual explícita en una única sesión visible de Chrome."""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Callable
from urllib.parse import urldefrag, urlsplit

from src.config.settings import AnalysisTarget, ProjectConfig
from src.extractor.base import ExtractionBatch, PageSnapshot
from selenium.webdriver.common.by import By

from src.inspector.dom_inspector import DomInspector, _CAPTCHA_SELECTORS
from src.inspector.html_analyzer import HtmlAnalyzer
from src.utils.auth import origin
from src.utils.security import sanitize_url, validate_url_security


def manual_eligible(target: AnalysisTarget, diagnosis: dict[str, Any]) -> bool:
    """Solo desafíos identificados o login, con robots expresamente permitido."""
    http = diagnosis.get("http") or {}
    protection = diagnosis.get("protection") or {}
    status = http.get("status_code")
    challenge = str((http.get("security_headers") or {}).get("Cf-Mitigated", "")).lower() == "challenge"
    if protection.get("robots_crawl_allowed") is not True or not target.respect_robots_txt:
        return False
    if (protection.get("ssrf_blocked") or http.get("ssrf_blocked")
            or protection.get("rate_limiting") or status not in (200, 401, 403)):
        return False
    if status == 403 and not challenge:
        return False
    return bool(challenge or protection.get("captcha_detected") or protection.get("auth_required"))


def _verified_status(driver: Any, url: str) -> int | None:
    """Busca la última respuesta Document del marco principal para la URL actual."""
    frame = driver.execute_cdp_cmd("Page.getFrameTree", {})["frameTree"]["frame"]["id"]
    final = urldefrag(url)[0]
    found = None
    for entry in driver.get_log("performance"):
        try:
            message = json.loads(entry["message"])["message"]
            params = message.get("params", {})
            if (message.get("method") != "Network.responseReceived"
                    or params.get("type") != "Document" or params.get("frameId") != frame):
                continue
            response = params.get("response") or {}
            if urldefrag(str(response.get("url", "")))[0] != final:
                continue
            headers = {str(k).lower(): str(v).lower() for k, v in (response.get("headers") or {}).items()}
            found = None if headers.get("cf-mitigated") == "challenge" else int(response["status"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return found


def acquire_manual(target: AnalysisTarget, config: ProjectConfig,
                   wait_for_user: Callable[[], bool]) -> ExtractionBatch:
    """El usuario interactúa; solo se captura la página comprobada en esa sesión."""
    batch = ExtractionBatch(method="selenium_manual")
    safe, _ = validate_url_security(target.url, resolve_dns=not target.allow_private_network,
                                    allow_private_network=target.allow_private_network)
    if not safe:
        batch.errors.append("URL bloqueada por anti-SSRF.")
        return batch
    try:
        with DomInspector(replace(config.selenium, headless=False),
                          allow_private_network=target.allow_private_network) as dom:
            driver = dom._driver
            driver.get(target.url)
            # La evidencia debe corresponder a una navegación durante la interacción,
            # no a una respuesta previa al control manual.
            driver.get_log("performance")
            if not wait_for_user():
                batch.errors.append("Verificación manual cancelada o tiempo agotado.")
                return batch
            current = driver.current_url
            safe, _ = validate_url_security(current, resolve_dns=not target.allow_private_network,
                                            allow_private_network=target.allow_private_network)
            if (not safe or origin(current) != origin(target.url)
                    or urlsplit(current).path.rstrip("/") != urlsplit(target.url).path.rstrip("/")
                    or urlsplit(current).query != urlsplit(target.url).query):
                batch.errors.append("La página final no coincide con la URL autorizada o fue bloqueada por anti-SSRF.")
                return batch
            status = _verified_status(driver, current)
            if status is None or not 200 <= status < 300:
                batch.errors.append("No se pudo confirmar una respuesta HTTP exitosa del documento principal en la sesión.")
                return batch
            html = driver.page_source
            visible_text = driver.find_element("tag name", "body").text.strip()
            if not html or not visible_text:
                batch.errors.append("La página verificada no contiene texto visible.")
                return batch
            selectors = _CAPTCHA_SELECTORS + (
                "input[type='password']", "form[action*='login']", "form[action*='signin']",
                "[id*='login-form']", "[class*='login-form']", "[id='challenge-form']",
            )
            # Una excepción al consultar el DOM no equivale a ausencia de barreras.
            blocked_dom = any(driver.find_elements(By.CSS_SELECTOR, selector) for selector in selectors)
            analysis = HtmlAnalyzer(html, base_url=current).analyze()
            if (blocked_dom
                    or analysis.get("captcha_indicators")
                    or (analysis.get("auth_indicators") or {}).get("has_login_form")
                    or "just a moment" in visible_text.lower()):
                batch.errors.append("La página aún muestra una verificación o autenticación; extracción detenida.")
                return batch
            batch.pages.append(PageSnapshot(url=sanitize_url(target.url), final_url=sanitize_url(current),
                                             html=html, status_code=status, method=batch.method))
    except Exception:
        batch.errors.append("No se pudo verificar la sesión manual. La extracción permanece detenida.")
    return batch
