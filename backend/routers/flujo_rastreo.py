"""
Flujo y rastreo (2026-11-08) -- reemplaza el kanban de "Flujo del Proceso" en Control del Proceso.

Por qué: el kanban ponía cada colada en una columna por etapa (En vaciado / En desmoldeo / En
limpieza / Terminado), pero desmoldeo y limpieza solo se registran en betamega_08 para ~1 de cada 7
piezas, así que casi todo caía en "En vaciado" (22 coladas en una columna, las demás vacías) y un
problema de REGISTRO se veía como un problema de PROCESO. JC: "es difícil de leer, muchas coladas,
y el rastreo está incompleto".

Cuatro piezas, todas sobre la misma evaluación y caché de la pestaña (core._evaluar_coladas_ventana):
  1. indicadores: piezas moldeadas -> con vaciado registrado y las que NO lo tienen (rastro
     perdido). No hay etapa "resultado": en la Torre una pieza vaciada cuenta como OK hasta que se
     registra un rechazo (no existe un evento "OK"), así que "con resultado" siempre igualaba a
     "vaciadas" y no medía nada;
  2. embudo de rastreo: % de piezas con registro en cada etapa y en qué tabla vive, para pedirle a
     IT exactamente qué falta;
  3. línea de tiempo por horno: cada colada en su hora, coloreada por su gravedad (peor de PC-1 a
     PC-4) y marcada si trae señal fuerte de Beta;
  4. envejecimiento: piezas moldeadas SIN registro de vaciado por antigüedad de su colada, siempre
     sobre los últimos 30 días. Al 8-nov el hueco no se cierra con los días (23 % aun en coladas de
     14-30 días): no es trabajo en proceso, es rastro perdido. Hipótesis para IT: la fuente es
     betamega_00_ticketsconvaciadodirecto -- los vaciados indirectos podrían vivir en otra tabla.
"""
import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Query

from backend.core import _evaluar_coladas_ventana
from backend.routers.control_resumen import PCS_PRODUCTO, _peor, senales_beta

router = APIRouter()

# (clave en colada["rastreo"], etapa, fuente, nivel) en el orden del proceso
ETAPAS = [
    ("piezas",      "Moldeo",              "betamega_aleks_idtickethistorial", "pieza"),
    ("carga",       "Carga al horno",      "betamega_00_cargasmetalicas", "colada"),
    ("quimica",     "Química (SpectroMAX)", "vistaanalisisquimicoparaitaca, ligada por el Note", "colada"),
    ("itaca",       "ITACA",               "itaca_acqresults, ligada por hora", "colada"),
    ("vaciadas",    "Vaciado",             "betamega_00_ticketsconvaciadodirecto", "pieza"),
    ("temperatura", "Temperatura de vaciado", "betamega_06_vaciado", "pieza"),
    ("desmoldeo",   "Desmoldeo",           "betamega_08_ticketrutacritica (bDESM)", "pieza"),
    ("limpieza",    "Limpieza",            "betamega_08_ticketrutacritica (bLIMP)", "pieza"),
] 
_ENVEJ_CACHE: dict = {}
# la colada ya se fundió y vació: pasado 1 día una pieza sin registro de vaciado ya no está "en proceso"
_TRAMOS = [(0, 1, "0–1 día", "en proceso"), (1, 3, "1–3 días", "atraso"), (3, 7, "3–7 días", "rastro perdido"),
           (7, 14, "7–14 días", "rastro perdido"), (14, 31, "14–30 días", "rastro perdido")]


def _dt(s) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(s)[:19]) if s else None
    except ValueError:
        return None


def _envejecimiento(ref_dt: datetime) -> dict:
    clave = (ref_dt.strftime("%Y-%m-%d"), int(time.time() // 1800))
    if clave in _ENVEJ_CACHE:
        return _ENVEJ_CACHE[clave]
    coladas = _evaluar_coladas_ventana(ref_dt.strftime("%Y-%m-%dT%H:%M"), 30 * 24)
    tramos = [{"tramo": t, "tipo": tipo, "moldeadas": 0, "sin_vaciado": 0, "coladas": 0} for _, _, t, tipo in _TRAMOS]
    for c in coladas:
        r = c.get("rastreo") or {}
        f = _dt(c.get("inicio_fusion"))
        if not f:
            continue
        hueco = max((r.get("piezas") or 0) - (r.get("vaciadas") or 0), 0)
        dias = (ref_dt - f).total_seconds() / 86400
        for i, (lo, hi, _, _) in enumerate(_TRAMOS):
            if lo <= dias < hi:
                tramos[i]["moldeadas"] += r.get("piezas") or 0
                tramos[i]["sin_vaciado"] += hueco
                tramos[i]["coladas"] += 1 if hueco else 0
                break
    for t in tramos:
        t["pct"] = round(t["sin_vaciado"] / t["moldeadas"] * 100, 1) if t["moldeadas"] else None
    out = {"ventana": "últimos 30 días", "tramos": tramos,
           "nota": "piezas moldeadas y asignadas a una colada que no tienen registro de vaciado "
                   "(betamega_00_ticketsconvaciadodirecto), por antigüedad de la colada"}
    for k in [k for k in _ENVEJ_CACHE if k[1] != clave[1]]:
        _ENVEJ_CACHE.pop(k, None)
    _ENVEJ_CACHE[clave] = out
    return out


@router.get("/v3/gestion/flujo-rastreo")
def flujo_rastreo(referencia: Optional[str] = Query(default=None), horas: int = Query(default=48)):
    if referencia is None:
        referencia = datetime.now().strftime("%Y-%m-%dT%H:%M")
    ref_dt = datetime.fromisoformat(referencia)
    coladas = _evaluar_coladas_ventana(referencia, horas)

    tot = {k: 0 for k, *_ in ETAPAS}
    for c in coladas:
        for k in tot:
            tot[k] += (c.get("rastreo") or {}).get(k) or 0
    piezas = tot["piezas"]
    embudo = [{"clave": k, "etapa": n, "fuente": f, "nivel": nivel, "piezas": tot[k],
               "pct": round(tot[k] / piezas * 100, 1) if piezas else None}
              for k, n, f, nivel in ETAPAS]

    grav = {c["id_colada"]: _peor([c["estados"][p] for p in PCS_PRODUCTO]) for c in coladas}
    sen = senales_beta(coladas, grav)
    linea = sorted(({
        "id_colada": c["id_colada"], "colada": c["u_colada"], "horno": c["horno"],
        "inicio": c["inicio_fusion"], "fin": c["fin_fusion"], "gravedad": grav[c["id_colada"]],
        "senal": (sen.get(c["id_colada"]) or {}).get("senal"), "piezas": c["piezas"], "clase": c.get("clase"),
        "rojos": [p for p in PCS_PRODUCTO if c["estados"][p] == "ROJO"],
        "ambar": [p for p in PCS_PRODUCTO if c["estados"][p] == "AMBAR"],
        "partes": [s["sku"] for s in (c.get("skus") or [])][:3],
    } for c in coladas), key=lambda x: x["inicio"])

    return {
        "referencia": referencia, "horas": horas,
        "indicadores": {
            "coladas": len(coladas), "moldeadas": piezas, "vaciadas": tot["vaciadas"],
            "sin_vaciado": max(piezas - tot["vaciadas"], 0),
            "pct_vaciadas": round(tot["vaciadas"] / piezas * 100, 1) if piezas else None,
            "pct_desmoldeo": round(tot["desmoldeo"] / piezas * 100, 1) if piezas else None,
            "pct_limpieza": round(tot["limpieza"] / piezas * 100, 1) if piezas else None,
        },
        "embudo": embudo,
        "linea_tiempo": {"desde": (ref_dt - timedelta(hours=horas)).strftime("%Y-%m-%d %H:%M"),
                         "hasta": ref_dt.strftime("%Y-%m-%d %H:%M"), "coladas": linea},
        "envejecimiento": _envejecimiento(ref_dt),
    }
