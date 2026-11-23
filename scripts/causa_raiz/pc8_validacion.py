"""Validación de un posible PC-8 «espera molde → vaciado» (23-nov-2026), mismo criterio que la recalibración
del semáforo del 18-nov: un umbral solo vale si se sostiene en las DOS mitades del periodo.

Hipótesis salidas de moldeo.py:
  CORTA  (< 2 h)  -> más TIRADO y CORAZÓN (molde/corazón sin curar)
  LARGA  (> 24 h) -> más FIERRO FRÍO y TIERRA SUELTA
Pruebas:
  1. Por pieza: O/E de los defectos ligados (y del rechazo total) en cada mitad, IC 90 % bootstrap por día.
  2. Por colada (lo que vería la Torre): colada ÁMBAR si >= UMBRAL_PCT de sus piezas caen en la banda;
     lift = rechazo observado / esperado por su mezcla de partes, ámbar vs. resto, en cada mitad.
Lo esperado de cada pieza = tasa de su parte en TODO el periodo, escalada por mes (quita temporada).
Uso: python scripts/causa_raiz/pc8_validacion.py   (solo lectura)
"""
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from backend.core import run
from coladas import norm_defecto, _defecto_sql

DESDE = "2026-01-01"
MITADES = [("1ª mitad ene-may", "2026-01-01", "2026-06-01"), ("2ª mitad ago-nov", "2026-08-01", "2026-12-01")]
BANDAS = {"CORTA < 2 h": (lambda h: h < 2, {"Tirado": ["TIRADO"], "Corazón": ["CORAZON DEFECTUOSO", "CORAZON MAL COLOCADO"]}),
          "LARGA > 24 h": (lambda h: h > 24, {"Fierro frío": ["FIERRO FRIO"], "Tierra suelta": ["TIERRA SUELTA"]})}
UMBRALES_PCT = [10, 25, 50]
N_BOOT = 1000
RNG = np.random.default_rng(20261123)


def cargar():
    mo = run("""SELECT IdTicket t, TRIM(MAX(no_parte)) p, MIN(FHrMoldeo) m, MAX(IdColada) c
                FROM betamega_aleks_idtickethistorial WHERE FHrMoldeo >= :d GROUP BY IdTicket""", {"d": DESDE})
    va = run("""SELECT idTicket t, MIN(FHrVaciado) v FROM betamega_00_ticketsconvaciadodirecto
                WHERE FHrVaciado >= :d GROUP BY idTicket""", {"d": DESDE})
    rz = run(f"""SELECT r.idticket t, {_defecto_sql()} nd FROM rechazo r
                 WHERE r.tipo_rech='Producción' AND r.fecha >= :d""", {"d": DESDE})
    vac = {str(r["t"]): r["v"] for r in va}
    dfs = defaultdict(set)
    for r in rz:
        dfs[str(r["t"])].add(norm_defecto(r["nd"]))
    out = []
    for r in mo:
        t, v = str(r["t"]), vac.get(str(r["t"]))
        if r["m"] is None or v is None:
            continue
        h = (v - r["m"]).total_seconds() / 3600
        if 0 < h < 336:
            out.append({"t": t, "p": r["p"], "m": r["m"], "h": h, "c": r["c"], "def": dfs.get(t, set())})
    return out


def esperado(pz, lista):
    """tasa por parte (todo el periodo) escalada para que cada mes tenga O/E = 1. Devuelve (o, e) por pieza."""
    prod, rech = defaultdict(int), defaultdict(int)
    o = np.array([1.0 if (any(d in x["def"] for d in lista) if lista else bool(x["def"])) else 0.0 for x in pz])
    for x, oi in zip(pz, o):
        prod[x["p"]] += 1; rech[x["p"]] += oi
    e = np.array([rech[x["p"]] / prod[x["p"]] for x in pz])
    mes = np.array([x["m"].strftime("%Y-%m") for x in pz])
    for m in set(mes):
        s = mes == m
        if e[s].sum() > 0 and o[s].sum() > 0:
            e[s] *= o[s].sum() / e[s].sum()
    return o, e


def oe_ic(o, e, marca, dias):
    m = np.asarray(marca, bool)
    if e[m].sum() == 0:
        return None
    ud = sorted(set(dias)); di = {d: i for i, d in enumerate(ud)}
    idx_d = np.array([di[d] for d in dias])
    O = np.bincount(idx_d, weights=o * m, minlength=len(ud)); E = np.bincount(idx_d, weights=e * m, minlength=len(ud))
    b = RNG.integers(0, len(ud), (N_BOOT, len(ud)))
    bo = O[b].sum(1) / np.maximum(E[b].sum(1), 1e-9)
    lo, hi = np.quantile(bo, [.05, .95])
    return O.sum() / E.sum(), lo, hi, int(O.sum()), int(m.sum())


def fmt(r):
    if not r:
        return "sin dato"
    oe, lo, hi, n_o, n = r
    s = " ▲" if lo > 1 else (" ▼" if hi < 1 else "")
    return f"O/E {oe:.2f} [{lo:.2f}-{hi:.2f}] · {n_o} rech / {n:,} pzas{s}"


if __name__ == "__main__":
    pz = cargar()
    print(f"piezas con espera válida: {len(pz):,}")
    for banda, (en_banda, defs) in BANDAS.items():
        print(f"\n================ {banda} ================")
        listas = list(defs.items()) + [("ligados (juntos)", sum(defs.values(), [])), ("rechazo total", None)]
        print("-- 1. Por pieza (dentro de la banda vs. resto)")
        for nombre, lista in listas:
            for mt, a, b in MITADES:
                sub = [x for x in pz if a <= x["m"].strftime("%Y-%m-%d") < b]
                o, e = esperado(sub, lista)
                dias = [x["m"].strftime("%Y-%m-%d") for x in sub]
                marca = [en_banda(x["h"]) for x in sub]
                print(f"   {nombre:<17} {mt}: banda {fmt(oe_ic(o, e, marca, dias))} | resto {fmt(oe_ic(o, e, [not v for v in marca], dias))}")
        print("-- 2. Por colada (colada ÁMBAR si >= X % de sus piezas están en la banda)")
        for umbral in UMBRALES_PCT:
            for nombre, lista in [("ligados", sum(defs.values(), [])), ("rechazo total", None)]:
                for mt, a, b in MITADES:
                    sub = [x for x in pz if a <= x["m"].strftime("%Y-%m-%d") < b and x["c"]]
                    o, e = esperado(sub, lista)
                    col = defaultdict(list)
                    for i, x in enumerate(sub):
                        col[x["c"]].append(i)
                    ambar = {c for c, ii in col.items() if 100 * sum(en_banda(sub[i]["h"]) for i in ii) / len(ii) >= umbral}
                    marca = [x["c"] in ambar for x in sub]
                    dias = [x["m"].strftime("%Y-%m-%d") for x in sub]
                    r = oe_ic(o, e, marca, dias)
                    print(f"   ≥{umbral:>2} % · {nombre:<13} {mt}: {len(ambar):>4}/{len(col):,} coladas ámbar ({100 * len(ambar) / max(1, len(col)):.0f} %) · {fmt(r)}")
    print("\nCriterio: el umbral se sostiene si el O/E de la banda (o de las coladas ámbar) es > 1 con el IC sin tocar 1 en AMBAS mitades.")
