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

Espera molde -> vaciado (2026-11-23, /v3/gestion/espera-moldeo) -- INDICADOR INFORMATIVO, no un PC.
Horas entre FHrMoldeo (aleks) y FHrVaciado (vaciado directo) de cada pieza vaciada en la ventana.
Validado en dos mitades del año (scripts/causa_raiz/pc8_validacion.py): un molde que espera > 24 h
trae ~37 % más fierro frío + tierra suelta (×1.38 / ×1.37) y uno vaciado en < 2 h ~30-40 % más
tirado + corazón (×1.43 / ×1.30); la sopladura y el rechazo total no cambian. Por colada el efecto
se diluye y ningún umbral se sostuvo en ambas mitades, por eso NO colorea la colada ni entra en la
gravedad: muestra dónde se rompe el flujo de moldeo para Moldeo y Programación.
"""
import time
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import median
from typing import Optional

from fastapi import APIRouter, Query

from backend.core import _evaluar_coladas_ventana, run
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


# ── Espera molde -> vaciado (informativo) ─────────────────────────────────────────────────────
ESPERA_CORTA_H, ESPERA_LARGA_H = 2, 24
_ESPERA_CACHE: dict = {}


def _piezas_espera(d0: datetime, d1: datetime) -> list:
    rows = run("""SELECT v.idTicket t, TRIM(v.no_parte) p, v.FHrVaciado v, a.m
                  FROM betamega_00_ticketsconvaciadodirecto v
                  JOIN (SELECT IdTicket, MIN(FHrMoldeo) m FROM betamega_aleks_idtickethistorial
                        WHERE FHrMoldeo >= :m0 GROUP BY IdTicket) a ON a.IdTicket = v.idTicket
                  WHERE v.FHrVaciado >= :d0 AND v.FHrVaciado < :d1""",
               {"m0": (d0 - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S"),
                "d0": d0.strftime("%Y-%m-%d %H:%M:%S"), "d1": d1.strftime("%Y-%m-%d %H:%M:%S")})
    out = []
    for r in rows:
        if r["m"] is None or r["v"] is None:
            continue
        h = (r["v"] - r["m"]).total_seconds() / 3600
        if 0 < h < 336:          # fuera de eso es error de captura
            out.append({"ticket": str(r["t"]), "parte": r["p"], "moldeo": r["m"], "vaciado": r["v"], "h": h})
    return out


def _banda(h: float) -> str:
    return "corta" if h < ESPERA_CORTA_H else ("larga" if h > ESPERA_LARGA_H else "normal")


@router.get("/v3/gestion/espera-moldeo")
def espera_moldeo(referencia: Optional[str] = Query(default=None), horas: int = Query(default=48)):
    if referencia is None:
        referencia = datetime.now().strftime("%Y-%m-%dT%H:%M")
    ref_dt = datetime.fromisoformat(referencia)
    clave = (referencia[:13], horas, int(time.time() // 1800))
    if clave in _ESPERA_CACHE:
        return _ESPERA_CACHE[clave]
    pz = _piezas_espera(ref_dt - timedelta(hours=horas), ref_dt)
    n = len(pz)
    cuenta = {"corta": 0, "normal": 0, "larga": 0}
    partes = defaultdict(lambda: {"piezas": 0, "corta": 0, "larga": 0})
    for x in pz:
        b = _banda(x["h"]); cuenta[b] += 1
        P = partes[x["parte"]]; P["piezas"] += 1
        if b != "normal":
            P[b] += 1
    top = sorted(({"parte": k, **v, "pct_fuera": round((v["corta"] + v["larga"]) / v["piezas"] * 100, 1)}
                  for k, v in partes.items() if v["corta"] + v["larga"]),
                 key=lambda r: -(r["corta"] + r["larga"]))[:10]
    extremas = sorted((x for x in pz if _banda(x["h"]) != "normal"), key=lambda x: -abs(x["h"] - 4.7))[:40]
    # tendencia: últimas 5 semanas cerradas a la referencia (siempre, para dar contexto a la ventana)
    semanas = []
    for k in range(5, 0, -1):
        a, b = ref_dt - timedelta(weeks=k), ref_dt - timedelta(weeks=k - 1)
        sp = _piezas_espera(a, b)
        m = len(sp)
        semanas.append({"desde": a.strftime("%Y-%m-%d"), "hasta": b.strftime("%Y-%m-%d"), "piezas": m,
                        "pct_corta": round(sum(_banda(x["h"]) == "corta" for x in sp) / m * 100, 1) if m else None,
                        "pct_larga": round(sum(_banda(x["h"]) == "larga" for x in sp) / m * 100, 1) if m else None,
                        "mediana_h": round(median(x["h"] for x in sp), 1) if m else None})
    out = {
        "referencia": referencia, "horas": horas, "piezas": n,
        "umbrales_h": {"corta": ESPERA_CORTA_H, "larga": ESPERA_LARGA_H},
        "mediana_h": round(median(x["h"] for x in pz), 1) if n else None,
        "bandas": [{"banda": b, "piezas": cuenta[b], "pct": round(cuenta[b] / n * 100, 1) if n else None} for b in ("corta", "normal", "larga")],
        "partes": top, "semanas": semanas,
        "piezas_fuera": [{"ticket": x["ticket"], "parte": x["parte"], "moldeo": x["moldeo"].strftime("%Y-%m-%d %H:%M"),
                          "vaciado": x["vaciado"].strftime("%Y-%m-%d %H:%M"), "horas": round(x["h"], 1), "banda": _banda(x["h"])}
                         for x in extremas],
        "riesgo": {"corta": "tirado y corazón ~30-40 % más (molde/corazón sin curar)",
                   "larga": "fierro frío y tierra suelta ~37 % más (molde rezagado)"},
        "nota": "Informativo: validado por pieza en dos mitades de 2026; por colada no predice rechazo, por eso no colorea.",
    }
    for k in [k for k in _ESPERA_CACHE if k[2] != clave[2]]:
        _ESPERA_CACHE.pop(k, None)
    _ESPERA_CACHE[clave] = out
    return out
