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

from datetime import datetime
from fastapi import APIRouter, Query

from backend.core import run, _evaluar_coladas_ventana, _buscar_protocolos, _REDISENO_COMBOS

router = APIRouter(prefix="/api/beta", tags=["beta"])

QUIMICA_UMBRAL_DIAS = 7  # dato más viejo que esto => compuerta no disponible


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
            "compuerta_criticidad": {"disponible": False, "motivo": "sin historial en la tabulación"},
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
    referencia: str = Query(default="2025-12-19T08:00"),
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
    """
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
