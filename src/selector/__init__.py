"""Módulo 4 — Selección de información (Avance 2)."""

from src.selector.builder import SchemaBuildError, SchemaBuilder, load_schema_file
from src.selector.schema import ExtractionSchema, FieldSelector

__all__ = [
    "ExtractionSchema",
    "FieldSelector",
    "SchemaBuildError",
    "SchemaBuilder",
    "load_schema_file",
]
