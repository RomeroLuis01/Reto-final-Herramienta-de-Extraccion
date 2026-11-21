"""Interfaz Tkinter para los servicios existentes de diagnóstico y extracción."""
from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Any

from src.config.settings import get_config, register_target
from src.extractor.extraction_service import ExtractionService
from src.inspector.web_inspector import WebInspector
from src.monitor.reporter import DiagnosticReporter
from src.selector.builder import load_schema_file
from src.utils.profiles import ProfileManager


class ExtractionApp:
    """Ventana principal; el trabajo de red se ejecuta fuera del hilo de Tk."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.config = get_config()
        self.messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        root.title("Herramienta de extracción web")
        root.geometry("900x720")
        root.minsize(680, 500)
        self.url = tk.StringVar()
        self.label = tk.StringVar()
        self.strategy = tk.StringVar(value="auto")
        self.auth = tk.StringVar(value="none")
        self.pages = tk.StringVar(value=str(self.config.extraction.max_pages))
        self.records = tk.StringVar(value=str(self.config.extraction.max_records))
        self.schema = tk.StringVar()
        self.selenium = tk.BooleanVar(value=True)
        self.formats = {name: tk.BooleanVar(value=name == "json")
                        for name in ("json", "csv", "excel", "sqlite", "postgresql", "mongodb")}
        self.status = tk.StringVar(value="Listo")
        self._build()
        root.after(100, self._poll)

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(8, weight=1)
        ttk.Label(frame, text="URL del sitio").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=self.url).grid(row=0, column=1, columnspan=3, sticky="ew", pady=3)
        ttk.Label(frame, text="Etiqueta (opcional)").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=self.label).grid(row=1, column=1, columnspan=3, sticky="ew", pady=3)
        ttk.Checkbutton(frame, text="Analizar contenido dinámico con Selenium", variable=self.selenium).grid(row=2, column=1, sticky="w")
        ttk.Label(frame, text="Autenticación HTTP").grid(row=2, column=2, sticky="e")
        ttk.Combobox(frame, textvariable=self.auth, values=("none", "basic", "bearer"), state="readonly", width=8).grid(row=2, column=3, sticky="w")
        ttk.Label(frame, text="Estrategia").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Combobox(frame, textvariable=self.strategy, values=("auto", "requests", "selenium", "api"), state="readonly", width=14).grid(row=3, column=1, sticky="w")
        ttk.Label(frame, text="Máx. páginas").grid(row=3, column=2, sticky="e", padx=5)
        ttk.Entry(frame, textvariable=self.pages, width=8).grid(row=3, column=3, sticky="w")
        ttk.Label(frame, text="Máx. registros").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=self.records, width=10).grid(row=4, column=1, sticky="w")
        formats = ttk.Frame(frame)
        formats.grid(row=5, column=1, columnspan=3, sticky="w")
        ttk.Label(frame, text="Formatos").grid(row=5, column=0, sticky="w")
        for name, value in self.formats.items():
            ttk.Checkbutton(formats, text=name.upper(), variable=value).pack(side="left", padx=3)
        ttk.Label(frame, text="Esquema JSON (opcional)").grid(row=6, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=self.schema).grid(row=6, column=1, columnspan=2, sticky="ew")
        ttk.Button(frame, text="Examinar…", command=self._browse).grid(row=6, column=3, padx=5)
        actions = ttk.Frame(frame)
        actions.grid(row=7, column=0, columnspan=4, sticky="w", pady=8)
        self.diagnose_button = ttk.Button(actions, text="Diagnosticar", command=lambda: self._start(False))
        self.diagnose_button.pack(side="left", padx=(0, 8))
        self.extract_button = ttk.Button(actions, text="Diagnosticar y extraer", command=lambda: self._start(True))
        self.extract_button.pack(side="left")
        ttk.Label(actions, textvariable=self.status).pack(side="left", padx=12)
        self.output = scrolledtext.ScrolledText(frame, wrap="word", state="disabled")
        self.output.grid(row=8, column=0, columnspan=4, sticky="nsew")

    def _browse(self) -> None:
        path = filedialog.askopenfilename(title="Seleccionar esquema", filetypes=[("JSON", "*.json"), ("Todos", "*.*")])
        if path:
            self.schema.set(path)

    def _start(self, extract: bool) -> None:
        try:
            target = register_target(self.url.get(), label=self.label.get(),
                                     respect_robots_txt=self.config.respect_robots_txt,
                                     auth_mode=self.auth.get())
            pages, records = int(self.pages.get()), int(self.records.get())
            if pages < 1 or records < 1:
                raise ValueError("Los límites deben ser enteros positivos.")
            formats = [name for name, value in self.formats.items() if value.get()]
            if extract and not formats:
                raise ValueError("Seleccione al menos un formato de salida.")
            options = {"target": target, "use_selenium": self.selenium.get(),
                       "strategy": self.strategy.get(), "max_pages": pages,
                       "max_records": records, "formats": formats,
                       "schema_path": self.schema.get().strip(), "extract": extract}
        except ValueError as exc:
            messagebox.showerror("Configuración inválida", str(exc), parent=self.root)
            return
        self.diagnose_button.config(state="disabled")
        self.extract_button.config(state="disabled")
        self.status.set("Procesando…")
        self._append("Iniciando diagnóstico…\n")
        threading.Thread(target=self._run, args=(options,), daemon=True).start()

    def _run(self, options: dict[str, Any]) -> None:
        try:
            target = options["target"]
            schema = load_schema_file(options["schema_path"]) if options["extract"] and options["schema_path"] else None
            profiles = ProfileManager(self.config.paths.profiles_dir)
            previous = profiles.load(target.url)
            diagnosis = WebInspector(self.config).inspect(target, use_selenium=options["use_selenium"], previous_profile=previous)
            profile_path = profiles.save(diagnosis)
            reports = DiagnosticReporter(self.config.paths.reports_dir).generate(diagnosis)
            self.messages.put(("text", "Diagnóstico completado.\nPerfil: " + str(profile_path) + "\nReportes: " + ", ".join(map(str, reports.values())) + "\n" + json.dumps(diagnosis, ensure_ascii=False, indent=2, default=str) + "\n"))
            if options["extract"]:
                summary = ExtractionService(self.config).run(
                    target, diagnosis=diagnosis, profile=previous, schema_config=schema,
                    strategy=options["strategy"], max_pages=options["max_pages"],
                    max_records=options["max_records"], formats=options["formats"],
                    use_selenium=options["use_selenium"])
                self.messages.put(("text", "\nResultado de extracción:\n" + json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n"))
        except Exception as exc:
            self.messages.put(("error", str(exc)))
        finally:
            self.messages.put(("done", None))

    def _append(self, text: str) -> None:
        self.output.config(state="normal")
        self.output.insert("end", text)
        self.output.see("end")
        self.output.config(state="disabled")

    def _poll(self) -> None:
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "text":
                    self._append(value)
                elif kind == "error":
                    self._append("\nError: " + value + "\n")
                    messagebox.showerror("Error", value, parent=self.root)
                elif kind == "done":
                    self.status.set("Listo")
                    self.diagnose_button.config(state="normal")
                    self.extract_button.config(state="normal")
        except queue.Empty:
            pass
        self.root.after(100, self._poll)


def main() -> None:
    root = tk.Tk()
    ExtractionApp(root)
    root.mainloop()
