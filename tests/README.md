# Tests — Torre de Control FASA

Smoke test de Playwright contra un servidor local ya levantado. No es cobertura
exhaustiva — es una red de seguridad barata antes de tocar `backend/main.py`.

## Uso

```
cd tests
npm install
npx playwright install chromium   # solo la primera vez
node smoke_v4.js
```

Requiere el backend corriendo en `http://127.0.0.1:8000` (`python -m uvicorn
backend.main:app --port 8000` desde la raíz del repo).

## Qué cubre

- Los endpoints de API más usados por el frontend (health, ejecutivo, gestión,
  pedidos, beta, alfa, programa) responden 200.
- Cada una de las 7 pestañas de `/v4` carga contenido real (no se queda en
  "Cargando…") y no genera errores de consola.

## Qué NO cubre

No es una suite exhaustiva — no prueba cada endpoint, ni valida valores
específicos, ni cubre interacciones profundas (drill-downs, formularios). Está
pensada para atrapar regresiones grandes (algo se rompió, un endpoint quedó
mal cableado) durante refactors, no bugs sutiles de lógica.
