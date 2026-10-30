"""Cruce de propiedades de arena (laboratorio, betamega_09) contra rechazo, por DÍA DE MOLDEO.

La arena afecta al molde el día que se moldea, no el día que se vacía: cada pieza se asigna al día
de su FHrMoldeo (aleks). Por día: rechazos observados de cada defecto vs. esperados según las partes
moldeadas ese día (misma lógica O/E que el resto de la vista). Por variable de arena, los días se
clasifican en "algún valor fuera de límite" vs. "todo en límite", y por tercios del promedio diario.

Dos trampas que se corrigen:
- TEMPORADA: la temperatura de la arena sigue la estación y varios defectos tienen tendencia (el
  Caído subió jun-oct 2026, la Contracción fue alta ene-abr). Sin corregir, cualquier variable
  estacional "explica" cualquier defecto con tendencia. Por eso lo esperado se escala para que cada
  mes tenga O/E = 1 y la permutación baraja días solo DENTRO del mismo mes: la señal que queda es
  "días de este mes con arena así vs. el resto del mismo mes".
- AZAR: con ~40 variables y varios defectos, alguna saldría por casualidad. La prueba es de
  permutación por días (respeta que los rechazos vienen en rachas de una misma parte) y solo se
  reportan señales con p < P_MAX; se informa cuántas se esperarían por azar.

Uso exploratorio: python scripts/causa_raiz/arena.py   (imprime todas las señales)
"""
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from backend.core import run
from coladas import norm_defecto, _defecto_sql

DESDE = "2026-01-01"           # el laboratorio captura en betamega_09 desde ene-2026
P_MAX = 0.01                   # umbral de la prueba de permutación
MIN_DIAS = 20                  # días mínimos en cada lado de la comparación
MIN_ESPERADO = 10.0            # rechazos esperados mínimos en el grupo señalado
N_PERM = 2000
_RNG = np.random.default_rng(20261030)


def _num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _por_revisar(v, lo, hi):
    v = v.upper()
    return (v.startswith("AFS") and lo == 48 and hi == 54) or "RETENIDO" in v


def datos_dia(defectos: dict):
    """obs[clave][dia], esp[clave][dia] por día de moldeo; defectos = {clave: [nomDefecto,...]}."""
    tk = run(f"""SELECT IdTicket t, TRIM(MAX(no_parte)) p, DATE(MIN(FHrMoldeo)) d
                 FROM betamega_aleks_idtickethistorial WHERE FHrMoldeo >= '2025-01-01' GROUP BY IdTicket""", {})
    rz = run(f"""SELECT r.idticket t, {_defecto_sql()} nd FROM rechazo r
                 WHERE r.tipo_rech='Producción' AND r.fecha >= '2025-01-01'""", {})
    por_tk = defaultdict(set)
    for r in rz:
        por_tk[str(r["t"])].add(norm_defecto(r["nd"]))
    prod = defaultdict(int)
    rech = defaultdict(lambda: defaultdict(int))
    for r in tk:
        prod[r["p"]] += 1
        for nd in por_tk.get(str(r["t"]), ()):
            rech[r["p"]][nd] += 1
    tasa = {k: {p: sum(rech[p].get(x, 0) for x in nds) / prod[p] for p in prod} for k, nds in defectos.items()}
    obs = {k: defaultdict(float) for k in defectos}
    esp = {k: defaultdict(float) for k in defectos}
    pzs = defaultdict(int)
    for r in tk:
        if r["d"] is None or str(r["d"]) < DESDE:
            continue
        d = str(r["d"])
        pzs[d] += 1
        nds = por_tk.get(str(r["t"]), ())
        for k, lista in defectos.items():
            esp[k][d] += tasa[k][r["p"]]
            if any(x in nds for x in lista):
                obs[k][d] += 1
    return obs, esp, pzs


def datos_arena():
    """{variable: {dia: {"prom": x, "fuera": bool|None, "lo":, "hi":}}} del laboratorio."""
    rows = run("""SELECT TRIM(Variable) v, DATE(Fecha) d, Respuesta, LimiteInferior lo, LimiteSuperior hi
                  FROM betamega_09_variablesdecontrol
                  WHERE Departamento='Laboratorio' AND TipoVariable='Cuantitativo' AND Fecha >= :d""", {"d": DESDE})
    acc = defaultdict(lambda: defaultdict(list))
    lim = {}
    for r in rows:
        x = _num(r["Respuesta"])
        if x is None:
            continue
        lo, hi = _num(r["lo"]), _num(r["hi"])
        acc[r["v"]][str(r["d"])].append((x, lo, hi))
        lim[r["v"]] = (lo, hi)
    out = {}
    for v, dias in acc.items():
        out[v] = {}
        for d, L in dias.items():
            fuera = None
            if not any(_por_revisar(v, lo, hi) for _, lo, hi in L):
                ev = [(x, lo, hi) for x, lo, hi in L if lo is not None and hi is not None]
                fuera = any(not (lo <= x <= hi) for x, lo, hi in ev) if ev else None
            out[v][d] = {"prom": sum(x for x, _, _ in L) / len(L), "fuera": fuera}
    return out, lim


def _perms_por_mes(dias):
    """Matriz N_PERM x len(dias) de índices: cada fila baraja los días solo dentro de su mes."""
    meses = defaultdict(list)
    for i, d in enumerate(dias):
        meses[d[:7]].append(i)
    P = np.tile(np.arange(len(dias)), (N_PERM, 1))
    for pos in meses.values():
        pos = np.array(pos)
        P[:, pos] = pos[np.argsort(_RNG.random((N_PERM, len(pos))), axis=1)]
    return P


def _desestacionalizar(o, e, dias):
    """Escala lo esperado para que cada mes quede en O/E = 1 (quita tendencia y temporada)."""
    e = e.copy()
    meses = defaultdict(list)
    for i, d in enumerate(dias):
        meses[d[:7]].append(i)
    for pos in meses.values():
        so, se = o[pos].sum(), e[pos].sum()
        if se > 0:
            e[pos] *= so / se if so > 0 else 1.0
    return e


def _prueba(o, e, marca, P):
    """O/E del grupo marcado vs. el resto y p de permutación dentro de cada mes (dos colas, escala log)."""
    m = np.asarray(marca, bool)
    if m.sum() < MIN_DIAS or (~m).sum() < MIN_DIAS:
        return None
    eo, ee = o[m].sum(), e[m].sum()
    if ee < MIN_ESPERADO:
        return None
    oe_m = eo / ee
    oe_r = o[~m].sum() / e[~m].sum() if e[~m].sum() else None
    tot_o, tot_e = o.sum(), e.sum()
    obs_stat = abs(np.log((eo + .5) / ee) - np.log((tot_o - eo + .5) / (tot_e - ee)))
    k = int(m.sum())
    mp = m[P]                      # la marca barajada dentro de cada mes
    po, pe = (o[None, :] * mp).sum(1), (e[None, :] * mp).sum(1)
    stat = np.abs(np.log((po + .5) / pe) - np.log((tot_o - po + .5) / (tot_e - pe)))
    p = (1 + (stat >= obs_stat - 1e-12).sum()) / (N_PERM + 1)
    return {"oe": round(float(oe_m), 2), "oe_resto": round(float(oe_r), 2) if oe_r else None,
            "obs": int(eo), "esp": round(float(ee), 1), "dias": k, "p": round(float(p), 4)}


def senales_arena(defectos: dict):
    """{clave: [señal...]} solo con p < P_MAX, ordenadas por p; + resumen de cobertura."""
    obs, esp, pzs = datos_dia(defectos)
    arena, lim = datos_arena()
    dias_mold = sorted(d for d in pzs if pzs[d] >= 20)   # días con producción real
    res = {k: [] for k in defectos}
    probadas = 0
    for v, porDia in arena.items():
        dias = [d for d in dias_mold if d in porDia]
        if len(dias) < 2 * MIN_DIAS:
            continue
        prom = np.array([porDia[d]["prom"] for d in dias])
        t1, t2 = np.quantile(prom, [1 / 3, 2 / 3])
        grupos = []
        fuera = [porDia[d]["fuera"] for d in dias]
        if all(f is not None for f in fuera) and any(fuera):
            grupos.append(("fuera", "fuera de límite", [bool(f) for f in fuera]))
        if t2 > t1:
            grupos.append(("bajo", f"tercio bajo (< {t1:.3g})", list(prom < t1)))
            grupos.append(("alto", f"tercio alto (> {t2:.3g})", list(prom > t2)))
        P = _perms_por_mes(dias)
        for k in defectos:
            o = np.array([obs[k][d] for d in dias])
            e = _desestacionalizar(o, np.array([esp[k][d] for d in dias]), dias)
            for gk, gtxt, marca in grupos:
                probadas += 1
                r = _prueba(o, e, marca, P)
                if r and r["p"] < P_MAX:
                    res[k].append({"variable": v, "grupo": gk, "etiqueta": f"{v}: {gtxt}",
                                   "limite": lim.get(v), **r})
    for k in res:
        res[k].sort(key=lambda s: (s["p"], -abs(np.log(s["oe"] or 1))))
    return res, {"desde": DESDE, "dias": len(dias_mold), "variables": len(arena), "pruebas": probadas,
                 "p_max": P_MAX, "falsos_esperados": round(probadas * P_MAX, 1)}


if __name__ == "__main__":
    DEF = {"caido": ["CAIDO"], "tirado": ["TIRADO"], "sopladura": ["SOPLADURA"], "porosidad": ["POROSIDAD"],
           "contraccion": ["RECHUPE", "DEPRESION", "MICRORECHUPE"], "fierro_frio": ["FIERRO FRIO"],
           "tierra_suelta": ["TIERRA SUELTA"], "corazon": ["CORAZON DEFECTUOSO", "CORAZON MAL COLOCADO"]}
    res, meta = senales_arena(DEF)
    print(meta)
    for k, L in res.items():
        print(f"\n## {k}: {len(L)} señales")
        for s in L[:12]:
            print(f"   O/E {s['oe']:.2f} (resto {s['oe_resto']}) p={s['p']:.4f} · {s['obs']} obs/{s['esp']} esp · {s['dias']} días · {s['etiqueta']}")
