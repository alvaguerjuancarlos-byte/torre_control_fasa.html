"""
Tablero de riesgo por parte (2026-11-08) -- Calidad del Producto > "Todas las partes".

Pedido de JC: la "Probabilidad de Rechazo por Número de Parte" tenía la información correcta pero
era una lista de 139 partes (~10,000 px de alto) donde no se veía qué atacar primero. Este endpoint
envuelve el mismo cálculo (main.v2_riesgo_partes: tasa real de la ventana, tasa ajustada por
shrinkage y modelo de Beta) y le agrega lo que convierte la lista en decisiones:

  - costo: toneladas rechazadas (rechazos x PesoKgs) y USD con el costo de venta por tonelada de
    Finanzas (routers/costo_calidad.COSTO_TON_USD), promedio de los meses de la ventana que ya
    tienen costo;
  - tendencia: tasa de la ventana contra la ventana anterior del mismo largo (misma función,
    otro corte); "empeora"/"mejora" solo con >= 50 piezas en ambas y >= 3 pp de cambio;
  - en producción: piezas vaciadas en los últimos 14 días (betamega_00_ticketsconvaciadodirecto),
    porque una parte de riesgo alto que no se vacía desde hace meses importa menos hoy;
  - defecto dominante histórico (compuerta 1 de Beta, una sola consulta para todas las partes);
  - cuadrante de la matriz de prioridad: volumen alto = >= mediana de piezas de la ventana;
    tasa alta = tasa ajustada >= tasa global.

La lista completa se sigue pudiendo ver: la vista nueva muestra las 15 de más costo y "ver todas".
Importa main de forma diferida (dentro de la función) para no crear un import circular.
"""
from datetime import date, datetime, timedelta
from statistics import median
from typing import Optional

from fastapi import APIRouter, Query

from backend.core import run
from backend.routers.beta import _riesgo_lote_partes
from backend.routers.costo_calidad import COSTO_TON_USD

router = APIRouter()
_CACHE: dict = {}
_TTL = 1800
_MIN_PIEZAS_TENDENCIA = 50
_MIN_PP_TENDENCIA = 3.0


def _costo_ton(desde: date, hasta: date) -> Optional[float]:
    vals = []
    y, m = desde.year, desde.month
    while (y, m) <= (hasta.year, hasta.month):
        v = (COSTO_TON_USD.get(y) or {}).get(m)
        if v:
            vals.append(v)
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return round(sum(vals) / len(vals), 2) if vals else None


@router.get("/v3/calidad/partes-tablero")
def partes_tablero(ventana: int = Query(default=12)):
    import time
    from backend.main import v2_riesgo_partes  # diferido: main importa este router
    clave = (ventana, int(time.time() // _TTL))
    if clave in _CACHE:
        return _CACHE[clave]

    act = v2_riesgo_partes(ventana=ventana)
    desde = date.fromisoformat(str(act["ventana"]["desde"])[:10])
    hasta = date.fromisoformat(str(act["ventana"]["hasta"])[:10])
    ant = v2_riesgo_partes(anio=desde.year, mes=desde.month, ventana=ventana)
    previa = {p["no_parte"]: p for p in ant.get("partes", [])}
    costo_t = _costo_ton(desde, hasta)

    hace14 = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    prod = {r["np"]: int(r["n"]) for r in run("""
        SELECT TRIM(no_parte) AS np, COUNT(*) AS n FROM betamega_00_ticketsconvaciadodirecto
        WHERE FHrVaciado >= :d GROUP BY TRIM(no_parte)
    """, {"d": hace14})}
    partes = act.get("partes", [])
    hist = _riesgo_lote_partes([p["no_parte"] for p in partes])
    med_n = median([p["n_periodo"] for p in partes]) if partes else 0
    g = act["global_rate"]

    out = []
    for p in partes:
        np_ = p["no_parte"]
        ton = round((p["rechazos_periodo"] or 0) * (p.get("peso_kgs") or 0) / 1000, 2)
        pr = previa.get(np_)
        cambio = tendencia = None
        if pr and pr["n_periodo"] >= _MIN_PIEZAS_TENDENCIA and p["n_periodo"] >= _MIN_PIEZAS_TENDENCIA:
            cambio = round(p["rate_periodo"] - pr["rate_periodo"], 1)
            tendencia = "empeora" if cambio >= _MIN_PP_TENDENCIA else "mejora" if cambio <= -_MIN_PP_TENDENCIA else "estable"
        alto_v, alta_t = p["n_periodo"] >= med_n, (p.get("rate_shrunk") or 0) >= g
        h = hist.get(np_) or {}
        out.append({
            "no_parte": np_, "piezas": p["n_periodo"], "rechazos": p["rechazos_periodo"],
            "tasa_real": p["rate_periodo"], "tasa_ajustada": p.get("rate_shrunk"),
            "prob_modelo": p.get("prob_ml"), "peso_kg": p.get("peso_kgs"),
            "ton_rechazadas": ton, "costo_usd": round(ton * costo_t) if costo_t else None,
            "tasa_anterior": pr["rate_periodo"] if pr else None, "cambio_pp": cambio, "tendencia": tendencia,
            "vaciadas_14d": prod.get(np_, 0),
            "defecto_dominante": h.get("defecto_dominante") or None,
            "cuadrante": ("atacar" if alto_v and alta_t else "revisar" if alta_t else
                          "vigilar" if alto_v else "sin_accion"),
        })
    out.sort(key=lambda x: -(x["costo_usd"] or x["ton_rechazadas"] or 0))

    tot_ton = sum(x["ton_rechazadas"] for x in out)
    acum, n80 = 0.0, 0
    for x in out:
        acum += x["ton_rechazadas"]
        n80 += 1
        if tot_ton and acum / tot_ton >= 0.8:
            break
    res = {
        "ventana": act["ventana"], "ventana_anterior": ant.get("ventana"), "tasa_global": g,
        "costo_ton_usd": costo_t, "mediana_piezas": med_n,
        "totales": {
            "partes": len(out), "piezas": sum(x["piezas"] for x in out),
            "rechazos": sum(x["rechazos"] for x in out), "ton_rechazadas": round(tot_ton, 1),
            "costo_usd": round(tot_ton * costo_t) if costo_t else None,
            "partes_80_costo": n80,
            "en_produccion_14d": sum(1 for x in out if x["vaciadas_14d"]),
            "atacar": sum(1 for x in out if x["cuadrante"] == "atacar"),
        },
        "partes": out,
    }
    for k in [k for k in _CACHE if k[1] != clave[1]]:
        _CACHE.pop(k, None)
    _CACHE[clave] = res
    return res
