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
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Query

from backend.core import run

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

    return {"semanas": resumenes, "detalle": {**res, "defectos": defectos, "top_80": top}}
