"""
Módulo 3 — Motor de extracción multitecnología (Avance 2).

Define el contrato común de los motores de adquisición y los objetos de
datos que intercambian con el Módulo 4 (selección):

- RequestsExtractor: HTML/JSON/XML/CSV estático.
- SeleniumExtractor: DOM renderizado por JavaScript, scroll infinito y
  botones "cargar más".
- ApiExtractor: endpoints REST/JSON autorizados descubiertos en el
  diagnóstico del Avance 1.

Todos los motores reutilizan la validación anti-SSRF y respetan las
barreras detectadas por el Módulo 2: ante una protección bloqueante no
extraen información, solo devuelven un error estructurado.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.config.settings import ProjectConfig, get_config
from src.monitor.logger import get_logger
from src.utils.security import sanitize_url

logger = get_logger("extractor.base")


class ExtractionBlockedError(RuntimeError):
    """Se lanza cuando una barrera ética/legal impide iniciar la extracción."""


@dataclass
class PageSnapshot:
    """Una página (o respuesta de API) obtenida por un motor de extracción."""

    url: str
    html: str = ""
    data: Any = None
    content_format: str = "html"
    status_code: int | None = None
    final_url: str = ""
    method: str = "requests"
    page_number: int = 1
    acquired_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serializa el snapshot para reportes (sin cuerpo HTML crudo)."""
        return {
            "url": sanitize_url(self.url),
            "final_url": sanitize_url(self.final_url or self.url),
            "content_format": self.content_format,
            "status_code": self.status_code,
            "method": self.method,
            "page_number": self.page_number,
            "acquired_at": self.acquired_at,
            "metadata": self.metadata,
        }


@dataclass
class ExtractionBatch:
    """Resultado de una ejecución de extracción (una o varias páginas)."""

    method: str
    pages: list[PageSnapshot] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serializa el lote para el reporte final."""
        return {
            "method": self.method,
            "pages": [p.to_dict() for p in self.pages],
            "errors": self.errors,
            "metadata": self.metadata,
        }


class BaseExtractor(ABC):
    """Contrato que cumplen todos los motores de adquisición."""

    method = "base"

    def __init__(
        self,
        config: ProjectConfig | None = None,
        *,
        allow_private_network: bool = False,
    ) -> None:
        self.config = config or get_config()
        self.allow_private_network = allow_private_network

    @abstractmethod
    def extract(self, url: str, config: dict[str, Any]) -> ExtractionBatch:
        """Extrae páginas/respuestas de la URL según la configuración dada."""
        raise NotImplementedError

    @abstractmethod
    def is_applicable(self, diagnosis: dict[str, Any]) -> bool:
        """Indica si el motor es adecuado para el diagnóstico recibido."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Utilidades compartidas
    # ------------------------------------------------------------------

    @staticmethod
    def _barrier_message(config: dict[str, Any]) -> str | None:
        """Devuelve el motivo por el que no debe extraerse, si existe."""
        diagnosis = config.get("diagnosis") or {}
        protection = diagnosis.get("protection") or {}
        viability = diagnosis.get("viability") or {}

        if protection.get("blocking_barrier") or viability.get("blocked_by_barrier"):
            reasons = protection.get("restrictions") or []
            detail = "; ".join(str(r) for r in reasons) if reasons else ""
            return (
                "Extracción detenida por una barrera de protección detectada "
                "en el diagnóstico (CAPTCHA, autenticación, bloqueo HTTP o "
                f"robots.txt). {detail}".strip()
            )
        return None

    def _check_barrier(self, config: dict[str, Any]) -> None:
        """Lanza ExtractionBlockedError si el sitio presenta una barrera crítica."""
        message = self._barrier_message(config)
        if message:
            logger.warning("Extracción bloqueada: %s", message)
            raise ExtractionBlockedError(message)

    @staticmethod
    def _limits(config: dict[str, Any]) -> tuple[int, int, float]:
        """Lee límites prudentes por defecto: 3 páginas, 50 registros, 1s."""
        max_pages = max(1, int(config.get("max_pages", 3) or 3))
        max_records = max(1, int(config.get("max_records", 50) or 50))
        delay = max(0.0, float(config.get("delay_s", 1.0) or 0.0))
        return max_pages, max_records, delay


class ExtractorFactory:
    """Fábrica de motores (patrón Factory), extensible sin tocar el cliente."""

    _registry: dict[str, type[BaseExtractor]] = {}

    @classmethod
    def register(cls, name: str, extractor_class: type[BaseExtractor]) -> None:
        """Registra un nuevo tipo de extractor."""
        cls._registry[name.lower()] = extractor_class

    @classmethod
    def available(cls) -> list[str]:
        """Devuelve los motores registrados."""
        return sorted(cls._registry)

    @classmethod
    def create(
        cls,
        name: str,
        config: ProjectConfig | None = None,
        *,
        allow_private_network: bool = False,
    ) -> BaseExtractor:
        """Crea una instancia del motor solicitado."""
        key = (name or "").strip().lower()
        extractor_class = cls._registry.get(key)
        if extractor_class is None:
            raise ValueError(
                f"Motor de extracción desconocido: '{name}'. "
                f"Disponibles: {', '.join(cls.available())}."
            )
        return extractor_class(
            config,
            allow_private_network=allow_private_network,
        )

    @classmethod
    def select(cls, diagnosis: dict[str, Any]) -> str:
        """
        Recomienda el motor más adecuado según el diagnóstico del Avance 1.

        Prioridad: API autorizada > Selenium (dynámico/scroll) > Requests.
        """
        viability = diagnosis.get("viability") or {}
        network = diagnosis.get("network") or {}
        pagination = diagnosis.get("pagination") or {}
        acquisition = diagnosis.get("acquisition_mechanisms") or {}
        strategies = [
            str(s).lower() for s in viability.get("recommended_strategies", [])
        ]

        api_strategy = any(
            "api rest" in s or "graphql" in s or "json/xml/csv" in s
            for s in strategies
        )
        content_format = str(acquisition.get("content_format") or "").lower()
        if api_strategy and (
            network.get("rest_api_detected") or network.get("endpoints_observed")
        ) and content_format in ("json", "xml", "csv"):
            return "api"

        selenium_strategy = any(
            "selenium" in s or "playwright" in s or "scroll infinito" in s
            for s in strategies
        )
        dynamic = bool(
            pagination.get("infinite_scroll")
            or (diagnosis.get("acquisition_mechanisms") or {}).get("js_generated_content")
            or (diagnosis.get("technologies") or {}).get("render_type")
            in ("dynamic_probable", "spa_probable", "mixed_probable")
        )
        if selenium_strategy and dynamic:
            return "selenium"
        if dynamic and diagnosis.get("selenium_status") == "executed":
            return "selenium"
        return "requests"

    @classmethod
    def create_for_diagnosis(
        cls,
        diagnosis: dict[str, Any],
        config: ProjectConfig | None = None,
        *,
        strategy: str | None = None,
        allow_private_network: bool = False,
    ) -> BaseExtractor:
        """Selecciona (o fuerza) y crea el motor adecuado."""
        name = (strategy or "").strip().lower()
        if not name or name == "auto":
            name = cls.select(diagnosis)
        return cls.create(
            name,
            config,
            allow_private_network=allow_private_network,
        )
