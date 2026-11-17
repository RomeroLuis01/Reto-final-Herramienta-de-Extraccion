# Manual de Usuario — Avance 1
## Herramienta de Extracción Inteligente y Automatizada de Información Web

### 1. Propósito
Este sistema realiza un diagnóstico técnico automatizado de sitios web antes de cualquier proceso de extracción. Permite evaluar accesibilidad HTTP, estructura HTML/DOM, tecnologías empleadas, APIs observables, mecanismos de paginación, controles de protección, viabilidad técnica y generación de perfiles y reportes reutilizables.

### 2. Requisitos previos
- Python 3.10 o superior (probado en Python 3.13.7).
- Google Chrome instalado (para análisis dinámico con Selenium).
- Dependencias instaladas mediante `pip install -r requirements.txt`.

### 3. Configuración (`.env`)
Copie `.env.example` a `.env` y ajuste únicamente los parámetros necesarios (por ejemplo `REQUEST_TIMEOUT`, `SELENIUM_HEADLESS`, `LOG_LEVEL`). Nunca versione el archivo `.env`.

### 4. Ejecución

#### Modo interactivo
```bash
python main.py
```
El sistema solicitará:
1. URL a analizar (`http://` o `https://` público).
2. Etiqueta identificadora opcional.
3. Si desea ejecutar análisis dinámico DOM con Selenium.

#### Modo por línea de comandos
```bash
python main.py --url https://books.toscrape.com --label "Catalogo Demo"
python main.py --url https://books.toscrape.com --no-selenium
python main.py --list-profiles
```

### 5. Archivos generados
- `profiles/<dominio>.json`: Perfil reutilizable sanitizado del sitio (incluye historial de hasta 10 análisis, tecnologías, selectores identificados y configuración reutilizable).
- `reports/<dominio>_<fecha>.json` y `.txt`: Reportes detallados con distinción entre información **confirmada**, **probable** y **no identificada**.
- `logs/session_<fecha>.log`: Trazabilidad de ejecución sin credenciales ni tokens.

### 6. Controles de seguridad y ética
- La herramienta rechaza esquemas no permitidos (`file://`, `ftp://`, etc.) y destinos internos/loopback/metadatos para prevenir SSRF.
- Si detecta CAPTCHA, bloqueo HTTP (401/403/429/503) o restricción en `robots.txt`, detiene el análisis dinámico, registra la incidencia y no recomienda extracción automatizada.
- La viabilidad técnica no sustituye la revisión legal ni de Términos de Servicio del sitio analizado.

