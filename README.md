# Herramienta de Extracción Inteligente y Automatizada de Información Web

## Avance 1 — Arquitectura, Configuración, Seguridad e Inspección Web

### Estructura del proyecto

```
reto-final/
├── src/
│   ├── config/        # Módulo 1: Configuración, URL, parámetros y objetivos
│   ├── inspector/     # Módulo 2: Inspección y diagnóstico web (HTTP/HTML/DOM)
│   ├── extractor/     # Módulo 3: Motor de extracción (arquitectura Avance 2)
│   ├── selector/      # Módulo 4: Selección de información (arquitectura Avance 2)
│   ├── transformer/   # Módulo 5: Transformación y calidad (arquitectura Avance 2)
│   ├── storage/       # Módulo 6: Almacenamiento y exportación (JSON base; Avance 2)
│   ├── monitor/       # Módulo 7: Logging y reportes de diagnóstico
│   └── utils/         # Perfiles reutilizables y seguridad (anti-SSRF/sanitización)
├── profiles/          # Perfiles JSON sanitizados de sitios analizados
├── reports/           # Reportes de diagnóstico (JSON + TXT)
├── logs/              # Logs del sistema
├── tests/             # Pruebas automatizadas (unitarias, Selenium local e integración)
├── main.py            # Punto de entrada CLI / interactivo
├── .env.example       # Plantilla de configuración
├── .gitignore         # Protección de .env y salidas de ejecución
├── pyproject.toml     # Configuración de pytest y marcadores
└── requirements.txt   # Dependencias
```

### Instalación

```bash
pip install -r requirements.txt
copy .env.example .env
```

### Uso

```bash
# Modo interactivo
python main.py

# Modo directo por línea de comandos
python main.py --url https://books.toscrape.com --label "Books Demo"

# Sin análisis Selenium
python main.py --url https://books.toscrape.com --no-selenium

# Listar perfiles guardados
python main.py --list-profiles
```

### Pruebas

```bash
# Todas las pruebas (unitarias + Selenium controlado + integración)
python -m pytest tests/ -v

# Solo pruebas unitarias (sin red ni navegador)
python -m pytest tests/ -m "not integration and not selenium" -v

# Solo pruebas de Selenium con páginas locales controladas
python -m pytest tests/ -m selenium -v

# Solo pruebas de integración con red
python -m pytest tests/ -m integration -v
```

### Seguridad y limitaciones documentadas

- **Anti-SSRF:** Se permiten únicamente esquemas `http` y `https`. Se rechazan `localhost`, loopback (`127.0.0.0/8`, `::1`), redes privadas, link-local/metadatos (`169.254.169.254`, `fe80::/10`), multicast, reservadas y resoluciones DNS internas. Se revalidan manualmente las redirecciones HTTP (máximo 5 saltos) y la URL final en Selenium.
- **Limitación conocida (DNS rebinding):** La verificación DNS previa a la conexión mitiga accesos internos, aunque existe una ventana teórica TOCTOU entre la consulta DNS previa y la conexión realizada por `urllib3` o Chrome, dado que no se deshabilita TLS ni se reemplaza el resolutor del navegador.
- **Protección de secretos:** No se almacenan contraseñas, tokens de consulta, cabeceras `Authorization`/`Set-Cookie` ni cookies de sesión en logs, reportes ni perfiles.
- **Respeto a barreras:** El sistema identifica protecciones (CAPTCHA, HTTP 401/403/429/503, WAF, `robots.txt`) y detiene la operación sin eludir controles.
