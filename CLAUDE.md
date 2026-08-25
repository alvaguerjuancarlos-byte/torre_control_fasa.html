# CLAUDE.md — Torre de Control FASA

> Generado 2026-08-24 por lectura directa del código (no existía antes). Refleja lo que hay en el repo hoy — si algo cambia, actualizar esto en el mismo commit que el cambio, no después.

## 1. Qué es esto

Dashboard de producción y calidad para FASA (Fundición CSC), construido por MindBridge. FastAPI sirve tanto la API como el HTML del frontend (sin build step, sin framework JS — HTML/CSS/JS planos servidos como `FileResponse`). Corre **localmente** (`arrancar.bat` levanta `uvicorn` y abre `http://127.0.0.1:8000/v2` en el navegador) — no hay despliegue a un dominio ni a Vercel/otro host, a diferencia de MindBridge/SMT Developer/SMTBROKER.

El dashboard activo es **`/v4`** (`frontend/torre_v4.html`, 406KB), con 7 pestañas:

| `data-tab` | Nombre visible |
|---|---|
| `ejecutivo` | Ejecutivo |
| `flujo` | Control del Proceso |
| `riesgo` | Calidad del Producto |
| `pron` | Plan de Producción |
| `pedidos` | Clientes · Pedidos · Partes |
| `beta` | 🤖 Agente Beta |
| `alfa` | 🤖 Agente Alfa |

"Control del Proceso" y "Calidad del Producto" son fusiones recientes de tabs que antes vivían separados (ver `git log`: *"Unifica Flujo del Proceso + Alertas y Protocolos"*, *"Unificar Probabilidad de Rechazo + Criticidad + Simulador"*).

## 2. Cómo correrlo

```bat
arrancar.bat
```
Esto mata cualquier proceso ya escuchando en el puerto 8000, abre el navegador en `/v2`, y levanta `python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`.

Manual (equivalente, sin el paso de matar el puerto ni abrir navegador):
```
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

**Variables de entorno requeridas** (`.env`, no versionado — ver `.env.example`):
```
DB_HOST=
DB_PORT=3306
DB_USER=
DB_PWD=
DB_SCHEMA=ia_fasa
ANTHROPIC_API_KEY=      # requerido por backend/routers/chat.py (Agentes Alfa/Beta conversacionales)
```

**Dependencias:** `pip install -r backend/requirements.txt` (FastAPI, uvicorn, pymysql, SQLAlchemy, pandas, openpyxl, anthropic).

### Smoke tests (Playwright, en `tests/`, independiente del backend Python)
```
cd tests
npm install
npx playwright install chromium   # solo la primera vez
node smoke_v4.js
```
Requiere el backend ya corriendo en `127.0.0.1:8000`. Verifica que los endpoints más usados respondan 200 y que las 7 pestañas de `/v4` carguen contenido real sin errores de consola. **No es cobertura exhaustiva** — es una red de seguridad barata antes de tocar `backend/main.py` (según `tests/README.md`). Correrlo siempre antes/después de un refactor de `main.py`.

## 3. Arquitectura

```
backend/
  main.py        3791 líneas — TODAS las rutas HTTP salvo Alfa/Beta/Chat (ver abajo)
  core.py        infraestructura compartida: engine de BD, run(), rango(), los 7 Puntos
                 de Control (PC-1..PC-7), evaluación de carriles/ventanas
  plan_lector.py lee el Plan de Producción Mensual DIRECTO de un Excel (ver §4)
  models/        modelo ML v2: RandomForest + calibrador Platt + lookup de partes
                 (rf_rechazo_v2.joblib, ~9MB) — riesgo de rechazo por parte/colada
  routers/
    alfa.py      Agente Alfa — ritmo real, cuello de botella, proyección de entrega
    beta.py      Agente Beta — riesgo de calidad / predictivo
    chat.py      capa conversacional (tool-use) sobre alfa.py + beta.py
frontend/
  torre_v4.html  DASHBOARD ACTIVO (7 tabs, ver §1)
  torre_v3.html, gestion.html, ejecutivo.html, piso.html   versiones anteriores,
                 sus rutas (/v3, /gestion, /ejecutivo, /piso) siguen vivas en main.py
index.html       copia de una versión anterior servida en "/" (legado v1, sin prefijo)
scripts/         ~30 scripts de auditoría/exploración de datos, uso histórico puntual
                 — NO son parte de la app en ejecución, no tocar salvo que se pida
                 explícitamente revisar/re-ejecutar una auditoría
check_ciclo.py   diagnóstico suelto de tiempo de ciclo, no se importa desde backend/
```

**`main.py` es un monolito con 4 generaciones de rutas acumuladas** (`/` legado, `/v2`, `/v3`, `/v4`, más `/piso`, `/ejecutivo`, `/gestion` sueltos) — un refactor en curso ya extrajo Beta, Alfa y Chat a `backend/routers/` (ver commits `Refactor backend (1/N)` a `(3/N)` en el historial), pero el resto de los ~50 endpoints de `main.py` sigue sin modularizar. Si agregas un endpoint nuevo, sigue el patrón de refactor ya empezado (extraer a router en `backend/routers/`, importar lo compartido de `core.py`) en vez de seguir creciendo `main.py`.

**Regla de imports de `core.py` (ya establecida, no romper):** `core.py` no importa nada de `backend.routers.*` — los routers importan de `core.py`, nunca al revés. Evita imports circulares.

## 4. Datos

- MySQL, conexión única vía SQLAlchemy (`backend/core.py`), schema por defecto `ia_fasa` (`DB_SCHEMA` en `.env`). Varias queries referencian explícitamente un **segundo schema en el mismo servidor, `corex_test`** (ej. `corex_test.modelos`, `corex_test.clientes`) — es autoritativo para proceso/cliente, independiente del Excel del Plan de Producción.
- **El usuario de BD (`sapiens`) es SOLO LECTURA.** Todo el backend es de solo lectura por diseño (`main.py` docstring: *"FastAPI backend (READ-ONLY)"*, CORS solo permite `GET`). No agregar endpoints de escritura sin confirmar explícitamente con JC que el permiso de BD cambió.
- **El Plan de Producción Mensual se lee directo de un Excel**, no de la BD (`plan_lector.py`, ruta hardcodeada `C:\Users\Administrator\Documents\FASA\plan de produccion mensual`) — porque las tablas `programa_produccion_*` en `ia_fasa` están bloqueadas para `sapiens`. Cuando IT/Jasso otorguen permiso de escritura, la intención documentada en el propio código es que `/api/programa` empiece a preferir la BD y use el Excel como fallback — no al revés.
- Varias fuentes de datos tienen calidad/cobertura documentada inline en los docstrings de `alfa.py`/`beta.py` (ej. columnas de `cscmega_08ruta` "confirmadas confiables ~93-99%", ETL de química "congelado desde 2025-12-29"). **Leer el docstring del router antes de confiar en una columna** — ya hay decisiones tomadas ahí sobre qué es autoritativo y qué no.
- `core.py::rango()` sin fechas explícitas **cae a un mes hardcodeado** (`2025-12-01` a `2025-12-31`), no a "los últimos N días". Si un endpoint parece devolver datos viejos sin razón aparente, es probablemente esto.

## 5. Agentes Alfa / Beta / Chat

- **Alfa** (`backend/routers/alfa.py`): responde "¿vamos a cumplir la entrega de esta parte/cliente?" cruzando ritmo real por etapa + proceso/cliente (autoritativo) + saldo pendiente (no autoritativo, se degrada con gracia si la tabla no existe).
- **Beta** (`backend/routers/beta.py`): riesgo de calidad, cruzando criticidad histórica (`gamamega_01_riesgodefectoparte`, verificada fila a fila contra tabulación manual de Carlos Cruz) con química en vivo (`cscmega_05cquimicosbase`) — **dos compuertas de datos que nunca se mezclan**.
- **Chat** (`backend/routers/chat.py`): capa conversacional real (tool-use) sobre Alfa/Beta usando `anthropic` SDK, modelo **`claude-opus-5`**. Las "tools" son wrappers delgados que llaman directo a las funciones Python de Alfa/Beta (mismo proceso, sin HTTP) — **regla dura del system prompt: el agente solo puede afirmar lo que vino de un resultado de tool, nunca inventa datos.** Si tocas `chat.py`, no debilitar esa regla.

## 6. Convenciones observadas en este repo

- Los módulos clave (`core.py`, `alfa.py`, `beta.py`, `plan_lector.py`) llevan un docstring de cabecera que explica **por qué** están estructurados así, decisiones de diseño y qué se descartó — no qué hace cada función. Si edita o crea un módulo nuevo en `backend/`, seguir ese mismo estilo (ya es el estándar real del proyecto, no una sugerencia externa).
- Mensajes de commit narran la razón del cambio, no solo el qué (`"Corregir supuesto de freeze desactualizado en alfamega_01_rechazosbyidticket"`, no `"fix bug"`). Refactors grandes se numeran explícitamente (`Refactor backend (1/N)`, `(2/N)`, `(3/N)`) para que el historial se lea como una serie.
- `.gitignore` excluye `*.xlsx`, `logs/`, `.env`, `__pycache__/` — el Excel del Plan de Producción y los logs de ejecución son locales a cada máquina, no se versionan.

## 7. Estado / historial reciente (ver `git log` para más)

Commits más recientes al momento de escribir esto: fix de `NameError` en `/v2/ejecutivo/pronostico` sobre `_ALFAMEGA_MONTHLY`, y la extracción de Chat a router (paso 3 del refactor de `main.py`). El refactor de extracción a `routers/` está en progreso activo — antes de asumir dónde vive una función, revisar si ya se movió a `core.py` o a algún router.

## 8. Cómo trabajar en este repo

- Es de solo lectura contra la BD real de producción de FASA — cualquier cambio que implique escribir a `ia_fasa`/`corex_test` requiere confirmación explícita de JC, no es una decisión de implementación.
- Antes de tocar `main.py`, correr `tests/smoke_v4.js` con el backend levantado (§2) para tener una línea base.
- No hay `requirements-dev.txt` ni linter configurado en este repo — no asumir convenciones de estilo (black/ruff/etc.) que no estén ya en uso.
- `scripts/` es un cajón de auditorías históricas puntuales (muchas con sufijos `_v2`, `_v3`, `b`, `c`, `d` — iteraciones dejadas ahí, no una API estable). No reutilizar ni "limpiar" sin que se pida explícitamente — a diferencia del CSS muerto de MindBridge, aquí no está claro qué script sigue siendo relevante para alguna auditoría futura.
