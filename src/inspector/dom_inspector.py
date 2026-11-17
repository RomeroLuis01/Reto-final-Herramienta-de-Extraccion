"""
Módulo 2 — Inspector DOM dinámico con Selenium.

Carga la página en un navegador real (Chrome headless), ejecuta el JavaScript
y analiza el DOM resultante. Detecta:
- Diferencia entre HTML estático y DOM/texto renderizado.
- Contenido generado por JavaScript y dependiente del tiempo de ejecución.
- Solicitudes de red (XHR/Fetch/REST/GraphQL/WebSocket) con método,
  tipo de recurso, código HTTP y Content-Type, sin almacenar secretos.
- Scroll infinito y paginación dinámica mediante simulación controlada.
- CAPTCHAs y muros de autenticación en el DOM renderizado.
- Prevención de SSRF previa y posterior a la navegación.
- Liberación estricta de recursos del navegador y del servicio WebDriver.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlparse

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from src.config.settings import SeleniumConfig, get_config
from src.monitor.logger import get_logger
from src.utils.security import sanitize_url, validate_url_security

logger = get_logger("inspector.dom")


# ---------------------------------------------------------------------------
# Probes JavaScript en el DOM post-render
# ---------------------------------------------------------------------------

_JS_FRAMEWORK_PROBES: dict[str, str] = {
    "React": (
        "return !!(window.React || document.querySelector('[data-reactroot]') "
        "|| window.__REACT_DEVTOOLS_GLOBAL_HOOK__?.renderers?.size > 0)"
    ),
    "Next.js": "return !!(window.__NEXT_DATA__ || document.querySelector('#__NEXT_DATA__'))",
    "Vue.js": (
        "return !!(window.Vue || document.querySelector('[data-v-app]') "
        "|| Array.from(document.querySelectorAll('*')).some(el => '__vue__' in el || '__vue_app__' in el))"
    ),
    "Nuxt.js": "return !!(window.__NUXT__ || window.$nuxt)",
    "Angular": (
        "return !!(window.ng || document.querySelector('[ng-version]') "
        "|| typeof window.getAllAngularRootElements === 'function')"
    ),
    "Svelte": "return !!(document.querySelector('[class*=\"svelte-\"]'))",
    "jQuery": "return !!(window.jQuery || (window.$ && window.$.fn && window.$.fn.jquery))",
    "Gatsby": "return !!(window.___gatsby || document.querySelector('#___gatsby'))",
}

_CAPTCHA_SELECTORS: tuple[str, ...] = (
    "[class*='recaptcha']",
    "[id*='recaptcha']",
    "[class*='hcaptcha']",
    "[id*='hcaptcha']",
    "[class*='cf-turnstile']",
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
    "[class*='captcha']",
    "[id*='captcha']",
)

_TELEMETRY_HINTS = (
    "google-analytics",
    "googletagmanager",
    "doubleclick",
    "facebook.com/tr",
    "hotjar",
    "clarity.ms",
    "sentry.io",
    "segment.io",
    "mixpanel",
    "amplitude",
    "gtag",
    "/collect",
    "/beacon",
    "/pixel",
    "/telemetry",
)

_DATA_API_HINTS = (
    "/api/",
    "/v1/",
    "/v2/",
    "/v3/",
    "/rest/",
    ".json",
    "/wp-json",
)


class DomInspector:
    """
    Inspecciona el DOM dinámico usando Selenium con Chrome headless.

    Se gestiona como context manager para garantizar el cierre del driver
    y del proceso ChromeDriver en cualquier escenario.
    """

    def __init__(
        self,
        config: SeleniumConfig | None = None,
        *,
        allow_private_network: bool = False,
        wait_after_load_s: float = 1.5,
    ) -> None:
        self._cfg = config or get_config().selenium
        self._allow_private_network = allow_private_network
        self._wait_after_load_s = max(0.0, wait_after_load_s)
        self._driver: webdriver.Chrome | None = None
        self._service: Service | None = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "DomInspector":
        try:
            self._driver = self._create_driver()
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        """Libera el WebDriver y el servicio ChromeDriver garantizando cero huérfanos."""
        driver = self._driver
        service = self._service
        self._driver = None
        self._service = None

        if driver is not None:
            try:
                driver.quit()
            except Exception as exc:
                logger.debug("Excepción al cerrar WebDriver: %s", exc)

        if service is not None:
            try:
                service.stop()
            except Exception as exc:
                logger.debug("Excepción al detener ChromeDriver service: %s", exc)

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def inspect(
        self,
        url: str,
        static_html: str = "",
        static_text_size: int = 0,
    ) -> dict[str, Any]:
        """
        Carga la URL en Selenium y analiza el DOM dinámico.

        Args:
            url: URL a inspeccionar.
            static_html: HTML estático previo (para comparar tamaños).
            static_text_size: Longitud de texto visible del HTML estático.

        Returns:
            Diccionario con resultados del análisis DOM.
        """
        safe_input_url = sanitize_url(url)
        result: dict[str, Any] = {
            "selenium_available": False,
            "ssrf_blocked": False,
            "final_url": safe_input_url,
            "dynamic_dom_size": None,
            "dynamic_text_size": None,
            "dom_elements_count": None,
            "dom_difference": None,
            "text_difference": None,
            "dom_significantly_different": False,
            "js_generated_content": False,
            "time_dependent_content": False,
            "page_load_time_s": None,
            "js_frameworks_confirmed": [],
            "js_frameworks_probable": [],
            "network_requests": [],
            "http_methods_observed": [],
            "content_types_observed": [],
            "rest_api_detected": False,
            "graphql_detected": False,
            "xhr_fetch_detected": False,
            "websocket_detected": False,
            "endpoints_observed": [],
            "captcha_detected": False,
            "captcha_type": None,
            "infinite_scroll_confirmed": False,
            "pagination_dom": {"detected": False, "selectors": []},
            "interactive_elements_dom": {},
            "auth_wall_detected": False,
            "error": None,
        }

        # Validación anti-SSRF antes de navegar
        safe, reason = validate_url_security(
            url,
            resolve_dns=not self._allow_private_network,
            allow_private_network=self._allow_private_network,
        )
        if not safe:
            result["ssrf_blocked"] = True
            result["error"] = f"Navegación Selenium bloqueada por anti-SSRF: {reason}"
            logger.warning("Selenium SSRF bloqueado (%s): %s", safe_input_url, reason)
            return result

        if self._driver is None:
            result["error"] = "WebDriver no inicializado. Use como context manager."
            return result

        try:
            # Desactivar espera implícita para evitar bloqueos de 10s por selector ausente
            try:
                self._driver.implicitly_wait(0)
            except Exception:
                pass

            initial_text_len = self._load_page(url, result)
            if result.get("error"):
                return result

            # Revalidación anti-SSRF post-navegación (redirecciones HTTP/JS/meta)
            current_url = getattr(self._driver, "current_url", "") or url
            result["final_url"] = sanitize_url(current_url)
            if current_url and not current_url.startswith("about:"):
                safe_after, reason_after = validate_url_security(
                    current_url,
                    resolve_dns=not self._allow_private_network,
                    allow_private_network=self._allow_private_network,
                )
                if not safe_after:
                    result["ssrf_blocked"] = True
                    result["error"] = (
                        f"Redirección en navegador bloqueada por anti-SSRF: {reason_after}"
                    )
                    logger.warning("Redirección Selenium SSRF bloqueada: %s", reason_after)
                    try:
                        self._driver.get("about:blank")
                    except Exception:
                        pass
                    return result

            result["selenium_available"] = True
            self._analyze_dom(result, static_html, static_text_size, initial_text_len)
            self._detect_js_frameworks(result)
            self._analyze_network_logs(result)
            self._detect_captcha_dom(result)
            self._simulate_scroll(result)
            self._detect_pagination_dom(result)
            self._detect_interactive_dom(result)
            self._detect_auth_wall(result)

        except WebDriverException as exc:
            result["error"] = f"Error de WebDriver: {exc}"
            logger.error("Error de WebDriver en %s: %s", safe_input_url, exc)
        except Exception as exc:
            result["error"] = f"Error inesperado en DOM inspector: {exc}"
            logger.exception("Error inesperado en DOM inspector")

        return result

    # ------------------------------------------------------------------
    # Carga de página
    # ------------------------------------------------------------------

    def _load_page(self, url: str, result: dict[str, Any]) -> int:
        """Carga la página, mide tiempo de carga y devuelve texto inicial."""
        start = time.perf_counter()
        try:
            self._driver.get(url)  # type: ignore[union-attr]
            WebDriverWait(self._driver, self._cfg.page_load_timeout).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            initial_text = self._safe_inner_text_length()

            if self._wait_after_load_s > 0:
                time.sleep(self._wait_after_load_s)

            elapsed = round(time.perf_counter() - start, 3)
            result["page_load_time_s"] = elapsed
            logger.debug("Página cargada en %.2fs: %s", elapsed, sanitize_url(url))
            return initial_text

        except TimeoutException:
            result["error"] = "Timeout al cargar la página con Selenium."
            logger.warning("Timeout Selenium: %s", sanitize_url(url))
            return 0
        except WebDriverException as exc:
            result["error"] = f"Error al cargar URL: {exc}"
            logger.error("Error al cargar URL con Selenium: %s", exc)
            return 0

    def _safe_inner_text_length(self) -> int:
        """Retorna la longitud de texto visible del body de forma segura."""
        try:
            val = self._driver.execute_script(  # type: ignore[union-attr]
                "return document.body ? document.body.innerText.trim().length : 0;"
            )
            return int(val or 0)
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Análisis del DOM
    # ------------------------------------------------------------------

    def _analyze_dom(
        self,
        result: dict[str, Any],
        static_html: str,
        static_text_size: int,
        initial_text_len: int,
    ) -> None:
        """Compara el DOM dinámico con el HTML estático."""
        dom_html = self._driver.page_source  # type: ignore[union-attr]
        dynamic_size = len(dom_html.encode("utf-8"))
        static_size = len(static_html.encode("utf-8")) if static_html else 0

        dynamic_text_size = self._safe_inner_text_length()
        try:
            elements_count = int(
                self._driver.execute_script(  # type: ignore[union-attr]
                    "return document.getElementsByTagName('*').length;"
                )
                or 0
            )
        except Exception:
            elements_count = 0

        result["dynamic_dom_size"] = dynamic_size
        result["dynamic_text_size"] = dynamic_text_size
        result["dom_elements_count"] = elements_count

        if static_size > 0:
            diff_pct = round((dynamic_size - static_size) / static_size * 100, 1)
            result["dom_difference"] = f"{diff_pct:+.1f}%"
            if abs(diff_pct) > 20:
                result["dom_significantly_different"] = True
                logger.info("Diferencia DOM vs HTML estático: %s", result["dom_difference"])

        if static_text_size > 0:
            text_diff_pct = round(
                (dynamic_text_size - static_text_size) / static_text_size * 100, 1
            )
            result["text_difference"] = f"{text_diff_pct:+.1f}%"
            if text_diff_pct > 25 or (dynamic_text_size - static_text_size) > 150:
                result["js_generated_content"] = True
        elif dynamic_text_size > 50 and static_html:
            result["js_generated_content"] = True

        if initial_text_len >= 0 and dynamic_text_size > initial_text_len + 20:
            result["time_dependent_content"] = True
            result["js_generated_content"] = True

    def _detect_js_frameworks(self, result: dict[str, Any]) -> None:
        """Detecta frameworks JS ejecutando probes JavaScript en el navegador."""
        confirmed: list[str] = []
        for framework, probe_js in _JS_FRAMEWORK_PROBES.items():
            try:
                detected = self._driver.execute_script(probe_js)  # type: ignore[union-attr]
                if detected:
                    confirmed.append(framework)
                    logger.debug("Framework confirmado en DOM: %s", framework)
            except Exception:
                pass

        result["js_frameworks_confirmed"] = confirmed
        result["js_frameworks_probable"] = []

    # ------------------------------------------------------------------
    # Análisis de red mediante logs de rendimiento + Performance API
    # ------------------------------------------------------------------

    def _analyze_network_logs(self, result: dict[str, Any]) -> None:
        """
        Analiza los logs de rendimiento de Chrome (Network.*) junto con
        window.performance.getEntriesByType('resource') para clasificar:
        URL sanitizada, método HTTP, tipo de recurso, código HTTP y Content-Type.
        """
        requests_by_id: dict[str, dict[str, Any]] = {}
        websocket_found = False
        graphql_found = False

        try:
            logs = self._driver.get_log("performance")  # type: ignore[union-attr]
        except Exception:
            logs = []
            logger.debug("Logs de rendimiento de Chrome no disponibles.")

        for entry in logs:
            try:
                message = json.loads(entry.get("message", "{}"))
                msg = message.get("message", {})
                cdp_method = msg.get("method", "")
                params = msg.get("params", {})

                if cdp_method == "Network.requestWillBeSent":
                    req_id = str(params.get("requestId", ""))
                    req = params.get("request", {})
                    req_url = str(req.get("url", ""))
                    if not req_url or req_url.startswith("data:") or req_url.startswith("chrome"):
                        continue

                    http_method = str(req.get("method", "GET")).upper()
                    resource_type = str(params.get("type", "")).lower() or "other"
                    post_data = str(req.get("postData", ""))

                    if "graphql" in req_url.lower() or (
                        '"query"' in post_data and '"operationName"' in post_data
                    ):
                        graphql_found = True

                    if req_url.startswith("ws://") or req_url.startswith("wss://") or resource_type == "websocket":
                        websocket_found = True

                    requests_by_id[req_id or req_url] = {
                        "url": sanitize_url(req_url),
                        "method": http_method,
                        "resource_type": resource_type,
                        "status_code": None,
                        "content_type": None,
                        "is_xhr_or_fetch": resource_type in ("xhr", "fetch"),
                        "is_graphql_hint": "graphql" in req_url.lower() or '"query"' in post_data,
                    }

                elif cdp_method == "Network.responseReceived":
                    req_id = str(params.get("requestId", ""))
                    resp = params.get("response", {})
                    if req_id in requests_by_id:
                        requests_by_id[req_id]["status_code"] = resp.get("status")
                        requests_by_id[req_id]["content_type"] = resp.get("mimeType")

                elif cdp_method == "Network.webSocketCreated":
                    ws_url = str(params.get("url", ""))
                    websocket_found = True
                    if ws_url:
                        requests_by_id[f"ws:{ws_url}"] = {
                            "url": sanitize_url(ws_url),
                            "method": "WS",
                            "resource_type": "websocket",
                            "status_code": None,
                            "content_type": "websocket",
                            "is_xhr_or_fetch": False,
                            "is_graphql_hint": False,
                        }
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

        # Complementar con window.performance.getEntriesByType('resource')
        seen_urls = {item["url"] for item in requests_by_id.values()}
        try:
            perf_entries = self._driver.execute_script(  # type: ignore[union-attr]
                "return (window.performance && window.performance.getEntriesByType) "
                "? window.performance.getEntriesByType('resource').map(e => ({name: e.name, type: e.initiatorType})) "
                ": [];"
            ) or []
            for ent in perf_entries:
                ent_url = sanitize_url(str(ent.get("name", "")))
                init_type = str(ent.get("type", "")).lower()
                if not ent_url or ent_url in seen_urls:
                    continue
                if init_type in ("xmlhttprequest", "fetch"):
                    mapped_type = "xhr" if init_type == "xmlhttprequest" else "fetch"
                    requests_by_id[f"perf:{ent_url}"] = {
                        "url": ent_url,
                        "method": "GET",
                        "resource_type": mapped_type,
                        "status_code": None,
                        "content_type": None,
                        "is_xhr_or_fetch": True,
                        "is_graphql_hint": "graphql" in ent_url.lower(),
                    }
                    seen_urls.add(ent_url)
        except Exception:
            pass

        classified_requests: list[dict[str, Any]] = []
        endpoints_observed: list[str] = []
        methods_set: set[str] = set()
        content_types_set: set[str] = set()
        xhr_fetch_found = False
        rest_api_found = False

        for item in requests_by_id.values():
            relation = self._classify_network_relation(item)
            item["possible_data_relation"] = relation

            is_relevant = (
                item["is_xhr_or_fetch"]
                or item["resource_type"] == "websocket"
                or relation in ("probable_api_datos", "probable_graphql")
            )
            if not is_relevant:
                continue

            if item["is_xhr_or_fetch"]:
                xhr_fetch_found = True
            if relation == "probable_api_datos":
                rest_api_found = True
            if relation == "probable_graphql":
                graphql_found = True

            methods_set.add(item["method"])
            if item.get("content_type"):
                content_types_set.add(str(item["content_type"]))
            if item["url"] not in endpoints_observed:
                endpoints_observed.append(item["url"])

            classified_requests.append({
                "url": item["url"],
                "method": item["method"],
                "resource_type": item["resource_type"],
                "status_code": item["status_code"],
                "content_type": item["content_type"],
                "is_xhr_or_fetch": item["is_xhr_or_fetch"],
                "possible_data_relation": relation,
            })

        result["network_requests"] = classified_requests[:30]
        result["http_methods_observed"] = sorted(methods_set)
        result["content_types_observed"] = sorted(content_types_set)
        result["xhr_fetch_detected"] = xhr_fetch_found
        result["graphql_detected"] = graphql_found
        result["websocket_detected"] = websocket_found
        result["rest_api_detected"] = rest_api_found
        result["endpoints_observed"] = endpoints_observed[:20]

        logger.info(
            "Red DOM: XHR/Fetch=%s, REST=%s, GraphQL=%s, WS=%s, relevantes=%d",
            xhr_fetch_found,
            rest_api_found,
            graphql_found,
            websocket_found,
            len(classified_requests),
        )

    @staticmethod
    def _classify_network_relation(item: dict[str, Any]) -> str:
        """
        Distingue solicitudes de datos/APIs relevantes de telemetría o recursos estáticos.
        """
        url_l = str(item.get("url", "")).lower()
        ct_l = str(item.get("content_type") or "").lower()
        path_l = urlparse(url_l).path

        if item.get("is_graphql_hint") or "/graphql" in path_l:
            return "probable_graphql"

        if any(t in url_l for t in _TELEMETRY_HINTS):
            return "telemetria_o_recurso"

        if any(ext in path_l for ext in (".js", ".css", ".png", ".jpg", ".svg", ".woff", ".woff2")):
            return "telemetria_o_recurso"

        if "application/json" in ct_l or "+json" in ct_l or "application/xml" in ct_l or "text/csv" in ct_l:
            return "probable_api_datos"

        if any(pat in path_l for pat in _DATA_API_HINTS):
            return "probable_api_datos"

        if item.get("is_xhr_or_fetch"):
            return "no_concluyente"

        return "telemetria_o_recurso"

    # ------------------------------------------------------------------
    # Detección de CAPTCHA en DOM
    # ------------------------------------------------------------------

    def _detect_captcha_dom(self, result: dict[str, Any]) -> None:
        """Detecta elementos de CAPTCHA en el DOM renderizado."""
        for selector in _CAPTCHA_SELECTORS:
            try:
                elements = self._driver.find_elements(By.CSS_SELECTOR, selector)  # type: ignore[union-attr]
                if elements:
                    result["captcha_detected"] = True
                    if "recaptcha" in selector:
                        result["captcha_type"] = "reCAPTCHA (Google)"
                    elif "hcaptcha" in selector:
                        result["captcha_type"] = "hCaptcha"
                    elif "cf-turnstile" in selector:
                        result["captcha_type"] = "Cloudflare Turnstile"
                    else:
                        result["captcha_type"] = "CAPTCHA (tipo no identificado)"
                    logger.warning("CAPTCHA confirmado en DOM: %s", result["captcha_type"])
                    break
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Simulación de scroll controlado
    # ------------------------------------------------------------------

    def _simulate_scroll(self, result: dict[str, Any]) -> None:
        """
        Ejecuta un scroll controlado y verifica si el DOM/contenido aumenta.
        """
        try:
            height_before = int(
                self._driver.execute_script("return document.body ? document.body.scrollHeight : 0;")  # type: ignore[union-attr]
                or 0
            )
            text_before = self._safe_inner_text_length()
            dom_before = len(self._driver.page_source)

            self._driver.execute_script(  # type: ignore[union-attr]
                "window.scrollTo(0, document.body ? document.body.scrollHeight : 0);"
            )
            time.sleep(min(1.5, max(0.3, self._wait_after_load_s)))

            height_after = int(
                self._driver.execute_script("return document.body ? document.body.scrollHeight : 0;")  # type: ignore[union-attr]
                or 0
            )
            text_after = self._safe_inner_text_length()
            dom_after = len(self._driver.page_source)

            if (
                height_after > height_before + 50
                or dom_after > dom_before * 1.05
                or text_after > text_before + 30
            ):
                result["infinite_scroll_confirmed"] = True
                logger.info(
                    "Scroll infinito confirmado: altura %d→%d px, texto %d→%d",
                    height_before,
                    height_after,
                    text_before,
                    text_after,
                )

        except Exception as exc:
            logger.debug("No se pudo simular scroll: %s", exc)

    # ------------------------------------------------------------------
    # Paginación e interacción en DOM
    # ------------------------------------------------------------------

    def _detect_pagination_dom(self, result: dict[str, Any]) -> None:
        """Detecta elementos de paginación en el DOM renderizado."""
        pagination_selectors = (
            "nav[aria-label*='page' i]",
            "nav[aria-label*='pagination' i]",
            "[class*='pagination']",
            "[class*='pager']",
            "a[rel='next']",
            "a[rel='prev']",
            "[class*='load-more']",
            "button[class*='loadMore']",
        )

        found_selectors: list[str] = []
        for sel in pagination_selectors:
            try:
                elements = self._driver.find_elements(By.CSS_SELECTOR, sel)  # type: ignore[union-attr]
                if elements:
                    found_selectors.append(sel)
            except Exception:
                pass

        result["pagination_dom"] = {
            "detected": bool(found_selectors),
            "selectors": found_selectors,
        }

    def _detect_interactive_dom(self, result: dict[str, Any]) -> None:
        """Detecta controles interactivos visibles en el DOM renderizado."""
        try:
            counts = self._driver.execute_script(  # type: ignore[union-attr]
                "return {"
                "  buttons: document.querySelectorAll('button').length,"
                "  tabs: document.querySelectorAll('[role=\"tab\"]').length,"
                "  accordions: document.querySelectorAll('details,[aria-expanded]').length,"
                "  selects: document.querySelectorAll('select').length"
                "};"
            )
            if isinstance(counts, dict):
                result["interactive_elements_dom"] = counts
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Muro de autenticación
    # ------------------------------------------------------------------

    def _detect_auth_wall(self, result: dict[str, Any]) -> None:
        """Detecta si la página presenta un muro de login."""
        auth_selectors = (
            "input[type='password']",
            "form[action*='login']",
            "form[action*='signin']",
            "[id*='login-form']",
            "[class*='login-form']",
        )
        for sel in auth_selectors:
            try:
                elements = self._driver.find_elements(By.CSS_SELECTOR, sel)  # type: ignore[union-attr]
                if elements:
                    result["auth_wall_detected"] = True
                    logger.info("Muro de autenticación detectado en el DOM.")
                    return
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Creación del WebDriver
    # ------------------------------------------------------------------

    def _create_driver(self) -> webdriver.Chrome:
        """Crea y configura el WebDriver de Chrome con timeouts estrictos."""
        options = Options()

        if self._cfg.headless:
            options.add_argument("--headless=new")

        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument(f"--user-agent={get_config().request.user_agent}")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        driver: webdriver.Chrome | None = None
        try:
            if self._cfg.chromedriver_path:
                self._service = Service(executable_path=self._cfg.chromedriver_path)
                driver = webdriver.Chrome(service=self._service, options=options)
            else:
                try:
                    # Selenium 4 incluye Selenium Manager integrado
                    driver = webdriver.Chrome(options=options)
                except Exception as sm_exc:
                    logger.debug("Selenium Manager fallback a webdriver-manager: %s", sm_exc)
                    from webdriver_manager.chrome import ChromeDriverManager

                    self._service = Service(ChromeDriverManager().install())
                    driver = webdriver.Chrome(service=self._service, options=options)

            driver.implicitly_wait(0)
            driver.set_page_load_timeout(self._cfg.page_load_timeout)
            driver.set_script_timeout(self._cfg.script_timeout)
            logger.info("WebDriver Chrome inicializado correctamente.")
            return driver

        except Exception as exc:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            if self._service is not None:
                try:
                    self._service.stop()
                except Exception:
                    pass
                self._service = None
            logger.error("No se pudo inicializar Chrome WebDriver: %s", exc)
            raise
