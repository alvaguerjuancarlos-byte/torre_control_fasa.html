"""Moldeo contra rechazo (exploratorio, 23-nov-2026): ¿influye cuánto espera el molde antes de vaciarse,
el turno o el día en que se moldea?

Por pieza (ticket): FHrMoldeo (aleks) y FHrVaciado (vaciado directo, vdir; aleks.FHrVaciado = FHrMoldeo en
~76 % y no sirve). Espera = vaciado - moldeo en horas. Para cada defecto: O/E de cada grupo, donde lo esperado
sale de la tasa de rechazo de ESA parte en todo el periodo (así no confunde "partes difíciles esperan más").

Trampas que se cuidan:
- TEMPORADA/tendencia: lo esperado se escala para que cada mes tenga O/E = 1 (igual que arena.py).
- RACHAS: el intervalo es bootstrap por DÍA de moldeo (las piezas de un mismo día no son independientes).
- La espera solo se cuenta si 0 < espera < 14 días (fuera de eso es error de captura).

Uso: python scripts/causa_raiz/moldeo.py      (solo lectura, imprime tablas)
"""
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from backend.core import run
from coladas import norm_defecto, _defecto_sql

DESDE = "2026-01-01"
N_BOOT = 1000
RNG = np.random.default_rng(20261123)
DEF = {"Sopladura": ["SOPLADURA"], "Tierra suelta": ["TIERRA SUELTA"], "Caído": ["CAIDO"], "Tirado": ["TIRADO"],
       "Porosidad": ["POROSIDAD"], "Contracción": ["RECHUPE", "DEPRESION", "MICRORECHUPE"],
       "Fierro frío": ["FIERRO FRIO"], "Corazón": ["CORAZON DEFECTUOSO", "CORAZON MAL COLOCADO"]}
ESPERA = [(0, 2, "< 2 h"), (2, 6, "2-6 h"), (6, 12, "6-12 h"), (12, 24, "12-24 h"), (24, 48, "1-2 días"), (48, 336, "> 2 días")]


def cargar():
    mo = run("""SELECT IdTicket t, TRIM(MAX(no_parte)) p, MIN(FHrMoldeo) m
                FROM betamega_aleks_idtickethistorial WHERE FHrMoldeo >= :d GROUP BY IdTicket""", {"d": DESDE})
    va = run("""SELECT idTicket t, MIN(FHrVaciado) v FROM betamega_00_ticketsconvaciadodirecto
                WHERE FHrVaciado >= :d GROUP BY idTicket""", {"d": DESDE})
    rz = run(f"""SELECT r.idticket t, {_defecto_sql()} nd FROM rechazo r
                 WHERE r.tipo_rech='Producción' AND r.fecha >= :d""", {"d": DESDE})
    vac = {str(r["t"]): r["v"] for r in va}
    dfs = defaultdict(set)
    for r in rz:
        dfs[str(r["t"])].add(norm_defecto(r["nd"]))
    piezas = []
    for r in mo:
        t = str(r["t"])
        if r["m"] is None:
            continue
        v = vac.get(t)
        esp_h = (v - r["m"]).total_seconds() / 3600 if v else None
        if esp_h is not None and not (0 < esp_h < 336):
            esp_h = None
        piezas.append({"t": t, "p": r["p"], "m": r["m"], "espera": esp_h, "def": dfs.get(t, set())})
    return piezas


def tasas(piezas):
    prod = defaultdict(int); rech = defaultdict(lambda: defaultdict(int))
    for x in piezas:
        prod[x["p"]] += 1
        for k, lista in DEF.items():
            if any(d in x["def"] for d in lista):
                rech[x["p"]][k] += 1
    return {k: {p: rech[p][k] / prod[p] for p in prod} for k in DEF}


def tabla(piezas, tasa, grupo_de, orden, titulo):
    """O/E por grupo con IC 90 % bootstrap por día; lo esperado desestacionalizado por mes."""
    dias = sorted({x["m"].strftime("%Y-%m-%d") for x in piezas})
    di = {d: i for i, d in enumerate(dias)}
    G = {g: j for j, g in enumerate(orden)}
    print(f"\n### {titulo}")
    n_g = np.zeros(len(orden))
    for x in piezas:
        g = grupo_de(x)
        if g in G:
            n_g[G[g]] += 1
    print("piezas: " + " · ".join(f"{g} {int(n):,}" for g, n in zip(orden, n_g)))
    for k, lista in DEF.items():
        O = np.zeros((len(dias), len(orden))); E = np.zeros((len(dias), len(orden)))
        mes_o = defaultdict(float); mes_e = defaultdict(float)
        for x in piezas:
            mes = x["m"].strftime("%Y-%m")
            o = 1.0 if any(d in x["def"] for d in lista) else 0.0
            e = tasa[k].get(x["p"], 0.0)
            mes_o[mes] += o; mes_e[mes] += e
        esc = {m: (mes_o[m] / mes_e[m] if mes_e[m] > 0 and mes_o[m] > 0 else 1.0) for m in mes_e}
        for x in piezas:
            g = grupo_de(x)
            if g not in G:
                continue
            i, j = di[x["m"].strftime("%Y-%m-%d")], G[g]
            O[i, j] += 1.0 if any(d in x["def"] for d in lista) else 0.0
            E[i, j] += tasa[k].get(x["p"], 0.0) * esc[x["m"].strftime("%Y-%m")]
        if O.sum() < 30:
            continue
        oe = O.sum(0) / np.maximum(E.sum(0), 1e-9)
        idx = RNG.integers(0, len(dias), (N_BOOT, len(dias)))
        bo = O[idx].sum(1) / np.maximum(E[idx].sum(1), 1e-9)
        lo, hi = np.quantile(bo, [0.05, 0.95], axis=0)
        celdas = []
        for j, g in enumerate(orden):
            marca = " ▲" if lo[j] > 1 else (" ▼" if hi[j] < 1 else "")
            celdas.append(f"{g}: {oe[j]:.2f} [{lo[j]:.2f}-{hi[j]:.2f}] ({int(O[:, j].sum())}){marca}")
        print(f"  {k:<13} " + " | ".join(celdas))


def grupo_espera(x):
    if x["espera"] is None:
        return None
    for a, b, t in ESPERA:
        if a <= x["espera"] < b:
            return t


def grupo_turno(x):
    h = x["m"].hour
    return "1° (7-15)" if 7 <= h < 15 else ("2° (15-23)" if 15 <= h < 23 else "3° (23-7)")


DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


if __name__ == "__main__":
    pz = cargar()
    con = [x for x in pz if x["espera"] is not None]
    print(f"piezas moldeadas desde {DESDE}: {len(pz):,} · con espera válida: {len(con):,} ({len(con) / len(pz):.0%})")
    if con:
        e = np.array([x["espera"] for x in con])
        print("espera molde->vaciado (h): p10 {:.1f} · mediana {:.1f} · p90 {:.1f}".format(*np.quantile(e, [.1, .5, .9])))
        por_mes = defaultdict(list)
        for x in con:
            por_mes[x["m"].strftime("%Y-%m")].append(x["espera"])
        print("mediana por mes: " + " · ".join(f"{m} {np.median(v):.1f}h ({len(v)})" for m, v in sorted(por_mes.items())))
    ta = tasas(pz)
    tabla(con, ta, grupo_espera, [t for _, _, t in ESPERA], "Espera molde -> vaciado")
    tabla(pz, ta, grupo_turno, ["1° (7-15)", "2° (15-23)", "3° (23-7)"], "Turno de moldeo")
    tabla(pz, ta, lambda x: DIAS[x["m"].weekday()], DIAS, "Día de la semana del moldeo")
    print("\nLectura: O/E > 1 = más rechazo del que tocaría por las partes de ese grupo. [IC 90 %] bootstrap por día;"
          " ▲/▼ = el intervalo no toca 1.")
