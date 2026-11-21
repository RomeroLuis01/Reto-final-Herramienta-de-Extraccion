# Manual de Usuario — Avances 1 y 2

## Herramienta de Extracción Inteligente y Automatizada de Información Web

### 1. Propósito

El sistema realiza un diagnóstico técnico automatizado (Avance 1) y, a partir de él, ejecuta una extracción multitecnología controlada (Avance 2): selecciona campos, transforma y valida datos, guarda originales y procesados por separado, y genera reportes con linaje por registro.

### 2. Requisitos previos

- Python 3.10 o superior (probado en Python 3.13).
- Google Chrome para análisis/extracción dinámica con Selenium.
- Dependencias: `pip install -r requirements.txt`.

### 3. Configuración (`.env`)

Copie `.env.example` a `.env`. Además de los parámetros del Avance 1, puede ajustar:

| Variable | Valor por defecto | Uso |
|---|---:|---|
| `EXTRACTION_MAX_PAGES` | `3` | Páginas máximas por ejecución. |
| `EXTRACTION_MAX_RECORDS` | `50` | Registros máximos por ejecución. |
| `EXTRACTION_DELAY_S` | `1.0` | Pausa entre peticiones/páginas. |
| `EXTRACTION_DEFAULT_FORMAT` | `json` | Formato por defecto de salida. |
| `EXTRACTION_PAGE_PARAM` | `page` | Parámetro de paginación para APIs. |
| `DATA_DIR` | `data` | Carpeta de datasets `raw`/`processed`. |

Nunca versione `.env`.

### 4. Ejecución

Para acceso HTTP autorizado, configure en el entorno o `.env` las variables
`EXTRACTION_AUTH_USERNAME` y `EXTRACTION_AUTH_PASSWORD` (Basic) o
`EXTRACTION_AUTH_TOKEN` (Bearer). Elija el modo en la interfaz gráfica o use
`--auth basic` / `--auth bearer` en la línea de comandos. Nunca incluya las
credenciales en la URL ni comparta el archivo `.env`. Use HTTPS, salvo pruebas
locales. Solo se admiten los motores requests y API y se bloquean redirecciones
o endpoints fuera del origen; Selenium y formularios no heredan la sesión.

#### Modo interactivo

```bash
python main.py
```

El flujo solicita URL, etiqueta y si se incluye Selenium. Después del diagnóstico pregunta si desea extraer; si responde `s`, solicita estrategia (`auto`, `requests`, `selenium`, `api`), límites, formatos y ruta de esquema opcional.

#### Modo por línea de comandos

```bash
# Extracción automática (recomendada)
python main.py --url https://books.toscrape.com --label "Books" --extract

# Contenido dinámico con Selenium
python main.py --url https://quotes.toscrape.com/js/ --extract --strategy selenium \
  --max-pages 2 --max-records 25

# API REST con esquema propio
python main.py --url https://api.spacexdata.com/v4/launches --no-selenium \
  --extract --strategy api --schema schema.json --format json,sqlite

# Reutilizar diagnóstico previo del perfil
python main.py --url https://books.toscrape.com --extract --reuse-profile

# Prueba local controlada (único caso en que se permite loopback)
python main.py --url http://127.0.0.1:8000 --extract --allow-private-network

# Solo diagnóstico / perfiles
python main.py --url https://books.toscrape.com --no-selenium
python main.py --list-profiles
```

### 5. Selección de campos

Si no se proporciona `--schema`, la herramienta infiere un esquema del perfil del Avance 1 y de la primera página obtenida:

- Tablas: usa encabezados `<th>` o la first row para nombrar columnas.
- Listas/tarjetas: usa clases y etiquetas repetidas (`article`, `.product`, `.quote`, `.card`, `li`, etc.).
- APIs: usa las claves del primer objeto JSON y el contenedor `data`/`results`/`items`.

Si la inferencia no es suficiente, cree un JSON como este:

```json
{
  "name": "libros",
  "base_selector": "article.product_pod",
  "fields": [
    {"name": "titulo", "selector": "h3 a", "attribute": "text", "required": true, "transform": "strip"},
    {"name": "precio", "selector": "p.price_color", "transform": "number"},
    {"name": "detalle", "selector": "h3 a", "attribute": "href"}
  ]
}
```

Transformaciones disponibles en `transform`: `strip`, `upper`, `lower`, `title`, `number`, `integer`, `date`, `boolean`, `url`.

### 6. Archivos generados

| Ruta | Contenido |
|---|---|
| `profiles/<dominio>.json` | Perfil reutilizable del sitio (incluye diagnóstico actual). |
| `reports/<dominio>_<fecha>.json/.txt` | Reporte de diagnóstico del Avance 1. |
| `reports/<dominio>_extraccion_<fecha>.json/.txt` | Reporte de extracción del Avance 2. |
| `data/<dominio>_raw_<fecha>_<id>.*` | Registros originales, sin transformar. |
| `data/<dominio>_processed_<fecha>_<id>.*` | Registros limpios, validados y con `_lineage`. |
| `logs/session_<fecha>.log` | Trazabilidad sin secretos. |

En SQLite, ambos datasets se guardan como tablas distintas dentro de `<dominio>_datasets.sqlite`.

### 7. Linaje y calidad

Cada registro procesado incluye `_lineage`:

```json
{
  "source_url": "https://books.toscrape.com/catalogue/page-1.html",
  "acquisition_date": "2026-11-17T12:34:56",
  "extraction_method": "requests",
  "source_field": "titulo, precio, detalle",
  "validation_status": "valido",
  "applied_transformations": ["clean", "normalize:precio:number", "validate", "dedupe"],
  "execution_id": "uuid..."
}
```

El reporte de extracción resume registros crudos/procesados, válidos/incompletos, duplicados eliminados, completitud por campo y cobertura de linaje.

### 8. Mecanismos y escenarios válidos

| Mecanismo | Cuándo usarlo |
|---|---|
| `requests` (HTML) | Páginas estáticas con o sin paginación clásica. |
| `requests` (JSON/XML/CSV) | Feeds y respuestas estructuradas directas. |
| `selenium` | DOM generado por JavaScript, scroll infinito o botón "cargar más". |
| `api` | Endpoints REST autorizados detectados en el diagnóstico. |

Escenarios recomendados para prueba controlada: `books.toscrape.com`, `quotes.toscrape.com/js/`, `quotes.toscrape.com/scroll`, `api.spacexdata.com/v4/launches` y páginas locales con CAPTCHA/403/robots desfavorable.

### 9. Controles de seguridad y ética

- Solo `http`/`https`; se bloquean destinos internos, loopback y metadatos salvo en pruebas locales explícitas.
- Redirecciones se validan una por una; TLS permanece activo.
- Las cabeceras y URLs se sanitizan antes de guardarse.
- Si el diagnóstico detecta CAPTCHA, autenticación, HTTP 401/403/429/503 o `robots.txt` desfavorable, la extracción **se detiene y se reporta**. La herramienta no intenta eludir estas barreras.
- La viabilidad técnica no sustituye la revisión de términos de servicio ni autorización legal.

### Interfaz gráfica

Ejecute `python gui.py` para abrir la aplicación de escritorio (requiere Tkinter,
incluido habitualmente con Python). Escriba la URL y pulse **Diagnosticar** o
**Diagnosticar y extraer**. Puede configurar la estrategia, límites, formatos y
un esquema JSON opcional; los resultados y las rutas aparecen en la ventana.
El diagnóstico y la extracción se ejecutan en segundo plano. Se mantienen los
controles de barreras y `robots.txt` de la aplicación de consola.
