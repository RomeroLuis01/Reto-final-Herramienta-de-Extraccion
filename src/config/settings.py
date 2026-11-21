"""
Módulo 1 — Configuración del proyecto.

Gestiona URL, parámetros, objetivos y reglas de extracción.
Centraliza toda la configuración del sistema para evitar valores
dispersos en el código.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv

from src.utils.security import sanitize_url, validate_url_security

# Carga variables de entorno desde .env si existe
_env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=_env_path, override=False)


# ---------------------------------------------------------------------------
# Constantes y valores por defecto
# ---------------------------------------------------------------------------

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Dataclasses de configuración
# ---------------------------------------------------------------------------


@dataclass
class SeleniumConfig:
    """Parámetros de configuración para el controlador Selenium."""

    implicit_wait: int = int(os.getenv("SELENIUM_IMPLICIT_WAIT", "0"))
    page_load_timeout: int = int(os.getenv("SELENIUM_PAGE_LOAD_TIMEOUT", "30"))
    script_timeout: int = int(os.getenv("SELENIUM_SCRIPT_TIMEOUT", "15"))
    headless: bool = os.getenv("SELENIUM_HEADLESS", "true").lower() == "true"
    chromedriver_path: Optional[str] = os.getenv("CHROMEDRIVER_PATH") or None


@dataclass
class RequestConfig:
    """Parámetros de configuración para solicitudes HTTP."""

    timeout: int = int(os.getenv("REQUEST_TIMEOUT", "15"))
    max_retries: int = int(os.getenv("REQUEST_MAX_RETRIES", "3"))
    retry_delay: float = float(os.getenv("REQUEST_RETRY_DELAY", "2"))
    max_redirects: int = int(os.getenv("REQUEST_MAX_REDIRECTS", "5"))
    max_response_bytes: int = int(
        os.getenv("REQUEST_MAX_RESPONSE_BYTES", str(5 * 1024 * 1024))
    )
    user_agent: str = os.getenv("DEFAULT_USER_AGENT", "") or _DEFAULT_USER_AGENT

    @property
    def headers(self) -> dict[str, str]:
        """Cabeceras HTTP por defecto para las solicitudes."""
        return {
            "User-Agent": self.user_agent,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "es-MX,es;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }


@dataclass
class PathConfig:
    """Rutas del sistema de archivos del proyecto."""

    base_dir: Path = field(
        default_factory=lambda: Path(__file__).resolve().parents[2]
    )

    profiles_dir: Optional[Path] = None
    reports_dir: Optional[Path] = None
    logs_dir: Optional[Path] = None
    data_dir: Optional[Path] = None

    def __post_init__(self) -> None:
        profiles_env = os.getenv("PROFILES_DIR", "profiles")
        reports_env = os.getenv("REPORTS_DIR", "reports")
        logs_env = os.getenv("LOGS_DIR", "logs")
        data_env = os.getenv("DATA_DIR", "data")

        if self.profiles_dir is None:
            self.profiles_dir = self.base_dir / profiles_env
        if self.reports_dir is None:
            self.reports_dir = self.base_dir / reports_env
        if self.logs_dir is None:
            self.logs_dir = self.base_dir / logs_env
        if self.data_dir is None:
            self.data_dir = self.base_dir / data_env

        for d in (self.profiles_dir, self.reports_dir, self.logs_dir, self.data_dir):
            d.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]


@dataclass
class DiagnosticParameters:
    """Parámetros básicos configurables para la ejecución del diagnóstico."""

    use_selenium: bool = True
    check_robots_txt: bool = True
    simulate_scroll: bool = True
    capture_network_logs: bool = True
    follow_redirects: bool = True
    wait_after_load_s: float = 2.0


@dataclass
class ExtractionParameters:
    """Parámetros prudentes por defecto para la extracción del Avance 2."""

    max_pages: int = int(os.getenv("EXTRACTION_MAX_PAGES", "3"))
    max_records: int = int(os.getenv("EXTRACTION_MAX_RECORDS", "50"))
    delay_s: float = float(os.getenv("EXTRACTION_DELAY_S", "1.0"))
    default_format: str = os.getenv("EXTRACTION_DEFAULT_FORMAT", "json").lower()
    page_param: str = os.getenv("EXTRACTION_PAGE_PARAM", "")
    table_name: str = os.getenv("EXTRACTION_SQLITE_TABLE", "dataset")


@dataclass
class ProjectConfig:
    """Configuración global del proyecto."""

    selenium: SeleniumConfig = field(default_factory=SeleniumConfig)
    request: RequestConfig = field(default_factory=RequestConfig)
    paths: PathConfig = field(default_factory=PathConfig)
    diagnostic: DiagnosticParameters = field(default_factory=DiagnosticParameters)
    extraction: ExtractionParameters = field(default_factory=ExtractionParameters)
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
    respect_robots_txt: bool = (
        os.getenv("RESPECT_ROBOTS_TXT", "true").lower() == "true"
    )


_config: Optional[ProjectConfig] = None


def get_config() -> ProjectConfig:
    """Retorna la instancia global de configuración, creándola si es necesario."""
    global _config
    if _config is None:
        _config = ProjectConfig()
    return _config


def normalize_url(url: str) -> str:
    """Normaliza una URL limpiando espacios y agregando esquema HTTPS si falta."""
    cleaned = url.strip()
    if cleaned and "://" not in cleaned:
        cleaned = f"https://{cleaned}"
    return cleaned


# ---------------------------------------------------------------------------
# Modelo y validación de URL de análisis
# ---------------------------------------------------------------------------


@dataclass
class AnalysisTarget:
    """
    Representa una URL objetivo registrada para análisis.

    Attributes:
        url: URL completa y validada.
        label: Etiqueta opcional para identificar el sitio.
        notes: Observaciones adicionales del usuario.
        respect_robots_txt: Si se debe respetar robots.txt para este objetivo.
        allow_private_network: Solo para pruebas controladas locales.
        objectives: Objetivos declarados de análisis/extracción.
        parameters: Parámetros específicos del diagnóstico para este objetivo.
        extraction_rules: Reglas de extracción preparadas para el Avance 2.
    """

    url: str
    label: str = ""
    notes: str = ""
    respect_robots_txt: bool = True
    allow_private_network: bool = False
    objectives: list[str] = field(default_factory=list)
    parameters: DiagnosticParameters = field(default_factory=DiagnosticParameters)
    extraction_rules: dict[str, Any] = field(default_factory=dict)
    auth_mode: str = "none"

    def __post_init__(self) -> None:
        if self.auth_mode not in ("none", "basic", "bearer"):
            raise ValueError("Modo de autenticación desconocido.")
        if self.auth_mode != "none":
            from src.utils.auth import authorization
            authorization(self.auth_mode, self.url, self.url)
        self.url = self._normalize_url(self.url)
        validated, message = validate_url(
            self.url, allow_private_network=self.allow_private_network
        )
        if not validated:
            raise ValueError(f"URL no válida: {message}")
        self.parameters.check_robots_txt = self.respect_robots_txt

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Normaliza la URL agregando esquema si falta."""
        return normalize_url(url)

    @property
    def domain(self) -> str:
        """Dominio principal de la URL (sin credenciales)."""
        netloc = urlparse(self.url).netloc
        return netloc.rsplit("@", 1)[-1]

    @property
    def scheme(self) -> str:
        """Esquema HTTP/HTTPS de la URL."""
        return urlparse(self.url).scheme.lower()

    @property
    def safe_url(self) -> str:
        """URL sanitizada sin credenciales ni tokens visibles."""
        return sanitize_url(self.url)


def validate_url(
    url: str,
    *,
    resolve_dns: bool = False,
    allow_private_network: bool = False,
) -> tuple[bool, str]:
    """
    Valida que una URL sea técnicamente correcta y segura frente a SSRF.

    Args:
        url: Cadena de texto con la URL a validar.
        resolve_dns: Si True, verifica las IPs resueltas por DNS.
        allow_private_network: Solo True en pruebas controladas locales.

    Returns:
        Tupla (válida, mensaje).
    """
    return validate_url_security(
        url,
        resolve_dns=resolve_dns,
        allow_private_network=allow_private_network,
    )


def register_target(
    url: str,
    label: str = "",
    notes: str = "",
    respect_robots_txt: bool = True,
    allow_private_network: bool = False,
    objectives: list[str] | None = None,
    parameters: DiagnosticParameters | None = None,
    extraction_rules: dict[str, Any] | None = None,
    auth_mode: str = "none",
) -> AnalysisTarget:
    """
    Registra y valida una URL objetivo para análisis.

    Raises:
        ValueError: Si la URL no es válida o infringe política anti-SSRF.
    """
    return AnalysisTarget(
        url=url,
        label=label,
        notes=notes,
        respect_robots_txt=respect_robots_txt,
        allow_private_network=allow_private_network,
        objectives=objectives or [],
        parameters=parameters or DiagnosticParameters(check_robots_txt=respect_robots_txt),
        extraction_rules=extraction_rules or {},
        auth_mode=auth_mode,
    )
