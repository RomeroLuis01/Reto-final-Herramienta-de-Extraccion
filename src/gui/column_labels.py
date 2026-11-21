"""Etiquetas locales de presentación, separadas de los datos originales."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


class ColumnLabels:
    def __init__(self, profiles_dir: Path) -> None:
        self.path = Path(profiles_dir) / "viewer" / "column_labels.json"

    @staticmethod
    def key(source: str, dataset: str, table: str = "", scope: str = "") -> str:
        return json.dumps([source, dataset, table, scope], ensure_ascii=False)

    @staticmethod
    def database_scope(source: str) -> str:
        """Distingue bases remotas sin guardar usuarios, contraseñas ni parámetros."""
        if source not in ("PostgreSQL", "MongoDB"):
            return ""
        variable = "EXTRACTION_POSTGRES_URI" if source == "PostgreSQL" else "EXTRACTION_MONGODB_URI"
        parts = urlsplit(os.getenv(variable, ""))
        host = parts.netloc.rsplit("@", 1)[-1]
        database = parts.path if source == "PostgreSQL" else os.getenv("EXTRACTION_MONGODB_DATABASE", "extraction")
        return f"{parts.scheme}://{host}/{database.lstrip('/')}"

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError
            return data
        except (ValueError, UnicodeError) as exc:
            raise ValueError("El archivo de nombres de columnas no es válido.") from exc

    def get(self, key: str, columns: list[str]) -> list[str]:
        saved = self._load().get(key)
        if not isinstance(saved, dict) or saved.get("columns") != columns:
            return list(columns)
        labels = saved.get("labels")
        if not isinstance(labels, list) or len(labels) != len(columns) or not all(isinstance(x, str) and x for x in labels):
            raise ValueError("El archivo de nombres de columnas no es válido.")
        return labels

    def save(self, key: str, columns: list[str], labels: list[str]) -> None:
        if len(columns) != len(labels) or any(not value.strip() for value in labels):
            raise ValueError("Escriba un nombre para cada columna.")
        data = self._load()
        normalized = [value.strip() for value in labels]
        if normalized == columns:
            data.pop(key, None)
        else:
            data[key] = {"columns": columns, "labels": normalized}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as stream:
                temp_name = stream.name
                json.dump(data, stream, ensure_ascii=False, indent=2)
            os.replace(temp_name, self.path)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)
