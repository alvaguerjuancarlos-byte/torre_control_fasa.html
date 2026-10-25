"""
Pronóstico semanal de rechazo de CSC (2026-10-25) -- ia_fasa.gamamega_01_paretopesoperiodo.

Qué es: el pronóstico que CSC ya calcula cada semana sobre el programa semanal
(gamamega_01_programaperiodo, Periodo YYYYWW): por parte programada trae nPzasProg,
KgsPzasProgAsProgProd y los kg de rechazo esperados por defecto (KgsDefecto), ordenados en
Pareto. Implica ~15-18 % de rechazo por semana. Las tasas por parte×defecto se parecen a
PrcRchz2025 de gamamega_01_riesgodefectoparte pero no son idénticas (DEPRESION×42091101:
16.3 % vs 19.5 %): la ventana histórica que usa CSC no está documentada. Aquí se muestra tal
cual, sin recalcularlo con la tasa shrunk de la Torre (eso es Riesgo del Plan, mensual).

% esperado = KgsDefecto / KgsPzasProgAsProgProd, ambos en la base de CSC (esos kg vienen
~6-12 % arriba de piezas × PesoKgs, ver plan_corex.py). No se usa PrcContribucion: en la
semana 202640 suma 2.0.

Semana 202640 = dos cargas que NO coinciden (165 combinaciones parte×defecto con piezas/kg
distintos, partes repetidas con espacios al final, Seq reiniciado). No se suman: por
parte×defecto se toma la fila de la carga más reciente (Seq más alto) y la semana se marca
`carga_duplicada` para que el frontend avise. Reportado a IT (reporte del 13-oct).

No se compara contra rechazo real: bRechazo está congelado desde 2026-07-09
(core.meses_rechazo_no_confiables), así que las 13 semanas cargadas (desde la 202632)
caen en el hueco.

Comparación CSC vs Torre (pedida por JC el mismo día): por parte programada, el % que
espera CSC contra la tasa shrunk de la Torre (core.historial_rechazo_partes +
core.tasa_shrunk, k=75, 12 meses sin los meses no confiables -- misma tasa que Riesgo del
Plan). CSC es por kg y la Torre por pieza; dentro de una parte es lo mismo (peso fijo por
pieza). Las dos se apoyan en historia anterior al 9-jul, así que la diferencia dice dónde
DISCREPAN los métodos, no cuál acierta. Se ordena por kg de diferencia (dif pp × kg
programados), que es lo que pesa en la semana, no por pp crudos.
"""
import time
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Query

from backend.core import run, historial_rechazo_partes, tasa_shrunk
from backend.plan_corex import _proceso_partes

# La tasa de la Torre usa historia que ya no cambia (rechazo congelado) y la consulta
# agrega ~1 año de tickets: se recalcula a lo más una vez por hora.
_HIST_CACHE: dict = {}
_HIST_TTL_SEG = 3600
_K_SHRINK = 75
_DIF_RELEVANTE_PP = 5.0


def _tasas_torre() -> tuple:
    hoy = date.today()
    hit = _HIST_CACHE.get(hoy)
    if hit and time.time() - hit[0] < _HIST_TTL_SEG:
        return hit[1]
    agg_raw = historial_rechazo_partes((hoy - timedelta(days=365)).isoformat() + " 00:00:00",
                                       hoy.isoformat() + " 23:59:59")
    agg: dict = {}
    for parte, v in agg_raw.items():  # No_Parte con espacios al final en algunas fuentes
        a = agg.setdefault(parte.strip(), {"n": 0, "rechazos": 0})
        a["n"] += v["n"]
        a["rechazos"] += v["rechazos"]
    total_n = sum(v["n"] for v in agg.values())
    global_rate = sum(v["rechazos"] for v in agg.values()) / total_n if total_n else 0.15
    _HIST_CACHE.clear()
    _HIST_CACHE[hoy] = (time.time(), (agg, global_rate))
    return agg, global_rate


def _comparar(filas: list) -> dict:
    agg, global_rate = _tasas_torre()
    partes: dict = {}
    for f in filas:
        p = partes.setdefault(f["no_parte"], {"kg_prog": float(f["kg_prog"] or 0),
                                              "piezas_prog": int(f["nPzasProg"] or 0), "kg_def": 0.0})
        p["kg_def"] += float(f["kg_def"] or 0)
    out = []
    for parte, p in partes.items():
        h = agg.get(parte, {"n": 0, "rechazos": 0})
        pct_torre, w = tasa_shrunk(h["n"], h["rechazos"], global_rate, k=_K_SHRINK)
        pct_csc = round(p["kg_def"] / p["kg_prog"] * 100, 1) if p["kg_prog"] else None
        dif = round(pct_torre - pct_csc, 1) if pct_csc is not None else None
        out.append({
            "no_parte": parte, "piezas_prog": p["piezas_prog"], "kg_prog": round(p["kg_prog"]),
            "pct_csc": pct_csc, "pct_torre": pct_torre, "dif_pp": dif,
            "kg_dif": round(dif / 100 * p["kg_prog"]) if dif is not None else None,
            "piezas_historia": h["n"], "peso_propio": w,
        })
    out.sort(key=lambda x: -abs(x["kg_dif"] or 0))
    kp = sum(x["kg_prog"] for x in out)
    kg_torre = sum(x["pct_torre"] / 100 * x["kg_prog"] for x in out)
    return {
        "pct_torre_semana": round(kg_torre / kp * 100, 1) if kp else None,
        "kg_rechazo_torre": round(kg_torre),
        "tasa_planta_pct": round(global_rate * 100, 1),
        "n_partes_dif_relevante": sum(1 for x in out if x["dif_pp"] is not None and abs(x["dif_pp"]) >= _DIF_RELEVANTE_PP),
        "umbral_pp": _DIF_RELEVANTE_PP,
        "partes": out,
    }

router = APIRouter(tags=["pronostico-semanal"])


def _rango_semana(periodo: int) -> tuple:
    lunes = date.fromisocalendar(periodo // 100, periodo % 100, 1)
    return lunes, date.fromordinal(lunes.toordinal() + 6)


def _filas_semanales() -> dict:
    """{periodo: (filas deduplicadas, carga_duplicada)} -- una fila por parte×defecto."""
    rows = run("""
        SELECT Periodo, TRIM(No_Parte) AS no_parte, nPzasProg, KgsPzasProgAsProgProd AS kg_prog,
               TRIM(nomDefecto) AS defecto, KgsDefecto AS kg_def, Seq
        FROM gamamega_01_paretopesoperiodo
        WHERE Periodo >= 100000
    """, {})
    por_semana: dict = {}
    for r in rows:
        sem = por_semana.setdefault(r["Periodo"], {"combos": {}, "duplicada": False})
        k = (r["no_parte"], r["defecto"])
        prev = sem["combos"].get(k)
        if prev is not None:
            if (prev["nPzasProg"], round(float(prev["kg_def"]), 3)) != (r["nPzasProg"], round(float(r["kg_def"]), 3)):
                sem["duplicada"] = True
            if prev["Seq"] >= r["Seq"]:
                continue
        sem["combos"][k] = r
    return {p: (list(s["combos"].values()), s["duplicada"]) for p, s in por_semana.items()}


def _resumen(periodo: int, filas: list, duplicada: bool) -> dict:
    kg_prog = {}
    for f in filas:  # kg/piezas programados son por parte: repetidos en cada defecto
        kg_prog[f["no_parte"]] = (float(f["kg_prog"] or 0), int(f["nPzasProg"] or 0))
    kp = sum(v[0] for v in kg_prog.values())
    kd = sum(float(f["kg_def"] or 0) for f in filas)
    ini, fin = _rango_semana(periodo)
    return {
        "periodo": periodo, "semana": periodo % 100, "desde": ini.isoformat(), "hasta": fin.isoformat(),
        "partes": len(kg_prog), "piezas_prog": sum(v[1] for v in kg_prog.values()),
        "kg_prog": round(kp), "kg_rechazo_esperado": round(kd),
        "pct_esperado": round(kd / kp * 100, 1) if kp else None,
        "carga_duplicada": duplicada,
    }


@router.get("/api/pronostico-semanal")
def pronostico_semanal(periodo: Optional[int] = Query(default=None, description="YYYYWW; por omisión la semana en curso o la última cargada")):
    semanas = _filas_semanales()
    if not semanas:
        return {"semanas": [], "detalle": None}
    resumenes = [_resumen(p, *semanas[p]) for p in sorted(semanas)]

    if periodo not in semanas:
        iso = date.today().isocalendar()
        actual = iso[0] * 100 + iso[1]
        # La semana en curso; si no está cargada, la más reciente anterior
        periodo = max((p for p in semanas if p <= actual), default=max(semanas))
    filas, duplicada = semanas[periodo]
    res = _resumen(periodo, filas, duplicada)
    kd_total = res["kg_rechazo_esperado"] or 1

    por_defecto: dict = {}
    for f in filas:
        d = por_defecto.setdefault(f["defecto"], {"defecto": f["defecto"], "kg": 0.0, "partes": set()})
        d["kg"] += float(f["kg_def"] or 0)
        d["partes"].add(f["no_parte"])
    defectos = sorted(por_defecto.values(), key=lambda d: -d["kg"])
    acum = 0.0
    for d in defectos:
        acum += d["kg"]
        d.update(kg=round(d["kg"]), partes=len(d["partes"]),
                 pct=round(d["kg"] / kd_total * 100, 1), pct_acum=round(acum / kd_total * 100, 1))

    combos = sorted(filas, key=lambda f: -float(f["kg_def"] or 0))
    acum, top = 0.0, []
    for f in combos:
        kg = float(f["kg_def"] or 0)
        kp = float(f["kg_prog"] or 0)
        top.append({
            "no_parte": f["no_parte"], "defecto": f["defecto"], "piezas_prog": int(f["nPzasProg"] or 0),
            "kg_esperado": round(kg, 1), "pct_de_la_parte": round(kg / kp * 100, 1) if kp else None,
            "pct_acum": round((acum + kg) / kd_total * 100, 1),
        })
        acum += kg
        if acum / kd_total >= 0.8:
            break

    return {"semanas": resumenes,
            "detalle": {**res, "defectos": defectos, "top_80": top, "vs_torre": _comparar(filas)}}


# ══════════════════════════════════════════════════════════════════════════════
# Plan semanal: programado vs moldeado/vaciado por parte (2026-10-25)
# ══════════════════════════════════════════════════════════════════════════════
# Programa: gamamega_01_programaperiodo con Periodo YYYYWW (semana ISO; contra el vaciado
# real empata mejor con desfase 0 que con ±1). Avance real: piezas VACIADAS de
# betamega_00_ticketsconvaciadodirecto (completa jul-oct) -- NO piezas terminadas
# (betamega_08.FHrLIMP, criterio del seguimiento mensual), que hoy registra 30-190 piezas
# por semana contra ~900 vaciadas. Moldeadas (betamega_aleks_idtickethistorial.FHrMoldeo)
# como dato adicional. FHrVaciado es hora de registro en lote: sirve a nivel semana.
#
# % cumplimiento = Σ mín(vaciadas, programadas) / Σ programadas: lo vaciado de más de una
# parte no compensa lo que faltó de otra; lo vaciado de partes fuera del programa se
# reporta aparte. Semana en curso: se compara contra lo esperado a la fecha (programa ×
# días transcurridos / 6, lunes a sábado).
# Semana 202640: partes repetidas con valores distintos (cargas duplicadas en Corex) -- se
# toma el mayor y se marca la semana.
_PLAN_MIN_PERIODO = 202630
_DIAS_SEMANA = 6


def _programa_semanal() -> dict:
    rows = run("""
        SELECT Periodo, TRIM(No_Parte) AS no_parte, nPzasProg, KgsPzasProgAsProgProd AS kg
        FROM gamamega_01_programaperiodo WHERE Periodo >= :p
    """, {"p": _PLAN_MIN_PERIODO})
    out: dict = {}
    for r in rows:
        sem = out.setdefault(r["Periodo"], {"partes": {}, "duplicada": False})
        n = int(r["nPzasProg"] or 0)
        prev = sem["partes"].get(r["no_parte"])
        if prev is not None:
            if prev["prog"] != n:
                sem["duplicada"] = True
            if prev["prog"] >= n:
                continue
        sem["partes"][r["no_parte"]] = {"prog": n, "kg": float(r["kg"] or 0)}
    return out


_REAL_CACHE: dict = {}
_REAL_TTL_SEG = 1800  # ticketsconvaciadodirecto sin índice en FHrVaciado: ~30 s por consulta


def _real_semanal(d1: date, d2: date) -> tuple:
    """({(semana ISO, parte): vaciadas}, {(semana ISO, parte): moldeadas})"""
    hit = _REAL_CACHE.get((d1, d2))
    if hit and time.time() - hit[0] < _REAL_TTL_SEG:
        return hit[1]
    out = _real_semanal_bd(d1, d2)
    _REAL_CACHE.clear()
    _REAL_CACHE[(d1, d2)] = (time.time(), out)
    return out


def precalentar_plan_semanal():
    prog = _programa_semanal()
    if prog:
        _real_semanal(_rango_semana(min(prog))[0], _rango_semana(max(prog))[1])


def _real_semanal_bd(d1: date, d2: date) -> tuple:
    params = {"d": datetime.combine(d1, datetime.min.time()), "h": datetime.combine(d2, datetime.max.time())}
    vac = run("""
        SELECT YEARWEEK(FHrVaciado, 3) AS w, TRIM(no_parte) AS p, COUNT(*) AS n
        FROM betamega_00_ticketsconvaciadodirecto WHERE FHrVaciado BETWEEN :d AND :h GROUP BY w, p
    """, params)
    mol = run("""
        SELECT YEARWEEK(FHrMoldeo, 3) AS w, TRIM(no_parte) AS p, COUNT(*) AS n
        FROM betamega_aleks_idtickethistorial WHERE FHrMoldeo BETWEEN :d AND :h GROUP BY w, p
    """, params)
    return ({(r["w"], r["p"]): int(r["n"]) for r in vac}, {(r["w"], r["p"]): int(r["n"]) for r in mol})


def _avance_semana(periodo: int) -> Optional[float]:
    """Fracción de la semana transcurrida (lunes-sábado); None si ya cerró o no empieza."""
    ini, _ = _rango_semana(periodo)
    hoy = date.today()
    dias = (hoy - ini).days + 1
    if dias < 1:
        return 0.0
    if dias > 7:
        return None
    return min(dias, _DIAS_SEMANA) / _DIAS_SEMANA


@router.get("/api/plan-semanal")
def plan_semanal(periodo: Optional[int] = Query(default=None, description="YYYYWW; por omisión la semana en curso")):
    prog = _programa_semanal()
    if not prog:
        return {"semanas": [], "detalle": None}
    periodos = sorted(prog)
    d1 = _rango_semana(periodos[0])[0]
    d2 = _rango_semana(periodos[-1])[1]
    vac, mol = _real_semanal(d1, d2)

    semanas = []
    for p in periodos:
        partes = prog[p]["partes"]
        tp = sum(x["prog"] for x in partes.values())
        en_prog = sum(min(x["prog"], vac.get((p, k), 0)) for k, x in partes.items())
        vac_total = sum(n for (w, _), n in vac.items() if w == p)
        fuera = sum(n for (w, k), n in vac.items() if w == p and k not in partes)
        ini, fin = _rango_semana(p)
        av = _avance_semana(p)
        semanas.append({
            "periodo": p, "semana": p % 100, "desde": ini.isoformat(), "hasta": fin.isoformat(),
            "partes": len(partes), "piezas_prog": tp, "vaciadas_en_programa": en_prog,
            "vaciadas_total": vac_total, "vaciadas_fuera_programa": fuera,
            "moldeadas_total": sum(n for (w, _), n in mol.items() if w == p),
            "pct_cumplimiento": round(en_prog / tp * 100, 1) if tp else None,
            "en_curso": av is not None, "avance_semana": av,
            "carga_duplicada": prog[p]["duplicada"],
        })

    if periodo not in prog:
        iso = date.today().isocalendar()
        actual = iso[0] * 100 + iso[1]
        periodo = max((p for p in periodos if p <= actual), default=periodos[-1])
    sem = next(s for s in semanas if s["periodo"] == periodo)
    partes = prog[periodo]["partes"]
    procesos = _proceso_partes(list(partes))
    av = sem["avance_semana"]
    filas = []
    for k, x in partes.items():
        v = vac.get((periodo, k), 0)
        esperado = x["prog"] * av if av is not None else x["prog"]
        pct = round(v / x["prog"] * 100, 1) if x["prog"] else None
        ratio = v / esperado if esperado else None
        filas.append({
            "no_parte": k, "proceso": procesos.get(k), "programadas": x["prog"],
            "moldeadas": mol.get((periodo, k), 0), "vaciadas": v, "pct_cumplimiento": pct,
            "esperadas_a_la_fecha": round(esperado, 1) if av is not None else None,
            "estado": None if ratio is None else "VERDE" if ratio >= 1 else "AMBAR" if ratio >= 0.8 else "ROJO",
        })
    filas.sort(key=lambda f: (f["vaciadas"] / f["programadas"] if f["programadas"] else 9, -f["programadas"]))
    fuera = sorted(({"no_parte": k, "vaciadas": n} for (w, k), n in vac.items() if w == periodo and k not in partes),
                   key=lambda f: -f["vaciadas"])
    return {"semanas": semanas, "detalle": {**sem, "partes_detalle": filas, "fuera_programa": fuera[:20],
                                             "n_partes_fuera_programa": len(fuera)}}
