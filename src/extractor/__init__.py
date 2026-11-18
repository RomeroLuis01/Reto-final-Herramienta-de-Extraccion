"""Módulo 3 — Motor de extracción multitecnología (Avance 2)."""

from src.extractor.base import (
    BaseExtractor,
    ExtractionBatch,
    ExtractionBlockedError,
    ExtractorFactory,
    PageSnapshot,
)
from src.extractor.api_extractor import ApiExtractor
from src.extractor.requests_extractor import RequestsExtractor
from src.extractor.selenium_extractor import SeleniumExtractor

# Registro de motores disponibles en la fábrica.
ExtractorFactory.register("requests", RequestsExtractor)
ExtractorFactory.register("selenium", SeleniumExtractor)
ExtractorFactory.register("api", ApiExtractor)

__all__ = [
    "ApiExtractor",
    "BaseExtractor",
    "ExtractionBatch",
    "ExtractionBlockedError",
    "ExtractorFactory",
    "PageSnapshot",
    "RequestsExtractor",
    "SeleniumExtractor",
]
