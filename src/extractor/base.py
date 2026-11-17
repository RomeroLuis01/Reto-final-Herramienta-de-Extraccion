"""
Módulo 3 — Motor de extracción (arquitectura preparada para Avance 2).

Este módulo define las interfaces base que implementarán los
diferentes motores de extracción en el Avance 2:
- RequestsExtractor (HTML estático).
- SeleniumExtractor (contenido dinámico).
- ApiExtractor (APIs REST/GraphQL autorizadas).

Las clases concretas se implementarán en el Avance 2.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseExtractor(ABC):
    """
    Interfaz base para todos los motores de extracción.

    Establece el contrato que deben cumplir las implementaciones
    concretas de cada estrategia de extracción.
    """

    @abstractmethod
    def extract(self, url: str, config: dict[str, Any]) -> dict[str, Any]:
        """
        Extrae información de la URL según la configuración dada.

        Args:
            url: URL objetivo.
            config: Configuración de extracción (selectores, campos, etc.).

        Returns:
            Diccionario con datos extraídos y metadatos.
        """
        raise NotImplementedError

    @abstractmethod
    def is_applicable(self, diagnosis: dict[str, Any]) -> bool:
        """
        Determina si este extractor es aplicable al diagnóstico dado.

        Args:
            diagnosis: Diagnóstico del WebInspector.

        Returns:
            True si el extractor puede usarse para este sitio.
        """
        raise NotImplementedError


class ExtractorFactory:
    """
    Fábrica de extractores (patrón Factory).

    Selecciona el extractor más adecuado basándose en el diagnóstico
    del WebInspector. Se completará en el Avance 2.
    """

    _registry: dict[str, type[BaseExtractor]] = {}

    @classmethod
    def register(cls, name: str, extractor_class: type[BaseExtractor]) -> None:
        """Registra un nuevo tipo de extractor."""
        cls._registry[name] = extractor_class

    @classmethod
    def select(cls, diagnosis: dict[str, Any]) -> str:
        """
        Recomienda el nombre del extractor más adecuado.

        Args:
            diagnosis: Diagnóstico completo del sitio.

        Returns:
            Nombre del extractor recomendado.
        """
        viability = diagnosis.get("viability", {})
        strategies = viability.get("recommended_strategies", [])

        if not strategies:
            return "none"

        strategy = strategies[0].lower()
        if "selenium" in strategy or "playwright" in strategy:
            return "selenium"
        elif "api rest" in strategy or "graphql" in strategy:
            return "api"
        else:
            return "requests"

