# Manual de usuario

Esta guía describe el diagnóstico y la extracción de información accesible y autorizada. Los resultados dependen de cada sitio; detectar un mecanismo no garantiza que pueda usarse para extraer datos ni que una captura sea completa. Consulte los términos de uso, permisos y privacidad aplicables antes de analizar un sitio.

## 1. Instalación y preparación

Se requiere Python 3.10 o posterior. Para el contenido renderizado y la verificación manual se necesita Chrome compatible con Selenium y un controlador compatible. El acceso a la red es necesario para instalar dependencias y para consultar sitios; la descarga inicial de un controlador también puede requerirlo.

Abra PowerShell en la carpeta del proyecto y ejecute:

```powershell
python -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Si `.env` ya existe, revíselo y agregue solo los ajustes necesarios sin reemplazarlo. Las variables del entorno tienen prioridad. No comparta credenciales ni el archivo `.env`. Las rutas predeterminadas de datos, perfiles, reportes y registros pueden configurarse mediante las variables descritas en `.env.example`.

## 2. Flujo desde la interfaz

1. Ejecute `python gui.py` desde la carpeta del proyecto.
2. Escriba la URL o elija un sitio de la lista. Una opción de la lista no garantiza disponibilidad ni autorización.
3. Pulse **Diagnosticar**. Revise en la ventana y en **Ver reportes** el estado de acceso, restricciones, tecnología probable y estrategias. Puede desmarcar el análisis dinámico con Selenium si solo necesita el diagnóstico HTTP.
4. Configure los límites **Máx. páginas/capturas** y **Máx. registros**, la estrategia y uno o más formatos. Seleccione los datos como se explica en la siguiente sección.
5. Pulse **Diagnosticar y extraer**. El programa vuelve a diagnosticar, aplica las restricciones y muestra el resultado. Abra **Ver datos**, **Ver reportes** o **Ver logs** para revisarlo.

**Máx. páginas/capturas** incluye la primera captura. El límite de registros se aplica antes de eliminar duplicados: puede obtener menos registros únicos. Un límite alcanzado o un estado `ok` no demuestra que se haya obtenido todo el contenido; examine las advertencias y el motivo de parada.

## 3. Elegir sección y campos

### Vista previa de candidatos

1. Introduzca la URL y pulse **Diagnosticar**; espere a que termine.
2. Abra **Configurar sección y campos…**. Si el HTML estático recibido permite inferir candidatos, elija uno de la lista y revise el selector y hasta tres ejemplos.
3. Pulse **Usar candidato y editar**. Ajuste la sección y elimine o modifique las líneas de campos que no necesite. Pulse **Guardar selección**.
4. Compruebe que el campo **Esquema JSON** esté vacío y ejecute **Diagnosticar y extraer**. Revise los datos finales para confirmar que corresponden a la sección deseada.

La vista usa solamente HTML estático ya obtenido, en memoria, sin hacer otra petición ni guardar la muestra en el perfil. No se ofrece si hay una barrera o `robots.txt` no autoriza expresamente la ruta; tampoco representa necesariamente el DOM generado por JavaScript. No es una selección mediante clic sobre la página. Si no hay muestra o candidatos, introduzca selectores manualmente o use un esquema JSON.

### Selectores manuales

En **Configurar sección y campos…**, escriba el selector base que identifica cada registro y una línea por campo:

```text
nombre; selector; tipo; atributo
```

Por ejemplo, si cada registro se identifica con `.product`:

```text
titulo; h2; css; text
enlace; a; css; href
```

`tipo` y `atributo` son opcionales: por defecto son `css` y `text`. Los tipos aceptados son `css`, `xpath`, `regex`, `attribute` y `json_path`. Para datos JSON, CSV o XML ya interpretados, use rutas de campos con `json_path`; la sección puede ser la raíz. La inferencia XML es conservadora y no cubre todas las estructuras ni espacios de nombres. La validación del formulario comprueba el esquema, no que los selectores produzcan los datos deseados.

**Usar selección automática** elimina la configuración manual de la interfaz. También puede elegir un archivo en **Esquema JSON**, pero no combinarlo con la selección manual. El README incluye un ejemplo de esquema con campos requeridos, transformación y selector de página siguiente. La inferencia automática intenta evitar navegación, pero debe revisarse: no siempre distingue la sección correcta.

## 4. Estrategia y contenido dinámico

- `auto` usa la recomendación del diagnóstico. `requests` obtiene respuestas HTTP; `api` consulta JSON de la URL objetivo o endpoints expresamente configurados en uso programático. `selenium` obtiene el DOM renderizado con Chrome.
- Para forzar scroll infinito, elija **selenium** y marque **Activar scroll infinito**. Para un botón de carga, elija **selenium** e introduzca su selector en **Selector CSS cargar más**. Son opciones excluyentes. Si se detecta un botón pero no se configura su selector, no se pulsa automáticamente.
- La paginación por enlaces puede utilizar el selector detectado o `next_page_selector` en un esquema JSON. Los reportes indican capturas, mecanismo y motivo de parada cuando están disponibles.
- En scroll y carga por botón, las capturas pueden contener registros acumulados y repetidos. El límite se aplica antes de la deduplicación y no demuestra cobertura completa.

El diagnóstico puede identificar indicios de GraphQL, XHR/Fetch, eventos y carga temporal, así como solicitudes observadas por el navegador. Los indicios no prueban que un evento se haya ejecutado; una solicitud observada no demuestra autorización. No existe soporte general para extraer por GraphQL, WebSocket u otros protocolos solo porque se detecten.

## 5. Barreras y autenticación

No intente eludir CAPTCHAs, controles de acceso ni restricciones. Ante una barrera o respuesta restrictiva, la aplicación informa el motivo y puede detener la extracción.

Para autenticación HTTP autorizada, configure en el entorno o en `.env` `EXTRACTION_AUTH_USERNAME` y `EXTRACTION_AUTH_PASSWORD` para Basic, o `EXTRACTION_AUTH_TOKEN` para Bearer. Seleccione el modo en **Autenticación HTTP** o use `--auth basic` / `--auth bearer` en la CLI. Las credenciales no se pasan como argumentos. La autenticación HTTP configurada se aplica a `requests` o API, no a Selenium; use HTTPS fuera de pruebas locales.

Cuando se identifica una verificación o formulario compatible y `robots.txt` permite la ruta, la interfaz puede ofrecer Chrome visible. La interacción la realiza el usuario:

1. Acepte **Abrir navegador**, complete la verificación o inicio de sesión y envíe el formulario si corresponde. Permanezca en la URL solicitada y pulse **Continuar**. Una casilla marcada no confirma por sí sola el acceso.
2. Si la aplicación permite comprobar de nuevo, siga las instrucciones y vuelva a intentarlo en la misma sesión. El tiempo total de la sesión manual es de cinco minutos.
3. Si configuró más de una captura, puede navegar manualmente a otro documento del mismo origen y pulsar **Comprobar página**. Cada documento necesita respuesta HTTP 2xx propia, una navegación nueva, `robots.txt` permitido y ausencia de barreras. Para terminar sin otra captura, pulse **Finalizar**. Si la comprobación de una página adicional falla, se detiene y el reporte puede conservar como parcial lo verificado anteriormente.

No se avanza automáticamente ni se guardan las cookies para otra ejecución. Un 403 genérico, 429, 503, SSRF o `robots.txt` desfavorable no habilita el flujo manual. Chrome valida destinos antes y después de la navegación, pero no intercepta cada redirección antes de que el navegador la solicite. La CLI no presenta el diálogo de verificación manual de la interfaz.

## 6. Línea de comandos

Para ver todas las opciones:

```powershell
python main.py --help
python main.py --list-profiles
```

Ejemplos, sustituyendo la URL por un destino permitido:

```powershell
python main.py --url https://example.com/ --no-selenium
python main.py --url https://example.com/ --extract --strategy requests --max-pages 1 --max-records 20 --format json,csv
python main.py --url https://example.com/ --extract --schema esquema.json --format sqlite --output-dir resultados
```

`--no-selenium` omite el análisis DOM dinámico del diagnóstico. `--schema` usa un archivo JSON; sin él se intenta inferir un esquema. `--reuse-profile` reutiliza el diagnóstico disponible, que podría estar desactualizado. `--allow-private-network` relaja la protección de red y se reserva para servidores locales controlados. Para la API, `EXTRACTION_PAGE_PARAM` debe configurarse solo si el servicio admite ese parámetro; los endpoints observados no se usan como autorización implícita.

## 7. Datos, reportes y linaje

Por defecto `data/` contiene conjuntos separados `raw` y `processed`; `reports/` contiene reportes de diagnóstico y extracción; `profiles/` contiene perfiles y comparaciones; `logs/` contiene registros de ejecución. Las ubicaciones pueden variar según la configuración. La ventana de resultados también indica las rutas generadas.

Los datos originales incluyen `_source` y `_field_meta`. Los procesados conservan `_lineage` por registro y `_field_lineage` por campo, con procedencia, estado de validación y transformaciones registradas. `no_evaluado` indica que un campo no tuvo validación individual. En formatos tabulares, las estructuras anidadas se representan como texto serializado. Revise calidad, campos vacíos, duplicados, cobertura y errores en el reporte; `partial` indica que hubo un fallo parcial.

El perfil compara, cuando hay una ejecución anterior compatible, selectores, campos, columnas, estructura JSON, volumen y endpoints configurados u observados por separado. Una diferencia no prueba por sí sola que el sitio haya cambiado: también puede deberse a límites, selección o condiciones de acceso diferentes.

## 8. PostgreSQL y MongoDB

Instale las dependencias opcionales si aún no están instaladas:

```powershell
python -m pip install "psycopg[binary]>=3.1" "pymongo>=4.6"
```

Configure `EXTRACTION_POSTGRES_URI`, `EXTRACTION_MONGODB_URI` y, si corresponde, `EXTRACTION_MONGODB_DATABASE` en el entorno o `.env`. Seleccione los formatos en la interfaz o con `--format postgresql,mongodb`. PostgreSQL almacena cada registro en una columna JSONB `record`; MongoDB lo guarda en un documento `record`. Se usan tablas o colecciones separadas para datos originales y procesados. **Ver datos** permite consultar estas fuentes con la conexión configurada; la vista es de solo lectura y muestra hasta 3000 registros.

Para servidores locales puede utilizar `docker compose up -d` y revisar `docker compose ps`, previa configuración coherente con `.env.example`. No sobrescriba configuraciones existentes ni comparta las URI. Los volúmenes mantienen datos al detener los servicios; `docker compose down -v` los elimina. Consulte los detalles de Docker en el README.

## 9. Pruebas

```powershell
python -m pytest -q
python -m pytest -q tests/test_selenium_dynamic_real.py
python -m pytest -q tests/test_mechanism_detection_real.py
```

Las pruebas Selenium usan Chrome y un servidor local controlado; no equivalen a pruebas en sitios públicos distintos. La prueba de bases reales es optativa y está desactivada por defecto. Para ejecutarla configure ambas URI en el entorno y use:

```powershell
$env:RUN_EXTERNAL_STORAGE_TESTS = '1'
python -m pytest -q tests/test_external_storage_integration.py
Remove-Item Env:RUN_EXTERNAL_STORAGE_TESTS
```

La prueba crea nombres únicos con datos sintéticos marcados y verifica el contenido antes de limpiar únicamente los objetos creados por ella. Si las URI no están configuradas, se omite. No imprima ni copie las URI en reportes o capturas.

## 10. Problemas frecuentes

- **No hay candidatos:** diagnostique primero la misma URL; compruebe acceso, barreras y `robots.txt`. La página puede depender de JavaScript o no contener estructuras inferibles en el HTML estático.
- **No se seleccionaron registros:** revise la sección, los campos, atributos y el contenido obtenido. Ajuste los selectores o use un esquema JSON.
- **Chrome no inicia:** compruebe Chrome, el controlador y la compatibilidad de versiones; para una página estática puede probar el diagnóstico sin Selenium.
- **Acceso bloqueado:** lea la incidencia. No repita masivamente solicitudes ni intente eludir el bloqueo; use el procedimiento de autorización disponible o elija otra fuente permitida.
- **Base de datos inaccesible:** compruebe que el servidor esté activo y que la URI, base y credenciales sean correctas, sin publicarlas. La existencia de un contenedor activo no prueba que la aplicación pueda autenticarse.
- **Menos datos de los esperados:** revise límites, deduplicación, paginación, tipo de carga y advertencias. Una captura manual adicional requiere una navegación nueva y comprobaciones independientes.
