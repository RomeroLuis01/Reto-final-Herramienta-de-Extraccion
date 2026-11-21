# Herramienta de Extracción Inteligente y Automatizada de Información Web

**Estado:** Avance 1 (diagnóstico y perfilamiento) + **Avance 2 (extracción, selección, transformación/calidad y almacenamiento)**.

El Avance 2 convierte los módulos 3 a 6 en componentes funcionales y los conecta al diagnóstico ya implementado en el Avance 1. La herramienta es modular, reutilizable, respeta `robots.txt` y barreras técnicas, y conserva datos originales y procesados por separado.

## Estructura del proyecto

```
reto-final/
├── src/
│   ├── config/        # M1: configuración, objetivos y reglas de extracción
│   ├── inspector/     # M2: HTTP, HTML, DOM, protecciones y viabilidad
│   ├── extractor/     # M3: requests, Selenium, API + servicio orquestador
│   │   ├── base.py              # contratos, fábrica y selección automática
│   │   ├── requests_extractor.py# HTML / JSON / XML / CSV estático
│   │   ├── selenium_extractor.py# DOM dinámico, scroll infinito, "cargar más"
│   │   ├── api_extractor.py     # REST/JSON con paginación controlada
│   │   └── extraction_service.py# flujo completo diagnóstico → datasets
│   ├── selector/      # M4: esquemas CSS/XPath/regex/atributo/JSONPath
│   ├── transformer/   # M5: limpieza, normalización, validación, linaje, calidad
│   ├── storage/       # M6: JSON, CSV, Excel, SQLite (+ stubs PostgreSQL/MongoDB)
│   ├── monitor/       # M7: logging, reportes de diagnóstico y de extracción
│   └── utils/         # perfiles, anti-SSRF y sanitización de secretos
├── profiles/          # Perfiles JSON sanitizados
├── reports/           # Reportes de diagnóstico y extracción (JSON + TXT)
├── data/              # Datasets raw/processed (JSON/CSV/Excel/SQLite)
├── logs/              # Logs sin credenciales ni tokens
├── tests/             # Pruebas unitarias, Selenium simulado e integración local
├── main.py            # CLI interactiva y por argumentos
├── .env.example       # Plantilla de configuración
├── pyproject.toml     # Configuración de pytest
└── requirements.txt   # Dependencias
```

## Instalación

```bash
pip install -r requirements.txt
copy .env.example .env
```

Variables nuevas del Avance 2 en `.env`: `EXTRACTION_MAX_PAGES`, `EXTRACTION_MAX_RECORDS`, `EXTRACTION_DELAY_S`, `EXTRACTION_DEFAULT_FORMAT`, `EXTRACTION_PAGE_PARAM`, `EXTRACTION_SQLITE_TABLE` y `DATA_DIR`.

## Uso

### Autenticación HTTP autorizada

Para un sitio al que tenga acceso legítimo, defina `EXTRACTION_AUTH_USERNAME` y
`EXTRACTION_AUTH_PASSWORD` para Basic, o `EXTRACTION_AUTH_TOKEN` para Bearer,
en el entorno (o en `.env`, que no debe compartirse). Active explícitamente
`--auth basic` o `--auth bearer`; en la interfaz gráfica seleccione el modo
correspondiente. Las credenciales no se pasan como argumentos ni se guardan en
los reportes. Use HTTPS fuera de pruebas locales.

El modo autenticado solo admite `requests` y API; Selenium y formularios de
inicio de sesión no están soportados. Se realiza un diagnóstico nuevo y se
bloquean destinos o redirecciones de otro origen para no transmitir cabeceras
ni cookies de sesión fuera del sitio. Se sigue consultando `robots.txt` sin
credenciales y se mantienen los bloqueos por CAPTCHA y respuestas restrictivas.

### Modo interactivo

```bash
python main.py
```

Tras el diagnóstico se pregunta si se desea ejecutar la extracción y se solicitan estrategia, límites y formatos.

### Modo por línea de comandos

```bash
# Diagnóstico + extracción automática
python main.py --url https://books.toscrape.com --label "Books Demo" --extract

# Estrategia forzada, límites prudentes y varios formatos
python main.py --url https://quotes.toscrape.com/js/ --extract \
  --strategy selenium --max-pages 2 --max-records 25 --format json,csv

# API REST con esquema explícito y reutilización del perfil
python main.py --url https://api.spacexdata.com/v4/launches --no-selenium \
  --extract --strategy api --reuse-profile --schema schema.json --format sqlite

# Solo diagnóstico / listado de perfiles
python main.py --url https://books.toscrape.com --no-selenium
python main.py --list-profiles
```

| Opción | Descripción |
|---|---|
| `--extract` | Ejecuta el flujo completo de extracción tras el diagnóstico. |
| `--schema RUTA` | Esquema JSON de selección. Si se omite, se infiere del perfil/diagnóstico y de una página de muestra. |
| `--strategy auto\|requests\|selenium\|api` | Motor de adquisición. `auto` usa `viability.recommended_strategies`. |
| `--max-pages N` | Máximo de páginas/respuestas (por defecto 3). |
| `--max-records N` | Máximo de registros (por defecto 50). |
| `--format json,csv,excel,sqlite` | Formatos de salida; se generan versiones `raw` y `processed`. |
| `--output-dir DIR` | Directorio de datasets (por defecto `data/`). |
| `--reuse-profile` | Reutiliza el `current_diagnosis` guardado en el perfil. |
| `--allow-private-network` | **Solo pruebas controladas:** permite `127.0.0.1`/`localhost` (anti-SSRF relajado). |

### Esquema de selección (opcional)

```json
{
  "name": "productos",
  "base_selector": "div.product",
  "fields": [
    {"name": "titulo", "selector": "span.title", "selector_type": "css", "attribute": "text", "required": true, "transform": "strip"},
    {"name": "precio", "selector": "span.price", "selector_type": "css", "transform": "number"},
    {"name": "url", "selector": "a.link", "attribute": "href"}
  ],
  "next_page_selector": "a[rel='next']"
}
```

Tipos de selector soportados: `css`, `xpath`, `regex`, `attribute` y `json_path`. En APIs se usan rutas tipo `data.items.title` o `results[0].name`.

## Flujo implementado (Avance 2)

1. **Diagnóstico reutilizable (M1/M2):** se ejecuta o se recupera del perfil.
2. **Barreras:** antes de cualquier petición se revisan CAPTCHA, autenticación, HTTP 401/403/429/503, SSRF y `robots.txt`.
3. **Adquisición (M3):** el motor recomendado obtiene hasta `max_pages` páginas con pausa configurable y redirecciones validadas.
4. **Selección (M4):** el esquema declarativo (explícito o inferido) convierte cada página en registros crudos.
5. **Transformación (M5):** limpieza → normalización → validación → deduplicación → linaje.
6. **Calidad:** se generan métricas de registros válidos/incompletos, duplicados y completitud por campo.
7. **Almacenamiento (M6):** `<dominio>_raw_*` y `<dominio>_processed_*` separados en cada formato solicitado.
8. **Reporte (M7):** JSON sanitizado + TXT legible con estrategia, esquema, páginas, calidad, linaje y archivos.
9. **Trazabilidad:** cada registro procesado incluye `_lineage` con siete atributos obligatorios.

## Linaje por registro

Cada registro en `*_processed_*` conserva:

- `source_url`
- `acquisition_date`
- `extraction_method`
- `source_field`
- `validation_status`
- `applied_transformations`
- `execution_id`

Los registros `*_raw_*` conservan `_source` y `_field_meta` sin transformar, para permitir comparación y auditoría.

## Mecanismos de adquisición y escenarios validados

| # | Mecanismo | Motor | Escenario de validación |
|---|---|---|---|
| 1 | HTML estático + paginación clásica | `requests` + BeautifulSoup | `https://books.toscrape.com` y servidor local con `rel="next"` |
| 2 | Datos estructurados JSON/XML/CSV | `requests` + parser integrado | respuestas locales JSON y endpoints públicos |
| 3 | DOM renderizado / scroll infinito / cargar más | `Selenium` | `https://quotes.toscrape.com/js/`, `/scroll` y DOM simulado en pruebas |
| 4 | API REST/JSON paginada | `api` | `https://api.spacexdata.com/v4/launches` y API local mockeada |

Se cubren además cinco escenarios de control: página estática paginada, página dinámica, API paginada, `robots.txt` desfavorable y barrera CAPTCHA/403 simulada. Las pruebas automatizadas usan `127.0.0.1` con `allow_private_network=True` **solo en pruebas controladas**.

## Seguridad y ética

- Anti-SSRF en cada petición y redirección; se rechazan loopback, privadas, link-local, metadatos y DNS interno salvo habilitación explícita en pruebas.
- Sanitización centralizada de URLs, cabeceras y estructuras antes de logs, perfiles y reportes.
- Límite de tamaño de respuesta, reintentos, pausa entre páginas y máximo de registros para evitar cargas excesivas.
- **No se eluden** CAPTCHAs, autenticaciones, controles de acceso ni restricciones de `robots.txt`. Ante una barrera, la ejecución se detiene y se reporta.
- Limitación conocida: existe una ventana teórica de DNS rebinding (TOCTOU) entre la validación y la conexión, documentada también en el Avance 1.

## Pruebas

```bash
python -m pytest -q
python -m pytest -q tests/test_extraction_avance2.py
python -m pytest tests/ -m "not integration and not selenium" -v
```

El conjunto incluye 146 pruebas: 130 del Avance 1 y 16 nuevas del Avance 2 (extractores, esquemas CSS/XPath/regex/JSONPath, transformación/linaje, almacenamiento, integración local, barreras y reportes).

### Interfaz gráfica

Ejecute `python gui.py`. La ventana permite diagnosticar, configurar y ejecutar
extracciones; consulte [el manual](docs/manual_usuario.md) para más detalles.
