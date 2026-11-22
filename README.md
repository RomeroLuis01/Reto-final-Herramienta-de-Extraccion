# Herramienta de Extracción Inteligente y Automatizada de Información Web

**Estado:** diagnóstico, extracción, selección, transformación, almacenamiento y comparación de perfiles; consulte las limitaciones y pruebas descritas abajo.

La herramienta conecta módulos de diagnóstico y extracción, permite respetar `robots.txt` y conserva datos originales y procesados por separado. La detección de un mecanismo no garantiza que se pueda extraer mediante él ni que los resultados sean completos. Consulte el [manual de usuario](docs/manual_usuario.md) para el procedimiento completo.

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
│   ├── storage/       # M6: JSON, CSV, Excel, SQLite (+ PostgreSQL/MongoDB opcionales)
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

```powershell
python -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Si `.env` ya existe, no se sobrescribe: agregue manualmente los parámetros que necesite de `.env.example`.

Variables nuevas del Avance 2 en `.env`: `EXTRACTION_MAX_PAGES`, `EXTRACTION_MAX_RECORDS`, `EXTRACTION_DELAY_S`, `EXTRACTION_DEFAULT_FORMAT`, `EXTRACTION_PAGE_PARAM`, `EXTRACTION_SQLITE_TABLE` y `DATA_DIR`.

## Uso

Si el diagnóstico detecta una verificación identificable o un inicio de sesión y `robots.txt` permite la ruta, la interfaz puede ofrecer abrir Chrome visible. La interacción es opcional y la realiza el usuario. Tras completarla, permanezca en la URL original en el mismo navegador y pulse **Continuar** antes de cinco minutos. Si todavía aparece una verificación, puede seguir en ese navegador y comprobar de nuevo; algunos formularios requieren pulsar **Submit** manualmente, y marcar la casilla no garantiza que el servidor haya aceptado el acceso. Si el sitio muestra una confirmación tras enviar el formulario manualmente, permanezca en ella y compruebe de nuevo sin recargar; solo recargue si la aplicación indica que no pudo verificar la respuesta. La aplicación exige evidencia HTTP exitosa del documento principal y vuelve a comprobar las barreras; si no puede verificarlas, se detiene. La demostración reCAPTCHA es un formulario de prueba y no garantiza una página con datos protegidos. Con un límite mayor que uno, la interfaz ofrece capturar más documentos: el usuario navega y confirma cada uno en la misma sesión; la herramienta comprueba de nuevo el origen, `robots.txt`, la respuesta y las barreras. No hay paginación automática ni se guardan cookies. Un 403 genérico, 429, 503, SSRF o `robots.txt` desfavorable no habilita esta opción. La comprobación anti-SSRF se realiza antes y después de la navegación; no intercepta cada redirección antes de que el navegador la solicite.

### Autenticación HTTP autorizada

Para un sitio al que tenga acceso legítimo, defina `EXTRACTION_AUTH_USERNAME` y
`EXTRACTION_AUTH_PASSWORD` para Basic, o `EXTRACTION_AUTH_TOKEN` para Bearer,
en el entorno (o en `.env`, que no debe compartirse). Active explícitamente
`--auth basic` o `--auth bearer`; en la interfaz gráfica seleccione el modo
correspondiente. Las credenciales no se pasan como argumentos ni se guardan en
los reportes. Use HTTPS fuera de pruebas locales.

La autenticación HTTP configurada por credenciales solo admite `requests` y API;
no se aplica a Selenium. Un formulario identificado puede requerir la interacción
manual descrita arriba. Se realiza un diagnóstico nuevo y se
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
| `--format json,csv,excel,sqlite,postgresql,mongodb` | Formatos de salida; se generan versiones `raw` y `processed`. |
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

Para una URL que devuelve JSON directamente, `--strategy api` o la selección automática usan esa URL. Los endpoints observados durante el diagnóstico no se consideran autorizados automáticamente. Configure `EXTRACTION_PAGE_PARAM` (por ejemplo, `page`) solo cuando la API admita paginación; si está vacío, se obtiene una respuesta por endpoint. En uso programático, `extraction_rules` permite indicar `api_endpoints` expresamente autorizados y `api_page_param`. La paginación se detiene si el servidor repite la respuesta anterior.

## Flujo implementado (Avance 2)

1. **Diagnóstico reutilizable (M1/M2):** se ejecuta o se recupera del perfil.
2. **Barreras:** el diagnóstico y los extractores revisan las restricciones detectadas, incluidas CAPTCHA, autenticación, respuestas restrictivas, SSRF y `robots.txt` según la configuración.
3. **Adquisición (M3):** el motor recomendado obtiene hasta `max_pages` páginas con pausa configurable y redirecciones validadas.
4. **Selección (M4):** el esquema declarativo (explícito o inferido) convierte cada página en registros crudos.
5. **Transformación (M5):** limpieza → normalización → validación → deduplicación → linaje.
6. **Calidad:** se generan métricas de registros válidos/incompletos, duplicados y completitud por campo.
7. **Almacenamiento (M6):** `<dominio>_raw_*` y `<dominio>_processed_*` separados en cada formato solicitado.
8. **Reporte (M7):** JSON sanitizado + TXT legible con estrategia, esquema, páginas, calidad, linaje y archivos.
9. **Trazabilidad:** cada registro procesado incluye `_lineage` y `_field_lineage` con procedencia y resultados por campo.

Si ningún registro coincide con el esquema, la ejecución se informa como error y
recomienda revisar los selectores. En modo automático, si Selenium no consigue
obtener páginas, se puede intentar con requests y el reporte conserva el motivo;
al seleccionar Selenium explícitamente no se cambia de motor.

## Linaje por registro

Cada registro en `*_processed_*` conserva:

- `source_url`
- `acquisition_date`
- `extraction_method`
- `source_field`
- `validation_status`
- `applied_transformations`
- `execution_id`

Cada registro procesado también incluye `_field_lineage`, con URL, fecha de adquisición, método, ejecución, nombre del campo, página, selector, tipo y atributo. Contiene el estado de validación de cada campo y las operaciones aplicadas; `no_evaluado` indica que no hubo una validación individual. En CSV y otros formatos tabulares, los objetos anidados se serializan como texto. Los registros `*_raw_*` conservan `_source` y `_field_meta` sin transformar para comparación y auditoría.

## Mecanismos y alcance

| Mecanismo | Detección y extracción disponibles |
|---|---|
| HTML estático y paginación por enlaces | `requests` obtiene HTML y puede seguir un enlace siguiente. |
| JSON, CSV y XML | `requests` interpreta respuestas estructuradas; la inferencia XML es conservadora y no cubre todas las estructuras o espacios de nombres. |
| DOM renderizado, scroll y botón de carga | Selenium captura el DOM y puede avanzar mediante scroll, selector de botón o enlace siguiente. |
| REST/JSON | El motor API consulta la URL objetivo o endpoints configurados explícitamente, con paginación limitada cuando se configura. |
| XHR/Fetch, GraphQL y WebSocket | El diagnóstico puede detectar indicios o peticiones observadas; eso no implica soporte general de extracción mediante esos protocolos. |

Las pruebas incluyen servidores locales controlados, pruebas de componentes con Selenium simulado y algunas pruebas de Chrome. No se presentan los servidores locales como sitios públicos diferentes ni se da por demostrada la extracción de todos los mecanismos y sitios del criterio de aceptación. Un endpoint observado no se considera autorizado; los perfiles anteriores que lo etiquetaban como autorizado lo conservan como dato de autorización no verificada.

`max_pages` limita páginas o capturas, no garantiza cobertura completa. En scroll y botones, las capturas pueden acumular registros repetidos: el límite de registros se aplica antes de deduplicar, por lo que puede quedar reducido el número final de registros únicos. Los reportes indican mecanismo, motivo de parada y advertencias; si se detecta un botón sin selector configurado, no se pulsa automáticamente. En uso programático, `extraction_rules` acepta `load_more_selector` e `infinite_scroll`. En modo manual, cada captura adicional requiere navegación y confirmación explícitas del usuario; no continúa automáticamente.

## Seguridad y ética

- Se validan destinos y redirecciones HTTP; Selenium valida antes y después de navegar, pero no intercepta cada redirección antes de que el navegador la solicite. Se rechazan destinos privados salvo habilitación explícita en pruebas.
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

Las pruebas abarcan extractores, selección, linaje, almacenamiento, servidores locales, barreras y reportes. Las marcadas `selenium` requieren Chrome compatible; los resultados dependen del entorno disponible. Las pruebas reales de PostgreSQL y MongoDB son optativas: consulte las condiciones y ejecución en el [manual](docs/manual_usuario.md#9-pruebas).

### Interfaz gráfica

El menú **Sitio de prueba** permite seleccionar LeetCode, NASA, Books to Scrape
o Quotes to Scrape con JavaScript. La URL también se puede escribir o editar.

Ejecute `python gui.py`. La ventana permite diagnosticar, configurar límites y formatos, ejecutar extracciones y abrir reportes, logs y datos. Para ver candidatos de sección y ejemplos, primero pulse **Diagnosticar** y después abra **Configurar sección y campos…**. La vista se basa en la muestra HTML estática recibida durante ese diagnóstico, requiere que `robots.txt` permita la ruta y que no haya barreras, y no realiza otra petición. No es un inspector visual del DOM dinámico.

Para elegir información sin crear un archivo, pulse **Configurar sección y campos…**. Introduzca un selector CSS que identifique los contenedores de registros; para datos estructurados, use una ruta o déjelo vacío para la raíz. Escriba un campo por línea con el formato `nombre; selector; tipo; atributo`. Por ejemplo:

```text
titulo; h2; css; text
enlace; a; css; href
```

El tipo y el atributo son opcionales: sus valores por defecto son `css` y `text`. Para JSON/CSV/XML, use `json_path` y nombres o rutas de campos. Pulse **Guardar selección**; la interfaz valida los campos, aunque no verifica que los selectores coincidan con el sitio hasta extraer. **Usar selección automática** limpia esta configuración. Como alternativa, seleccione un archivo en **Esquema JSON**; no combine archivo y selección manual. La inferencia automática intenta descartar navegación, pero revise siempre que la sección y los resultados correspondan a lo deseado.

Para contenido dinámico, elija la estrategia **selenium** y active **Scroll infinito** o escriba el selector CSS del botón en **Selector CSS cargar más**. Las dos opciones son excluyentes. Si no configura ninguna, se conserva la detección automática del diagnóstico. **Máx. páginas/capturas** limita las capturas, incluida la inicial; alcanzarlo no demuestra que se haya obtenido todo el contenido. Consulte el motivo de parada y las advertencias en el resultado y los reportes.

## Bases de datos externas

Para usar PostgreSQL o MongoDB instale `pip install "psycopg[binary]>=3.1" "pymongo>=4.6"`, configure respectivamente `EXTRACTION_POSTGRES_URI` o `EXTRACTION_MONGODB_URI` y, opcionalmente, `EXTRACTION_MONGODB_DATABASE` en `.env`. Las credenciales no se incluyen en los reportes. Seleccione `postgresql` o `mongodb` con `--format` o en la interfaz. PostgreSQL crea tablas con una columna JSONB `record`; MongoDB crea colecciones con documentos que contienen `record`. Los conjuntos originales y procesados se guardan por separado. Se requiere acceso a un servidor configurado por el usuario.

### PostgreSQL y MongoDB locales con Docker

Docker Compose permite ejecutar ambas bases sin instalarlas directamente en Windows. Se requiere Docker Desktop (o Docker Engine con Compose), iniciado, y Python en el equipo: la interfaz gráfica sigue ejecutándose fuera de Docker.

1. Desde la carpeta del proyecto instale las dependencias y cree `.env` solo si aún no existe:

   ```powershell
   python -m pip install -r requirements.txt
   if (-not (Test-Path .env)) { Copy-Item .env.example .env }
   ```

2. Si ya tenía `.env`, conserve su contenido y copie a ese archivo las líneas de la sección **Bases de datos locales con Docker** de `.env.example`. Los usuarios, contraseñas y nombres `DOCKER_*` deben coincidir con los que aparecen en `EXTRACTION_POSTGRES_URI` y `EXTRACTION_MONGODB_URI`. Si cambia una contraseña con caracteres especiales en una URI, codifíquela para URL. Los valores de la plantilla son exclusivamente de demostración local; cambie las contraseñas para cualquier otro uso y no comparta `.env`.
3. Inicie los servidores y compruebe su estado:

   ```powershell
   docker compose up -d
   docker compose ps
   ```

   Espere a que los servicios estén `healthy`. La aplicación se conecta mediante `127.0.0.1` a los puertos 5432 y 27017, publicados solo en la computadora local. Esta dirección identifica al mismo equipo en el que se ejecuta la aplicación. Si alguno está ocupado, detenga el servicio que lo usa o cambie el puerto del host en `compose.yaml` y en la URI correspondiente en `.env`.
4. Inicie o reinicie `python gui.py` después de editar `.env` y seleccione PostgreSQL y/o MongoDB en la interfaz. La primera descarga de imágenes requiere conexión a Internet. También se pueden usar las opciones `--format postgresql,mongodb` desde la CLI.

Los datos permanecen en los volúmenes nombrados `postgres_data` y `mongo_data` al detener o recrear los contenedores. Para detenerlos sin borrar los datos:

```powershell
docker compose down
```

Las imágenes inicializan usuarios y contraseñas solo cuando el volumen está vacío: editar las variables después de la primera inicialización no cambia las credenciales ya guardadas. `docker compose down -v` elimina los volúmenes y sus datos, así que no lo use si desea conservarlos. Si su entorno usa servidores externos, puede dejar los contenedores apagados y configurar sus URI directamente.

Desde `python gui.py`, los botones **Ver reportes** y **Ver logs** permiten explorar los archivos generados en vistas organizadas por secciones o por eventos.

En **Ver datos**, el botón **Renombrar columnas** permite asignar nombres visibles cuando la fuente no proporciona encabezados. Los nombres se guardan localmente para el conjunto y tabla seleccionados; no modifican los datos ni sus campos originales. Use **Restablecer originales** para volver a los nombres guardados en el dataset.

El botón **Ver datos** permite seleccionar la fuente **Archivos locales**, **PostgreSQL** o **MongoDB**. Para las bases de datos utiliza las conexiones configuradas en `.env` y muestra los datasets originales y procesados disponibles, con un máximo de 3000 registros por vista. Las consultas son de solo lectura; si el servidor no está disponible, inicie los contenedores y pulse **Actualizar**.

Los nombres largos se acortan y terminan en un identificador; el visor los incluye como candidatos, pues ese patrón por sí solo no garantiza que pertenezcan al proyecto.
