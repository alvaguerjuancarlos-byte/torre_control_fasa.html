// Smoke test de Torre de Control FASA V4 — corre contra un servidor local ya
// levantado (python -m uvicorn backend.main:app --port 8000).
// Objetivo: red de seguridad barata antes de refactors grandes del backend
// (ej. dividir main.py en routers) — no es cobertura exhaustiva, es "¿sigue
// vivo lo esencial de cada pestaña y endpoint clave?".
//
// Uso: cd tests && npm install && node smoke_v4.js
//
// Regla aprendida en este proyecto (memoria del repo): nunca usar timeouts
// fijos para esperar contenido de /v2/gestion/* o /v3/gestion/* — son lentos
// y un timeout corto da falsos negativos. Se usa waitForFunction en su lugar.

const { chromium } = require('playwright')

const BASE = 'http://127.0.0.1:8000'
const TABS = ['ejecutivo', 'flujo', 'riesgo', 'pron', 'pedidos', 'beta', 'alfa']

const API_ENDPOINTS = [
  '/health',
  '/rango-datos',
  '/v2/ejecutivo/resumen-anual?anio=2025',
  '/v3/gestion/coladas',
  '/v3/gestion/alertas',
  '/v2/gestion/riesgo-partes',
  '/v2/pedidos/lista?limit=5',
  '/api/beta/riesgo-alto?limit=5',
  '/api/beta/criticas',
  '/api/alfa/clientes',
  '/api/programa?mes=' + new Date().toISOString().slice(0, 7),  // puede dar 404 si no hay plan cargado para el mes actual - se acepta
]

let fails = 0
function ok(cond, label) {
  if (cond) { console.log('  ✓', label) }
  else { console.log('  ✗ FALLO:', label); fails++ }
}

async function checkApiEndpoints() {
  console.log('\n== Endpoints de API ==')
  for (const path of API_ENDPOINTS) {
    try {
      const res = await fetch(BASE + path)
      const okStatus = res.status === 200 || (path.startsWith('/api/programa') && res.status === 404)
      ok(okStatus, `GET ${path} -> ${res.status}`)
    } catch (e) {
      ok(false, `GET ${path} -> excepción: ${e.message}`)
    }
  }
}

async function checkTabs() {
  console.log('\n== Pestañas de /v4 ==')
  const browser = await chromium.launch()
  const page = await browser.newPage()
  const consoleErrors = []
  page.on('console', msg => { if (msg.type() === 'error') consoleErrors.push(msg.text()) })
  page.on('pageerror', err => consoleErrors.push('pageerror: ' + err.message))

  await page.goto(BASE + '/v4', { waitUntil: 'networkidle' })

  for (const tab of TABS) {
    consoleErrors.length = 0
    await page.click(`.tab[data-tab="${tab}"]`)

    // Espera contenido real (no timeout fijo) — el panel debe tener texto
    // renderizado, no solo el placeholder "Cargando…".
    await page.waitForFunction((t) => {
      const el = document.getElementById('tab-' + t)
      if (!el) return false
      const text = el.innerText || ''
      return text.length > 200 && !/^Cargando/i.test(text.trim())
    }, tab, { timeout: 20000 }).catch(() => {})

    const el = await page.$(`#tab-${tab}`)
    const text = el ? await el.innerText() : ''
    ok(!!el, `#tab-${tab} existe en el DOM`)
    ok(text.length > 200, `#tab-${tab} tiene contenido real (${text.length} chars)`)
    ok(consoleErrors.length === 0, `#tab-${tab} sin errores de consola` + (consoleErrors.length ? `: ${consoleErrors[0]}` : ''))
  }

  await browser.close()
}

async function main() {
  console.log('Smoke test Torre de Control FASA V4 —', new Date().toISOString())
  await checkApiEndpoints()
  await checkTabs()
  console.log('\n' + (fails === 0 ? `✓ TODO PASÓ` : `✗ ${fails} fallo(s)`))
  process.exit(fails === 0 ? 0 : 1)
}

main()
