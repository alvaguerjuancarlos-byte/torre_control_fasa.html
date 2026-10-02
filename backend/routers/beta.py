"""
AGENTE BETA — calidad / predictivo (v1, 2026-08-05)

Dos compuertas de datos, nunca mezcladas:
  1. Criticidad histórica — gamamega_01_riesgodefectoparte. Confirmado
     idéntica fila a fila (mismo z_defecto_parte con 10+ decimales) contra
     TabulacionDeCriticidad_junio28_2026.xlsx (tabulación manual de Carlos
     Cruz) — se decidió NO duplicarla en una tabla nueva, leer esta directo.
     FlagCriticidadDefectoParte/FlagTendInterAnual son -1/0/1/NULL, no
     binarios (713/575/87/6 filas resp.) — ver _riesgo_label().
  2. Química en vivo — cscmega_05cquimicosbase (SpectroMAX). ETL congelado
     desde 2025-12-29 (responsable: Jasso). Disponibilidad se calcula en
     vivo contra MAX(u_Fecha) real, nunca hardcodeada ni simulada/inferida
     cuando falta.
"""

import calendar
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Query

from backend.core import run, _evaluar_coladas_ventana, _buscar_protocolos, _REDISENO_COMBOS

router = APIRouter(prefix="/api/beta", tags=["beta"])

QUIMICA_UMBRAL_DIAS = 7  # dato más viejo que esto => compuerta no disponible
UMBRAL_PIEZAS_MES_CONFIABLE = 200  # muestra mínima para considerar evaluable el rechazo de un mes

# Verificado 2026-10-02: nPzasTotal = nPzas2023 + nPzas2025 EXACTO en las 1,381 filas de
# gamamega_01_riesgodefectoparte -- "histórico acumulado"/PrcRchzTotal es la suma de 2023+2025
# específicamente, 2024 NO está incluido (tabulación manual de Carlos Cruz, no una ventana móvil).
# JC preguntó explícitamente "¿es 2026? ¿últimos 6 meses? ¿último año?" -- ninguna de esas, se
# declara literal en la respuesta del tool para que el chat lo diga con precisión en vez de un
# genérico "sin corte de período".
PERIODO_CRITICIDAD = ("Compara 2023 y 2025 únicamente (2024 no está incluido) -- 'total'/"
                       "'histórico acumulado' es la suma de esos dos años, no es ventana móvil "
                       "ni el año más reciente. Tabulación manual (Carlos Cruz), no se actualiza sola.")


def _riesgo_label(flag) -> str:
    """-1 y NULL se reportan igual como 'sin info' (v1, decisión de JC
    2026-08-05) — no se distingue 'riesgo negativo' de 'no calculable'."""
    if flag == 1:
        return "alto"
    if flag == 0:
        return "normal"
    return "sin info"


def _compuerta_quimica() -> dict:
    row = run("SELECT MAX(u_Fecha) AS mx FROM cscmega_05cquimicosbase", {})
    ultima = row[0]["mx"] if row else None
    disponible = bool(ultima) and (datetime.now() - ultima).days <= QUIMICA_UMBRAL_DIAS
    return {
        "disponible": disponible,
        "motivo": None if disponible else "ETL congelado",
        "ultima_actualizacion": ultima.strftime("%Y-%m-%d") if ultima else None,
    }


def _recomendacion_beta(defecto: str, riesgo_label: str, quimica: dict) -> str:
    if riesgo_label != "alto":
        return "Sin acción prioritaria — criticidad histórica no indica riesgo alto para esta parte."

    base = "Aumentar frecuencia de inspección visual — criticidad histórica alta"
    if not quimica["disponible"]:
        base += ", sin corroboración química disponible"
    base += "."

    if defecto and defecto.upper() == "SOPLADURA":
        base += (" Nota: SOPLADURA suele asociarse más a molde/inoculante que a "
                  "composición química de colada — la falta de dato químico no "
                  "es la limitante principal para este defecto.")
    return base


@router.get("/evaluar")
def beta_evaluar(no_parte: str = Query(...)):
    rows = run("""
        SELECT nomDefecto, PrcRchzTotal, zDefectoParte, FlagCriticidadDefectoParte
        FROM gamamega_01_riesgodefectoparte
        WHERE No_Parte = :np
        ORDER BY zDefectoParte DESC
    """, {"np": no_parte})

    quimica = _compuerta_quimica()

    if not rows:
        return {
            "no_parte": no_parte,
            "compuerta_criticidad": {"disponible": False, "motivo": "sin historial en la tabulación",
                                      "periodo": PERIODO_CRITICIDAD},
            "compuerta_quimica": quimica,
            "recomendacion": "Sin datos históricos suficientes para evaluar esta parte.",
        }

    top = rows[0]
    riesgo_label = _riesgo_label(top["FlagCriticidadDefectoParte"])
    defecto = str(top["nomDefecto"] or "")

    return {
        "no_parte": no_parte,
        "compuerta_criticidad": {
            "disponible": True,
            "periodo": PERIODO_CRITICIDAD,
            "defecto_dominante": defecto,
            "pct_rchz_total": round(float(top["PrcRchzTotal"] or 0), 4),
            "z_defecto_parte": round(float(top["zDefectoParte"] or 0), 2),
            "riesgo": top["FlagCriticidadDefectoParte"],
            "riesgo_label": riesgo_label,
        },
        "compuerta_quimica": quimica,
        "recomendacion": _recomendacion_beta(defecto, riesgo_label, quimica),
    }


@router.get("/riesgo-alto")
def beta_riesgo_alto(limit: int = Query(default=20, le=200)):
    rows = run(f"""
        SELECT No_Parte AS no_parte, nomDefecto AS defecto_dominante,
               PrcRchzTotal AS pct_rchz_total, zDefectoParte AS z_defecto_parte
        FROM gamamega_01_riesgodefectoparte
        WHERE FlagCriticidadDefectoParte = 1
        ORDER BY zDefectoParte DESC
        LIMIT {int(limit)}
    """, {})

    return {
        "periodo": PERIODO_CRITICIDAD,
        "partes": [
            {
                "no_parte": r["no_parte"],
                "defecto_dominante": str(r["defecto_dominante"] or ""),
                "pct_rchz_total": round(float(r["pct_rchz_total"] or 0), 4),
                "z_defecto_parte": round(float(r["z_defecto_parte"] or 0), 2),
            }
            for r in rows
        ]
    }


def _riesgo_lote_partes(no_partes: list) -> dict:
    """Compuerta ① de Beta para un lote de partes en una sola query — mismo
    patrón que _tasa_rechazo_partes/_ubicacion_partes (routers/pedidos.py).
    Para cada parte se toma el defecto de mayor z_defecto_parte, igual
    criterio que beta_evaluar."""
    if not no_partes:
        return {}
    placeholders = ",".join(f":p{i}" for i in range(len(no_partes)))
    params = {f"p{i}": p for i, p in enumerate(no_partes)}
    rows = run(f"""
        SELECT No_Parte AS no_parte, nomDefecto, PrcRchzTotal, zDefectoParte, FlagCriticidadDefectoParte
        FROM gamamega_01_riesgodefectoparte
        WHERE No_Parte IN ({placeholders})
        ORDER BY No_Parte, zDefectoParte DESC
    """, params)
    out = {}
    for r in rows:
        np_ = r["no_parte"]
        if np_ in out:
            continue  # ya se tomó la fila de mayor z para esta parte
        out[np_] = {
            "defecto_dominante": str(r["nomDefecto"] or ""),
            "pct_rchz_total": round(float(r["PrcRchzTotal"] or 0), 4),
            "z_defecto_parte": round(float(r["zDefectoParte"] or 0), 2),
            "riesgo": r["FlagCriticidadDefectoParte"],
            "riesgo_label": _riesgo_label(r["FlagCriticidadDefectoParte"]),
        }
    return out


@router.get("/riesgo-lote")
def beta_riesgo_lote(no_partes: str = Query(...)):
    """Mismo dato que /api/beta/evaluar pero para varias partes en una sola
    llamada — usado por el badge contextual de Control del Proceso (no dispara
    N requests por colada)."""
    partes = [p.strip() for p in no_partes.split(",") if p.strip()]
    return _riesgo_lote_partes(partes)


@router.get("/criticas")
def beta_criticas(
    referencia: Optional[str] = Query(default=None),
    horas: int = Query(default=48),
):
    """
    Panel curado de Agente Beta: coladas en ROJO por PC-6 cuya parte dominante
    TAMBIÉN tiene antecedente de riesgo alto (compuerta 1) — la intersección de
    "está fallando ahora" + "ya fallaba antes" es la señal fuerte, no todo rojo
    por igual. Excluye combos REDISEÑO (no accionables por proceso, igual
    criterio que /v3/gestion/alertas). Ordenado por tiempo transcurrido desde
    la detección, más urgente (más tiempo sin atender) primero.

    No existe ningún SLA/deadline real en las fuentes de datos — se reporta
    tiempo transcurrido como proxy de urgencia, nunca un plazo inventado.

    Sin `referencia` cae a "ahora" (mismo motivo que /v3/gestion/coladas en
    main.py — el default viejo "2025-12-19T08:00" escondía datos vivos desde
    que _evaluar_coladas_ventana() se migró a betamega_* el 2026-09-23).
    """
    if referencia is None:
        referencia = datetime.now().strftime("%Y-%m-%dT%H:%M")
    ref_dt = datetime.fromisoformat(referencia)
    coladas = _evaluar_coladas_ventana(referencia, horas)

    rojas_pc6 = [c for c in coladas if c["estados"].get("PC-6") == "ROJO"]
    if not rojas_pc6:
        return {"referencia": referencia, "horas": horas, "n_criticas": 0, "criticas": []}

    u_list = sorted({c["u_colada"] for c in rojas_pc6})
    u_str = ",".join(str(int(u)) for u in u_list)
    rows = run(f"""
        SELECT rb.u_Colada, t.nomDefecto, t.No_Parte, COUNT(*) AS n
        FROM cscmega_01rechazosbyidticket rb
        JOIN cscmega_01resultadoidticket t ON t.idTicket = rb.idTicket
        WHERE rb.u_Colada IN ({u_str}) AND rb.bRechazo = 1 AND t.nomDefecto IS NOT NULL
        GROUP BY rb.u_Colada, t.nomDefecto, t.No_Parte
        ORDER BY rb.u_Colada, n DESC
    """, {})
    top_idx = {}
    for r in rows:
        top_idx.setdefault(r["u_Colada"], {"defecto": r["nomDefecto"], "no_parte": r["No_Parte"]})

    partes = list({v["no_parte"] for v in top_idx.values() if v["no_parte"]})
    riesgo_map = _riesgo_lote_partes(partes)

    defectos_unicos = sorted({v["defecto"] for v in top_idx.values() if v["defecto"]})
    protocolos_por_defecto = {}
    for p in _buscar_protocolos(defectos_unicos):
        protocolos_por_defecto.setdefault(p["defecto"], []).append(p)

    criticas = []
    for c in rojas_pc6:
        top = top_idx.get(c["u_colada"])
        if not top or not top["no_parte"]:
            continue
        defecto, no_parte = top["defecto"], top["no_parte"].strip()
        if (defecto, no_parte) in _REDISENO_COMBOS:
            continue
        riesgo = riesgo_map.get(no_parte)
        if not riesgo or riesgo["riesgo_label"] != "alto":
            continue
        inicio = c.get("inicio_fusion")  # ya viene como str desde _evaluar_coladas_ventana
        inicio_dt = datetime.fromisoformat(inicio) if inicio else None
        horas_transcurridas = round((ref_dt - inicio_dt).total_seconds() / 3600, 1) if inicio_dt else None
        criticas.append({
            "id_colada": c["id_colada"],
            "u_colada": c["u_colada"],
            "horno": c["horno"],
            "inicio_fusion": inicio,
            "horas_transcurridas": horas_transcurridas,
            "pct_rechazo": c.get("pct_rechazo"),
            "defecto": defecto,
            "no_parte": no_parte,
            "z_defecto_parte": riesgo["z_defecto_parte"],
            "pct_rchz_historico": riesgo["pct_rchz_total"],
            "protocolos": protocolos_por_defecto.get(defecto, []),
        })

    criticas.sort(key=lambda x: x["horas_transcurridas"] or 0, reverse=True)
    return {"referencia": referencia, "horas": horas, "n_criticas": len(criticas), "criticas": criticas}


# ══════════════════════════════════════════════════════════════════════════════
# TENDENCIA MENSUAL DE RECHAZO POR PARTE (2026-10-02)
# ══════════════════════════════════════════════════════════════════════════════
# JC preguntó al Agente Beta "qué parte tiene mayor tendencia de rechazo este mes
# (septiembre)" -- el agente dijo que no tenía serie temporal/corte mensual, correcto
# hasta ahora (ni beta_evaluar ni beta_riesgo_alto traen nada por mes, solo criticidad
# histórica acumulada de gamamega_01_riesgodefectoparte). Esta sección agrega esa
# serie mensual real sobre betamega_03_coladasproceso.bRechazo -- PERO esa misma
# columna depende de un pipeline (betamega_02_pks_ticketcorrida) detenido desde
# 2026-07-09 10:09:02 (ver auditoría "Estado real de los 7 Puntos de Control",
# 2026-10-01): desde entonces TODA pieza sale con bRechazo=0, sea cual sea la calidad
# real -- confirmado también en betamega_01_rechazosbyidticket (mismo patrón exacto).
# Por eso _mes_confiable() es el primer paso, no un detalle: un mes con muestra grande
# y CERO rechazos es estadísticamente imposible (meses sanos traen 7-22%), así que se
# marca "sin dato confiable" en vez de devolver un 0% que se leería como buena noticia
# falsa. El chequeo es dinámico (agregado en vivo, no una fecha hardcodeada) -- en
# cuanto IT reactive el pipeline, el mes vuelve a evaluarse solo sin tocar este código.

def _rango_mes(mes: str) -> tuple:
    anio, m = (int(x) for x in mes.split("-"))
    ultimo_dia = calendar.monthrange(anio, m)[1]
    return f"{anio}-{m:02d}-01 00:00:00", f"{anio}-{m:02d}-{ultimo_dia:02d} 23:59:59"


def _mes_desplazado(mes: str, n: int) -> str:
    """mes='2026-09', n=3 -> '2026-06' (n meses antes)."""
    anio, m = (int(x) for x in mes.split("-"))
    m -= n
    while m <= 0:
        m += 12
        anio -= 1
    return f"{anio}-{m:02d}"


def _mes_confiable(d_ini: str, d_fin: str) -> dict:
    row = run("""
        SELECT COUNT(*) AS total, SUM(bRechazo) AS n_rechazo
        FROM betamega_03_coladasproceso
        WHERE FHrVaciado BETWEEN :d AND :h
    """, {"d": d_ini, "h": d_fin})
    r = row[0] if row else {}
    total = int(r.get("total") or 0)
    n_rechazo = int(r.get("n_rechazo") or 0)
    return {
        "confiable": total >= UMBRAL_PIEZAS_MES_CONFIABLE and n_rechazo > 0,
        "total_piezas": total,
        "n_rechazo": n_rechazo,
    }


def tendencia_rechazo_mensual(mes: str, meses_baseline: int = 3, min_piezas_mes: int = 5, limit: int = 15) -> dict:
    """Compara la tasa de rechazo de cada No_Parte en `mes` contra su promedio de los
    `meses_baseline` meses anteriores -- la parte con mayor delta positivo es la de
    "mayor tendencia de rechazo" ese mes. Devuelve `confiable: False` (sin lista de
    partes) si el mes evaluado o la ventana de comparación no tienen veredicto de
    rechazo confiable -- ver docstring de sección arriba."""
    d_ini, d_fin = _rango_mes(mes)
    chk_mes = _mes_confiable(d_ini, d_fin)
    if not chk_mes["confiable"]:
        motivo = (
            f"{chk_mes['n_rechazo']} rechazos de {chk_mes['total_piezas']} piezas en {mes} -- "
            "bRechazo depende de un pipeline (betamega_02_pks_ticketcorrida) detenido desde "
            "2026-07-09; no hay veredicto de calidad confiable para este mes todavía."
            if chk_mes["total_piezas"] >= UMBRAL_PIEZAS_MES_CONFIABLE else
            f"Muestra insuficiente para {mes}: solo {chk_mes['total_piezas']} piezas registradas."
        )
        return {"mes": mes, "confiable": False, "motivo": motivo, "baseline": None, "partes": []}

    mes_base_ini = _mes_desplazado(mes, meses_baseline)
    mes_base_fin = _mes_desplazado(mes, 1)
    base_d_ini, _ = _rango_mes(mes_base_ini)
    _, base_d_fin = _rango_mes(mes_base_fin)
    chk_base = _mes_confiable(base_d_ini, base_d_fin)
    if not chk_base["confiable"]:
        motivo = (
            f"El mes evaluado ({mes}) sí tiene dato confiable, pero la ventana de comparación "
            f"({mes_base_ini} a {mes_base_fin}) no -- {chk_base['n_rechazo']} rechazos de "
            f"{chk_base['total_piezas']} piezas, mismo pipeline detenido. No se puede calcular una "
            "tendencia sin una base confiable para comparar."
        )
        return {"mes": mes, "confiable": False, "motivo": motivo,
                "baseline": f"{mes_base_ini} a {mes_base_fin}", "partes": []}

    rows_mes = run("""
        SELECT No_Parte AS no_parte, COUNT(*) AS total, SUM(bRechazo) AS rechazos
        FROM betamega_03_coladasproceso
        WHERE FHrVaciado BETWEEN :d AND :h AND No_Parte IS NOT NULL
        GROUP BY No_Parte HAVING total >= :minp
    """, {"d": d_ini, "h": d_fin, "minp": min_piezas_mes})
    rows_base = run("""
        SELECT No_Parte AS no_parte, COUNT(*) AS total, SUM(bRechazo) AS rechazos
        FROM betamega_03_coladasproceso
        WHERE FHrVaciado BETWEEN :d AND :h AND No_Parte IS NOT NULL
        GROUP BY No_Parte HAVING total >= :minp
    """, {"d": base_d_ini, "h": base_d_fin, "minp": min_piezas_mes})
    base_idx = {r["no_parte"]: r for r in rows_base}

    partes = []
    for r in rows_mes:
        base = base_idx.get(r["no_parte"])
        if not base:
            continue  # sin piezas suficientes en el baseline -- no se puede medir tendencia
        tasa_mes  = float(r["rechazos"]) / float(r["total"]) * 100
        tasa_base = float(base["rechazos"]) / float(base["total"]) * 100
        partes.append({
            "no_parte":          r["no_parte"],
            "piezas_mes":        int(r["total"]),
            "rechazos_mes":      int(r["rechazos"]),
            "tasa_mes_pct":      round(tasa_mes, 1),
            "piezas_baseline":   int(base["total"]),
            "tasa_baseline_pct": round(tasa_base, 1),
            "delta_pct":         round(tasa_mes - tasa_base, 1),
        })
    partes.sort(key=lambda x: x["delta_pct"], reverse=True)

    return {
        "mes": mes,
        "confiable": True,
        "baseline": f"{mes_base_ini} a {mes_base_fin}",
        "partes": partes[:limit],
    }


@router.get("/tendencia-mensual")
def beta_tendencia_mensual(
    mes: str = Query(..., description="YYYY-MM, mes a evaluar, ej. 2026-09"),
    meses_baseline: int = Query(default=3, ge=1, le=12),
    min_piezas_mes: int = Query(default=5, ge=1),
    limit: int = Query(default=15, le=100),
):
    return tendencia_rechazo_mensual(mes, meses_baseline, min_piezas_mes, limit)
