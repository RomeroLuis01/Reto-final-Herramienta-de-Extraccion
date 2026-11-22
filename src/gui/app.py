"""Interfaz Tkinter para los servicios existentes de diagnóstico y extracción."""
from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Any

from src.config.settings import get_config, register_target
from src.extractor.extraction_service import ExtractionService
from src.extractor.manual_session import manual_eligible
from src.inspector.web_inspector import WebInspector
from src.gui.viewers import DatasetViewer, FileViewer
from src.monitor.logger import setup_logging
from src.monitor.reporter import DiagnosticReporter
from src.selector.builder import load_schema_file
from src.utils.profiles import ProfileManager

TEST_SITES = {
    "LeetCode — lista de problemas": "https://leetcode.com/problemset/",
    "NASA": "https://www.nasa.gov/",
    "Books to Scrape": "https://books.toscrape.com/",
    "Quotes to Scrape — JavaScript": "https://quotes.toscrape.com/js/",
}
CUSTOM_SITE = "URL personalizada"


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
        self.site = tk.StringVar(value=CUSTOM_SITE)
        self.url.trace_add("write", self._sync_site)
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
        frame.rowconfigure(9, weight=1)
        ttk.Label(frame, text="Sitio de prueba").grid(row=0, column=0, sticky="w", pady=3)
        sites = ttk.Combobox(frame, textvariable=self.site, values=(CUSTOM_SITE, *TEST_SITES), state="readonly")
        sites.grid(row=0, column=1, columnspan=3, sticky="ew", pady=3)
        sites.bind("<<ComboboxSelected>>", self._select_site)
        ttk.Label(frame, text="URL del sitio").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=self.url).grid(row=1, column=1, columnspan=3, sticky="ew", pady=3)
        ttk.Label(frame, text="Etiqueta (opcional)").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=self.label).grid(row=2, column=1, columnspan=3, sticky="ew", pady=3)
        ttk.Checkbutton(frame, text="Analizar contenido dinámico con Selenium", variable=self.selenium).grid(row=3, column=1, sticky="w")
        ttk.Label(frame, text="Autenticación HTTP").grid(row=3, column=2, sticky="e")
        ttk.Combobox(frame, textvariable=self.auth, values=("none", "basic", "bearer"), state="readonly", width=8).grid(row=3, column=3, sticky="w")
        ttk.Label(frame, text="Estrategia").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Combobox(frame, textvariable=self.strategy, values=("auto", "requests", "selenium", "api"), state="readonly", width=14).grid(row=4, column=1, sticky="w")
        ttk.Label(frame, text="Máx. páginas").grid(row=4, column=2, sticky="e", padx=5)
        ttk.Entry(frame, textvariable=self.pages, width=8).grid(row=4, column=3, sticky="w")
        ttk.Label(frame, text="Máx. registros").grid(row=5, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=self.records, width=10).grid(row=5, column=1, sticky="w")
        formats = ttk.Frame(frame)
        formats.grid(row=6, column=1, columnspan=3, sticky="w")
        ttk.Label(frame, text="Formatos").grid(row=6, column=0, sticky="w")
        for name, value in self.formats.items():
            ttk.Checkbutton(formats, text=name.upper(), variable=value).pack(side="left", padx=3)
        ttk.Label(frame, text="Esquema JSON (opcional)").grid(row=7, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=self.schema).grid(row=7, column=1, columnspan=2, sticky="ew")
        ttk.Button(frame, text="Examinar…", command=self._browse).grid(row=7, column=3, padx=5)
        actions = ttk.Frame(frame)
        actions.grid(row=8, column=0, columnspan=4, sticky="w", pady=8)
        self.diagnose_button = ttk.Button(actions, text="Diagnosticar", command=lambda: self._start(False))
        self.diagnose_button.pack(side="left", padx=(0, 8))
        self.extract_button = ttk.Button(actions, text="Diagnosticar y extraer", command=lambda: self._start(True))
        self.extract_button.pack(side="left")
        ttk.Button(actions, text="Ver reportes", command=lambda: FileViewer(self.root, self.config.paths.reports_dir, "reports")).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Ver logs", command=lambda: FileViewer(self.root, self.config.paths.logs_dir, "logs")).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Ver datos", command=lambda: DatasetViewer(self.root, self.config.paths.data_dir, self.config.paths.profiles_dir)).pack(side="left", padx=(8, 0))
        ttk.Label(frame, textvariable=self.status).grid(row=10, column=0, columnspan=4, sticky="w")
        self.output = scrolledtext.ScrolledText(frame, wrap="word", state="disabled")
        self.output.grid(row=9, column=0, columnspan=4, sticky="nsew")

    def _select_site(self, _event: Any = None) -> None:
        url = TEST_SITES.get(self.site.get())
        if url:
            self.url.set(url)

    def _sync_site(self, *_args: str) -> None:
        selected = self.site.get()
        if selected in TEST_SITES and self.url.get() != TEST_SITES[selected]:
            self.site.set(CUSTOM_SITE)

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
                manual_wait = None
                if manual_eligible(target, diagnosis) and self._manual_prompt("consent"):
                    manual_wait = lambda: self._manual_prompt("continue")
                summary = ExtractionService(self.config).run(
                    target, diagnosis=diagnosis, profile=previous, schema_config=schema,
                    strategy=options["strategy"], max_pages=options["max_pages"],
                    max_records=options["max_records"], formats=options["formats"],
                    use_selenium=options["use_selenium"], manual_wait=manual_wait,
                    manual_retry=(lambda message, remaining: self._manual_prompt("retry", message, remaining))
                    if manual_wait else None)
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

    def _manual_prompt(self, stage: str, feedback: str = "", timeout: float = 300) -> bool:
        """Solicita una decisión al hilo Tk; cancela automáticamente tras cinco minutos."""
        event = threading.Event()
        request = {"event": event, "answer": False, "stage": stage,
                   "feedback": feedback, "timeout": max(0, timeout)}
        self.messages.put(("manual_prompt", request))
        return bool(event.wait(max(0, timeout)) and request["answer"])

    def _show_manual_prompt(self, request: dict[str, Any]) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Verificación manual")
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        if request["stage"] == "consent":
            text = ("Se detectó una verificación o inicio de sesión. ¿Deseas abrir un "
                    "navegador visible para completar la interacción manualmente? "
                    "La extracción solo continuará si se confirma el acceso autorizado.")
            accept = "Abrir navegador"
        elif request["stage"] == "retry":
            text = (request["feedback"] + "\n\nSi necesita completar el formulario, hágalo manualmente en el navegador. "
                    "Si ya ve una confirmación, permanezca en esa página y pulse Comprobar de nuevo, "
                    "sin recargar el formulario. La casilla marcada no confirma por sí sola el acceso.")
            accept = "Comprobar de nuevo"
        else:
            text = ("Completa la verificación o inicio de sesión en el navegador abierto. "
                    "Si el sitio lo requiere, envía el formulario manualmente. "
                    "Al terminar, permanece en la página solicitada y pulsa Continuar. "
                    "No se resolverá ninguna verificación automáticamente.")
            accept = "Continuar"
        ttk.Label(frame, text=text, wraplength=460).pack(pady=(0, 12))
        buttons = ttk.Frame(frame)
        buttons.pack()
        def finish(answer: bool) -> None:
            request["answer"] = answer
            request["event"].set()
            dialog.destroy()
        ttk.Button(buttons, text=accept, command=lambda: finish(True)).pack(side="left", padx=5)
        ttk.Button(buttons, text="Cancelar", command=lambda: finish(False)).pack(side="left", padx=5)
        dialog.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        dialog.after(max(1, int(request.get("timeout", 300) * 1000) - 100),
                     lambda: finish(False) if dialog.winfo_exists() else None)

    def _poll(self) -> None:
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "text":
                    self._append(value)
                elif kind == "error":
                    self._append("\nError: " + value + "\n")
                    messagebox.showerror("Error", value, parent=self.root)
                elif kind == "manual_prompt":
                    self._show_manual_prompt(value)
                elif kind == "done":
                    self.status.set("Listo")
                    self.diagnose_button.config(state="normal")
                    self.extract_button.config(state="normal")
        except queue.Empty:
            pass
        self.root.after(100, self._poll)


def main() -> None:
    config = get_config()
    log_path = config.paths.logs_dir / f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    setup_logging(level=config.log_level, log_file=log_path)
    root = tk.Tk()
    ExtractionApp(root)
    root.mainloop()
