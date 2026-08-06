"""
Torre de Control FASA — FastAPI backend (READ-ONLY)
Corre localmente; el HTML apunta a http://localhost:8000
"""

import os
import json
import math
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple, List
from dataclasses import dataclass
import numpy as np
import anthropic
from anthropic import beta_tool
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# ── Mapeo de defectos a 5 familias ───────────────────────────────────────────
FAMILIA_SQL = """
CASE
  WHEN nomDefecto IN ('SOPLADURA','POROSIDAD','CARBON LUSTROSO','INCLUSION',
                      'PENETRADO','PROYECCION METALICA','GOTA DE FIERRO FRIO')
       THEN 'Gases / porosidad'
  WHEN nomDefecto IN ('DEPRESION','RECHUPE','MICROSETRUCTURA','MICRORECHUPE',
                      'HINCHADO','FISURADO','CHILL (DUREZA)','DUREZA',
                      'PROP  MEC Y QUIMICA','MICROESTRUCTURAS')
       THEN 'Micro / depresion'
  WHEN nomDefecto IN ('NO LLENO','FIERRO FRIO','TAPADO','PERDIDA POR COMPLEMENTO',
                      'PERDIDA POR COMPLEME','FUGA PIEZA (METAL)','FUGA DE PIEZA',
                      'FUGA PIEZA (CHAPLET)')
       THEN 'Llenado deficiente'
  WHEN nomDefecto IN ('TIERRA SUELTA','CORAZON DEFECTUOSO','CORAZON MAL COLOCADO',
                      'TIRADO','CAIDO','DARTA','ARRANQUE DE METAL')
       THEN 'Arena / corazon'
  WHEN nomDefecto IN ('FUERA DE DIMENSIONES','MAL MAQUINADO','MARCAS MECANICAS',
                      'MAL TERMINADO','CRUZADO','HERRAMENTAL','ACABADO SUPERFICIAL',
                      'ESPESOR BAJO DE PIEZ','MODELOS','QUEBRADO','CARGADO')
       THEN 'Dimensional / maquinado'
  ELSE 'Otros'
END
"""

HOST   = os.environ["DB_HOST"]
PORT   = int(os.environ.get("DB_PORT", 3306))
USER   = os.environ["DB_USER"]
PWD    = os.environ["DB_PWD"]
SCHEMA = os.environ.get("DB_SCHEMA", "ia_fasa")

engine = create_engine(
    f"mysql+pymysql://{USER}:{PWD}@{HOST}:{PORT}/{SCHEMA}?charset=utf8mb4",
    pool_pre_ping=True,
    pool_size=5,
)

app = FastAPI(title="Torre de Control FASA", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1", "null"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ── Modelo ML v2 (RF + calibrador Platt + lookup de partes) ──────────────────
_MODELS_DIR    = Path(__file__).parent / "models"
_ML_MODEL      = None
_ML_CALIBRATOR = None   # LogisticRegression Platt sobre OOF del RF
_ML_META       = None
_PARTE_LOOKUP  = None   # {no_parte: {rate_prior, peso_kgs, log_peso, sin_hist}}

def _load_ml():
    global _ML_MODEL, _ML_CALIBRATOR, _ML_META, _PARTE_LOOKUP
    try:
        import joblib
        _ML_MODEL     = joblib.load(_MODELS_DIR / "rf_rechazo_v2.joblib")
        _ML_META      = json.loads((_MODELS_DIR / "feature_meta.json").read_text())
        _PARTE_LOOKUP = json.loads((_MODELS_DIR / "parte_lookup.json").read_text(encoding="utf-8"))
        cal_path = _MODELS_DIR / "rf_calibrator.joblib"
        if cal_path.exists():
            _ML_CALIBRATOR = joblib.load(cal_path)
            print(f"[ML] modelo + calibrador Platt cargados — AUC cv={_ML_META['auc_cv']}  partes={len(_PARTE_LOOKUP)}")
        else:
            print(f"[ML] modelo cargado (sin calibrador) — AUC cv={_ML_META['auc_cv']}  partes={len(_PARTE_LOOKUP)}")
    except Exception as e:
        print(f"[ML] sin modelo ({e}) — endpoint /v2/ml/* devolvera null")

_load_ml()


def ml_score(no_parte: str,
             temp_ok: Optional[float] = None,
             pc5_cumple: Optional[float] = None,
             mes: Optional[int] = None,
             horno: Optional[str] = None,
             rate_prior_override: Optional[float] = None) -> Optional[float]:
    """Devuelve prob_rechazo calibrada (0-1). None si el modelo no está cargado.
    rate_prior_override: tasa observada del periodo actual (0-1). Si se pasa,
    reemplaza el rate_2024 del lookup — hace el modelo independiente del año."""
    if _ML_MODEL is None or _PARTE_LOOKUP is None or _ML_META is None:
        return None
    S     = _ML_META["sentinel"]
    gm    = _ML_META["global_mean"]
    gmp   = _ML_META["global_median_peso"]
    parte = _PARTE_LOOKUP.get(no_parte, {})
    rate_feature = rate_prior_override if rate_prior_override is not None \
                   else float(parte.get("rate_prior", gm))
    vals  = {
        "pct_quimica_ok":       S,
        "n_quimica_disponible": S,
        "temp_ok_n":            S if temp_ok    is None else float(temp_ok),
        "pc5_cumple_n":         S if pc5_cumple is None else float(pc5_cumple),
        "inoc_s_n":             S,
        "mes":                  S if mes        is None else float(mes),
        "rate_parte_2024":      rate_feature,
        "rate_parte_2024_flag": 0.0 if rate_prior_override is not None else float(parte.get("sin_hist", 1)),
        "log_peso":             float(parte.get("log_peso",   np.log1p(gmp))),
    }
    import pandas as pd
    row = {}
    for col in _ML_META["feature_columns"]:
        if col.startswith("horno_"):
            h_val = col.replace("horno_", "")
            row[col] = 1.0 if horno is not None and str(horno) == h_val else 0.0
        else:
            row[col] = vals.get(col, _ML_META["sentinel"])
    X = pd.DataFrame([row])[_ML_META["feature_columns"]]
    raw_prob = float(_ML_MODEL.predict_proba(X)[0, 1])
    if _ML_CALIBRATOR is not None:
        cal_prob = float(_ML_CALIBRATOR.predict_proba(np.array([[raw_prob]]))[0, 1])
        return round(cal_prob, 4)
    return round(raw_prob, 4)


def run(sql: str, params: dict = None):
    with engine.connect() as cx:
        result = cx.execute(text(sql), params or {})
        keys = result.keys()
        return [dict(zip(keys, row)) for row in result.fetchall()]


# ── Cache mensual de alfamega: carga única al arrancar ────────────────────────
# alfamega no tiene índice en FHrVaciado → cada scan toma ~5s.
# Precargamos todos los meses (2023-2024) para que los endpoints sean O(1).
_ALFAMEGA_MONTHLY: dict = {}

def _preload_alfamega():
    global _ALFAMEGA_MONTHLY
    try:
        rows = run("""
            SELECT DATE_FORMAT(FHrVaciado,'%Y-%m') AS mk,
                   COUNT(*) AS n, SUM(bRechazo) AS rec
            FROM alfamega_01_rechazosbyidticket
            GROUP BY mk
        """, {})
        _ALFAMEGA_MONTHLY = {r["mk"]: {"n": int(r["n"] or 0), "rec": int(r["rec"] or 0)}
                              for r in rows}
        print(f"[alfamega] cache mensual: {len(_ALFAMEGA_MONTHLY)} meses")
    except Exception as e:
        print(f"[alfamega] preload falló ({e}) — lookups al vuelo")

_preload_alfamega()


def rango(desde: str = None, hasta: str = None):
    """Devuelve (desde_str, hasta_str) para usar en queries.
    Acepta 'YYYY-MM-DD', 'YYYY-MM-DD HH:MM' o 'YYYY-MM-DD HH:MM:SS'.
    Si es solo fecha expande a día completo (comportamiento legacy).
    """
    if desde and hasta:
        d = desde if len(desde) > 10 else desde + " 00:00:00"
        h = hasta  if len(hasta)  > 10 else hasta  + " 23:59:59"
        if len(d) == 16: d += ":00"
        if len(h) == 16: h += ":00"
    else:
        # fallback: último mes de datos disponibles
        d = "2025-12-01 00:00:00"
        h = "2025-12-31 23:59:59"
    return d, h


# ── UI ───────────────────────────────────────────────────────────────────────

FRONTEND = Path(__file__).parent.parent / "frontend" / "torre_control.html"

@app.get("/", response_class=FileResponse)
def serve_ui():
    return FileResponse(
        FRONTEND,
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


# ── Health + rango disponible ────────────────────────────────────────────────

@app.get("/health")
def health():
    try:
        with engine.connect() as cx:
            cx.execute(text("SELECT 1"))
        return {"status": "ok", "schema": SCHEMA}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/rango-datos")
def rango_datos():
    """Retorna la fecha mínima y máxima disponible en la BD."""
    rows = run("""
        SELECT
            DATE(MIN(u_Fecha)) AS fecha_min,
            DATE(MAX(u_Fecha)) AS fecha_max,
            YEAR(MIN(u_Fecha)) AS anio_min,
            YEAR(MAX(u_Fecha)) AS anio_max
        FROM cscmega_01rechazosbyidticket
    """)
    return rows[0] if rows else {}


# ── Panel 1: KPIs ────────────────────────────────────────────────────────────

@app.get("/produccion/kpis")
def produccion_kpis(
    desde: str = Query(default=None),
    hasta: str = Query(default=None),
):
    d, h = rango(desde, hasta)
    rows = run("""
        SELECT
            COUNT(*)                        AS total_tickets,
            SUM(bRechazo)                   AS total_rechazos,
            ROUND(AVG(bRechazo)*100, 2)     AS pct_rechazo,
            COUNT(DISTINCT u_Colada)        AS coladas_unicas,
            COUNT(DISTINCT u_Horno)         AS hornos_activos,
            MIN(u_Fecha)                    AS primer_ticket,
            MAX(u_Fecha)                    AS ultimo_ticket
        FROM cscmega_01rechazosbyidticket
        WHERE u_Fecha BETWEEN :d AND :h
    """, {"d": d, "h": h})
    return rows[0] if rows else {}


# ── Panel 1: Bitácora ────────────────────────────────────────────────────────

@app.get("/produccion/turno")
def produccion_turno(
    desde: str = Query(default=None),
    hasta: str = Query(default=None),
    horno: int = Query(default=None),
):
    d, h = rango(desde, hasta)
    filtro_horno = "AND t.u_Horno = :horno" if horno else ""
    rows = run(f"""
        SELECT
            t.idTicket,
            t.u_Colada,
            t.u_Horno,
            t.u_Fecha,
            t.bRechazo,
            t.IntExt,
            t.nomDefecto,
            t.cveDefecto
        FROM cscmega_01resultadoidticket t
        WHERE t.u_FechaHr BETWEEN :d AND :h
        {filtro_horno}
        ORDER BY t.u_FechaHr DESC
        LIMIT 500
    """, {"d": d, "h": h, "horno": horno})
    return {"total": len(rows), "tickets": rows}


# ── Panel 1: Top defectos ─────────────────────────────────────────────────────

@app.get("/produccion/rechazos-por-defecto")
def rechazos_por_defecto(
    desde: str = Query(default=None),
    hasta: str = Query(default=None),
):
    d, h = rango(desde, hasta)
    return run("""
        SELECT
            nomDefecto,
            cveDefecto,
            COUNT(*) AS ocurrencias
        FROM cscmega_01resultadoidticket
        WHERE bRechazo = 1
          AND u_FechaHr BETWEEN :d AND :h
          AND nomDefecto IS NOT NULL
        GROUP BY nomDefecto, cveDefecto
        ORDER BY ocurrencias DESC
        LIMIT 20
    """, {"d": d, "h": h})


# ── Panel 2: Coladas recientes ───────────────────────────────────────────────

@app.get("/produccion/coladas-recientes")
def coladas_recientes(
    desde: str = Query(default=None),
    hasta: str = Query(default=None),
):
    d, h = rango(desde, hasta)
    return run("""
        SELECT
            c.c_IdColada          AS c_id_colada,
            c.u_Colada            AS u_colada,
            c.u_Horno             AS u_horno,
            c.c_Status            AS status,
            c.c_FechaInicial      AS fecha_inicio,
            c.c_FechaFinal        AS fecha_fin,
            c.c_Kilos             AS kilos,
            c.c_TiempoFundicion   AS tiempo_fusion,
            COUNT(r.idTicket)     AS total_tickets,
            SUM(r.bRechazo)       AS rechazos,
            ROUND(AVG(r.bRechazo)*100, 1) AS pct_rechazo
        FROM cscmega_03coladacargametalica c
        LEFT JOIN cscmega_01rechazosbyidticket r ON r.u_Colada = c.u_Colada
        WHERE c.u_Fecha BETWEEN :d AND :h
          AND c.c_IdColada > 0
        GROUP BY c.c_IdColada, c.u_Colada, c.u_Horno, c.c_Status,
                 c.c_FechaInicial, c.c_FechaFinal, c.c_Kilos, c.c_TiempoFundicion
        ORDER BY c.u_Fecha DESC
        LIMIT 20
    """, {"d": d, "h": h})


# ── Panel 2: Detalle de colada ────────────────────────────────────────────────

@app.get("/colada/{id_colada}")
def detalle_colada(id_colada: int):
    meta = run("""
        SELECT c_IdColada, u_Colada, u_Horno, u_Fecha, c_Kilos,
               c_TiempoFundicion, c_FechaInicial, c_FechaFinal,
               c_FechaInicioVaciado1, c_FechaFinalVaciado,
               c_Folio, c_InoculanteEnHornos, c_ItacaLadle, c_Status
        FROM cscmega_03coladacargametalica
        WHERE c_IdColada = :id LIMIT 1
    """, {"id": id_colada})

    if not meta:
        raise HTTPException(status_code=404, detail=f"Colada {id_colada} no encontrada")

    quim_base = run("""
        SELECT idTicket, u_Fecha, aB_C, aB_Si, aB_Mn, aB_S, aB_P, aB_Mg,
               aB_Origen, aB_FechaMuestra,
               ROUND(aB_C + aB_Si/3.0 + aB_P/3.0, 4) AS ce_pct
        FROM cscmega_05cquimicosbase
        WHERE u_Colada = :col AND aB_C > 0 LIMIT 1
    """, {"col": meta[0]["u_Colada"]})

    quim_final = run("""
        SELECT idTicket, u_Fecha, aF_C, aF_Si, aF_Mn, aF_S, aF_P, aF_Mg,
               aF_Origen, aF_FechaMuestra,
               ROUND(aF_C + aF_Si/3.0 + aF_P/3.0, 4) AS ce_pct
        FROM cscmega_05bquimicosfinal
        WHERE u_Colada = :col AND aF_C > 0 LIMIT 1
    """, {"col": meta[0]["u_Colada"]})

    temperatura = run("""
        SELECT idTicket, vt_TemperaturaVaciado, bCumplimientoTemperaturaRango,
               bCumplimientoTemperaturaOk, vt_TiempoVaciado, m_no_parte
        FROM cscmega_06modelosvaciado
        WHERE u_Colada = :col AND vt_TemperaturaVaciado BETWEEN 1100 AND 1600
        LIMIT 10
    """, {"col": meta[0]["u_Colada"]})

    return {
        "id_colada": id_colada,
        "metadatos": meta[0],
        "quimica_base": quim_base[0] if quim_base else None,
        "quimica_final": quim_final[0] if quim_final else None,
        "temperatura_vaciado": temperatura,
        "brechas": {
            "cementite_itaca": "SIN FUENTE en ia_fasa",
            "nodularization_itaca": "SIN FUENTE en ia_fasa",
            "porosity_itaca": "SIN FUENTE en ia_fasa",
            "igq_itaca": "SIN FUENTE en ia_fasa",
        }
    }


@app.get("/colada/{id_colada}/tickets")
def tickets_de_colada(id_colada: int):
    return run("""
        SELECT t.idTicket, t.u_Fecha, t.bRechazo, t.IntExt,
               t.nomDefecto, t.cveDefecto, r.FhrVACI, r.FHrMOLD, r.FHrLIMP
        FROM cscmega_01resultadoidticket t
        LEFT JOIN cscmega_08ruta r ON r.u_idTicket = t.idTicket
        WHERE t.u_Colada = :col
        ORDER BY t.u_FechaHr LIMIT 500
    """, {"col": id_colada})


# ── Panel 3: Flujo de proceso ─────────────────────────────────────────────────

# ══════════════════════════════════════════════════════════════════════════════
# TORRE DE CONTROL V2 — endpoints unificados
# ══════════════════════════════════════════════════════════════════════════════

V2_FRONTEND = Path(__file__).parent.parent / "frontend" / "torre_v2.html"

@app.get("/v2", response_class=FileResponse)
def serve_v2():
    return FileResponse(
        V2_FRONTEND, media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


def _tons_mensuales(d_ini: str, d_fin: str) -> Dict[str, dict]:
    """Toneladas producidas y rechazadas por mes usando peso real de pieza.
    Join alfamega_01_rechazosbyidticket x corex_test.modelos.PesoKgs (99.7% cobertura)."""
    rows = run("""
        SELECT
            DATE_FORMAT(r.FHrVaciado, '%Y-%m') AS mes,
            ROUND(SUM(m.PesoKgs)/1000, 2) AS tons,
            ROUND(SUM(CASE WHEN r.bRechazo=1 THEN m.PesoKgs ELSE 0 END)/1000, 2) AS tons_rech
        FROM alfamega_01_rechazosbyidticket r
        JOIN (
            SELECT NoParte, MAX(IdModelo) AS IdModelo
            FROM corex_test.modelos
            WHERE PesoKgs > 0
            GROUP BY NoParte
        ) best ON best.NoParte = r.No_Parte
        JOIN corex_test.modelos m ON m.IdModelo = best.IdModelo
        WHERE r.FHrVaciado BETWEEN :d AND :h
        GROUP BY mes ORDER BY mes
    """, {"d": d_ini, "h": d_fin})
    return {r["mes"]: {"tons": float(r["tons"] or 0), "tons_rech": float(r["tons_rech"] or 0)}
            for r in rows}


def _resumen_anual_historico(anio: int, d_ini: str, d_fin: str) -> dict:
    """Resumen anual para años sin tablas cscmega (2023/2024).
    Usa alfamega_01_rechazosbyidticket con FHrVaciado como eje de fecha."""
    MES_LBLS = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
    CAT_ZERO = {
        "aceptada":       {"col":0,"pz":0,"r":0},
        "en_objetivo":    {"col":0,"pz":0,"r":0},
        "fuera_objetivo": {"col":0,"pz":0,"r":0},
        "grave":          {"col":0,"pz":0,"r":0},
    }

    meses_raw = run("""
        SELECT DATE_FORMAT(FHrVaciado,'%Y-%m') AS mes,
               COUNT(*) AS n, SUM(bRechazo) AS r,
               ROUND(AVG(bRechazo)*100,2) AS rate
        FROM alfamega_01_rechazosbyidticket
        WHERE FHrVaciado BETWEEN :d AND :h
        GROUP BY mes ORDER BY mes
    """, {"d": d_ini, "h": d_fin})

    fam_raw = run(f"""
        SELECT DATE_FORMAT(FHrVaciado,'%Y-%m') AS mes,
               {FAMILIA_SQL} AS familia, COUNT(*) AS n
        FROM alfamega_01_rechazosbyidticket
        WHERE bRechazo=1 AND FHrVaciado BETWEEN :d AND :h AND nomDefecto IS NOT NULL
        GROUP BY mes, familia ORDER BY mes, n DESC
    """, {"d": d_ini, "h": d_fin})
    fam_idx: Dict[str, list] = {}
    for f in fam_raw:
        fam_idx.setdefault(f["mes"], []).append(f)

    coladas_raw = run("""
        SELECT DATE_FORMAT(FHrVaciado,'%Y-%m') AS mes,
               COUNT(DISTINCT Colada) AS coladas
        FROM alfamega_06_vaciado
        WHERE FHrVaciado BETWEEN :d AND :h
        GROUP BY mes
    """, {"d": d_ini, "h": d_fin})
    coladas_idx = {c["mes"]: int(c["coladas"] or 0) for c in coladas_raw}

    tons_idx = _tons_mensuales(d_ini, d_fin)

    mensual = []
    for m in meses_raw:
        mes_num = int(m["mes"].split("-")[1]) - 1
        fams = fam_idx.get(m["mes"], [])
        total_fam = sum(f["n"] for f in fams) or 1
        rate = float(m["rate"] or 0)
        tons_entry = tons_idx.get(m["mes"], {})
        tons = tons_entry.get("tons", 0.0)
        tons_rech = tons_entry.get("tons_rech", 0.0)
        mensual.append({
            "lbl": MES_LBLS[mes_num],
            "rate": rate,
            "n": int(m["n"] or 0),
            "r": int(m["r"] or 0),
            "coladas": coladas_idx.get(m["mes"], 0),
            "tons": tons,
            "tons_rech": tons_rech,
            "fusion_min": 0.0,
            "ciclo_min": 0.0,
            "cat": {k: dict(v) for k, v in CAT_ZERO.items()},
            "fams": [{"f": f["familia"], "pct": round(f["n"]/total_fam*100,1), "n": int(f["n"])}
                     for f in fams if f["familia"] != "Otros"],
        })

    Q_LBLS = ["1Q","2Q","3Q","4Q"]
    trimestral = []
    for q in range(4):
        mq = mensual[q*3:(q+1)*3]
        if not mq: continue
        n_t = sum(m["n"] for m in mq)
        r_t = sum(m["r"] for m in mq)
        tons_t = round(sum(m["tons"] for m in mq), 1)
        tons_rech_t = round(sum(m["tons_rech"] for m in mq), 1)
        fam_ac: Dict[str, int] = {}
        for m in mq:
            for f in m["fams"]:
                fam_ac[f["f"]] = fam_ac.get(f["f"], 0) + f["n"]
        tot_fac = sum(fam_ac.values()) or 1
        trimestral.append({
            "lbl": Q_LBLS[q], "rate": round(r_t/n_t*100,2) if n_t else 0,
            "n": n_t, "r": r_t, "coladas": sum(m["coladas"] for m in mq),
            "tons": tons_t, "tons_rech": tons_rech_t,
            "fusion_min": 0.0, "ciclo_min": 0.0,
            "cat": {k: dict(v) for k, v in CAT_ZERO.items()},
            "fams": [{"f":k,"pct":round(v/tot_fac*100,1),"n":v}
                     for k,v in sorted(fam_ac.items(), key=lambda x:-x[1])],
        })

    sem_raw = run("""
        SELECT YEARWEEK(FHrVaciado,3) AS wk,
               COUNT(*) AS n, SUM(bRechazo) AS r,
               ROUND(AVG(bRechazo)*100,2) AS rate
        FROM alfamega_01_rechazosbyidticket
        WHERE FHrVaciado BETWEEN :d AND :h
        GROUP BY wk HAVING n >= 50 ORDER BY wk
    """, {"d": d_ini, "h": d_fin})

    sf_raw = run(f"""
        SELECT YEARWEEK(FHrVaciado,3) AS wk,
               {FAMILIA_SQL} AS familia, COUNT(*) AS n
        FROM alfamega_01_rechazosbyidticket
        WHERE bRechazo=1 AND FHrVaciado BETWEEN :d AND :h AND nomDefecto IS NOT NULL
        GROUP BY wk, familia ORDER BY wk, n DESC
    """, {"d": d_ini, "h": d_fin})
    sf_idx: Dict[int, list] = {}
    for f in sf_raw:
        sf_idx.setdefault(f["wk"], []).append(f)

    semanal = []
    for s in sem_raw:
        fams = sf_idx.get(s["wk"], [])
        tf = sum(f["n"] for f in fams) or 1
        semanal.append({
            "lbl": "S"+str(s["wk"])[-2:],
            "rate": float(s["rate"] or 0),
            "n": int(s["n"] or 0),
            "r": int(s["r"] or 0),
            "coladas": 0, "tons": 0.0, "tons_rech": 0.0,
            "fusion_min": 0.0, "ciclo_min": 0.0,
            "cat": {k: dict(v) for k, v in CAT_ZERO.items()},
            "fams": [{"f":f["familia"],"pct":round(f["n"]/tf*100,1),"n":int(f["n"])}
                     for f in fams if f["familia"] != "Otros"],
        })

    n_a = sum(m["n"] for m in mensual)
    r_a = sum(m["r"] for m in mensual)
    tons_a = round(sum(m["tons"] for m in mensual), 1)
    tons_rech_a = round(sum(m["tons_rech"] for m in mensual), 1)
    fam_an: Dict[str, int] = {}
    for m in mensual:
        for f in m["fams"]:
            fam_an[f["f"]] = fam_an.get(f["f"], 0) + f["n"]
    tot_an = sum(fam_an.values()) or 1
    anual = {
        "lbl": str(anio), "n": n_a, "r": r_a, "coladas": sum(m["coladas"] for m in mensual),
        "tons": tons_a, "tons_rech": tons_rech_a,
        "fusion_min": 0.0, "ciclo_min": 0.0,
        "cat": {k: dict(v) for k, v in CAT_ZERO.items()},
        "rate": round(r_a/n_a*100,2) if n_a else 0,
        "fams": [{"f":k,"pct":round(v/tot_an*100,1),"n":v}
                 for k,v in sorted(fam_an.items(), key=lambda x:-x[1])],
    }

    parts_raw = run("""
        SELECT No_Parte AS p, COUNT(*) AS n, ROUND(AVG(bRechazo)*100,1) AS rate
        FROM alfamega_01_rechazosbyidticket
        WHERE FHrVaciado BETWEEN :d AND :h AND No_Parte IS NOT NULL
        GROUP BY No_Parte HAVING n >= 50
        ORDER BY rate DESC LIMIT 10
    """, {"d": d_ini, "h": d_fin})

    rates = [(m["lbl"], m["rate"]) for m in mensual if m["n"] >= 100]
    best  = min(rates, key=lambda x: x[1]) if rates else ("—", 0)
    worst = max(rates, key=lambda x: x[1]) if rates else ("—", 0)

    return {
        "meta": {
            "target": 7, "bandLo": 5, "bandHi": 7,
            "tonPt": 87.6, "cLo": 711, "cHi": 1185,
            "avg": round(r_a/n_a*100,2) if n_a else 0,
        },
        "mensual": mensual, "trimestral": trimestral, "semanal": semanal, "anual": anual,
        "parts": [{"p":p["p"],"n":int(p["n"]),"rate":float(p["rate"]),"tag":"—"}
                  for p in parts_raw],
        "best":  {"lbl": best[0],  "rate": best[1]},
        "worst": {"lbl": worst[0], "rate": worst[1]},
    }


@app.get("/v2/ejecutivo/resumen-anual")
def v2_resumen_anual(anio: int = Query(default=2025)):
    """
    Devuelve la estructura D completa que consume el dashboard ejecutivo:
    mensual, trimestral, semanal, anual, parts, best, worst, meta.
    """
    d_ini = f"{anio}-01-01 00:00:00"
    d_fin = f"{anio}-12-31 23:59:59"

    if anio < 2025:
        return _resumen_anual_historico(anio, d_ini, d_fin)

    # ── Totales por mes ──────────────────────────────────────────────────────
    meses_raw = run("""
        SELECT DATE_FORMAT(u_Fecha,'%Y-%m') AS mes,
               COUNT(*) AS n, SUM(bRechazo) AS r,
               ROUND(AVG(bRechazo)*100,2) AS rate,
               COUNT(DISTINCT CONCAT(u_Colada,'-',u_Horno)) AS coladas
        FROM cscmega_01rechazosbyidticket
        WHERE u_Fecha BETWEEN :d AND :h
        GROUP BY mes ORDER BY mes
    """, {"d": d_ini, "h": d_fin})

    # ── Categorías de calidad por colada por mes (coladas + piezas + rechazos) ─
    cat_raw = run("""
        SELECT mes,
               SUM(CASE WHEN rate  = 0              THEN 1      ELSE 0 END) AS ac_col,
               SUM(CASE WHEN rate  = 0              THEN piezas ELSE 0 END) AS ac_pz,
               SUM(CASE WHEN rate  = 0              THEN rech   ELSE 0 END) AS ac_r,
               SUM(CASE WHEN rate  > 0 AND rate < 7 THEN 1      ELSE 0 END) AS eo_col,
               SUM(CASE WHEN rate  > 0 AND rate < 7 THEN piezas ELSE 0 END) AS eo_pz,
               SUM(CASE WHEN rate  > 0 AND rate < 7 THEN rech   ELSE 0 END) AS eo_r,
               SUM(CASE WHEN rate >= 7 AND rate < 15 THEN 1      ELSE 0 END) AS fo_col,
               SUM(CASE WHEN rate >= 7 AND rate < 15 THEN piezas ELSE 0 END) AS fo_pz,
               SUM(CASE WHEN rate >= 7 AND rate < 15 THEN rech   ELSE 0 END) AS fo_r,
               SUM(CASE WHEN rate >= 15              THEN 1      ELSE 0 END) AS gr_col,
               SUM(CASE WHEN rate >= 15              THEN piezas ELSE 0 END) AS gr_pz,
               SUM(CASE WHEN rate >= 15              THEN rech   ELSE 0 END) AS gr_r
        FROM (
            SELECT DATE_FORMAT(u_Fecha,'%Y-%m') AS mes,
                   CONCAT(u_Colada,'-',u_Horno) AS ck,
                   COUNT(*) AS piezas,
                   SUM(bRechazo) AS rech,
                   ROUND(SUM(bRechazo)/COUNT(*)*100, 2) AS rate
            FROM cscmega_01rechazosbyidticket
            WHERE u_Fecha BETWEEN :d AND :h
            GROUP BY mes, ck
        ) sub
        GROUP BY mes ORDER BY mes
    """, {"d": d_ini, "h": d_fin})
    cat_idx = {c["mes"]: c for c in cat_raw}

    # ── Tiempos de ciclo por mes (cscmega_03coladacargametalica) ─────────────
    ciclo_raw = run("""
        SELECT DATE_FORMAT(u_Fecha,'%Y-%m') AS mes,
               ROUND(AVG(CASE
                   WHEN c_TiempoFundicion > 0 AND c_TiempoFundicion < 300
                   THEN c_TiempoFundicion END), 1) AS fusion_min,
               ROUND(AVG(CASE
                   WHEN c_FechaCarga IS NOT NULL AND c_FechaLiberado IS NOT NULL
                    AND TIMESTAMPDIFF(MINUTE,c_FechaCarga,c_FechaLiberado) BETWEEN 30 AND 500
                   THEN TIMESTAMPDIFF(MINUTE,c_FechaCarga,c_FechaLiberado)
               END), 1) AS ciclo_min
        FROM cscmega_03coladacargametalica
        WHERE u_Fecha BETWEEN :d AND :h
        GROUP BY mes ORDER BY mes
    """, {"d": d_ini, "h": d_fin})
    ciclo_idx = {c["mes"]: c for c in ciclo_raw}

    # ── Familias por mes ─────────────────────────────────────────────────────
    fam_raw = run(f"""
        SELECT DATE_FORMAT(u_FechaHr,'%Y-%m') AS mes,
               {FAMILIA_SQL} AS familia,
               COUNT(*) AS n
        FROM cscmega_01resultadoidticket
        WHERE bRechazo=1 AND u_FechaHr BETWEEN :d AND :h
          AND nomDefecto IS NOT NULL
        GROUP BY mes, familia ORDER BY mes, n DESC
    """, {"d": d_ini, "h": d_fin})

    # Indexar familias por mes
    fam_idx = {}
    for f in fam_raw:
        fam_idx.setdefault(f["mes"], []).append(f)

    tons_idx = _tons_mensuales(d_ini, d_fin)

    MES_LBLS = ["Ene","Feb","Mar","Abr","May","Jun",
                "Jul","Ago","Sep","Oct","Nov","Dic"]

    mensual = []
    for m in meses_raw:
        mes_num = int(m["mes"].split("-")[1]) - 1
        fams = fam_idx.get(m["mes"], [])
        total_fam = sum(f["n"] for f in fams) or 1
        cic = ciclo_idx.get(m["mes"], {})
        cat = cat_idx.get(m["mes"], {})
        rate = float(m["rate"] or 0)
        tons_entry = tons_idx.get(m["mes"], {})
        tons = tons_entry.get("tons", 0.0)
        tons_rech = tons_entry.get("tons_rech", 0.0)
        mensual.append({
            "lbl": MES_LBLS[mes_num],
            "rate": rate,
            "n": int(m["n"] or 0),
            "r": int(m["r"] or 0),
            "coladas": int(m["coladas"] or 0),
            "tons": tons,
            "tons_rech": tons_rech,
            "fusion_min": float(cic.get("fusion_min") or 0),
            "ciclo_min": float(cic.get("ciclo_min") or 0),
            "cat": {
                "aceptada":       {"col": int(cat.get("ac_col") or 0), "pz": int(cat.get("ac_pz") or 0), "r": int(cat.get("ac_r") or 0)},
                "en_objetivo":    {"col": int(cat.get("eo_col") or 0), "pz": int(cat.get("eo_pz") or 0), "r": int(cat.get("eo_r") or 0)},
                "fuera_objetivo": {"col": int(cat.get("fo_col") or 0), "pz": int(cat.get("fo_pz") or 0), "r": int(cat.get("fo_r") or 0)},
                "grave":          {"col": int(cat.get("gr_col") or 0), "pz": int(cat.get("gr_pz") or 0), "r": int(cat.get("gr_r") or 0)},
            },
            "fams": [{"f": f["familia"],
                      "pct": round(f["n"]/total_fam*100,1),
                      "n": int(f["n"])} for f in fams if f["familia"] != "Otros"]
        })

    # ── Trimestral ───────────────────────────────────────────────────────────
    Q_LBLS = ["1Q","2Q","3Q","4Q"]
    trimestral = []
    for q in range(4):
        mq = mensual[q*3:(q+1)*3]
        if not mq: continue
        n_t = sum(m["n"] for m in mq)
        r_t = sum(m["r"] for m in mq)
        c_t = sum(m["coladas"] for m in mq)
        rate_t = round(r_t/n_t*100,2) if n_t else 0
        tons_t = round(sum(m["tons"] for m in mq), 1)
        tons_rech_t = round(sum(m["tons_rech"] for m in mq), 1)
        mq_f = [m for m in mq if m["fusion_min"] > 0]
        mq_c = [m for m in mq if m["ciclo_min"] > 0]
        fusion_t = round(sum(m["fusion_min"] for m in mq_f)/len(mq_f), 1) if mq_f else 0
        ciclo_t  = round(sum(m["ciclo_min"]  for m in mq_c)/len(mq_c), 1) if mq_c else 0
        fam_ac = {}
        for m in mq:
            for f in m["fams"]:
                fam_ac[f["f"]] = fam_ac.get(f["f"],0) + f["n"]
        tot_fac = sum(fam_ac.values()) or 1
        cat_t = {k: {"col": sum(m["cat"][k]["col"] for m in mq), "pz": sum(m["cat"][k]["pz"] for m in mq), "r": sum(m["cat"][k]["r"] for m in mq)} for k in ("aceptada","en_objetivo","fuera_objetivo","grave")}
        trimestral.append({
            "lbl": Q_LBLS[q], "rate": rate_t, "n": n_t, "r": r_t, "coladas": c_t,
            "tons": tons_t, "tons_rech": tons_rech_t,
            "fusion_min": fusion_t, "ciclo_min": ciclo_t, "cat": cat_t,
            "fams": [{"f":k,"pct":round(v/tot_fac*100,1),"n":v}
                     for k,v in sorted(fam_ac.items(),key=lambda x:-x[1])]
        })

    # ── Anual ────────────────────────────────────────────────────────────────
    n_a = sum(m["n"] for m in mensual)
    r_a = sum(m["r"] for m in mensual)
    c_a = sum(m["coladas"] for m in mensual)
    tons_a = round(sum(m["tons"] for m in mensual), 1)
    tons_rech_a = round(sum(m["tons_rech"] for m in mensual), 1)
    ma_f = [m for m in mensual if m["fusion_min"] > 0]
    ma_c = [m for m in mensual if m["ciclo_min"] > 0]
    fusion_a = round(sum(m["fusion_min"] for m in ma_f)/len(ma_f), 1) if ma_f else 0
    ciclo_a  = round(sum(m["ciclo_min"]  for m in ma_c)/len(ma_c), 1) if ma_c else 0
    fam_an = {}
    for m in mensual:
        for f in m["fams"]:
            fam_an[f["f"]] = fam_an.get(f["f"],0) + f["n"]
    tot_an = sum(fam_an.values()) or 1
    cat_a = {k: {"col": sum(m["cat"][k]["col"] for m in mensual), "pz": sum(m["cat"][k]["pz"] for m in mensual), "r": sum(m["cat"][k]["r"] for m in mensual)} for k in ("aceptada","en_objetivo","fuera_objetivo","grave")}
    anual = {
        "lbl": str(anio), "n": n_a, "r": r_a, "coladas": c_a,
        "tons": tons_a, "tons_rech": tons_rech_a,
        "fusion_min": fusion_a, "ciclo_min": ciclo_a, "cat": cat_a,
        "rate": round(r_a/n_a*100,2) if n_a else 0,
        "fams": [{"f":k,"pct":round(v/tot_an*100,1),"n":v}
                 for k,v in sorted(fam_an.items(),key=lambda x:-x[1])]
    }

    # ── Semanal (últimas semanas del año) ────────────────────────────────────
    sem_raw = run("""
        SELECT YEARWEEK(u_Fecha,3) AS wk,
               MIN(DATE(u_Fecha)) AS lunes,
               COUNT(*) AS n, SUM(bRechazo) AS r,
               ROUND(AVG(bRechazo)*100,2) AS rate,
               COUNT(DISTINCT CONCAT(u_Colada,'-',u_Horno)) AS coladas
        FROM cscmega_01rechazosbyidticket
        WHERE u_Fecha BETWEEN :d AND :h
        GROUP BY wk HAVING n >= 50 ORDER BY wk
    """, {"d": d_ini, "h": d_fin})

    sem_fam = run(f"""
        SELECT YEARWEEK(u_FechaHr,3) AS wk,
               {FAMILIA_SQL} AS familia, COUNT(*) AS n
        FROM cscmega_01resultadoidticket
        WHERE bRechazo=1 AND u_FechaHr BETWEEN :d AND :h
          AND nomDefecto IS NOT NULL
        GROUP BY wk, familia ORDER BY wk, n DESC
    """, {"d": d_ini, "h": d_fin})

    sf_idx = {}
    for f in sem_fam:
        sf_idx.setdefault(f["wk"], []).append(f)

    sem_ciclo = run("""
        SELECT YEARWEEK(u_Fecha,3) AS wk,
               ROUND(AVG(CASE
                   WHEN c_TiempoFundicion > 0 AND c_TiempoFundicion < 300
                   THEN c_TiempoFundicion END), 1) AS fusion_min,
               ROUND(AVG(CASE
                   WHEN c_FechaCarga IS NOT NULL AND c_FechaLiberado IS NOT NULL
                    AND TIMESTAMPDIFF(MINUTE,c_FechaCarga,c_FechaLiberado) BETWEEN 30 AND 500
                   THEN TIMESTAMPDIFF(MINUTE,c_FechaCarga,c_FechaLiberado)
               END), 1) AS ciclo_min
        FROM cscmega_03coladacargametalica
        WHERE u_Fecha BETWEEN :d AND :h
        GROUP BY wk
    """, {"d": d_ini, "h": d_fin})
    sc_idx = {c["wk"]: c for c in sem_ciclo}

    sem_cat = run("""
        SELECT wk,
               SUM(CASE WHEN rate  = 0              THEN 1      ELSE 0 END) AS ac_col,
               SUM(CASE WHEN rate  = 0              THEN piezas ELSE 0 END) AS ac_pz,
               SUM(CASE WHEN rate  = 0              THEN rech   ELSE 0 END) AS ac_r,
               SUM(CASE WHEN rate  > 0 AND rate < 7 THEN 1      ELSE 0 END) AS eo_col,
               SUM(CASE WHEN rate  > 0 AND rate < 7 THEN piezas ELSE 0 END) AS eo_pz,
               SUM(CASE WHEN rate  > 0 AND rate < 7 THEN rech   ELSE 0 END) AS eo_r,
               SUM(CASE WHEN rate >= 7 AND rate < 15 THEN 1      ELSE 0 END) AS fo_col,
               SUM(CASE WHEN rate >= 7 AND rate < 15 THEN piezas ELSE 0 END) AS fo_pz,
               SUM(CASE WHEN rate >= 7 AND rate < 15 THEN rech   ELSE 0 END) AS fo_r,
               SUM(CASE WHEN rate >= 15              THEN 1      ELSE 0 END) AS gr_col,
               SUM(CASE WHEN rate >= 15              THEN piezas ELSE 0 END) AS gr_pz,
               SUM(CASE WHEN rate >= 15              THEN rech   ELSE 0 END) AS gr_r
        FROM (
            SELECT YEARWEEK(u_Fecha,3) AS wk,
                   CONCAT(u_Colada,'-',u_Horno) AS ck,
                   COUNT(*) AS piezas,
                   SUM(bRechazo) AS rech,
                   ROUND(SUM(bRechazo)/COUNT(*)*100, 2) AS rate
            FROM cscmega_01rechazosbyidticket
            WHERE u_Fecha BETWEEN :d AND :h
            GROUP BY wk, ck
        ) sub
        GROUP BY wk
    """, {"d": d_ini, "h": d_fin})
    scat_idx = {c["wk"]: c for c in sem_cat}

    semanal = []
    for s in sem_raw:
        fams = sf_idx.get(s["wk"], [])
        tf = sum(f["n"] for f in fams) or 1
        cic = sc_idx.get(s["wk"], {})
        scat = scat_idx.get(s["wk"], {})
        semanal.append({
            "lbl": "S"+str(s["wk"])[-2:],
            "rate": float(s["rate"] or 0),
            "n": int(s["n"] or 0),
            "r": int(s["r"] or 0),
            "coladas": int(s["coladas"] or 0),
            "fusion_min": float(cic.get("fusion_min") or 0),
            "ciclo_min": float(cic.get("ciclo_min") or 0),
            "cat": {
                "aceptada":       {"col": int(scat.get("ac_col") or 0), "pz": int(scat.get("ac_pz") or 0), "r": int(scat.get("ac_r") or 0)},
                "en_objetivo":    {"col": int(scat.get("eo_col") or 0), "pz": int(scat.get("eo_pz") or 0), "r": int(scat.get("eo_r") or 0)},
                "fuera_objetivo": {"col": int(scat.get("fo_col") or 0), "pz": int(scat.get("fo_pz") or 0), "r": int(scat.get("fo_r") or 0)},
                "grave":          {"col": int(scat.get("gr_col") or 0), "pz": int(scat.get("gr_pz") or 0), "r": int(scat.get("gr_r") or 0)},
            },
            "fams": [{"f":f["familia"],"pct":round(f["n"]/tf*100,1),"n":int(f["n"])}
                     for f in fams if f["familia"] != "Otros"]
        })

    # ── Top partes por rechazo ───────────────────────────────────────────────
    parts_raw = run("""
        SELECT No_Parte AS p,
               COUNT(*) AS n,
               ROUND(AVG(bRechazo)*100,1) AS rate
        FROM cscmega_01resultadoidticket
        WHERE u_FechaHr BETWEEN :d AND :h AND No_Parte IS NOT NULL
        GROUP BY No_Parte HAVING n >= 50
        ORDER BY rate DESC LIMIT 10
    """, {"d": d_ini, "h": d_fin})

    # ── Best / worst mes ─────────────────────────────────────────────────────
    rates = [(m["lbl"], m["rate"]) for m in mensual if m["n"] >= 100]
    best  = min(rates, key=lambda x: x[1]) if rates else ("—", 0)
    worst = max(rates, key=lambda x: x[1]) if rates else ("—", 0)

    return {
        "meta": {
            "target": 7, "bandLo": 5, "bandHi": 7,
            "tonPt": 87.6, "cLo": 711, "cHi": 1185,
            "avg": round(r_a/n_a*100,2) if n_a else 13.3
        },
        "mensual": mensual,
        "trimestral": trimestral,
        "semanal": semanal,
        "anual": anual,
        "parts": [{"p":p["p"],"n":int(p["n"]),"rate":float(p["rate"]),"tag":"—"}
                  for p in parts_raw],
        "best":  {"lbl": best[0],  "rate": best[1]},
        "worst": {"lbl": worst[0], "rate": worst[1]},
    }


@app.get("/v2/ejecutivo/pronostico")
def v2_pronostico(ventana: int = 12, periodos: int = 3, year: int = 0):
    """
    Pronostico de produccion y tasa de rechazo.
    ventana: meses historicos a incluir (6, 12, 18, 24).
    year: si > 0 y < 2025, modo historico — usa max_date = 31 dic de ese año.
    Devuelve historico mensual, pronostico (vacío si periodos=0), partes_80.
    """
    from datetime import date, timedelta
    import calendar as cal_mod

    ventana = max(6, min(24, int(ventana)))
    periodos = max(0, min(3, int(periodos)))   # permite 0 para años históricos
    MESES = ['','Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']
    PIVOT = date(2025, 1, 1)

    # Modo histórico: fijar max_date al último día del año solicitado
    if 0 < year < 2025:
        max_date = date(year, 12, 31)
    else:
        # Ultimo dato disponible en 2025
        max_row = run("SELECT MAX(DATE(u_Fecha)) AS mx FROM cscmega_01rechazosbyidticket", {})
        max_date = date.fromisoformat(str(max_row[0]["mx"])) if max_row and max_row[0]["mx"] else date(2025, 12, 29)

    # Lista de meses del historico
    end = date(max_date.year, max_date.month, 1)
    months = []
    for i in range(ventana - 1, -1, -1):
        m = end.month - i
        y = end.year
        while m <= 0:
            m += 12
            y -= 1
        months.append(date(y, m, 1))

    pre_months  = [m for m in months if m < PIVOT]
    post_months = [m for m in months if m >= PIVOT]

    # ── Batch query: conteos por mes ──────────────────────────────
    hist_agg: dict = {}

    # Pre-2025 (alfamega): usar cache en memoria, sin scan a BD
    for pm in pre_months:
        mk = f"{pm.year}-{pm.month:02d}"
        if mk in _ALFAMEGA_MONTHLY:
            hist_agg[mk] = {**_ALFAMEGA_MONTHLY[mk], "col": 0}

    if post_months:
        h_fin_post = date(post_months[-1].year, post_months[-1].month,
                          cal_mod.monthrange(post_months[-1].year, post_months[-1].month)[1])
        for r in run("""
            SELECT DATE_FORMAT(u_Fecha,'%Y-%m') AS mk,
                   COUNT(*) AS n, SUM(bRechazo) AS rec,
                   COUNT(DISTINCT CONCAT(u_Colada,'-',u_Horno)) AS col
            FROM cscmega_01rechazosbyidticket
            WHERE u_Fecha BETWEEN :d AND :h
            GROUP BY mk
        """, {"d": post_months[0].isoformat() + " 00:00:00", "h": h_fin_post.isoformat() + " 23:59:59"}):
            hist_agg[r["mk"]] = {"n": int(r["n"] or 0), "rec": int(r["rec"] or 0), "col": int(r["col"] or 0)}

    # ── Batch query: toneladas por mes (via parte_lookup) ─────────
    lookup = _PARTE_LOOKUP or {}
    tons_agg: dict = {}

    def _acum_tons(rows):
        for r in rows:
            mk = r["mk"]
            pk = (lookup.get(r["no_parte"]) or {}).get("peso_kgs", 0) or 0
            if mk not in tons_agg:
                tons_agg[mk] = {"tons": 0.0, "tons_rech": 0.0}
            tons_agg[mk]["tons"]      += pk * int(r["cnt"] or 0) / 1000
            tons_agg[mk]["tons_rech"] += pk * int(r["rec"] or 0) / 1000

    if pre_months:
        h_fin_pre2 = date(pre_months[-1].year, pre_months[-1].month,
                          cal_mod.monthrange(pre_months[-1].year, pre_months[-1].month)[1])
        _acum_tons(run("""
            SELECT DATE_FORMAT(FHrVaciado,'%Y-%m') AS mk,
                   No_Parte AS no_parte, COUNT(*) AS cnt, SUM(bRechazo) AS rec
            FROM alfamega_01_rechazosbyidticket
            WHERE FHrVaciado BETWEEN :d AND :h AND No_Parte IS NOT NULL AND No_Parte != ''
            GROUP BY mk, No_Parte
        """, {"d": months[0].isoformat() + " 00:00:00", "h": h_fin_pre2.isoformat() + " 23:59:59"}))

    if post_months:
        h_fin_post2 = date(post_months[-1].year, post_months[-1].month,
                           cal_mod.monthrange(post_months[-1].year, post_months[-1].month)[1])
        _acum_tons(run("""
            SELECT DATE_FORMAT(rb.u_Fecha,'%Y-%m') AS mk,
                   rt.No_Parte AS no_parte, COUNT(*) AS cnt, SUM(rb.bRechazo) AS rec
            FROM cscmega_01rechazosbyidticket rb
            JOIN cscmega_01resultadoidticket rt ON rt.idTicket = rb.idTicket
            WHERE rb.u_Fecha BETWEEN :d AND :h AND rt.No_Parte IS NOT NULL AND rt.No_Parte != ''
            GROUP BY mk, rt.No_Parte
        """, {"d": post_months[0].isoformat() + " 00:00:00", "h": h_fin_post2.isoformat() + " 23:59:59"}))

    # ── Historico list ────────────────────────────────────────────
    historico = []
    for m in months:
        mk = f"{m.year}-{m.month:02d}"
        agg = hist_agg.get(mk, {"n": 0, "rec": 0, "col": 0})
        t   = tons_agg.get(mk, {"tons": 0.0, "tons_rech": 0.0})
        n, rec = agg["n"], agg["rec"]
        historico.append({
            "anio": m.year, "mes": m.month,
            "label": f"{MESES[m.month]} {str(m.year)[2:]}",
            "n_piezas":    n,
            "n_rechazos":  rec,
            "rate":        round(rec / n * 100, 1) if n else 0,
            "coladas":     agg["col"],
            "tons":        round(t["tons"], 1),
            "tons_rech":   round(t["tons_rech"], 1),
        })

    # ── Forecast: proximo mes ─────────────────────────────────────
    nm = max_date.month + 1
    ny = max_date.year
    if nm > 12:
        nm, ny = 1, ny + 1

    def _query_month(y, m):
        mk = f"{y}-{m:02d}"
        if mk in hist_agg:
            t = tons_agg.get(mk, {"tons": 0.0, "tons_rech": 0.0})
            return {**hist_agg[mk], **t}
        # Pre-2025: cache en memoria (evita scan individual a alfamega)
        if y < 2025 and mk in _ALFAMEGA_MONTHLY:
            return {**_ALFAMEGA_MONTHLY[mk], "col": 0, "tons": 0.0, "tons_rech": 0.0}
        ld = cal_mod.monthrange(y, m)[1]
        d0 = f"{y}-{m:02d}-01 00:00:00"
        d1 = f"{y}-{m:02d}-{ld:02d} 23:59:59"
        if y >= 2025:
            rows = run("SELECT COUNT(*) AS n, SUM(bRechazo) AS rec FROM cscmega_01rechazosbyidticket WHERE u_Fecha BETWEEN :d AND :h", {"d": d0, "h": d1})
        else:
            rows = run("SELECT COUNT(*) AS n, SUM(bRechazo) AS rec FROM alfamega_01_rechazosbyidticket WHERE FHrVaciado BETWEEN :d AND :h", {"d": d0, "h": d1})
        if rows and rows[0]["n"]:
            return {"n": int(rows[0]["n"]), "rec": int(rows[0]["rec"] or 0), "col": 0, "tons": 0.0, "tons_rech": 0.0}
        return None

    # ── Estimados retrospectivos: cache alfamega + hist_agg ───────
    def _retro(y, m):
        mk = f"{y}-{m:02d}"
        return hist_agg.get(mk) or _ALFAMEGA_MONTHLY.get(mk)

    for h in historico:
        yp1 = _retro(h["anio"] - 1, h["mes"])
        yp2 = _retro(h["anio"] - 2, h["mes"])
        if yp1 and yp2 and (yp1.get("n") or 0) > 0 and (yp2.get("n") or 0) > 0:
            n_e   = round(0.65 * yp1["n"]   + 0.35 * yp2["n"])
            rec_e = round(0.65 * yp1["rec"] + 0.35 * yp2["rec"])
        elif yp1 and (yp1.get("n") or 0) > 0:
            n_e, rec_e = yp1["n"], yp1["rec"]
        else:
            n_e = rec_e = None
        h["n_piezas_est"] = n_e
        h["rate_est"]     = round(rec_e / n_e * 100, 1) if n_e else None

    # ── Shrinkage Bayesiano sobre tasas mensuales (congruente con simulación) ──
    K_SHRINK = 75
    _tot_n   = sum(h["n_piezas"]   for h in historico)
    _tot_rec = sum(h["n_rechazos"] for h in historico)
    _global_rate = _tot_rec / _tot_n if _tot_n else 0.15
    for h in historico:
        n = h["n_piezas"]
        w = n / (n + K_SHRINK) if n else 0
        raw = h["n_rechazos"] / n if n else _global_rate
        h["rate_shrunk"] = round((w * raw + (1 - w) * _global_rate) * 100, 1)

    def _shrink_rate(data):
        """Aplica shrinkage a un dict {n, rec} y devuelve rec_shrunk."""
        if not data or not data.get("n"):
            return None
        n = data["n"]
        w = n / (n + K_SHRINK)
        raw = data["rec"] / n
        return (w * raw + (1 - w) * _global_rate) * n

    # ── Promedio ponderado por recencia del horizonte visible ────────────────
    # decay=0.88: el mes más reciente pesa 1.0, cada mes atrás pesa 12% menos.
    _decay = 0.88
    _active = [h for h in historico if h["n_piezas"] > 0]
    if _active:
        _wts  = [_decay ** (len(_active) - 1 - i) for i in range(len(_active))]
        _W    = sum(_wts)
        _wt_n    = sum(h["n_piezas"]   * w for h, w in zip(_active, _wts)) / _W
        _wt_rec  = sum(h["rate_shrunk"] / 100 * h["n_piezas"] * w for h, w in zip(_active, _wts)) / _W
        _wt_tons = sum(h["tons"]        * w for h, w in zip(_active, _wts)) / _W
    else:
        _wt_n = _wt_rec = _wt_tons = 0.0

    periodos = max(0, min(3, int(periodos)))
    pronostico = []
    n_est_first = 0
    for p_idx in range(periodos):
        fm = max_date.month + 1 + p_idx
        fy = max_date.year
        while fm > 12:
            fm -= 12
            fy += 1

        yp1 = _query_month(fy - 1, fm)
        yp2 = _query_month(fy - 2, fm)

        if yp1 and yp2:
            seas_n    = 0.65 * yp1["n"]   + 0.35 * yp2["n"]
            rec1 = _shrink_rate(yp1) or 0
            rec2 = _shrink_rate(yp2) or 0
            seas_rec  = 0.65 * rec1 + 0.35 * rec2
            seas_tons = 0.65 * (yp1.get("tons") or 0) + 0.35 * (yp2.get("tons") or 0)
            confianza, metodo = "alta", "blend_estacional_shrinkage"
        elif yp1:
            seas_n    = yp1["n"]
            seas_rec  = _shrink_rate(yp1) or 0
            seas_tons = yp1.get("tons") or 0
            confianza, metodo = "media", "blend_estacional_shrinkage"
        else:
            seas_n, seas_rec, seas_tons = _wt_n, _wt_rec, _wt_tons
            confianza, metodo = "baja", "promedio_ponderado_shrinkage"

        # Blend 50% estacional + 50% promedio ponderado (ambos con shrinkage)
        n_est    = round(0.5 * seas_n    + 0.5 * _wt_n)
        rec_est  = round(0.5 * seas_rec  + 0.5 * _wt_rec)
        tons_est = round(0.5 * seas_tons + 0.5 * _wt_tons, 1)

        rate_est = round(rec_est / n_est * 100, 1) if n_est else 0

        # Dato real del período pronosticado (si ya ocurrió)
        actual = _query_month(fy, fm)
        n_real   = int(actual["n"])   if actual and actual.get("n") else None
        rec_real = int(actual["rec"]) if actual and actual.get("n") else None
        rate_real = round(rec_real / n_real * 100, 1) if n_real else None

        pronostico.append({
            "anio": fy, "mes": fm,
            "label":          f"{MESES[fm]} {str(fy)[2:]}",
            "n_piezas_est":   n_est,
            "n_rechazos_est": rec_est,
            "rate_est":       rate_est,
            "tons_est":       tons_est,
            "tons_rech_est":  round(tons_est * rate_est / 100, 1),
            "confianza":      confianza,
            "metodo":         metodo,
            "n_piezas_real":  n_real,
            "rate_real":      rate_real,
        })
        if p_idx == 0:
            n_est_first = n_est

    # ── Partes 80/20 ──────────────────────────────────────────────
    parts_agg: dict = {}

    def _acum_parts(rows):
        for r in rows:
            p = r["no_parte"]
            parts_agg.setdefault(p, {"n": 0, "rec": 0})
            parts_agg[p]["n"]   += int(r["n"] or 0)
            parts_agg[p]["rec"] += int(r["rec"] or 0)

    if pre_months:
        h_fp = date(pre_months[-1].year, pre_months[-1].month,
                    cal_mod.monthrange(pre_months[-1].year, pre_months[-1].month)[1])
        _acum_parts(run("""
            SELECT No_Parte AS no_parte, COUNT(*) AS n, SUM(bRechazo) AS rec
            FROM alfamega_01_rechazosbyidticket
            WHERE FHrVaciado BETWEEN :d AND :h AND No_Parte IS NOT NULL AND No_Parte != ''
            GROUP BY No_Parte
        """, {"d": months[0].isoformat() + " 00:00:00", "h": h_fp.isoformat() + " 23:59:59"}))

    if post_months:
        h_pp = date(post_months[-1].year, post_months[-1].month,
                    cal_mod.monthrange(post_months[-1].year, post_months[-1].month)[1])
        _acum_parts(run("""
            SELECT rt.No_Parte AS no_parte, COUNT(*) AS n, SUM(rb.bRechazo) AS rec
            FROM cscmega_01rechazosbyidticket rb
            JOIN cscmega_01resultadoidticket rt ON rt.idTicket = rb.idTicket
            WHERE rb.u_Fecha BETWEEN :d AND :h AND rt.No_Parte IS NOT NULL AND rt.No_Parte != ''
            GROUP BY rt.No_Parte
        """, {"d": post_months[0].isoformat() + " 00:00:00", "h": h_pp.isoformat() + " 23:59:59"}))

    total_vol = sum(d["n"] for d in parts_agg.values())
    sorted_parts = sorted(parts_agg.items(), key=lambda x: x[1]["n"], reverse=True)
    partes_80, acum = [], 0
    for no_parte, d in sorted_parts:
        acum += d["n"]
        pct_acum = round(acum / total_vol * 100, 1) if total_vol else 0
        rate_p   = round(d["rec"] / d["n"] * 100, 1) if d["n"] else 0
        share    = d["n"] / total_vol if total_vol else 0
        prod_est_p = round(share * n_est_first)
        rech_est_p = round(rate_p / 100 * prod_est_p)
        prob = ml_score(no_parte)
        ml_tend = None
        if prob is not None and d["n"] >= 10:
            rf = rate_p / 100
            ml_tend = "up" if prob > rf + 0.05 else "down" if prob < rf - 0.05 else "flat"
        partes_80.append({
            "no_parte":    no_parte,
            "n_ventana":   d["n"],
            "rate_ventana": rate_p,
            "pct_vol_acum": pct_acum,
            "prod_est":    prod_est_p,
            "rech_est":    rech_est_p,
            "peso_kgs":    (lookup.get(no_parte) or {}).get("peso_kgs"),
            "ml_tend":     ml_tend,
        })
        if pct_acum >= 80:
            break

    return {
        "ventana":   ventana,
        "max_date":  max_date.isoformat(),
        "historico": historico,
        "pronostico": pronostico,
        "partes_80": partes_80,
    }


@app.get("/v2/ejecutivo/cierre-ciclo")
def v2_cierre_ciclo(anio: int = Query(default=2025)):
    """
    Distribución de coladas por tiempo de cierre de ciclo.
    Grupos: cerradas (≤8h…>32h), cuarentena (>120h sin cerrar), en_proceso (<120h sin cerrar), atipico (>30d).
    La referencia para 'abierto' es el fin del año analizado.
    """
    d_ini = f"{anio}-01-01 00:00:00"
    d_fin = f"{anio}-12-31 23:59:59"

    if anio < 2025:
        return {"anio": anio, "total_coladas": 0, "cerradas": 0, "en_meta_24h": 0,
                "pct_en_meta": 0, "cuarentena": 0, "cuarentena_piezas": 0, "distribucion": []}

    rows = run("""
        SELECT
            CASE
                WHEN sub.n_limp >= sub.total AND sub.ciclo_h <=   8 THEN 'h08'
                WHEN sub.n_limp >= sub.total AND sub.ciclo_h <=  16 THEN 'h16'
                WHEN sub.n_limp >= sub.total AND sub.ciclo_h <=  24 THEN 'h24'
                WHEN sub.n_limp >= sub.total AND sub.ciclo_h <=  32 THEN 'h32'
                WHEN sub.n_limp >= sub.total AND sub.ciclo_h <= 720 THEN 'h32p'
                WHEN sub.n_limp >= sub.total                        THEN 'atipico'
                WHEN TIMESTAMPDIFF(HOUR, sub.vaci_min, :ref) < 120  THEN 'en_proceso'
                ELSE                                                     'cuarentena'
            END AS bucket,
            COUNT(*)   AS coladas,
            ROUND(AVG(CASE
                WHEN sub.n_limp >= sub.total AND sub.ciclo_h <= 720
                THEN sub.ciclo_h END), 1)          AS ciclo_prom_h,
            SUM(sub.total - sub.n_limp)            AS piezas_pendientes
        FROM (
            SELECT
                rb.u_Colada, rb.u_Horno,
                COUNT(*)        AS total,
                SUM(r.bLIMP)    AS n_limp,
                MIN(r.FhrVACI)  AS vaci_min,
                ROUND(TIMESTAMPDIFF(MINUTE,
                    MIN(r.FhrVACI), MAX(r.FHrLIMP)) / 60.0, 1) AS ciclo_h
            FROM cscmega_01rechazosbyidticket rb
            JOIN cscmega_08ruta r ON r.u_idTicket = rb.idTicket
            WHERE rb.u_Fecha BETWEEN :d AND :h
              AND r.FhrVACI IS NOT NULL
            GROUP BY rb.u_Colada, rb.u_Horno
        ) sub
        GROUP BY bucket
    """, {"d": d_ini, "h": d_fin, "ref": d_fin})

    ORDER  = ["h08", "h16", "h24", "h32", "h32p", "en_proceso", "cuarentena", "atipico"]
    LABELS = {
        "h08":       "≤ 8 h",
        "h16":       "8 – 16 h",
        "h24":       "16 – 24 h",
        "h32":       "24 – 32 h",
        "h32p":      "> 32 h",
        "en_proceso":"En proceso  (< 120 h)",
        "cuarentena":"Cuarentena  (> 120 h sin cierre)",
        "atipico":   "Atípico  (> 30 días)",
    }
    idx   = {r["bucket"]: r for r in rows}
    total = sum(int(r["coladas"]) for r in rows)

    distribucion = []
    for k in ORDER:
        r = idx.get(k, {})
        n = int(r.get("coladas") or 0)
        distribucion.append({
            "bucket":          k,
            "label":           LABELS[k],
            "n":               n,
            "pct":             round(n / total * 100, 1) if total else 0,
            "ciclo_prom_h":    float(r.get("ciclo_prom_h") or 0),
            "piezas_pendientes": int(r.get("piezas_pendientes") or 0),
        })

    cerradas  = sum(d["n"] for d in distribucion
                    if d["bucket"] in ("h08","h16","h24","h32","h32p"))
    en_meta   = sum(d["n"] for d in distribucion
                    if d["bucket"] in ("h08","h16","h24"))
    cuarentena_n = idx.get("cuarentena", {}).get("coladas") or 0
    cuarentena_pz = idx.get("cuarentena", {}).get("piezas_pendientes") or 0

    return {
        "anio":            anio,
        "total_coladas":   total,
        "cerradas":        cerradas,
        "en_meta_24h":     en_meta,
        "pct_en_meta":     round(en_meta / cerradas * 100, 1) if cerradas else 0,
        "cuarentena":      int(cuarentena_n),
        "cuarentena_piezas": int(cuarentena_pz),
        "distribucion":    distribucion,
    }


@app.get("/v2/ml/riesgo-parte")
def v2_ml_riesgo_parte(
    no_parte:   str            = Query(...),
    temp_ok:    Optional[float]= Query(default=None),
    pc5_cumple: Optional[float]= Query(default=None),
    mes:        Optional[int]  = Query(default=None),
    horno:      Optional[str]  = Query(default=None),
):
    """
    Score ML de riesgo de rechazo para un numero de parte.
    Devuelve prob_rechazo (0-1) y metadata de la parte desde el lookup.
    Features de proceso (temp_ok, pc5_cumple, mes, horno) son opcionales;
    sin ellos usa sentinel y el score se basa principalmente en historial de parte y peso.
    """
    prob = ml_score(no_parte, temp_ok, pc5_cumple, mes, horno)
    parte_info = (_PARTE_LOOKUP or {}).get(no_parte, {})
    return {
        "no_parte":       no_parte,
        "prob_rechazo":   prob,
        "rate_prior_2024": parte_info.get("rate_prior"),
        "peso_kgs":        parte_info.get("peso_kgs"),
        "sin_historial":   parte_info.get("sin_hist", 1),
        "modelo_cargado":  _ML_MODEL is not None,
    }


@app.get("/v2/ml/riesgo-colada")
def v2_ml_riesgo_colada(
    u_colada: int,
    horno:    int,
    desde:    str = Query(default=None),
    hasta:    str = Query(default=None),
):
    """
    Score ML promedio para una colada completa.
    Agrega el score por pieza (no_parte) ponderado por n piezas de ese tipo en la colada.
    """
    d, h = rango(desde, hasta)
    tickets = run("""
        SELECT rt.No_Parte AS no_parte, COUNT(*) AS n,
               ROUND(AVG(rb.bRechazo)*100,1) AS rate_real,
               MAX(v.bCumplimientoTemperaturaOk) AS temp_ok
        FROM cscmega_01rechazosbyidticket rb
        JOIN cscmega_01resultadoidticket rt ON rt.idTicket = rb.idTicket
        LEFT JOIN alfamega_06_vaciado v ON v.idTicket = rb.idTicket
        WHERE rb.u_Colada = :col AND rb.u_Horno = :horn
          AND rb.u_Fecha BETWEEN :d AND :h
          AND rt.No_Parte IS NOT NULL AND rt.No_Parte != ''
        GROUP BY rt.No_Parte ORDER BY n DESC
    """, {"col": u_colada, "horn": horno, "d": d, "h": h})

    if not tickets:
        return {"u_colada": u_colada, "horno": horno, "prob_promedio": None, "partes": []}

    mes_actual = datetime.now().month
    partes_scored = []
    total_n = sum(int(t["n"]) for t in tickets)

    for t in tickets:
        temp = None if t["temp_ok"] is None else float(t["temp_ok"])
        prob = ml_score(t["no_parte"], temp_ok=temp, mes=mes_actual, horno=str(horno))
        partes_scored.append({
            "no_parte":     t["no_parte"],
            "n":            int(t["n"]),
            "rate_real":    float(t["rate_real"] or 0),
            "prob_rechazo": prob,
            "rate_prior":   (_PARTE_LOOKUP or {}).get(t["no_parte"], {}).get("rate_prior"),
        })

    if any(p["prob_rechazo"] is not None for p in partes_scored):
        prob_pond = sum(
            p["prob_rechazo"] * p["n"] for p in partes_scored if p["prob_rechazo"] is not None
        ) / total_n
    else:
        prob_pond = None

    return {
        "u_colada":     u_colada,
        "horno":        horno,
        "prob_promedio": round(prob_pond, 4) if prob_pond is not None else None,
        "total_piezas": total_n,
        "partes":       partes_scored,
    }


@app.get("/v2/ejecutivo/partes-scatter")
def v2_partes_scatter(anio: int = Query(default=2025)):
    """
    Scatter de rechazo por NoParte para el año indicado.
    Retorna: no_parte, n (piezas), rechazos, rate (%), tons, tons_rech.
    Filtrado a partes con n >= 30 piezas en el año.
    """
    d_ini = f"{anio}-01-01 00:00:00"
    d_fin = f"{anio}-12-31 23:59:59"

    if anio >= 2025:
        rows = run("""
            SELECT rt.No_Parte AS no_parte,
                   COUNT(*)                                              AS n,
                   SUM(rb.bRechazo)                                      AS rechazos,
                   ROUND(AVG(rb.bRechazo)*100, 2)                        AS rate,
                   ROUND(SUM(COALESCE(m.PesoKgs,0))/1000.0, 2)          AS tons,
                   ROUND(SUM(CASE WHEN rb.bRechazo=1 THEN COALESCE(m.PesoKgs,0) ELSE 0 END)/1000.0, 2) AS tons_rech,
                   COUNT(DISTINCT MONTH(rb.u_Fecha))                     AS meses_activa
            FROM cscmega_01rechazosbyidticket rb
            JOIN cscmega_01resultadoidticket rt ON rt.idTicket = rb.idTicket
            LEFT JOIN (
                SELECT NoParte, MAX(IdModelo) AS IdModelo
                FROM corex_test.modelos WHERE PesoKgs > 0 GROUP BY NoParte
            ) best ON best.NoParte = rt.No_Parte
            LEFT JOIN corex_test.modelos m ON m.IdModelo = best.IdModelo
            WHERE rb.u_Fecha BETWEEN :d AND :h
              AND rt.No_Parte IS NOT NULL AND rt.No_Parte != ''
            GROUP BY rt.No_Parte
            HAVING n >= 100
            ORDER BY tons_rech DESC
        """, {"d": d_ini, "h": d_fin})
    else:
        rows = run("""
            SELECT rb.No_Parte AS no_parte,
                   COUNT(*)                                              AS n,
                   SUM(rb.bRechazo)                                      AS rechazos,
                   ROUND(AVG(rb.bRechazo)*100, 2)                        AS rate,
                   ROUND(SUM(COALESCE(m.PesoKgs,0))/1000.0, 2)          AS tons,
                   ROUND(SUM(CASE WHEN rb.bRechazo=1 THEN COALESCE(m.PesoKgs,0) ELSE 0 END)/1000.0, 2) AS tons_rech,
                   COUNT(DISTINCT MONTH(rb.FHrVaciado))                  AS meses_activa
            FROM alfamega_01_rechazosbyidticket rb
            LEFT JOIN (
                SELECT NoParte, MAX(IdModelo) AS IdModelo
                FROM corex_test.modelos WHERE PesoKgs > 0 GROUP BY NoParte
            ) best ON best.NoParte = rb.No_Parte
            LEFT JOIN corex_test.modelos m ON m.IdModelo = best.IdModelo
            WHERE rb.FHrVaciado BETWEEN :d AND :h
              AND rb.No_Parte IS NOT NULL AND rb.No_Parte != ''
            GROUP BY rb.No_Parte
            HAVING n >= 100
            ORDER BY tons_rech DESC
        """, {"d": d_ini, "h": d_fin})

    return [
        {
            "no_parte":     r["no_parte"],
            "n":            int(r["n"] or 0),
            "rechazos":     int(r["rechazos"] or 0),
            "rate":         float(r["rate"] or 0),
            "tons":         float(r["tons"] or 0),
            "tons_rech":    float(r["tons_rech"] or 0),
            "meses_activa": int(r["meses_activa"] or 0),
        }
        for r in rows
    ]


@app.get("/v2/gestion/riesgo-partes")
def v2_riesgo_partes(anio: int = None, mes: int = None, ventana: int = 12):
    """
    Catalogo de probabilidad de rechazo por numero de parte.
    ventana: meses a analizar (6, 12, 18 o 24). Default 12.
    Si se pasan anio+mes: ventana de meses previos al mes objetivo (prior al mes a simular).
    Sin params: ventana hacia atras desde el ultimo dato disponible.
    """
    from datetime import date, timedelta
    import calendar

    def _restar_meses(d: date, meses: int) -> date:
        m = d.month - meses
        y = d.year
        while m <= 0:
            m += 12
            y -= 1
        return date(y, m, 1)

    if anio and mes:
        max_date = date(anio, mes, 1) - timedelta(days=1)
        ini_date = _restar_meses(date(anio, mes, 1), ventana)
    else:
        max_row = run("SELECT MAX(DATE(u_Fecha)) AS mx FROM cscmega_01rechazosbyidticket", {})
        max_date_str = str(max_row[0]["mx"]) if max_row and max_row[0]["mx"] else "2025-12-31"
        max_date = date.fromisoformat(max_date_str)
        ini_date = _restar_meses(max_date, ventana)
    d_ini = ini_date.isoformat() + " 00:00:00"
    d_fin = max_date.isoformat()  + " 23:59:59"
    PIVOT = "2025-01-01 00:00:00"

    # Datos de 2025 (cscmega)
    rows_2025 = []
    if d_fin >= PIVOT:
        d_ini_25 = max(d_ini, PIVOT)
        rows_2025 = run("""
            SELECT rt.No_Parte AS no_parte,
                   COUNT(*) AS n, SUM(rb.bRechazo) AS rechazos
            FROM cscmega_01rechazosbyidticket rb
            JOIN cscmega_01resultadoidticket rt ON rt.idTicket = rb.idTicket
            WHERE rb.u_Fecha BETWEEN :d AND :h
              AND rt.No_Parte IS NOT NULL AND rt.No_Parte != ''
            GROUP BY rt.No_Parte
        """, {"d": d_ini_25, "h": d_fin})

    # Datos pre-2025 (alfamega) si el rango lo requiere
    rows_hist = []
    if d_ini < PIVOT:
        d_fin_hist = min(d_fin, "2024-12-31 23:59:59")
        rows_hist = run("""
            SELECT No_Parte AS no_parte,
                   COUNT(*) AS n, SUM(bRechazo) AS rechazos
            FROM alfamega_01_rechazosbyidticket
            WHERE FHrVaciado BETWEEN :d AND :h
              AND No_Parte IS NOT NULL AND No_Parte != ''
            GROUP BY No_Parte
        """, {"d": d_ini, "h": d_fin_hist})

    # Merge en Python: acumular n y rechazos por parte
    agg: dict = {}
    for r in rows_hist + rows_2025:
        p = r["no_parte"]
        if p not in agg:
            agg[p] = {"n": 0, "rechazos": 0}
        agg[p]["n"]        += int(r["n"] or 0)
        agg[p]["rechazos"] += int(r["rechazos"] or 0)

    # Score ML + métricas por parte
    lookup = _PARTE_LOOKUP or {}
    resultado = []
    for no_parte, d in agg.items():
        n, rec = d["n"], d["rechazos"]
        if n < 50:          # partes con muy poco volumen no son representativas
            continue
        rate_periodo = round(rec / n * 100, 1) if n else 0
        prob = ml_score(no_parte, rate_prior_override=rec/n if n else None)
        parte_info = lookup.get(no_parte, {})
        rech_esp = round(prob * n, 0) if prob is not None else None
        resultado.append({
            "no_parte":        no_parte,
            "n_periodo":       n,
            "rechazos_periodo": rec,
            "rate_periodo":    rate_periodo,
            "prob_ml":         prob,
            "rechazos_esp":    int(rech_esp) if rech_esp is not None else None,
            "rate_prior":      round(float(parte_info.get("rate_prior", 0)) * 100, 1) if parte_info else None,
            "peso_kgs":        parte_info.get("peso_kgs"),
            "sin_historial":   parte_info.get("sin_hist", 1),
        })

    # ── Shrinkage Bayesiano ──────────────────────────────────────────────────
    # Tasa global ponderada por volumen (prior empírico)
    total_n   = sum(r["n_periodo"]        for r in resultado)
    total_rec = sum(r["rechazos_periodo"] for r in resultado)
    global_rate = total_rec / total_n if total_n else 0.15

    # k = tamaño de muestra del prior; con n=k el peso propio es 50%
    # k=75 → parte con 75 piezas: w=0.5 | 200 piezas: w=0.73 | 500: w=0.87
    K = 75
    for r in resultado:
        n   = r["n_periodo"]
        rec = r["rechazos_periodo"]
        w   = n / (n + K)
        r["rate_shrunk"] = round((w * (rec / n) + (1 - w) * global_rate) * 100, 1) if n else round(global_rate * 100, 1)
        r["shrink_w"]    = round(w, 2)   # peso de la tasa propia (0-1)
    # ────────────────────────────────────────────────────────────────────────

    resultado.sort(key=lambda x: x["rechazos_esp"] or 0, reverse=True)
    return {
        "ventana_meses": ventana,
        "global_rate":   round(global_rate * 100, 1),
        "shrink_k":      K,
        "ventana":       {"desde": d_ini[:10], "hasta": d_fin[:10]},
        "partes":        resultado,
    }


@app.get("/v2/gestion/simulacion-programa")
def v2_simulacion_programa(anio: int = 2025, mes: int = 3, ventana: int = 12):
    """
    Simulacion de prediccion de rechazo para un mes dado.
    - 'Programa': produccion real del mes objetivo (n piezas por parte).
    - 'Prediccion': tasa historica `ventana` meses previos al mes × n piezas (sin leakage).
    - 'Real': rechazos que ocurrieron en el mes objetivo.
    - prob_ml: senal de tendencia ML (ordinal, no absolutamente calibrada).
    """
    from datetime import date, timedelta
    import calendar

    ventana = max(6, min(ventana, 24))  # clamp 6–24

    last_day = calendar.monthrange(anio, mes)[1]
    d_ini = f"{anio}-{mes:02d}-01 00:00:00"
    d_fin = f"{anio}-{mes:02d}-{last_day:02d} 23:59:59"

    # Ventana historica: `ventana` meses antes del inicio del mes objetivo
    h_mes  = (mes - 1 - ventana) % 12 + 1
    h_anio = anio + (mes - 1 - ventana) // 12
    t_hist_ini = date(h_anio, h_mes, 1)
    t_hist_fin = date(anio, mes, 1) - timedelta(days=1)
    h_ini = t_hist_ini.isoformat() + " 00:00:00"
    h_fin = t_hist_fin.isoformat() + " 23:59:59"
    PIVOT = "2025-01-01 00:00:00"

    # --- Produccion real del mes objetivo ---
    if anio >= 2025:
        rows_mes = run("""
            SELECT rt.No_Parte AS no_parte,
                   COUNT(*) AS n,
                   SUM(rb.bRechazo) AS rechazos_real
            FROM cscmega_01rechazosbyidticket rb
            JOIN cscmega_01resultadoidticket rt ON rt.idTicket = rb.idTicket
            WHERE rb.u_Fecha BETWEEN :d AND :h
              AND rt.No_Parte IS NOT NULL AND rt.No_Parte != ''
            GROUP BY rt.No_Parte
        """, {"d": d_ini, "h": d_fin})
    else:
        rows_mes = run("""
            SELECT No_Parte AS no_parte,
                   COUNT(*) AS n,
                   SUM(bRechazo) AS rechazos_real
            FROM alfamega_01_rechazosbyidticket
            WHERE FHrVaciado BETWEEN :d AND :h
              AND No_Parte IS NOT NULL AND No_Parte != ''
            GROUP BY No_Parte
        """, {"d": d_ini, "h": d_fin})

    # --- Tasa historica 12m previos (cross-table) ---
    hist_agg: dict = {}
    if h_fin >= PIVOT:
        h_ini_25 = max(h_ini, PIVOT)
        for r in run("""
            SELECT rt.No_Parte AS no_parte,
                   COUNT(*) AS n, SUM(rb.bRechazo) AS rec
            FROM cscmega_01rechazosbyidticket rb
            JOIN cscmega_01resultadoidticket rt ON rt.idTicket = rb.idTicket
            WHERE rb.u_Fecha BETWEEN :d AND :h
              AND rt.No_Parte IS NOT NULL AND rt.No_Parte != ''
            GROUP BY rt.No_Parte
        """, {"d": h_ini_25, "h": h_fin}):
            p = r["no_parte"]
            hist_agg.setdefault(p, {"n": 0, "rec": 0})
            hist_agg[p]["n"]   += int(r["n"] or 0)
            hist_agg[p]["rec"] += int(r["rec"] or 0)

    if h_ini < PIVOT:
        h_fin_hist = min(h_fin, "2024-12-31 23:59:59")
        for r in run("""
            SELECT No_Parte AS no_parte,
                   COUNT(*) AS n, SUM(bRechazo) AS rec
            FROM alfamega_01_rechazosbyidticket
            WHERE FHrVaciado BETWEEN :d AND :h
              AND No_Parte IS NOT NULL AND No_Parte != ''
            GROUP BY No_Parte
        """, {"d": h_ini, "h": h_fin_hist}):
            p = r["no_parte"]
            hist_agg.setdefault(p, {"n": 0, "rec": 0})
            hist_agg[p]["n"]   += int(r["n"] or 0)
            hist_agg[p]["rec"] += int(r["rec"] or 0)

    GLOBAL_RATE = 0.1509  # fallback si la parte no tiene historial previo

    # Tasa global empírica del historial para shrinkage
    total_hist_n   = sum(v["n"]   for v in hist_agg.values())
    total_hist_rec = sum(v["rec"] for v in hist_agg.values())
    global_rate_hist = total_hist_rec / total_hist_n if total_hist_n else GLOBAL_RATE
    K = 75  # mismo K que en riesgo-partes

    partes = []
    total_n = 0
    total_pred = 0.0
    total_real = 0

    for r in rows_mes:
        n = int(r["n"] or 0)
        real = int(r["rechazos_real"] or 0)
        if n < 5:
            continue
        h = hist_agg.get(r["no_parte"], {})
        hn = h.get("n", 0)
        rate_hist = h["rec"] / hn if hn >= 10 else GLOBAL_RATE
        # Shrinkage Bayesiano sobre la tasa histórica
        w = hn / (hn + K) if hn > 0 else 0
        rate_shrunk = w * rate_hist + (1 - w) * global_rate_hist
        pred = round(rate_shrunk * n, 1)

        partes.append({
            "no_parte":      r["no_parte"],
            "n_piezas":      n,
            "rate_hist":     round(rate_hist * 100, 1),
            "rate_shrunk":   round(rate_shrunk * 100, 1),
            "rechazos_pred": pred,
            "rechazos_real": real,
            "rate_real":     round(real / n * 100, 1) if n else 0,
            "delta":         round(pred - real, 1),
            "hist_n":        hn,
            "shrink_w":      round(w, 2),
        })
        total_n    += n
        total_pred += pred
        total_real += real

    partes.sort(key=lambda x: x["rechazos_pred"], reverse=True)

    rate_pred_total = round(total_pred / total_n * 100, 1) if total_n else 0
    rate_real_total = round(total_real / total_n * 100, 1) if total_n else 0

    return {
        "periodo": {"anio": anio, "mes": mes, "desde": d_ini[:10], "hasta": d_fin[:10],
                    "hist_desde": h_ini[:10], "hist_hasta": h_fin[:10]},
        "ventana": ventana,
        "resumen": {
            "total_piezas":  total_n,
            "rechazos_pred": round(total_pred, 0),
            "rechazos_real": total_real,
            "rate_pred":     rate_pred_total,
            "rate_real":     rate_real_total,
            "delta_pct":     round(rate_pred_total - rate_real_total, 1),
            "n_partes":      len(partes),
        },
        "partes": partes,
    }


@app.get("/v2/gestion/simulacion-trimestre")
def v2_simulacion_trimestre(anio: int = 2025, trimestre: int = 1, ventana: int = 12):
    """
    Simulacion trimestral: agrega los 3 meses del trimestre como una sola unidad.
    - trimestre: 1=Ene-Mar, 2=Abr-Jun, 3=Jul-Sep, 4=Oct-Dic
    - ventana: meses de historial previos al trimestre (6/12/18/24)
    """
    from datetime import date, timedelta
    import calendar

    trimestre = max(1, min(trimestre, 4))
    ventana   = max(6, min(ventana, 24))

    mes_ini = (trimestre - 1) * 3 + 1   # 1, 4, 7, 10
    mes_fin = mes_ini + 2                # 3, 6, 9, 12
    last_day = calendar.monthrange(anio, mes_fin)[1]

    d_ini = f"{anio}-{mes_ini:02d}-01 00:00:00"
    d_fin = f"{anio}-{mes_fin:02d}-{last_day:02d} 23:59:59"

    # Ventana historica: `ventana` meses antes del inicio del trimestre
    h_mes  = (mes_ini - 1 - ventana) % 12 + 1
    h_anio = anio + (mes_ini - 1 - ventana) // 12
    t_hist_ini = date(h_anio, h_mes, 1)
    t_hist_fin = date(anio, mes_ini, 1) - timedelta(days=1)

    h_ini = t_hist_ini.isoformat() + " 00:00:00"
    h_fin = t_hist_fin.isoformat() + " 23:59:59"
    PIVOT = "2025-01-01 00:00:00"

    # --- Produccion real del trimestre ---
    if anio >= 2025:
        rows_tri = run("""
            SELECT rt.No_Parte AS no_parte,
                   COUNT(*) AS n,
                   SUM(rb.bRechazo) AS rechazos_real
            FROM cscmega_01rechazosbyidticket rb
            JOIN cscmega_01resultadoidticket rt ON rt.idTicket = rb.idTicket
            WHERE rb.u_Fecha BETWEEN :d AND :h
              AND rt.No_Parte IS NOT NULL AND rt.No_Parte != ''
            GROUP BY rt.No_Parte
        """, {"d": d_ini, "h": d_fin})
    else:
        rows_tri = run("""
            SELECT No_Parte AS no_parte,
                   COUNT(*) AS n,
                   SUM(bRechazo) AS rechazos_real
            FROM alfamega_01_rechazosbyidticket
            WHERE FHrVaciado BETWEEN :d AND :h
              AND No_Parte IS NOT NULL AND No_Parte != ''
            GROUP BY No_Parte
        """, {"d": d_ini, "h": d_fin})

    # --- Tasa historica previos al trimestre ---
    hist_agg: dict = {}
    if h_fin >= PIVOT:
        h_ini_25 = max(h_ini, PIVOT)
        for r in run("""
            SELECT rt.No_Parte AS no_parte,
                   COUNT(*) AS n, SUM(rb.bRechazo) AS rec
            FROM cscmega_01rechazosbyidticket rb
            JOIN cscmega_01resultadoidticket rt ON rt.idTicket = rb.idTicket
            WHERE rb.u_Fecha BETWEEN :d AND :h
              AND rt.No_Parte IS NOT NULL AND rt.No_Parte != ''
            GROUP BY rt.No_Parte
        """, {"d": h_ini_25, "h": h_fin}):
            p = r["no_parte"]
            hist_agg.setdefault(p, {"n": 0, "rec": 0})
            hist_agg[p]["n"]   += int(r["n"]   or 0)
            hist_agg[p]["rec"] += int(r["rec"] or 0)

    if h_ini < PIVOT:
        h_fin_hist = min(h_fin, "2024-12-31 23:59:59")
        for r in run("""
            SELECT No_Parte AS no_parte,
                   COUNT(*) AS n, SUM(bRechazo) AS rec
            FROM alfamega_01_rechazosbyidticket
            WHERE FHrVaciado BETWEEN :d AND :h
              AND No_Parte IS NOT NULL AND No_Parte != ''
            GROUP BY No_Parte
        """, {"d": h_ini, "h": h_fin_hist}):
            p = r["no_parte"]
            hist_agg.setdefault(p, {"n": 0, "rec": 0})
            hist_agg[p]["n"]   += int(r["n"]   or 0)
            hist_agg[p]["rec"] += int(r["rec"] or 0)

    GLOBAL_RATE      = 0.1509
    total_hist_n     = sum(v["n"]   for v in hist_agg.values())
    total_hist_rec   = sum(v["rec"] for v in hist_agg.values())
    global_rate_hist = total_hist_rec / total_hist_n if total_hist_n else GLOBAL_RATE
    K = 75

    partes = []
    total_n = 0; total_pred = 0.0; total_real = 0

    for r in rows_tri:
        n    = int(r["n"] or 0)
        real = int(r["rechazos_real"] or 0)
        if n < 5:
            continue
        h        = hist_agg.get(r["no_parte"], {})
        hn       = h.get("n", 0)
        rate_hist = h["rec"] / hn if hn >= 10 else GLOBAL_RATE
        w         = hn / (hn + K) if hn > 0 else 0
        rate_shrunk = w * rate_hist + (1 - w) * global_rate_hist
        pred = round(rate_shrunk * n, 1)

        partes.append({
            "no_parte":      r["no_parte"],
            "n_piezas":      n,
            "rate_hist":     round(rate_hist * 100, 1),
            "rate_shrunk":   round(rate_shrunk * 100, 1),
            "rechazos_pred": pred,
            "rechazos_real": real,
            "rate_real":     round(real / n * 100, 1) if n else 0,
            "delta":         round(pred - real, 1),
            "hist_n":        hn,
            "shrink_w":      round(w, 2),
        })
        total_n    += n
        total_pred += pred
        total_real += real

    partes.sort(key=lambda x: x["rechazos_pred"], reverse=True)

    rate_pred_total = round(total_pred / total_n * 100, 1) if total_n else 0
    rate_real_total = round(total_real / total_n * 100, 1) if total_n else 0
    NOMBRES_Q = {1: "Q1 · Ene–Mar", 2: "Q2 · Abr–Jun", 3: "Q3 · Jul–Sep", 4: "Q4 · Oct–Dic"}

    return {
        "periodo": {"anio": anio, "trimestre": trimestre,
                    "label": NOMBRES_Q[trimestre],
                    "desde": d_ini[:10], "hasta": d_fin[:10],
                    "hist_desde": h_ini[:10], "hist_hasta": h_fin[:10]},
        "ventana": ventana,
        "resumen": {
            "total_piezas":  total_n,
            "rechazos_pred": round(total_pred, 0),
            "rechazos_real": total_real,
            "rate_pred":     rate_pred_total,
            "rate_real":     rate_real_total,
            "delta_pct":     round(rate_pred_total - rate_real_total, 1),
            "n_partes":      len(partes),
        },
        "partes": partes,
    }


@app.get("/v2/gestion/pronostico-programa")
def v2_pronostico_programa(anio: int = 2025):
    """
    Pronóstico de rechazos por familia de defecto en el horizonte mensual.
    Cruza el programa de producción real de cada mes del año con las tasas
    de gamamega_01_riesgodefectoparte (sábana 2025), devolviendo rechazos
    esperados por familia + Pareto por parte, para cada mes del año.
    Una sola llamada = 2 queries DB (gamamega + producción anual).
    """
    import calendar

    MESES_NOM = ['','Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']

    # 1. Cargar tasas de gamamega (1,379 filas — estática)
    gama_rows = run(f"""
        SELECT No_Parte, nomDefecto,
               COALESCE(PrcRchz2025, 0.0)               AS prc25,
               COALESCE(FlagCriticidadDefectoParte, 0)   AS flag_crit,
               COALESCE(FlagTendInterAnual, 0)           AS flag_tend,
               {FAMILIA_SQL}                             AS familia
        FROM gamamega_01_riesgodefectoparte
        WHERE COALESCE(PrcRchz2025, 0) > 0
    """)
    gama_by_parte: dict = {}
    for g in gama_rows:
        p = g["No_Parte"]
        gama_by_parte.setdefault(p, []).append(g)

    # 2. Producción real + rechazos reales, agrupados mes × parte
    if anio >= 2025:
        prod_rows = run("""
            SELECT MONTH(rb.u_Fecha) AS mes,
                   rt.No_Parte       AS no_parte,
                   COUNT(*)          AS n_piezas,
                   SUM(rb.bRechazo)  AS real_rec
            FROM cscmega_01rechazosbyidticket rb
            JOIN cscmega_01resultadoidticket rt ON rt.idTicket = rb.idTicket
            WHERE rb.u_Fecha BETWEEN :d AND :h
              AND rt.No_Parte IS NOT NULL AND rt.No_Parte != ''
            GROUP BY MONTH(rb.u_Fecha), rt.No_Parte
            HAVING COUNT(*) >= 5
        """, {"d": f"{anio}-01-01 00:00:00", "h": f"{anio}-12-31 23:59:59"})
    else:
        prod_rows = run("""
            SELECT MONTH(FHrVaciado) AS mes,
                   No_Parte          AS no_parte,
                   COUNT(*)          AS n_piezas,
                   SUM(bRechazo)     AS real_rec
            FROM alfamega_01_rechazosbyidticket
            WHERE FHrVaciado BETWEEN :d AND :h
              AND No_Parte IS NOT NULL AND No_Parte != ''
            GROUP BY MONTH(FHrVaciado), No_Parte
            HAVING COUNT(*) >= 5
        """, {"d": f"{anio}-01-01 00:00:00", "h": f"{anio}-12-31 23:59:59"})

    # 3. Cruce gamamega × producción en Python, por mes
    mes_data: dict = {}
    for r in prod_rows:
        m = int(r["mes"])
        p = r["no_parte"]
        n = int(r["n_piezas"] or 0)
        real = int(r["real_rec"] or 0)

        if m not in mes_data:
            mes_data[m] = {"familias": {}, "partes": {}, "real_rec": 0, "total_piezas": 0}
        mes_data[m]["real_rec"]     += real
        mes_data[m]["total_piezas"] += n

        for g in gama_by_parte.get(p, []):
            esp = float(g["prc25"]) * n
            fam = g["familia"]
            mes_data[m]["familias"][fam] = mes_data[m]["familias"].get(fam, 0) + esp

            pd = mes_data[m]["partes"]
            if p not in pd:
                pd[p] = {"n": n, "total_esp": 0.0, "familias": {}, "defectos": []}
            pd[p]["total_esp"] += esp
            pd[p]["familias"][fam] = pd[p]["familias"].get(fam, 0) + esp
            pd[p]["defectos"].append({
                "defecto":   str(g["nomDefecto"]),
                "familia":   fam,
                "esp":       round(esp, 1),
                "prc25":     float(g["prc25"]),
                "flag_crit": int(g["flag_crit"]),
                "flag_tend": int(g["flag_tend"]),
            })

    # 4. Construir salida mes a mes
    fam_anual: dict  = {}
    total_esp_anual  = 0.0
    total_real_anual = 0

    meses_out = []
    for m in range(1, 13):
        md = mes_data.get(m)
        if not md:
            meses_out.append({"mes": m, "lbl": MESES_NOM[m],
                               "total_piezas": 0, "total_esp": 0, "real_rec": 0,
                               "familias": {}, "partes": [], "top_combos": []})
            continue

        total_esp_mes = sum(md["familias"].values())
        total_esp_anual  += total_esp_mes
        total_real_anual += md["real_rec"]
        for fam, esp in md["familias"].items():
            fam_anual[fam] = fam_anual.get(fam, 0) + esp

        # Pareto de partes del mes
        cum = 0.0
        partes_sorted = sorted(md["partes"].items(), key=lambda x: -x[1]["total_esp"])
        partes_out = []
        for p, pd in partes_sorted:
            cum += pd["total_esp"]
            top_def = sorted(pd["defectos"], key=lambda x: -x["esp"])
            partes_out.append({
                "no_parte":   p,
                "n_piezas":   pd["n"],
                "total_esp":  round(pd["total_esp"], 1),
                "tasa_esp":   round(pd["total_esp"] / pd["n"] * 100, 1) if pd["n"] else 0,
                "top_defecto": top_def[0]["defecto"] if top_def else "",
                "flag_crit":  any(x["flag_crit"] for x in pd["defectos"]),
                "flag_tend":  any(x["flag_tend"] for x in pd["defectos"]),
                "cum_pct":    round(cum / total_esp_mes * 100, 1) if total_esp_mes else 0,
                "familias":   {k: round(v, 1) for k, v in pd["familias"].items()},
            })

        # Top combos parte × defecto del mes
        combos = []
        for p, pd in md["partes"].items():
            for d in sorted(pd["defectos"], key=lambda x: -x["esp"])[:3]:
                combos.append({"no_parte": p, "n_piezas": pd["n"], **d})
        combos.sort(key=lambda x: -x["esp"])

        meses_out.append({
            "mes":          m,
            "lbl":          MESES_NOM[m],
            "total_piezas": md["total_piezas"],
            "total_esp":    round(total_esp_mes, 1),
            "real_rec":     md["real_rec"],
            "familias":     {k: round(v, 1) for k, v in md["familias"].items()},
            "partes":       partes_out[:20],
            "top_combos":   combos[:15],
        })

    # KPIs anuales
    top_mes = max((m for m in range(1, 13) if m in mes_data),
                  key=lambda m: sum(mes_data[m]["familias"].values()),
                  default=None)

    return {
        "anio": anio,
        "meses": meses_out,
        "kpis": {
            "total_esp":     round(total_esp_anual, 0),
            "total_real":    total_real_anual,
            "top_mes":       top_mes,
            "top_mes_lbl":   MESES_NOM[top_mes] if top_mes else "—",
            "top_familia":   max(fam_anual, key=fam_anual.get) if fam_anual else "",
            "familias_anual": {k: round(v, 1) for k, v in
                               sorted(fam_anual.items(), key=lambda x: -x[1])},
        },
    }


@app.get("/v2/gestion/timeline")
def v2_gestion_timeline(
    referencia: str = Query(default="2025-12-18T16:00"),
    horas: int = Query(default=8),
):
    """
    Timeline para el Gantt de gestión — queries simples, respuesta rápida.
    Tiempos en minutos desde medianoche de la fecha de referencia.
    """
    ref_dt = datetime.fromisoformat(referencia)
    desde_dt = ref_dt - timedelta(hours=horas)
    d = desde_dt.strftime("%Y-%m-%d %H:%M:%S")
    h = ref_dt.strftime("%Y-%m-%d %H:%M:%S")
    midnight = ref_dt.replace(hour=0, minute=0, second=0, microsecond=0)

    def to_min(iso):
        if not iso: return None
        try:
            dt = iso if isinstance(iso, datetime) else datetime.fromisoformat(str(iso))
            return round((dt - midnight).total_seconds() / 60)
        except Exception:
            return None

    # 1. Coladas básicas — query simple y rápida
    coladas_raw = run("""
        SELECT c_IdColada AS id, MAX(u_Horno) AS horno,
               MIN(c_FechaInicial) AS ini_dt, MAX(c_FechaFinal) AS fin_dt,
               MAX(c_TiempoFundicion) AS min_fusion, MAX(c_Status) AS status
        FROM cscmega_03coladacargametalica
        WHERE c_FechaInicial BETWEEN :d AND :h AND c_IdColada > 0
        GROUP BY c_IdColada
        ORDER BY ini_dt
        LIMIT 60
    """, {"d": d, "h": h})

    if not coladas_raw:
        return {"anchor": to_min(ref_dt), "coladas": []}

    ids = [int(c["id"]) for c in coladas_raw]
    id_list = ",".join(str(i) for i in ids)

    # 2. Mapear c_IdColada → u_Colada (necesario para los índices siguientes)
    colada_num_raw = run(f"""
        SELECT c_IdColada, u_Colada
        FROM cscmega_03coladacargametalica
        WHERE c_IdColada IN ({id_list})
    """, {})
    cnum_idx = {c["c_IdColada"]: c["u_Colada"] for c in colada_num_raw}
    u_coladas = list(set(v for v in cnum_idx.values() if v))
    u_list = ",".join(str(u) for u in u_coladas) if u_coladas else "0"

    # 3. Vaciado reconstituido de cscmega_08ruta vía rechazos (query directa)
    vac_raw = run(f"""
        SELECT t.u_Colada, MIN(r.FhrVACI) AS lib_dt
        FROM cscmega_01rechazosbyidticket t
        JOIN cscmega_08ruta r ON r.u_idTicket = t.idTicket
        WHERE t.u_Fecha BETWEEN :d AND :h
          AND r.FhrVACI IS NOT NULL
        GROUP BY t.u_Colada
    """, {"d": d, "h": h})
    vac_idx = {v["u_Colada"]: v["lib_dt"] for v in vac_raw}

    # 4. Temperatura de vaciado (filtrada por u_Colada de la ventana)
    temp_raw = run(f"""
        SELECT u_Colada,
               ROUND(AVG(vt_TemperaturaVaciado)) AS temp
        FROM cscmega_06modelosvaciado
        WHERE u_Colada IN ({u_list})
          AND vt_TemperaturaVaciado BETWEEN 1100 AND 1600
        GROUP BY u_Colada
    """, {})
    temp_idx = {t["u_Colada"]: int(t["temp"]) for t in temp_raw if t["temp"]}

    # 5. Clase ITACA para determinar nodular
    clase_raw = run(f"""
        SELECT u_Colada, MAX(cls_ClaseITACA) AS clase
        FROM cscmega_04clase
        WHERE u_Colada IN ({u_list})
        GROUP BY u_Colada
    """, {})
    clase_idx = {c["u_Colada"]: (c["clase"] or "") for c in clase_raw}

    anchor = to_min(ref_dt)
    coladas = []
    for c in coladas_raw:
        cid = int(c["id"])
        u_col = cnum_idx.get(cid)
        ini = to_min(c["ini_dt"])
        fin = to_min(c["fin_dt"])
        lib_dt = vac_idx.get(u_col)
        lib = to_min(lib_dt) if lib_dt else fin
        clase = clase_idx.get(u_col, "—")[:12]
        nod = any(k in str(clase).upper() for k in
                  ["GGG","NODULAR","NL","65-45","80-55","100-70","60-40"])
        temp = temp_idx.get(u_col)
        coladas.append({
            "id":    cid,
            "horno": int(c["horno"]),
            "clase": clase,
            "nod":   nod,
            "carga": (ini - 20) if ini else ini,
            "ini":   ini,
            "fin":   fin,
            "lib":   lib or fin,
            "temp":  temp,
        })

    return {"anchor": anchor, "coladas": coladas}


@app.get("/v2/gestion/colada/{id_colada}/abanico")
def v2_abanico(id_colada: int):
    """Piezas individuales de la colada para el abanico (molde pills)."""
    tickets = run("""
        SELECT t.idTicket, t.bRechazo, t.nomDefecto,
               ROW_NUMBER() OVER (ORDER BY t.u_FechaHr) AS seq
        FROM cscmega_01resultadoidticket t
        JOIN cscmega_02pksquimicos pks ON pks.idTicket = t.idTicket
        WHERE pks.c_IdColada = :id
        ORDER BY t.u_FechaHr
        LIMIT 100
    """, {"id": id_colada})

    moldes = {}
    for t in tickets:
        k = str(int(t["seq"]))
        moldes[k] = [{"ok": not t["bRechazo"],
                      "def": t["nomDefecto"] or ""}]

    np = len(tickets)
    nr = sum(1 for t in tickets if t["bRechazo"])
    return {"np": np, "nr": nr, "moldes": moldes}


@app.get("/v2/gestion/perfil-parte")
def v2_perfil_parte(no_parte: str = Query(default=None)):
    """
    Perfil de rechazo por defecto para simulación prospectiva (tab Simulador).
    Sin no_parte: lista de 279 partes disponibles para el selector.
    Con no_parte: todos los defectos de esa parte — tasas 2023/2025,
                  z-score de criticidad, z-score de tendencia y flags.
    """
    if not no_parte:
        rows = run(
            "SELECT DISTINCT No_Parte FROM gamamega_01_riesgodefectoparte ORDER BY No_Parte",
            {}
        )
        return {"partes": [r["No_Parte"] for r in rows if r["No_Parte"]]}

    rows = run("""
        SELECT
            nomDefecto,
            COALESCE(nPzas2023, 0)                   AS n23,
            COALESCE(nRchz2023, 0)                   AS rchz23,
            COALESCE(PrcRchz2023, 0.0)               AS prc23,
            COALESCE(nPzas2025, 0)                   AS n25,
            COALESCE(nRchz2025, 0)                   AS rchz25,
            COALESCE(PrcRchz2025, 0.0)               AS prc25,
            COALESCE(zDefectoParte, 0.0)             AS z,
            COALESCE(FlagCriticidadDefectoParte, 0)  AS flag_crit,
            COALESCE(zInterAnual, 0.0)               AS z_tend,
            COALESCE(FlagTendInterAnual, 0)          AS flag_tend,
            COALESCE(BestPractice, '')               AS best
        FROM gamamega_01_riesgodefectoparte
        WHERE No_Parte = :np
        ORDER BY nRchz2025 DESC, nRchz2023 DESC
    """, {"np": no_parte})

    defectos = []
    for r in rows:
        defectos.append({
            "defecto":   str(r["nomDefecto"] or ""),
            "n23":       int(r["n23"] or 0),
            "rchz23":    int(r["rchz23"] or 0),
            "prc23":     round(float(r["prc23"] or 0) * 100, 2),
            "n25":       int(r["n25"] or 0),
            "rchz25":    int(r["rchz25"] or 0),
            "prc25":     round(float(r["prc25"] or 0) * 100, 2),
            "z":         round(float(r["z"] or 0), 2),
            "flag_crit": int(r["flag_crit"] or 0),
            "z_tend":    round(float(r["z_tend"] or 0), 2),
            "flag_tend": int(r["flag_tend"] or 0),
            "best":      str(r["best"] or ""),
        })

    return {"no_parte": no_parte, "defectos": defectos}


# ══════════════════════════════════════════════════════════════════════════════
# PISO DE PRODUCCIÓN — endpoints
# ══════════════════════════════════════════════════════════════════════════════

PISO_FRONTEND = Path(__file__).parent.parent / "frontend" / "piso.html"

@app.get("/piso", response_class=FileResponse)
def serve_piso():
    return FileResponse(PISO_FRONTEND, media_type="text/html",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/piso/flujo")
def piso_flujo(
    referencia: str = Query(default="2025-12-19T08:00"),
    horas_atras: int = Query(default=72),
    horas_adelante: int = Query(default=8),
):
    """
    Coladas con ciclo completo de piezas por etapa.
    Ancla en FhrVACI (vaciado real), no en u_Fecha (registro del ticket).
    Lookback de 72h cubre el ciclo completo (~50h promedio).
    """
    ref_dt = datetime.fromisoformat(referencia)
    desde_dt = ref_dt - timedelta(hours=horas_atras)
    hasta_dt = ref_dt + timedelta(hours=horas_adelante)
    d = desde_dt.strftime("%Y-%m-%d %H:%M:%S")
    h = hasta_dt.strftime("%Y-%m-%d %H:%M:%S")
    midnight = ref_dt.replace(hour=0, minute=0, second=0, microsecond=0)

    def to_min(iso):
        if not iso: return None
        try:
            dt = iso if isinstance(iso, datetime) else datetime.fromisoformat(str(iso))
            return round((dt - midnight).total_seconds() / 60)
        except Exception: return None

    rows = run("""
        SELECT
            t.u_Colada, t.u_Horno,
            COUNT(DISTINCT t.idTicket)  AS piezas,
            SUM(t.bRechazo)             AS rechazos,
            SUM(r.bMOLD)  AS n_mold,
            SUM(r.bVACI)  AS n_vaci,
            SUM(r.bDESM)  AS n_desm,
            SUM(r.bLIMP)  AS n_limp,
            MIN(r.FhrVACI)  AS vaci_ini,
            MAX(r.FhrVACI)  AS vaci_fin,
            MIN(r.FHrDESM)  AS desm_ini,
            MAX(r.FHrDESM)  AS desm_fin,
            MIN(r.FHrLIMP)  AS limp_ini,
            MAX(r.FHrLIMP)  AS limp_fin
        FROM cscmega_01rechazosbyidticket t
        JOIN cscmega_08ruta r ON r.u_idTicket = t.idTicket
        WHERE r.FhrVACI BETWEEN :d AND :h
          AND r.FhrVACI IS NOT NULL
        GROUP BY t.u_Colada, t.u_Horno
        ORDER BY vaci_ini
    """, {"d": d, "h": h})

    anchor = to_min(ref_dt)
    coladas = []
    for c in rows:
        piezas = int(c["piezas"] or 0)
        n_limp = int(c["n_limp"] or 0)
        n_desm = int(c["n_desm"] or 0)
        n_vaci = int(c["n_vaci"] or 0)
        rechazos = int(c["rechazos"] or 0)
        # Etapa actual del lote (basada en la pieza más retrasada)
        if n_limp >= piezas:
            etapa = "Terminado"
        elif n_limp > 0:
            etapa = "En limpieza"
        elif n_desm > 0:
            etapa = "En desmoldeo"
        elif n_vaci > 0:
            etapa = "En vaciado"
        else:
            etapa = "Sin datos"

        coladas.append({
            "u_colada":   int(c["u_Colada"]),
            "horno":      int(c["u_Horno"]),
            "piezas":     piezas,
            "rechazos":   rechazos,
            "pct_rechazo": round(rechazos / piezas * 100, 1) if piezas else 0,
            "n_mold":  int(c["n_mold"] or 0),
            "n_vaci":  n_vaci,
            "n_desm":  n_desm,
            "n_limp":  n_limp,
            "etapa":   etapa,
            "vaci_ini": to_min(c["vaci_ini"]),
            "vaci_fin": to_min(c["vaci_fin"]),
            "desm_ini": to_min(c["desm_ini"]),
            "desm_fin": to_min(c["desm_fin"]),
            "limp_ini": to_min(c["limp_ini"]),
            "limp_fin": to_min(c["limp_fin"]),
        })

    # KPIs globales
    total   = sum(c["piezas"] for c in coladas)
    term    = sum(c["piezas"] for c in coladas if c["etapa"] == "Terminado")
    en_proc = total - term
    rechazos_t = sum(c["rechazos"] for c in coladas)

    return {
        "anchor": anchor,
        "referencia": referencia,
        "horas_atras": horas_atras,
        "horas_adelante": horas_adelante,
        "kpis": {
            "total_piezas": total,
            "terminadas": term,
            "en_proceso": en_proc,
            "rechazos": rechazos_t,
            "pct_rechazo": round(rechazos_t / total * 100, 1) if total else 0,
            "coladas": len(coladas),
        },
        "coladas": coladas,
    }


@app.get("/piso/colada/{u_colada}/{horno}/gates")
def piso_gates_colada(u_colada: int, horno: int,
                      desde: str = Query(default=None),
                      hasta: str = Query(default=None)):
    """Gates de control de proceso para una colada: fusión, hold, temperatura, calidad."""
    d, h = rango(desde, hasta)

    # G1 + G2: tiempos de fusión y hold post-fusión
    proc = run("""
        SELECT
            c_TiempoFundicion                                        AS fusion_min,
            TIMESTAMPDIFF(MINUTE, c_FechaFinal, c_FechaLiberado)    AS hold_min
        FROM cscmega_03coladacargametalica
        WHERE u_Colada = :col AND u_Horno = :horn
          AND u_Fecha BETWEEN :d AND :h
        ORDER BY c_FechaInicial DESC LIMIT 1
    """, {"col": u_colada, "horn": horno, "d": d, "h": h})

    # G3: temperatura de vaciado
    temp = run("""
        SELECT ROUND(AVG(vt_TemperaturaVaciado)) AS temp_avg
        FROM cscmega_06modelosvaciado
        WHERE u_Colada = :col
          AND vt_TemperaturaVaciado BETWEEN 1100 AND 1600
    """, {"col": u_colada})

    # G5: calidad del lote
    cal = run("""
        SELECT COUNT(*) AS piezas, SUM(bRechazo) AS rechazos,
               ROUND(AVG(bRechazo)*100, 1) AS pct_rechazo
        FROM cscmega_01rechazosbyidticket
        WHERE u_Colada = :col AND u_Horno = :horn
          AND u_Fecha BETWEEN :d AND :h
    """, {"col": u_colada, "horn": horno, "d": d, "h": h})

    # Defecto dominante (para protocolo G7)
    def_raw = run("""
        SELECT t.nomDefecto, COUNT(*) AS n
        FROM cscmega_01rechazosbyidticket rb
        JOIN cscmega_01resultadoidticket t ON t.idTicket = rb.idTicket
        WHERE rb.u_Colada = :col AND rb.u_Horno = :horn
          AND rb.bRechazo = 1 AND t.nomDefecto IS NOT NULL
          AND rb.u_Fecha BETWEEN :d AND :h
        GROUP BY t.nomDefecto ORDER BY n DESC LIMIT 1
    """, {"col": u_colada, "horn": horno, "d": d, "h": h})

    # G4-G6: avance por etapa (cscmega_08ruta)
    etapa_raw = run("""
        SELECT COUNT(*) AS total,
               SUM(r.bVACI) AS n_vaci,
               SUM(r.bDESM) AS n_desm,
               SUM(r.bLIMP) AS n_limp
        FROM cscmega_01rechazosbyidticket rb
        JOIN cscmega_08ruta r ON r.u_idTicket = rb.idTicket
        WHERE rb.u_Colada = :col AND rb.u_Horno = :horn
          AND rb.u_Fecha BETWEEN :d AND :h
    """, {"col": u_colada, "horn": horno, "d": d, "h": h})

    p       = proc[0]    if proc    else {}
    t_      = temp[0]    if temp    else {}
    c       = cal[0]     if cal     else {}
    e       = etapa_raw[0] if etapa_raw else {}
    dom_def = def_raw[0]["nomDefecto"] if def_raw else None

    fusion  = float(p.get("fusion_min") or 0)
    hold    = float(p.get("hold_min")   or 0) if p.get("hold_min") is not None else None
    temp_v  = float(t_.get("temp_avg")  or 0) if t_.get("temp_avg") is not None else None
    pct_rec = float(c.get("pct_rechazo") or 0)
    piezas  = int(c.get("piezas") or 0)

    total_e  = int(e.get("total")  or 0)
    n_vaci   = int(e.get("n_vaci") or 0)
    n_desm   = int(e.get("n_desm") or 0)
    n_limp   = int(e.get("n_limp") or 0)

    def pct_etapa(n, total):
        return round(n / total * 100) if total else 0

    def st_etapa(n, total):
        if not total:     return "pendiente"
        if n >= total:    return "ok"
        if n > 0:         return "proceso"
        return "pendiente"

    def st(cond_ok, cond_warn, has_data):
        if not has_data: return "pendiente"
        if cond_ok:      return "ok"
        if cond_warn:    return "alerta"
        return "bloqueante"

    pv = pct_etapa(n_vaci, total_e)
    pd = pct_etapa(n_desm, total_e)
    pl = pct_etapa(n_limp, total_e)

    gates = [
        {
            "id": "G1", "nombre": "Tiempo de fusión",
            "valor": f"{int(fusion)} min" if fusion else "—",
            "meta": "25 – 35 min",
            "estado": st(25 <= fusion <= 35, fusion < 25, bool(fusion)),
            "protocolo": (
                "Fusión corta: riesgo de metal no homogéneo. Extender tiempo o revisar carga."
                if fusion and fusion < 25 else
                "Fusión larga: consumo energético elevado. Revisar composición de carga."
                if fusion and fusion > 35 else None
            ),
        },
        {
            "id": "G2", "nombre": "Hold post-fusión",
            "valor": f"{int(hold)} min" if hold is not None else "—",
            "meta": "≤ 60 min",
            "estado": st(hold is not None and hold <= 60,
                         hold is not None and hold <= 90,
                         hold is not None),
            "protocolo": (
                "Hold prolongado: metal pierde temperatura. Evaluar re-calentamiento o priorizar vaciado."
                if hold is not None and hold > 60 else None
            ),
        },
        {
            "id": "G3", "nombre": "Temperatura de vaciado",
            "valor": f"{int(temp_v)} °C" if temp_v else "—",
            "meta": "1 360 – 1 420 °C",
            "estado": st(temp_v is not None and 1360 <= temp_v <= 1420,
                         temp_v is not None and 1340 <= temp_v < 1360,
                         temp_v is not None),
            "protocolo": (
                "Temp baja: alto riesgo de llenado deficiente y sopladuras. No vaciar sin autorización de fundición."
                if temp_v is not None and temp_v < 1360 else
                "Temp alta: riesgo de rechupe y microestructura deficiente. Esperar ventana de enfriamiento."
                if temp_v is not None and temp_v > 1420 else None
            ),
        },
        {
            "id": "G4", "nombre": "Vaciado",
            "valor": f"{n_vaci}/{total_e} pzas ({pv}%)" if total_e else "—",
            "meta": "100% vaciadas",
            "estado": st_etapa(n_vaci, total_e),
            "protocolo": None,
        },
        {
            "id": "G5", "nombre": "Desmoldeo",
            "valor": f"{n_desm}/{total_e} pzas ({pd}%)" if total_e else "—",
            "meta": "100% desmoldeadas",
            "estado": st_etapa(n_desm, total_e),
            "protocolo": None,
        },
        {
            "id": "G6", "nombre": "Limpieza",
            "valor": f"{n_limp}/{total_e} pzas ({pl}%)" if total_e else "—",
            "meta": "100% terminadas",
            "estado": st_etapa(n_limp, total_e),
            "protocolo": None,
        },
        {
            "id": "G7", "nombre": "Calidad del lote",
            "valor": f"{pct_rec}% rechazo" if piezas else "—",
            "meta": "< 7%",
            "estado": st(pct_rec < 7, pct_rec < 15, bool(piezas)),
            "protocolo": (
                (f"Rechazo grave ({pct_rec}%). Defecto dominante: {dom_def}. "
                 "Detener lote, convocar ingeniería, analizar causa raíz.")
                if pct_rec >= 15 else
                (f"Rechazo fuera de meta ({pct_rec}%). Monitorear tendencia por turno."
                 + (f" Defecto dominante: {dom_def}." if dom_def else ""))
                if pct_rec >= 7 else None
            ),
        },
    ]

    return {"gates": gates, "colada": u_colada, "horno": horno}


@app.get("/piso/colada/{u_colada}/{horno}/diagnostico")
def piso_diagnostico(u_colada: int, horno: int):
    """4 chips de diagnóstico por colada: fusión/hold, defecto top, distribución, benchmark horno."""

    # 1. Fusión, hold y carga metálica
    proc = run("""
        SELECT c_TiempoFundicion AS fusion_min,
               TIMESTAMPDIFF(MINUTE, c_FechaFinal, c_FechaLiberado) AS hold_min,
               c_Kilos AS kg_carga
        FROM cscmega_03coladacargametalica
        WHERE u_Colada = :col AND u_Horno = :horn
        ORDER BY c_FechaInicial DESC LIMIT 1
    """, {"col": u_colada, "horn": horno})
    p = proc[0] if proc else {}
    fusion   = float(p["fusion_min"]) if p.get("fusion_min") else None
    hold     = float(p["hold_min"])   if p.get("hold_min") is not None else None
    kg_carga = float(p["kg_carga"])   if p.get("kg_carga") else None

    fusion_estado = (
        "ok"    if fusion and 25 <= fusion <= 35 else
        "warn"  if fusion and fusion < 25 else
        "alert" if fusion and fusion > 35 else
        "sin_dato"
    )
    hold_estado = (
        "ok"    if hold is not None and hold <= 60 else
        "warn"  if hold is not None and hold <= 90 else
        "alert" if hold is not None and hold > 90 else
        "sin_dato"
    )

    # 2. Defecto dominante (top 3)
    def_raw = run("""
        SELECT t.nomDefecto, COUNT(*) AS n
        FROM cscmega_01rechazosbyidticket rb
        JOIN cscmega_01resultadoidticket t ON t.idTicket = rb.idTicket
        WHERE rb.u_Colada = :col AND rb.u_Horno = :horn
          AND rb.bRechazo = 1 AND t.nomDefecto IS NOT NULL
        GROUP BY t.nomDefecto ORDER BY n DESC LIMIT 3
    """, {"col": u_colada, "horn": horno})
    total_def = sum(d["n"] for d in def_raw) or 1
    defectos = [{"nombre": d["nomDefecto"],
                 "n": int(d["n"]),
                 "pct": round(d["n"] / total_def * 100, 1)} for d in def_raw]

    # 3. Distribución rechazo por SKU
    dist_raw = run("""
        SELECT t.No_Parte AS sku,
               COUNT(*) AS total,
               SUM(rb.bRechazo) AS rechazos
        FROM cscmega_01rechazosbyidticket rb
        JOIN cscmega_01resultadoidticket t ON t.idTicket = rb.idTicket
        WHERE rb.u_Colada = :col AND rb.u_Horno = :horn
        GROUP BY t.No_Parte HAVING total >= 2
        ORDER BY rechazos DESC
    """, {"col": u_colada, "horn": horno})
    total_rech_dist = sum(int(d["rechazos"] or 0) for d in dist_raw) or 1
    top_sku = dist_raw[0] if dist_raw else {}
    top_pct  = round(int(top_sku.get("rechazos") or 0) / total_rech_dist * 100, 1) if dist_raw else 0
    concentrado = top_pct >= 70 and len(dist_raw) > 1

    # 4. Benchmark: últimas 5 coladas del mismo horno (excluye la actual)
    bench_raw = run("""
        SELECT ROUND(AVG(bRechazo)*100, 1) AS pct
        FROM cscmega_01rechazosbyidticket
        WHERE u_Horno = :horn AND u_Colada != :col
          AND u_Fecha >= (
            SELECT DATE_SUB(MAX(u_Fecha), INTERVAL 30 DAY)
            FROM cscmega_01rechazosbyidticket WHERE u_Horno = :horn
          )
        GROUP BY u_Colada
        ORDER BY MAX(u_Fecha) DESC LIMIT 5
    """, {"col": u_colada, "horn": horno})
    bench_rates = [float(r["pct"]) for r in bench_raw if r["pct"] is not None]
    bench_avg   = round(sum(bench_rates) / len(bench_rates), 1) if bench_rates else None

    current_pct_raw = run("""
        SELECT ROUND(AVG(bRechazo)*100, 1) AS pct
        FROM cscmega_01rechazosbyidticket
        WHERE u_Colada = :col AND u_Horno = :horn
    """, {"col": u_colada, "horn": horno})
    current_pct = float(current_pct_raw[0]["pct"]) if current_pct_raw and current_pct_raw[0]["pct"] is not None else None
    delta = round(current_pct - bench_avg, 1) if current_pct is not None and bench_avg is not None else None

    # 5. Métricas en kg  (peso_pieza = c_Kilos / total_piezas)
    totales_raw = run("""
        SELECT COUNT(*) AS total, SUM(bRechazo) AS rech
        FROM cscmega_01rechazosbyidticket
        WHERE u_Colada = :col AND u_Horno = :horn
    """, {"col": u_colada, "horn": horno})
    tot = totales_raw[0] if totales_raw else {}
    total_piezas = int(tot.get("total") or 0)
    piezas_rech  = int(tot.get("rech")  or 0)
    piezas_ok    = total_piezas - piezas_rech

    peso_pieza_est = round(kg_carga / total_piezas, 2) if kg_carga and total_piezas > 0 else None
    kg_ok_est      = round(piezas_ok   * peso_pieza_est, 1) if peso_pieza_est else None
    kg_rech_est    = round(piezas_rech * peso_pieza_est, 1) if peso_pieza_est else None
    pct_rech_kg    = round(kg_rech_est / kg_carga * 100, 1) if kg_rech_est and kg_carga else None

    # delta kg vs benchmark (últimas 5 coladas del mismo horno por kg%)
    bench_kg_raw = run("""
        SELECT cc.u_Colada,
               cc.c_Kilos,
               COUNT(rb.idTicket)  AS total_p,
               SUM(rb.bRechazo)    AS rech_p
        FROM cscmega_03coladacargametalica cc
        JOIN cscmega_01rechazosbyidticket rb
             ON rb.u_Colada = cc.u_Colada AND rb.u_Horno = cc.u_Horno
        WHERE cc.u_Horno = :horn AND cc.u_Colada != :col
          AND cc.c_Kilos > 0 AND cc.c_FechaInicial >= (
              SELECT DATE_SUB(MAX(c_FechaInicial), INTERVAL 30 DAY)
              FROM cscmega_03coladacargametalica WHERE u_Horno = :horn
          )
        GROUP BY cc.u_Colada, cc.c_Kilos
        ORDER BY MAX(rb.u_Fecha) DESC LIMIT 5
    """, {"col": u_colada, "horn": horno})
    bench_kg_rates = [
        round(float(r["rech_p"]) / float(r["total_p"]) * 100, 1)
        for r in bench_kg_raw if r["total_p"] and float(r["total_p"]) > 0
    ]
    bench_avg_kg  = round(sum(bench_kg_rates) / len(bench_kg_rates), 1) if bench_kg_rates else None
    delta_kg      = round(pct_rech_kg - bench_avg_kg, 1) if pct_rech_kg is not None and bench_avg_kg is not None else None

    # Per-SKU kg estimado
    skus_kg = []
    if peso_pieza_est:
        for sk in dist_raw:
            sk_total = int(sk.get("total") or 0)
            sk_rech  = int(sk.get("rechazos") or 0)
            sk_ok    = sk_total - sk_rech
            skus_kg.append({
                "sku":       sk.get("sku"),
                "kg_total":  round(sk_total * peso_pieza_est, 1),
                "kg_ok":     round(sk_ok    * peso_pieza_est, 1),
                "kg_rech":   round(sk_rech  * peso_pieza_est, 1),
                "pct_kg":    round(sk_rech / sk_total * 100, 1) if sk_total else 0,
            })

    return {
        "fusion": {
            "fusion_min": fusion,
            "hold_min": hold,
            "fusion_estado": fusion_estado,
            "hold_estado": hold_estado,
        },
        "defecto": {
            "top": defectos[0] if defectos else None,
            "lista": defectos,
            "total_rechazos": total_def if def_raw else 0,
        },
        "distribucion": {
            "concentrado": concentrado,
            "sku_top": top_sku.get("sku"),
            "pct_sku_top": top_pct,
            "n_skus_con_rechazo": len([d for d in dist_raw if int(d.get("rechazos") or 0) > 0]),
        },
        "benchmark": {
            "pct_actual": current_pct,
            "pct_bench": bench_avg,
            "delta": delta,
            "n_coladas": len(bench_rates),
            "pct_actual_kg": pct_rech_kg,
            "pct_bench_kg": bench_avg_kg,
            "delta_kg": delta_kg,
        },
        "kg_metrics": {
            "kg_carga": kg_carga,
            "peso_pieza_est": peso_pieza_est,
            "total_piezas": total_piezas,
            "piezas_ok": piezas_ok,
            "piezas_rech": piezas_rech,
            "kg_ok_est": kg_ok_est,
            "kg_rech_est": kg_rech_est,
            "pct_rech_kg": pct_rech_kg,
            "skus_kg": skus_kg,
        },
    }


@app.get("/piso/colada/{u_colada}/{horno}/piezas")
def piso_piezas_colada(u_colada: int, horno: int,
                       desde: str = Query(default=None),
                       hasta: str = Query(default=None)):
    """Piezas individuales de una colada con su etapa actual y SKU."""
    d, h = rango(desde, hasta)
    rows = run("""
        SELECT
            t.idTicket,
            t.No_Parte          AS sku,
            t.bRechazo,
            t.nomDefecto        AS defecto,
            r.bMOLD, r.bVACI, r.bDESM, r.bLIMP,
            r.FhrVACI           AS ts_vaci,
            r.FHrLIMP           AS ts_limp,
            CASE
              WHEN r.bLIMP = 1  THEN 'Terminada'
              WHEN r.bDESM = 1  THEN 'En limpieza'
              WHEN r.bVACI = 1  THEN 'En desmoldeo'
              WHEN r.bMOLD = 1  THEN 'En vaciado'
              ELSE 'Sin inicio'
            END                 AS etapa
        FROM cscmega_01resultadoidticket t
        JOIN cscmega_08ruta r ON r.u_idTicket = t.idTicket
        JOIN cscmega_01rechazosbyidticket rb ON rb.idTicket = t.idTicket
        WHERE rb.u_Colada = :col AND rb.u_Horno = :horn
          AND r.FhrVACI BETWEEN :d AND :h
          AND r.FhrVACI IS NOT NULL
        ORDER BY r.FhrVACI
        LIMIT 200
    """, {"col": u_colada, "horn": horno, "d": d, "h": h})

    # Agrupar por SKU
    skus = {}
    for p in rows:
        s = p["sku"] or "—"
        if s not in skus:
            skus[s] = {"sku": s, "total": 0, "ok": 0, "rechazos": 0,
                       "terminadas": 0, "en_proceso": 0}
        skus[s]["total"] += 1
        if p["bRechazo"]:
            skus[s]["rechazos"] += 1
        else:
            skus[s]["ok"] += 1
        if p["etapa"] == "Terminada":
            skus[s]["terminadas"] += 1
        else:
            skus[s]["en_proceso"] += 1

    return {
        "u_colada": u_colada,
        "horno": horno,
        "total_piezas": len(rows),
        "skus": sorted(skus.values(), key=lambda x: -x["total"]),
        "piezas": rows,
    }


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD EJECUTIVO — endpoints
# ══════════════════════════════════════════════════════════════════════════════

EJECUTIVO_FRONTEND = Path(__file__).parent.parent / "frontend" / "ejecutivo.html"

@app.get("/ejecutivo", response_class=FileResponse)
def serve_ejecutivo():
    return FileResponse(
        EJECUTIVO_FRONTEND,
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


@app.get("/ejecutivo/kpis-periodo")
def ejecutivo_kpis_periodo(
    desde: str = Query(default=None),
    hasta: str = Query(default=None),
):
    """KPIs del período y del período anterior para calcular delta."""
    d, h = rango(desde, hasta)
    # Calcular período anterior de igual duración
    from datetime import datetime as dt2
    d_dt = dt2.strptime(d[:10], "%Y-%m-%d")
    h_dt = dt2.strptime(h[:10], "%Y-%m-%d")
    dur = h_dt - d_dt
    d_ant = (d_dt - dur).strftime("%Y-%m-%d") + " 00:00:00"
    h_ant = (d_dt).strftime("%Y-%m-%d") + " 23:59:59"

    actual = run("""
        SELECT
            COUNT(*)                            AS piezas_total,
            SUM(bRechazo)                       AS rechazos,
            ROUND(AVG(bRechazo)*100, 2)         AS pct_rechazo,
            COUNT(DISTINCT u_Colada)            AS coladas,
            COUNT(DISTINCT u_Horno)             AS hornos,
            COUNT(DISTINCT DATE(u_Fecha))       AS dias_produccion,
            MIN(u_Fecha)                        AS primer_registro,
            MAX(u_Fecha)                        AS ultimo_registro
        FROM cscmega_01rechazosbyidticket
        WHERE u_Fecha BETWEEN :d AND :h
    """, {"d": d, "h": h})

    anterior = run("""
        SELECT
            COUNT(*)                    AS piezas_total,
            SUM(bRechazo)               AS rechazos,
            ROUND(AVG(bRechazo)*100,2)  AS pct_rechazo,
            COUNT(DISTINCT u_Colada)    AS coladas
        FROM cscmega_01rechazosbyidticket
        WHERE u_Fecha BETWEEN :d AND :h
    """, {"d": d_ant, "h": h_ant})

    return {
        "actual": actual[0] if actual else {},
        "anterior": anterior[0] if anterior else {},
        "periodo": {"desde": d[:10], "hasta": h[:10]},
        "periodo_ant": {"desde": d_ant[:10], "hasta": h_ant[:10]},
    }


@app.get("/ejecutivo/tendencia-semanal")
def ejecutivo_tendencia_semanal(
    desde: str = Query(default=None),
    hasta: str = Query(default=None),
):
    """Rechazo y producción agrupados por semana ISO."""
    d, h = rango(desde, hasta)
    return run("""
        SELECT
            YEARWEEK(u_Fecha, 3)            AS semana_iso,
            MIN(DATE(u_Fecha))              AS semana_inicio,
            COUNT(*)                        AS piezas,
            SUM(bRechazo)                   AS rechazos,
            ROUND(AVG(bRechazo)*100, 2)     AS pct_rechazo,
            COUNT(DISTINCT u_Colada)        AS coladas
        FROM cscmega_01rechazosbyidticket
        WHERE u_Fecha BETWEEN :d AND :h
        GROUP BY semana_iso
        ORDER BY semana_iso
    """, {"d": d, "h": h})


@app.get("/ejecutivo/tendencia-mensual")
def ejecutivo_tendencia_mensual(
    desde: str = Query(default=None),
    hasta: str = Query(default=None),
):
    """Rechazo y producción agrupados por mes."""
    d, h = rango(desde, hasta)
    return run("""
        SELECT
            DATE_FORMAT(u_Fecha,'%Y-%m')    AS mes,
            COUNT(*)                        AS piezas,
            SUM(bRechazo)                   AS rechazos,
            ROUND(AVG(bRechazo)*100, 2)     AS pct_rechazo,
            COUNT(DISTINCT u_Colada)        AS coladas,
            COUNT(DISTINCT u_Horno)         AS hornos
        FROM cscmega_01rechazosbyidticket
        WHERE u_Fecha BETWEEN :d AND :h
        GROUP BY mes
        ORDER BY mes
    """, {"d": d, "h": h})


@app.get("/ejecutivo/top-defectos")
def ejecutivo_top_defectos(
    desde: str = Query(default=None),
    hasta: str = Query(default=None),
):
    """Top defectos del período con tendencia."""
    d, h = rango(desde, hasta)
    return run("""
        SELECT
            nomDefecto                      AS defecto,
            COUNT(*)                        AS ocurrencias,
            ROUND(COUNT(*) * 100.0 /
                SUM(COUNT(*)) OVER(), 1)    AS pct_del_total,
            COUNT(DISTINCT u_Colada)        AS coladas_afectadas
        FROM cscmega_01resultadoidticket
        WHERE bRechazo = 1
          AND u_FechaHr BETWEEN :d AND :h
          AND nomDefecto IS NOT NULL
        GROUP BY nomDefecto
        ORDER BY ocurrencias DESC
        LIMIT 10
    """, {"d": d, "h": h})


@app.get("/ejecutivo/por-horno")
def ejecutivo_por_horno(
    desde: str = Query(default=None),
    hasta: str = Query(default=None),
):
    """KPIs agrupados por horno."""
    d, h = rango(desde, hasta)
    return run("""
        SELECT
            u_Horno                         AS horno,
            COUNT(*)                        AS piezas,
            SUM(bRechazo)                   AS rechazos,
            ROUND(AVG(bRechazo)*100, 2)     AS pct_rechazo,
            COUNT(DISTINCT u_Colada)        AS coladas
        FROM cscmega_01rechazosbyidticket
        WHERE u_Fecha BETWEEN :d AND :h
        GROUP BY u_Horno
        ORDER BY pct_rechazo DESC
    """, {"d": d, "h": h})


@app.get("/ejecutivo/win-the-week")
def ejecutivo_win_the_week(
    desde: str = Query(default=None),
    hasta: str = Query(default=None),
):
    """Últimas 8 semanas para la tabla Win the Week."""
    d, h = rango(desde, hasta)
    return run("""
        SELECT
            YEARWEEK(u_Fecha, 3)                AS semana_iso,
            MIN(DATE(u_Fecha))                  AS lunes,
            MAX(DATE(u_Fecha))                  AS domingo,
            COUNT(*)                            AS piezas,
            SUM(bRechazo)                       AS rechazos,
            ROUND(AVG(bRechazo)*100, 2)         AS pct_rechazo,
            COUNT(DISTINCT u_Colada)            AS coladas,
            COUNT(DISTINCT DATE(u_Fecha))       AS dias_activos
        FROM cscmega_01rechazosbyidticket
        WHERE u_Fecha BETWEEN :d AND :h
        GROUP BY semana_iso
        ORDER BY semana_iso DESC
        LIMIT 8
    """, {"d": d, "h": h})


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD DE GESTIÓN — endpoints
# ══════════════════════════════════════════════════════════════════════════════

GESTION_FRONTEND = Path(__file__).parent.parent / "frontend" / "gestion.html"

@app.get("/gestion", response_class=FileResponse)
def serve_gestion():
    return FileResponse(
        GESTION_FRONTEND,
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


@app.get("/gestion/coladas")
def gestion_coladas(
    desde: str = Query(default=None),
    hasta: str = Query(default=None),
):
    """
    Coladas en la ventana temporal con su ciclo de vida completo.
    Usa c_IdColada como llave única (u_Colada se repite entre hornos).
    El rango de vaciado se reconstituye de cscmega_08ruta.FhrVACI.
    """
    d, h = rango(desde, hasta)
    return run("""
        SELECT
            c.c_IdColada            AS id_colada,
            c.u_Colada              AS num_colada,
            c.u_Horno               AS horno,
            c.c_Status              AS status,
            c.c_FechaInicial        AS inicio_fusion,
            c.c_FechaFinal          AS fin_fusion,
            c.c_Kilos               AS kilos,
            c.c_TiempoFundicion     AS min_fusion,
            MIN(rt.FhrVACI)         AS inicio_vaciado,
            MAX(rt.FhrVACI)         AS fin_vaciado,
            MIN(rt.FHrMOLD)         AS inicio_moldeo,
            MAX(rt.FHrLIMP)         AS fin_limpieza,
            COUNT(DISTINCT pks.idTicket)          AS total_piezas,
            SUM(r.bRechazo)                       AS rechazos,
            ROUND(AVG(r.bRechazo)*100, 1)         AS pct_rechazo,
            ROUND(MAX(q.aB_C) + MAX(q.aB_Si)/3.0
                  + MAX(q.aB_P)/3.0, 3)           AS ce_pct,
            MAX(q.aB_C)                           AS c_pct,
            MAX(q.aB_Si)                          AS si_pct,
            MAX(q.aB_Mg)                          AS mg_pct
        FROM cscmega_03coladacargametalica c
        LEFT JOIN cscmega_02pksquimicos pks
               ON pks.c_IdColada = c.c_IdColada
        LEFT JOIN cscmega_01rechazosbyidticket r
               ON r.idTicket = pks.idTicket
        LEFT JOIN cscmega_08ruta rt
               ON rt.u_idTicket = pks.idTicket
        LEFT JOIN cscmega_05cquimicosbase q
               ON q.u_Colada = c.u_Colada AND q.aB_C > 0
        WHERE c.c_FechaInicial BETWEEN :d AND :h
          AND c.c_IdColada > 0
        GROUP BY c.c_IdColada, c.u_Colada, c.u_Horno, c.c_Status,
                 c.c_FechaInicial, c.c_FechaFinal, c.c_Kilos, c.c_TiempoFundicion
        ORDER BY c.c_FechaInicial DESC
        LIMIT 80
    """, {"d": d, "h": h})


@app.get("/gestion/colada/{id_colada}/skus")
def gestion_skus(id_colada: int):
    """SKUs (no_parte) producidos en una colada con su rechazo."""
    rows = run("""
        SELECT
            t.No_Parte              AS no_parte,
            COUNT(*)                AS piezas,
            SUM(t.bRechazo)         AS rechazos,
            ROUND(AVG(t.bRechazo)*100, 1) AS pct_rechazo,
            MAX(t.nomDefecto)       AS defecto_top
        FROM cscmega_02pksquimicos pks
        JOIN cscmega_01resultadoidticket t ON t.idTicket = pks.idTicket
        WHERE pks.c_IdColada = :id
        GROUP BY t.No_Parte
        ORDER BY piezas DESC
        LIMIT 30
    """, {"id": id_colada})
    return rows


@app.get("/gestion/colada/{id_colada}/flujo")
def gestion_flujo(id_colada: int):
    """Conteo de piezas por etapa del proceso para una colada."""
    rows = run("""
        SELECT
            COUNT(DISTINCT rt.u_idTicket)   AS total_con_ruta,
            SUM(rt.bMOLD)                   AS paso_moldeo,
            SUM(rt.bVACI)                   AS paso_vaciado,
            SUM(rt.bLIMP)                   AS paso_limpieza,
            SUM(rt.bDESM)                   AS paso_desmoldeo,
            SUM(rt.bCELD)                   AS paso_celdas,
            MIN(rt.FHrMOLD)                 AS inicio_moldeo,
            MAX(rt.FHrMOLD)                 AS fin_moldeo,
            MIN(rt.FhrVACI)                 AS inicio_vaciado,
            MAX(rt.FhrVACI)                 AS fin_vaciado,
            MIN(rt.FHrLIMP)                 AS inicio_limpieza,
            MAX(rt.FHrLIMP)                 AS fin_limpieza
        FROM cscmega_02pksquimicos pks
        JOIN cscmega_08ruta rt ON rt.u_idTicket = pks.idTicket
        WHERE pks.c_IdColada = :id
    """, {"id": id_colada})
    return rows[0] if rows else {}


@app.get("/flujo/turno")
def flujo_turno(
    desde: str = Query(default=None),
    hasta: str = Query(default=None),
):
    d, h = rango(desde, hasta)
    return run("""
        SELECT r.u_idTicket AS idTicket, r.u_NoParte,
               r.u_FechaVaciado AS fecha_vaciado,
               r.FHrMOLD, r.FhrVACI, r.FHrLIMP, r.FHrDESM, r.FHrCELD,
               r.bVaciado, t.u_Colada, t.u_Horno, t.bRechazo
        FROM cscmega_08ruta r
        JOIN cscmega_01rechazosbyidticket t ON t.idTicket = r.u_idTicket
        WHERE r.u_FechaVaciado BETWEEN :d AND :h
        ORDER BY r.u_FechaVaciado DESC LIMIT 300
    """, {"d": d, "h": h})


# ══════════════════════════════════════════════════════════════════════════════
# TORRE V3 — Contrato de Visibilidad
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class VentanaControl:
    tipo: str          # "porcentaje_max" | "completitud" | "rango"
    umbral_verde: float
    umbral_ambar: Optional[float]
    unidad: str        # "pct" | "kg" | "min"

@dataclass
class CarrilControl:
    id_pc: str
    señal: str
    fuente: str
    ventana_de_control: Optional[VentanaControl]
    disparador: str
    logica_de_estado: str
    protocolo: str
    latencia_objetivo: Optional[int]   # minutos objetivo detección → acción
    disponibilidad: str  # "activo"|"gris_sin_dato"|"gris_no_confiable"|"gris_sin_ventana"

# Reloj de detección en memoria: (id_pc, id_colada) → datetime de entrada a ROJO/ÁMBAR
_detecciones: Dict[Tuple[str, int], datetime] = {}

MOTIVOS_GRIS = {
    "gris_sin_dato":     "sin dato / fuente no integrada en ia_fasa",
    "gris_no_confiable": "dato no confiable — desincronización de relojes",
    "gris_sin_ventana":  "fuente disponible — ventana de control pendiente de definir por Calidad",
}

CARRILES: List[CarrilControl] = [
    CarrilControl(
        "PC-1", "kg cargados por material vs receta",
        "ia_fasa.cscmega_03coladacargametalica.c_Kilos (kg total — sin desglose por material)",
        None, "inicio_fusion",
        "kg real por componente vs receta programada", "P-01", 15, "gris_sin_dato",
    ),
    CarrilControl(
        "PC-2", "análisis químico — elementos clave en especificación",
        "ia_fasa.cscmega_05aquimicoscumplimiento — flags _F via u_Colada + u_Horno",
        VentanaControl("completitud", 100.0, 80.0, "pct"),
        "fin_fusion",
        "% elementos clave (C,Si,Mg,S,P,Mn) con flag _F = pass ; 100%→VERDE ; ≥80%→ÁMBAR ; <80%→ROJO",
        "P-02", 10, "activo",
    ),
    CarrilControl(
        "PC-3", "inoculación / fading",
        "sin fuente integrada en ia_fasa",
        None, "inicio_vaciado",
        "tiempo desde inoculación ≤ ventana de fading por aleación", "P-03", 5, "gris_sin_dato",
    ),
    CarrilControl(
        "PC-4", "temperatura de vaciado (% tickets en rango)",
        "ia_fasa.alfamega_06_vaciado.vt_TemperaturaVaciado + bCumplimientoTemperaturaOk",
        VentanaControl("completitud", 95.0, 80.0, "pct"),
        "inicio_vaciado",
        "% tickets con bCumplimientoTemperaturaOk = 1 ≥ 95 % → VERDE ; ≥ 80 % → ÁMBAR ; < 80 % → ROJO",
        "P-04", 5, "activo",
    ),
    CarrilControl(
        "PC-5", "desmoldeo oportuno (% piezas >= umbral por parte)",
        "cscmega_08ruta.FHrMOLD + FHrDESM x corex_test.modelos.TiempoDesmoldeo",
        VentanaControl("completitud", 95.0, 80.0, "pct"),
        "fin_vaciado",
        "% piezas con tiempo_molde >= TiempoDesmoldeo por NoParte ; ≥95%→VERDE ; ≥80%→ÁMBAR ; <80%→ROJO",
        "P-05", 30, "activo",
    ),
    CarrilControl(
        "PC-6", "% rechazo del lote (qué, no causa metalúrgica)",
        "ia_fasa.cscmega_01rechazosbyidticket.bRechazo",
        VentanaControl("porcentaje_max", 7.0, None, "pct"),
        "fin_vaciado",
        "pct_rechazo < 7 % → VERDE ; ≥ 7 % → ROJO", "P-06", 60, "activo",
    ),
    CarrilControl(
        "PC-7", "cierre de colada — piezas con limpieza registrada",
        "ia_fasa.cscmega_08ruta.bLIMP — ventana: ≥95% piezas O ≤5 días desde vaciado",
        VentanaControl("completitud", 95.0, None, "pct"),
        "fin_vaciado",
        "pct_limp ≥ 95% → VERDE ; <95% y ≤3 días → GRIS ; <95% y 3–5 días → ÁMBAR ; <95% y >5 días → ROJO",
        "P-07", 120, "activo",
    ),
]


def evaluar_pc7(pct_ci, inicio_fusion, id_colada: int) -> dict:
    """PC-7: cierre en tiempo. Verde si ≥95% bLIMP; sino evalúa días transcurridos."""
    pc7 = next(c for c in CARRILES if c.id_pc == "PC-7")
    now = datetime.utcnow()
    key = ("PC-7", id_colada)

    def _base(estado, motivo=None, dias=None):
        if estado in ("ROJO", "AMBAR"):
            if key not in _detecciones:
                _detecciones[key] = inicio_fusion if isinstance(inicio_fusion, datetime) else now
            ts_det = _detecciones[key]
            lat = int((now - ts_det).total_seconds())
        else:
            _detecciones.pop(key, None)
            ts_det = None
            lat = None
        return {
            "id_pc": "PC-7", "señal": pc7.señal, "fuente": pc7.fuente,
            "estado": estado, "valor": pct_ci,
            "ts_deteccion": ts_det.isoformat() if ts_det else None,
            "latencia_seg": lat,
            "motivo_gris": motivo, "protocolo": pc7.protocolo if estado == "ROJO" else None,
            "detalle": {"pct_limp": pct_ci, "dias_transcurridos": round(dias, 1) if dias is not None else None},
        }

    if pct_ci is None:
        return _base("GRIS", "sin registro de limpieza en el período")
    if pct_ci >= 95.0:
        return _base("VERDE")

    # pct_ci < 95% → evaluar por tiempo
    if not isinstance(inicio_fusion, datetime):
        return _base("GRIS", "sin fecha de inicio de colada")
    dias = (now - inicio_fusion).total_seconds() / 86400
    if dias > 5:
        return _base("ROJO", dias=dias)
    if dias > 3:
        return _base("AMBAR", dias=dias)
    return _base("GRIS", f"en proceso normal ({dias:.1f} días, aún no vence)", dias=dias)


def evaluar_carril(
    carril: CarrilControl,
    señal_valor: Optional[float],
    ts_señal,           # datetime | str | None
    id_colada: int,
) -> dict:
    now = datetime.utcnow()
    key = (carril.id_pc, id_colada)

    def _gris(motivo: str, valor=None):
        _detecciones.pop(key, None)
        return {
            "id_pc":        carril.id_pc,
            "señal":        carril.señal,
            "fuente":       carril.fuente,
            "estado":       "GRIS",
            "valor":        valor,
            "ts_deteccion": None,
            "latencia_seg": None,
            "motivo_gris":  motivo,
            "protocolo":    None,
            "detalle":      {},
        }

    if carril.disponibilidad != "activo":
        return _gris(MOTIVOS_GRIS.get(carril.disponibilidad, carril.disponibilidad))
    if señal_valor is None:
        return _gris("sin dato en el período evaluado")
    if carril.ventana_de_control is None:
        return _gris("ventana de control no definida por Calidad", valor=señal_valor)

    v = carril.ventana_de_control
    if v.tipo == "porcentaje_max":
        if señal_valor < v.umbral_verde:
            estado = "VERDE"
        elif v.umbral_ambar is not None and señal_valor < v.umbral_ambar:
            estado = "AMBAR"
        else:
            estado = "ROJO"
    elif v.tipo == "completitud":
        if señal_valor >= v.umbral_verde:
            estado = "VERDE"
        elif v.umbral_ambar is not None and señal_valor >= v.umbral_ambar:
            estado = "AMBAR"
        else:
            estado = "ROJO"
    else:
        estado = "VERDE"

    if estado in ("ROJO", "AMBAR"):
        if key not in _detecciones:
            if isinstance(ts_señal, datetime):
                _detecciones[key] = ts_señal
            elif ts_señal:
                try:
                    _detecciones[key] = datetime.fromisoformat(str(ts_señal))
                except Exception:
                    _detecciones[key] = now
            else:
                _detecciones[key] = now
        ts_det = _detecciones[key]
        latencia_seg = int((now - ts_det).total_seconds())
    else:
        _detecciones.pop(key, None)
        ts_det = None
        latencia_seg = None

    return {
        "id_pc":        carril.id_pc,
        "señal":        carril.señal,
        "fuente":       carril.fuente,
        "estado":       estado,
        "valor":        señal_valor,
        "ts_deteccion": ts_det.isoformat() if ts_det else None,
        "latencia_seg": latencia_seg,
        "motivo_gris":  None,
        "protocolo":    carril.protocolo if estado == "ROJO" else None,
        "detalle":      {},
    }


V3_FRONTEND = Path(__file__).parent.parent / "frontend" / "torre_v3.html"
V4_FRONTEND = Path(__file__).parent.parent / "frontend" / "torre_v4.html"

@app.get("/v3", response_class=FileResponse)
def serve_v3():
    return FileResponse(V3_FRONTEND, media_type="text/html",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

@app.get("/v4", response_class=FileResponse)
def serve_v4():
    return FileResponse(V4_FRONTEND, media_type="text/html",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


def _evaluar_coladas_ventana(referencia: str, horas: int) -> list:
    """
    Evalúa los 7 carriles (PC-1..PC-7) para cada colada en la ventana [referencia-horas, referencia].
    Compartida por /v3/gestion/coladas y /v3/gestion/alertas para que ambos endpoints
    usen exactamente la misma lógica de evaluación (evita que diverjan con el tiempo).
    """
    ref_dt = datetime.fromisoformat(referencia)
    desde_dt = ref_dt - timedelta(hours=horas)
    d = desde_dt.strftime("%Y-%m-%d %H:%M:%S")
    h = ref_dt.strftime("%Y-%m-%d %H:%M:%S")

    coladas = run("""
        SELECT c.c_IdColada        AS id_colada,
               MAX(c.u_Colada)     AS u_colada,
               MAX(c.u_Horno)      AS horno,
               MIN(c.c_FechaInicial) AS inicio_fusion,
               MAX(c.c_FechaFinal)   AS fin_fusion,
               MAX(c.c_Kilos)      AS kilos,
               MAX(c.c_Status)     AS status
        FROM cscmega_03coladacargametalica c
        WHERE c.c_FechaInicial BETWEEN :d AND :h
          AND c.c_IdColada > 0
        GROUP BY c.c_IdColada
        ORDER BY MIN(c.c_FechaInicial) DESC
        LIMIT 60
    """, {"d": d, "h": h})

    if not coladas:
        return []

    u_list = ",".join(str(c["u_colada"]) for c in coladas if c["u_colada"]) or "0"

    rec_raw = run(f"""
        SELECT u_Colada,
               COUNT(*) AS total,
               SUM(bRechazo) AS rechazos,
               ROUND(AVG(bRechazo)*100, 1) AS pct_rechazo,
               MAX(u_Fecha) AS ts_ultimo
        FROM cscmega_01rechazosbyidticket
        WHERE u_Colada IN ({u_list})
        GROUP BY u_Colada
    """, {})
    rec_idx = {r["u_Colada"]: r for r in rec_raw}

    cierre_raw = run(f"""
        SELECT rb.u_Colada,
               COUNT(*) AS total,
               SUM(r.bLIMP) AS terminadas,
               ROUND(SUM(r.bLIMP)/COUNT(*)*100, 1) AS pct_cierre
        FROM cscmega_01rechazosbyidticket rb
        JOIN cscmega_08ruta r ON r.u_idTicket = rb.idTicket
        WHERE rb.u_Colada IN ({u_list})
        GROUP BY rb.u_Colada
    """, {})
    cierre_idx = {r["u_Colada"]: r for r in cierre_raw}

    # PC-4: batch de temperatura desde alfamega_06_vaciado por c_IdColada
    id_list = ",".join(str(c["id_colada"]) for c in coladas if c["id_colada"]) or "0"
    temp_raw = run(f"""
        SELECT c_IdColada,
               COUNT(*) AS n_tickets,
               SUM(bCumplimientoTemperaturaOk) AS n_ok,
               ROUND(AVG(CASE WHEN vt_TemperaturaVaciado BETWEEN 1100 AND 1600
                         THEN vt_TemperaturaVaciado END)) AS temp_avg,
               MIN(FHrVaciado) AS ts_primer_vaci
        FROM alfamega_06_vaciado
        WHERE c_IdColada IN ({id_list})
          AND vt_TemperaturaVaciado IS NOT NULL
          AND vt_TemperaturaVaciado > 0
        GROUP BY c_IdColada
    """, {})
    temp_idx = {t["c_IdColada"]: t for t in temp_raw}

    # PC-2: batch química — último análisis SpectroMAX BASE por (u_Colada, u_Horno)
    # bC_F IS NOT NULL filtra a filas SpectroMAX; filas copa/ITACA tienen todos los flags NULL
    # NOTA: análisis BASE (pre-inoculación). Mg siempre FAIL en BASE → excluido del score.
    #       Pendiente: usar análisis FINAL cuando haya cobertura suficiente de aB_Calidad='FINAL'.
    uid_list = ",".join(str(c["u_colada"]) for c in coladas) or "0"
    quim_batch = run(f"""
        SELECT q.u_Colada, q.u_Horno,
               q.bC_F, q.bSi_F, q.bMg_F, q.bS_F, q.bP_F, q.bMn_F
        FROM cscmega_05aquimicoscumplimiento q
        INNER JOIN (
            SELECT u_Colada, u_Horno, MAX(u_Fecha) AS max_fecha
            FROM cscmega_05aquimicoscumplimiento
            WHERE u_Colada IN ({uid_list})
              AND bC_F IS NOT NULL
            GROUP BY u_Colada, u_Horno
        ) lat ON q.u_Colada = lat.u_Colada
             AND q.u_Horno  = lat.u_Horno
             AND q.u_Fecha  = lat.max_fecha
        GROUP BY q.u_Colada, q.u_Horno
    """, {})
    quim_idx = {(r["u_Colada"], r["u_Horno"]): r for r in quim_batch}

    # PC-5 batch: tiempo en molde vs TiempoDesmoldeo por NoParte
    molde5_batch = run(f"""
        SELECT rb.u_Colada,
               SUM(CASE WHEN r.FHrMOLD IS NOT NULL AND r.FHrDESM IS NOT NULL
                        AND m.TiempoDesmoldeo > 0
                   THEN 1 ELSE 0 END) AS con_umbral,
               SUM(CASE WHEN r.FHrMOLD IS NOT NULL AND r.FHrDESM IS NOT NULL
                        AND m.TiempoDesmoldeo > 0
                        AND TIMESTAMPDIFF(MINUTE, r.FHrMOLD, r.FHrDESM) / 60.0 >= m.TiempoDesmoldeo
                   THEN 1 ELSE 0 END) AS cumple
        FROM cscmega_01rechazosbyidticket rb
        JOIN cscmega_08ruta r ON r.u_idTicket = rb.idTicket
        LEFT JOIN (
            SELECT NoParte, MAX(IdModelo) AS IdModelo
            FROM corex_test.modelos WHERE TiempoDesmoldeo > 0 GROUP BY NoParte
        ) best ON best.NoParte = r.u_NoParte
        LEFT JOIN corex_test.modelos m ON m.IdModelo = best.IdModelo
        WHERE rb.u_Colada IN ({uid_list})
          AND (r.FHrMOLD IS NULL OR r.FHrDESM IS NULL
               OR TIMESTAMPDIFF(HOUR, r.FHrMOLD, r.FHrDESM) BETWEEN 0 AND 72)
        GROUP BY rb.u_Colada
    """, {})
    molde5_idx = {r["u_Colada"]: r for r in molde5_batch}

    # Desglose por No. Parte (para el toggle "Por Colada / Por No. Parte" del Nivel 1)
    sku_batch = run(f"""
        SELECT rb.u_Colada, r.u_NoParte AS sku,
               COUNT(*) AS total, SUM(rb.bRechazo) AS rechazos
        FROM cscmega_01rechazosbyidticket rb
        JOIN cscmega_08ruta r ON r.u_idTicket = rb.idTicket
        WHERE rb.u_Colada IN ({uid_list})
        GROUP BY rb.u_Colada, r.u_NoParte
    """, {})
    sku_idx = {}
    for r in sku_batch:
        sku_idx.setdefault(r["u_Colada"], []).append({
            "sku": r["sku"] or "—",
            "total": int(r["total"] or 0),
            "rechazos": int(r["rechazos"] or 0),
        })

    # Etapa de flujo (mismo criterio que /piso/flujo): basada en la pieza más
    # retrasada de la colada, vía flags bMOLD/bVACI/bDESM/bLIMP de cscmega_08ruta.
    etapa_batch = run(f"""
        SELECT rb.u_Colada,
               SUM(r.bMOLD) AS n_mold, SUM(r.bVACI) AS n_vaci,
               SUM(r.bDESM) AS n_desm, SUM(r.bLIMP) AS n_limp
        FROM cscmega_01rechazosbyidticket rb
        JOIN cscmega_08ruta r ON r.u_idTicket = rb.idTicket
        WHERE rb.u_Colada IN ({uid_list})
        GROUP BY rb.u_Colada
    """, {})
    etapa_idx = {r["u_Colada"]: r for r in etapa_batch}

    pc2 = next(c for c in CARRILES if c.id_pc == "PC-2")
    pc4 = next(c for c in CARRILES if c.id_pc == "PC-4")
    pc5 = next(c for c in CARRILES if c.id_pc == "PC-5")
    pc6 = next(c for c in CARRILES if c.id_pc == "PC-6")
    pc7 = next(c for c in CARRILES if c.id_pc == "PC-7")

    resultado = []
    for col in coladas:
        uid = col["u_colada"]
        cid = col["id_colada"]

        r   = rec_idx.get(uid, {})
        ci  = cierre_idx.get(uid, {})
        tr  = temp_idx.get(cid, {})

        pct_rec  = float(r["pct_rechazo"]) if r.get("total") else None
        pct_ci   = float(ci["pct_cierre"]) if ci.get("total") else None
        n_t      = int(tr.get("n_tickets") or 0)
        n_ok_t   = int(tr.get("n_ok") or 0)
        pct_temp = round(n_ok_t / n_t * 100.0, 1) if n_t else None

        qq       = quim_idx.get((uid, col["horno"]), {})
        _QKEYS   = ["bC_F", "bSi_F", "bS_F", "bP_F", "bMn_F"]  # Mg excluido (BASE pre-inoculación)
        _q_total = sum(1 for k in _QKEYS if qq.get(k) is not None)
        _q_pass  = sum(1 for k in _QKEYS if qq.get(k) is not None and str(qq[k]).endswith(")1"))
        pct_quim = round(_q_pass / _q_total * 100.0, 1) if _q_total else None

        mo5 = molde5_idx.get(uid, {})
        con_umbral5 = int(mo5.get("con_umbral") or 0)
        cumple5     = int(mo5.get("cumple") or 0)
        pct_cumple5 = round(cumple5 / con_umbral5 * 100.0, 1) if con_umbral5 else None

        ev2 = evaluar_carril(pc2, pct_quim,    None,                    cid)
        ev4 = evaluar_carril(pc4, pct_temp,    tr.get("ts_primer_vaci"), cid)
        ev5 = evaluar_carril(pc5, pct_cumple5, None,                    cid)
        ev6 = evaluar_carril(pc6, pct_rec,     r.get("ts_ultimo"),       cid)
        ev7 = evaluar_pc7(pct_ci, col["inicio_fusion"], cid)

        et = etapa_idx.get(uid, {})
        n_limp_e = int(et.get("n_limp") or 0)
        n_desm_e = int(et.get("n_desm") or 0)
        n_vaci_e = int(et.get("n_vaci") or 0)
        piezas_e = int(r.get("total") or 0)
        if piezas_e and n_limp_e >= piezas_e:
            etapa = "Terminado"
        elif n_limp_e > 0:
            etapa = "En limpieza"
        elif n_desm_e > 0:
            etapa = "En desmoldeo"
        elif n_vaci_e > 0:
            etapa = "En vaciado"
        else:
            etapa = "Sin datos"

        estados = {
            "PC-1": "GRIS", "PC-2": ev2["estado"], "PC-3": "GRIS",
            "PC-4": ev4["estado"], "PC-5": ev5["estado"],
            "PC-6": ev6["estado"], "PC-7": ev7["estado"],
        }
        tiene_alerta = any(v in ("ROJO", "AMBAR") for v in estados.values())

        resultado.append({
            "id_colada":     cid,
            "u_colada":      uid,
            "horno":         col["horno"],
            "inicio_fusion": str(col["inicio_fusion"] or ""),
            "fin_fusion":    str(col["fin_fusion"] or ""),
            "kilos":         float(col["kilos"] or 0),
            "status":        col["status"],
            "piezas":        int(r.get("total") or 0),
            "rechazos":      int(r.get("rechazos") or 0),
            "pct_rechazo":   pct_rec,
            "pct_cierre":    pct_ci,
            "temp_avg":      int(tr["temp_avg"]) if tr.get("temp_avg") else None,
            "pct_temp_ok":   pct_temp,
            "estados":       estados,
            "etapa":         etapa,
            "tiene_alerta":  tiene_alerta,
            "skus":          sku_idx.get(uid, []),
            "latencia_pc4_seg": ev4.get("latencia_seg"),
            "latencia_pc6_seg": ev6.get("latencia_seg"),
        })

    return resultado


@app.get("/v3/gestion/coladas")
def v3_gestion_coladas(
    referencia: str = Query(default="2025-12-19T08:00"),
    horas: int = Query(default=48),
):
    """
    Lista de coladas en la ventana con los 7 carriles pre-evaluados.
    PC-1 y PC-3 son GRIS estructural (sin fuente integrada en ia_fasa);
    PC-2, PC-4, PC-5, PC-6, PC-7 se evalúan en vivo.
    """
    resultado = _evaluar_coladas_ventana(referencia, horas)
    return {"referencia": referencia, "horas": horas, "coladas": resultado}


@app.get("/v3/gestion/colada/{id_colada}/carriles")
def v3_carriles_colada(id_colada: int):
    """Evalúa los 7 carriles del contrato de visibilidad para una colada."""
    meta = run("""
        SELECT u_Colada, u_Horno, c_Kilos, c_FechaInicial, c_FechaFinal
        FROM cscmega_03coladacargametalica
        WHERE c_IdColada = :id LIMIT 1
    """, {"id": id_colada})
    if not meta:
        raise HTTPException(404, f"Colada {id_colada} no encontrada")
    m   = meta[0]
    uid = m["u_Colada"]

    rec = run("""
        SELECT COUNT(*) AS total, SUM(bRechazo) AS rechazos,
               ROUND(AVG(bRechazo)*100, 1) AS pct_rechazo,
               MAX(u_Fecha) AS ts_ultimo
        FROM cscmega_01rechazosbyidticket WHERE u_Colada = :col
    """, {"col": uid})
    r = rec[0] if rec else {}

    top_def = run("""
        SELECT t.nomDefecto, t.No_Parte, COUNT(*) AS n
        FROM cscmega_01rechazosbyidticket rb
        JOIN cscmega_01resultadoidticket t ON t.idTicket = rb.idTicket
        WHERE rb.u_Colada = :col AND rb.bRechazo = 1 AND t.nomDefecto IS NOT NULL
        GROUP BY t.nomDefecto, t.No_Parte ORDER BY n DESC LIMIT 1
    """, {"col": uid})

    cierre = run("""
        SELECT COUNT(*) AS total, SUM(r.bLIMP) AS terminadas,
               ROUND(SUM(r.bLIMP)/COUNT(*)*100, 1) AS pct_cierre,
               MAX(r.FHrLIMP) AS ts_ultimo_limp
        FROM cscmega_01rechazosbyidticket rb
        JOIN cscmega_08ruta r ON r.u_idTicket = rb.idTicket
        WHERE rb.u_Colada = :col
    """, {"col": uid})
    ci = cierre[0] if cierre else {}

    pct_rec = float(r["pct_rechazo"])  if r.get("total") else None
    pct_ci  = float(ci["pct_cierre"]) if ci.get("total") else None

    # PC-5: tiempo en molde vs TiempoDesmoldeo por NoParte (corex_test.modelos)
    molde_raw = run("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN r.FHrMOLD IS NOT NULL AND r.FHrDESM IS NOT NULL THEN 1 ELSE 0 END) AS con_dato,
            SUM(CASE WHEN r.FHrMOLD IS NOT NULL AND r.FHrDESM IS NOT NULL
                     AND m.TiempoDesmoldeo > 0
                THEN 1 ELSE 0 END) AS con_umbral,
            SUM(CASE WHEN r.FHrMOLD IS NOT NULL AND r.FHrDESM IS NOT NULL
                     AND m.TiempoDesmoldeo > 0
                     AND TIMESTAMPDIFF(MINUTE, r.FHrMOLD, r.FHrDESM) / 60.0 >= m.TiempoDesmoldeo
                THEN 1 ELSE 0 END) AS cumple,
            ROUND(AVG(CASE WHEN r.FHrMOLD IS NOT NULL AND r.FHrDESM IS NOT NULL
                           THEN TIMESTAMPDIFF(MINUTE, r.FHrMOLD, r.FHrDESM) / 60.0 END), 2) AS avg_h,
            ROUND(MIN(CASE WHEN r.FHrMOLD IS NOT NULL AND r.FHrDESM IS NOT NULL
                           THEN TIMESTAMPDIFF(MINUTE, r.FHrMOLD, r.FHrDESM) END) / 60.0, 2) AS min_h,
            ROUND(MAX(CASE WHEN r.FHrMOLD IS NOT NULL AND r.FHrDESM IS NOT NULL
                           THEN TIMESTAMPDIFF(MINUTE, r.FHrMOLD, r.FHrDESM) END) / 60.0, 2) AS max_h
        FROM cscmega_01rechazosbyidticket rb
        JOIN cscmega_08ruta r ON r.u_idTicket = rb.idTicket
        LEFT JOIN (
            SELECT NoParte, MAX(IdModelo) AS IdModelo
            FROM corex_test.modelos WHERE TiempoDesmoldeo > 0 GROUP BY NoParte
        ) best ON best.NoParte = r.u_NoParte
        LEFT JOIN corex_test.modelos m ON m.IdModelo = best.IdModelo
        WHERE rb.u_Colada = :col
          AND (r.FHrMOLD IS NULL OR r.FHrDESM IS NULL
               OR TIMESTAMPDIFF(HOUR, r.FHrMOLD, r.FHrDESM) BETWEEN 0 AND 72)
    """, {"col": uid})
    mo = molde_raw[0] if molde_raw else {}
    avg_molde_h = float(mo["avg_h"]) if mo.get("avg_h") is not None else None
    con_umbral_mo = int(mo.get("con_umbral") or 0)
    cumple_mo     = int(mo.get("cumple") or 0)
    pct_cumple_mo = round(cumple_mo / con_umbral_mo * 100.0, 1) if con_umbral_mo else None

    # PC-2: último análisis SpectroMAX BASE por u_Colada + u_Horno
    # bC_F IS NOT NULL filtra a filas SpectroMAX; filas copa/ITACA tienen todos los flags NULL
    # NOTA: análisis BASE (pre-inoculación). Mg siempre FAIL en BASE → excluido del score.
    #       Pendiente: usar análisis FINAL cuando haya cobertura suficiente de aB_Calidad='FINAL'.
    quim_raw = run("""
        SELECT bC_F, bSi_F, bMg_F, bS_F, bP_F, bMn_F, u_Fecha
        FROM cscmega_05aquimicoscumplimiento
        WHERE u_Colada = :col AND u_Horno = :horno
          AND bC_F IS NOT NULL
        ORDER BY u_Fecha DESC LIMIT 1
    """, {"col": uid, "horno": int(m["u_Horno"])})
    q = quim_raw[0] if quim_raw else {}

    def _flag_status(v):
        if v is None: return None
        s = str(v)
        if s.endswith(")1"):  return "PASS"
        if s.endswith("-1"):  return "FAIL"
        if s.endswith(")0"):  return "LÍMITE"
        return None

    quim_elem = {
        "C":  _flag_status(q.get("bC_F")),
        "Si": _flag_status(q.get("bSi_F")),
        "Mg": _flag_status(q.get("bMg_F")),
        "S":  _flag_status(q.get("bS_F")),
        "P":  _flag_status(q.get("bP_F")),
        "Mn": _flag_status(q.get("bMn_F")),
    }
    _SCORE_KEYS = ["C", "Si", "S", "P", "Mn"]  # Mg excluido del score (BASE pre-inoculación)
    _qk_total = sum(1 for k in _SCORE_KEYS if quim_elem.get(k) is not None)
    _qk_pass  = sum(1 for k in _SCORE_KEYS if quim_elem.get(k) == "PASS")
    pct_quim  = round(_qk_pass / _qk_total * 100.0, 1) if _qk_total else None

    # PC-4: temperatura de vaciado desde alfamega_06_vaciado (c_IdColada como llave)
    temp_raw = run("""
        SELECT COUNT(*) AS n_tickets,
               SUM(bCumplimientoTemperaturaOk) AS n_ok,
               ROUND(AVG(CASE WHEN vt_TemperaturaVaciado BETWEEN 1100 AND 1600
                         THEN vt_TemperaturaVaciado END)) AS temp_avg,
               MIN(m_tmin) AS tmin,
               MAX(m_tmax) AS tmax,
               MIN(FHrVaciado) AS ts_primer_vaci
        FROM alfamega_06_vaciado
        WHERE c_IdColada = :id
          AND vt_TemperaturaVaciado IS NOT NULL
          AND vt_TemperaturaVaciado > 0
    """, {"id": id_colada})
    tr = temp_raw[0] if temp_raw else {}
    n_t  = int(tr.get("n_tickets") or 0)
    n_ok = int(tr.get("n_ok") or 0)
    pct_temp_ok = round(n_ok / n_t * 100.0, 1) if n_t else None

    carriles_out = []
    for c in CARRILES:
        if c.id_pc == "PC-2":
            ev = evaluar_carril(c, pct_quim, q.get("u_Fecha"), id_colada)
            ev["detalle"] = {
                "fecha_analisis": str(q["u_Fecha"]) if q.get("u_Fecha") else None,
                "elementos": quim_elem,
                "fuera_de_spec": [k for k, v in quim_elem.items() if v == "FAIL" and k != "Mg"],
                "en_limite":     [k for k, v in quim_elem.items() if v == "LÍMITE"],
                "nota_mg": "Mg = análisis BASE pre-inoculación — no incluido en % score. Pendiente: cambiar a FINAL cuando haya cobertura.",
            }
        elif c.id_pc == "PC-4":
            ev = evaluar_carril(c, pct_temp_ok, tr.get("ts_primer_vaci"), id_colada)
            ev["detalle"] = {
                "n_tickets":  n_t,
                "n_ok":       n_ok,
                "temp_avg":   int(tr["temp_avg"]) if tr.get("temp_avg") else None,
                "tmin":       float(tr["tmin"]) if tr.get("tmin") else None,
                "tmax":       float(tr["tmax"]) if tr.get("tmax") else None,
                "nota":       "28% de coladas con timestamp único — compliance por flag, no por ts" if n_t else None,
            }
        elif c.id_pc == "PC-5":
            ev = evaluar_carril(c, pct_cumple_mo, None, id_colada)
            ev["detalle"] = {
                "total_piezas": int(mo.get("total") or 0),
                "con_dato":     int(mo.get("con_dato") or 0),
                "con_umbral":   con_umbral_mo,
                "cumple":       cumple_mo,
                "pct_cumple":   pct_cumple_mo,
                "avg_h":        avg_molde_h,
                "min_h":        float(mo["min_h"]) if mo.get("min_h") is not None else None,
                "max_h":        float(mo["max_h"]) if mo.get("max_h") is not None else None,
            }
        elif c.id_pc == "PC-6":
            ev = evaluar_carril(c, pct_rec, r.get("ts_ultimo"), id_colada)
            defecto_top  = top_def[0]["nomDefecto"] if top_def else None
            no_parte_top = top_def[0]["No_Parte"]   if top_def else None
            es_rediseno  = (defecto_top, (no_parte_top or "").strip()) in _REDISENO_COMBOS
            protocolos   = [] if es_rediseno or ev["estado"] not in ("ROJO", "AMBAR") \
                           else _buscar_protocolos([defecto_top] if defecto_top else [])
            ev["detalle"] = {
                "total_piezas": int(r.get("total") or 0),
                "rechazos":     int(r.get("rechazos") or 0),
                "defecto_top":  defecto_top,
                "no_parte_top": no_parte_top,
                "es_rediseno":  es_rediseno,
                "protocolos":   protocolos,
            }
        elif c.id_pc == "PC-7":
            ev = evaluar_pc7(pct_ci, m.get("c_FechaInicial"), id_colada)
            ev["detalle"] = {
                "total_piezas":    int(ci.get("total") or 0),
                "terminadas":      int(ci.get("terminadas") or 0),
                "kg_vaciados":     float(m.get("c_Kilos") or 0),
                "pct_limp":        pct_ci,
                "dias_transcurridos": round((datetime.utcnow() - m["c_FechaInicial"]).total_seconds() / 86400, 1)
                                       if isinstance(m.get("c_FechaInicial"), datetime) else None,
            }
        else:
            ev = evaluar_carril(c, None, None, id_colada)
        carriles_out.append(ev)

    return {
        "id_colada":     id_colada,
        "u_colada":      uid,
        "horno":         m["u_Horno"],
        "kilos":         float(m.get("c_Kilos") or 0),
        "inicio_fusion": str(m.get("c_FechaInicial") or ""),
        "carriles":      carriles_out,
    }


# ══════════════════════════════════════════════════════════════════════════════
# ALERTAS Y PROTOCOLOS DE ACCIÓN — Andon/Jidoka
# ══════════════════════════════════════════════════════════════════════════════
# Combos conocidos de track REDISEÑO (calibración FASA 2026-07) — no corregibles
# por ningún protocolo de proceso (son de ingeniería de alimentador/mazarota).
# Se marcan aparte para no sugerir un protocolo de piso inútil.
_REDISENO_COMBOS = {("DEPRESION", "42091101"), ("RECHUPE", "42091101")}


def _buscar_protocolos(defectos_unicos: list) -> list:
    """
    Cruza defectos contra el puente Defecto→ElementoControl→Puesto/Proceso
    (gamamega_02_recomendacionescontrol, tabla chica — 254 filas, se trae
    completa y se filtra en Python; DefectoCritico es CSV sin espacio garantizado).
    Compartido por /v3/gestion/alertas y /v3/gestion/colada/{id}/carriles (PC-6)
    para que ambos usen exactamente la misma lógica de cruce.
    """
    if not defectos_unicos:
        return []
    puente = run("""
        SELECT Puesto, Proceso, ElementoControl, DefectoCritico, NoParteCritica
        FROM gamamega_02_recomendacionescontrol
    """, {})
    protocolos = []
    for row in puente:
        crit_list = [d.strip() for d in (row["DefectoCritico"] or "").split(",")]
        for defecto in defectos_unicos:
            if defecto in crit_list:
                protocolos.append({
                    "defecto":           defecto,
                    "puesto":            row["Puesto"],
                    "proceso":           row["Proceso"],
                    "elemento_control":  row["ElementoControl"],
                    "no_parte_critica":  row["NoParteCritica"],
                })
    return protocolos


@app.get("/v3/gestion/alertas")
def v3_gestion_alertas(
    referencia: str = Query(default="2025-12-19T08:00"),
    horas: int = Query(default=48),
):
    """
    Alertas activas (algún PC en ROJO o ÁMBAR) en la ventana, con protocolo de
    acción sugerido desde el puente Defecto→ElementoControl→Puesto/Proceso
    (gamamega_02_recomendacionescontrol — sustituye a la familia deprecada
    gamamega_01_defectoelementocontrol).

    El defecto dominante solo se resuelve para alertas de PC-6, que es el único
    carril con un nomDefecto asociado; los demás PCs son mediciones de proceso
    (química, temperatura, tiempo) sin un defecto puntual que buscar en el puente.
    """
    coladas = _evaluar_coladas_ventana(referencia, horas)

    ESTADOS_ALERTA = ("ROJO", "AMBAR")
    alertas = []
    for col in coladas:
        for pc_id, estado in col["estados"].items():
            if estado in ESTADOS_ALERTA:
                alertas.append({
                    "id_colada":     col["id_colada"],
                    "u_colada":      col["u_colada"],
                    "horno":         col["horno"],
                    "inicio_fusion": col["inicio_fusion"],
                    "id_pc":         pc_id,
                    "estado":        estado,
                    "pct_rechazo":   col.get("pct_rechazo"),
                    "pct_cierre":    col.get("pct_cierre"),
                    "defecto_top":   None,
                    "no_parte_top":  None,
                })

    if not alertas:
        return {"referencia": referencia, "horas": horas,
                "n_alertas": 0, "n_protocolos": 0,
                "alertas": [], "protocolos": [], "rediseno_flags": []}

    # Defecto + parte dominantes por colada, solo para alertas de PC-6
    u_pc6 = sorted({a["u_colada"] for a in alertas if a["id_pc"] == "PC-6"})
    top_idx = {}
    if u_pc6:
        u_list = ",".join(str(int(u)) for u in u_pc6)
        rows = run(f"""
            SELECT rb.u_Colada, t.nomDefecto, t.No_Parte, COUNT(*) AS n
            FROM cscmega_01rechazosbyidticket rb
            JOIN cscmega_01resultadoidticket t ON t.idTicket = rb.idTicket
            WHERE rb.u_Colada IN ({u_list}) AND rb.bRechazo = 1 AND t.nomDefecto IS NOT NULL
            GROUP BY rb.u_Colada, t.nomDefecto, t.No_Parte
            ORDER BY rb.u_Colada, n DESC
        """, {})
        for r in rows:
            top_idx.setdefault(r["u_Colada"], {"defecto": r["nomDefecto"], "no_parte": r["No_Parte"]})

    rediseno_flags = []
    for a in alertas:
        if a["id_pc"] != "PC-6":
            continue
        top = top_idx.get(a["u_colada"])
        if not top:
            continue
        a["defecto_top"]  = top["defecto"]
        a["no_parte_top"] = top["no_parte"]
        if (top["defecto"], (top["no_parte"] or "").strip()) in _REDISENO_COMBOS:
            rediseno_flags.append({
                "u_colada": a["u_colada"], "defecto": top["defecto"], "no_parte": top["no_parte"],
                "nota": "Track REDISEÑO — no corregible por protocolo de proceso "
                        "(diseño de alimentador/mazarota). Escalar a Ingeniería, no a piso.",
            })

    defectos_unicos = sorted({a["defecto_top"] for a in alertas if a["defecto_top"]})
    protocolos = _buscar_protocolos(defectos_unicos)

    return {
        "referencia":     referencia,
        "horas":          horas,
        "n_alertas":      len(alertas),
        "n_protocolos":   len(protocolos),
        "alertas":        alertas,
        "protocolos":     protocolos,
        "rediseno_flags": rediseno_flags,
    }


# ══════════════════════════════════════════════════════════════════════════════
# CLIENTES · PEDIDOS · PARTES — capa informativa (piso_ia.fdbase)
# ══════════════════════════════════════════════════════════════════════════════
# fdbase es facturación/embarque por pedido, NO el pedido original capturado —
# es la fuente más cercana disponible hoy (no hay Orden de Trabajo de cliente
# utilizable: corex_test.ordenestrabajo es un sistema de solicitudes de
# ingeniería/herramental, sin cliente ni relación a coladas).
# Capa puramente informativa — todavía no ligada al control de proceso.

def _resolver_clientes(no_partes: list) -> dict:
    """Batch No_Parte -> nombre de cliente, vía corex_test.modelos + corex_test.clientes."""
    if not no_partes:
        return {}
    placeholders = ",".join(f":p{i}" for i in range(len(no_partes)))
    params = {f"p{i}": p for i, p in enumerate(no_partes)}
    rows = run(f"""
        SELECT m.NoParte AS no_parte, c.Cliente AS cliente
        FROM corex_test.modelos m
        JOIN corex_test.clientes c ON c.IdCliente = m.IdCliente
        WHERE m.NoParte IN ({placeholders})
    """, params)
    return {r["no_parte"]: r["cliente"] for r in rows}


def _tasa_rechazo_partes(no_partes: list) -> dict:
    """Tasa de rechazo histórica por No_Parte (todo el histórico disponible en
    alfamega_01_rechazosbyidticket, congelado desde 2025-12-29). No es el rechazo
    real de un pedido/embarque específico — fdbase no tiene llave hacia colada/ticket."""
    if not no_partes:
        return {}
    placeholders = ",".join(f":p{i}" for i in range(len(no_partes)))
    params = {f"p{i}": p for i, p in enumerate(no_partes)}
    rows = run(f"""
        SELECT No_Parte AS no_parte, COUNT(*) AS n, SUM(bRechazo) AS rec
        FROM alfamega_01_rechazosbyidticket
        WHERE No_Parte IN ({placeholders})
        GROUP BY No_Parte
    """, params)
    return {
        r["no_parte"]: {
            "n": int(r["n"]),
            "rate": round(float(r["rec"] or 0) / r["n"] * 100, 1) if r["n"] else None,
        }
        for r in rows
    }


def _ubicacion_partes(no_partes: list) -> dict:
    """Desglose de piezas por etapa (Sin datos/En vaciado/En desmoldeo/En limpieza/Terminado) de la
    colada más reciente conocida por No_Parte — mismas compuertas bMOLD/bVACI/bDESM/bLIMP que usa
    Flujo del Proceso, pero contadas de forma mutuamente excluyente por pieza (una pieza cae en una
    sola etapa: la más avanzada que alcanzó), no acumulada. No hay llave hacia un pedido/embarque
    específico — es la última actividad de producción conocida para ese No_Parte, sin importar cuándo."""
    if not no_partes:
        return {}
    placeholders = ",".join(f":p{i}" for i in range(len(no_partes)))
    params = {f"p{i}": p for i, p in enumerate(no_partes)}
    rows = run(f"""
        SELECT r.u_NoParte AS no_parte, rb.u_Colada AS u_colada,
               COUNT(*) AS piezas,
               SUM(CASE WHEN r.bLIMP=1 THEN 1 ELSE 0 END) AS terminado,
               SUM(CASE WHEN r.bLIMP=0 AND r.bDESM=1 THEN 1 ELSE 0 END) AS en_limpieza,
               SUM(CASE WHEN r.bDESM=0 AND r.bVACI=1 THEN 1 ELSE 0 END) AS en_desmoldeo,
               SUM(CASE WHEN r.bVACI=0 AND r.bMOLD=1 THEN 1 ELSE 0 END) AS en_vaciado,
               SUM(CASE WHEN r.bMOLD=0 THEN 1 ELSE 0 END) AS sin_datos,
               MAX(rb.u_Fecha) AS ultima_fecha
        FROM cscmega_01rechazosbyidticket rb
        JOIN cscmega_08ruta r ON r.u_idTicket = rb.idTicket
        WHERE r.u_NoParte IN ({placeholders})
        GROUP BY r.u_NoParte, rb.u_Colada
    """, params)

    mejor: Dict[str, dict] = {}
    for r in rows:
        np_ = r["no_parte"]
        cur = mejor.get(np_)
        if cur is None or (r["ultima_fecha"] is not None
                            and (cur["ultima_fecha"] is None or r["ultima_fecha"] > cur["ultima_fecha"])):
            mejor[np_] = r

    out = {}
    for np_, r in mejor.items():
        out[np_] = {
            "u_colada": r["u_colada"],
            "ultima_actividad": str(r["ultima_fecha"] or ""),
            "piezas": int(r["piezas"] or 0),
            "etapas": {
                "sin_datos":    int(r["sin_datos"] or 0),
                "en_vaciado":   int(r["en_vaciado"] or 0),
                "en_desmoldeo": int(r["en_desmoldeo"] or 0),
                "en_limpieza":  int(r["en_limpieza"] or 0),
                "terminado":    int(r["terminado"] or 0),
            },
        }
    return out


@app.get("/v2/pedidos/lista")
def v2_pedidos_lista(
    desde: str = Query(default=None),
    hasta: str = Query(default=None),
    cliente: str = Query(default=None),
    q: str = Query(default=None),
    limit: int = Query(default=200),
):
    """
    Pedidos con piezas facturadas/embarcadas por No_Parte (piso_ia.fdbase),
    cruzado con cliente. Default: últimos 90 días desde el dato más reciente
    disponible en fdbase (no desde "hoy", que puede estar fuera de cobertura).
    """
    if not hasta:
        row = run("SELECT MAX(fecha) AS mx FROM piso_ia.fdbase", {})
        hasta_dt = row[0]["mx"] if row and row[0]["mx"] else datetime.utcnow().date()
    else:
        hasta_dt = datetime.fromisoformat(hasta).date()
    desde_dt = datetime.fromisoformat(desde).date() if desde else hasta_dt - timedelta(days=90)

    rows = run("""
        SELECT nopedido, no_parte, fecha, SUM(cant) AS piezas
        FROM piso_ia.fdbase
        WHERE fecha BETWEEN :d AND :h
        GROUP BY nopedido, no_parte, fecha
    """, {"d": desde_dt.isoformat(), "h": hasta_dt.isoformat()})

    vacio = {"desde": desde_dt.isoformat(), "hasta": hasta_dt.isoformat(),
             "n_pedidos": 0, "n_clientes": 0, "n_partes": 0, "total_piezas": 0, "pedidos": []}
    if not rows:
        return vacio

    no_partes = sorted({r["no_parte"] for r in rows})
    cliente_idx = _resolver_clientes(no_partes)
    tasa_idx = _tasa_rechazo_partes(no_partes)

    pedidos: dict = {}
    for r in rows:
        ped = r["nopedido"]
        p = pedidos.setdefault(ped, {"fecha_ini": r["fecha"], "fecha_fin": r["fecha"], "partes": {}})
        p["fecha_ini"] = min(p["fecha_ini"], r["fecha"])
        p["fecha_fin"] = max(p["fecha_fin"], r["fecha"])
        p["partes"][r["no_parte"]] = p["partes"].get(r["no_parte"], 0) + float(r["piezas"] or 0)

    # Calidad por cliente: promedio de la tasa histórica de sus No_Parte, ponderado por piezas
    # de este período. No es rechazo real de estas piezas — ver nota en _tasa_rechazo_partes.
    calidad_clientes_acc: Dict[str, dict] = {}
    for ped, p in pedidos.items():
        for np_, piezas in p["partes"].items():
            cli = cliente_idx.get(np_) or "Sin cliente resuelto"
            acc = calidad_clientes_acc.setdefault(cli, {"piezas_total": 0.0, "piezas_con_dato": 0.0, "ponderado": 0.0})
            acc["piezas_total"] += piezas
            t = tasa_idx.get(np_)
            if t and t["rate"] is not None:
                acc["piezas_con_dato"] += piezas
                acc["ponderado"] += piezas * t["rate"]
    calidad_clientes = {
        cli: {
            "rate": round(acc["ponderado"] / acc["piezas_con_dato"], 1) if acc["piezas_con_dato"] else None,
            "cobertura_pct": round(acc["piezas_con_dato"] / acc["piezas_total"] * 100, 0) if acc["piezas_total"] else 0,
        }
        for cli, acc in calidad_clientes_acc.items()
    }

    salida = []
    for ped, p in pedidos.items():
        clientes_pedido: dict = {}
        for np_, piezas in p["partes"].items():
            cli = cliente_idx.get(np_) or "Sin cliente resuelto"
            clientes_pedido[cli] = clientes_pedido.get(cli, 0) + piezas
        cliente_principal = max(clientes_pedido.items(), key=lambda kv: kv[1])[0] if clientes_pedido else None
        salida.append({
            "nopedido":          ped,
            "fecha_ini":         str(p["fecha_ini"]),
            "fecha_fin":         str(p["fecha_fin"]),
            "cliente_principal": cliente_principal,
            "n_clientes":        len(clientes_pedido),
            "n_partes":          len(p["partes"]),
            "total_piezas":      round(sum(p["partes"].values()), 0),
        })

    if cliente:
        cl_low = cliente.lower()
        salida = [s for s in salida if s["cliente_principal"] and cl_low in s["cliente_principal"].lower()]
    if q:
        q_low = q.lower()
        salida = [s for s in salida
                  if q_low in s["nopedido"].lower()
                  or any(q_low in np_.lower() for np_ in pedidos[s["nopedido"]]["partes"])]

    salida.sort(key=lambda s: s["fecha_fin"], reverse=True)

    return {
        "desde": desde_dt.isoformat(), "hasta": hasta_dt.isoformat(),
        "n_pedidos":     len(salida),
        "n_clientes":    len({s["cliente_principal"] for s in salida if s["cliente_principal"]}),
        "n_partes":      len({np_ for s in salida for np_ in pedidos[s["nopedido"]]["partes"]}),
        "total_piezas":  round(sum(s["total_piezas"] for s in salida), 0),
        "pedidos":       salida[:limit],
        "calidad_clientes": calidad_clientes,
        "calidad_partes":   {np_: t["rate"] for np_, t in tasa_idx.items()},
        "calidad_nota":  "Tasa histórica de rechazo del No. Parte (todo el histórico disponible, "
                          "congelado desde 2025-12-29) — no es el rechazo real de las piezas de este "
                          "pedido, ya que no existe llave entre fdbase y las coladas/tickets de producción.",
    }


@app.get("/v2/pedidos/{nopedido}/detalle")
def v2_pedido_detalle(nopedido: str):
    rows = run("""
        SELECT no_parte, SUM(cant) AS piezas, MIN(fecha) AS fecha_ini, MAX(fecha) AS fecha_fin
        FROM piso_ia.fdbase
        WHERE nopedido = :ped
        GROUP BY no_parte
        ORDER BY piezas DESC
    """, {"ped": nopedido})
    if not rows:
        raise HTTPException(404, f"Pedido {nopedido} no encontrado")

    no_partes = [r["no_parte"] for r in rows]
    cliente_idx = _resolver_clientes(no_partes)
    tasa_idx = _tasa_rechazo_partes(no_partes)
    ubicacion_idx = _ubicacion_partes(no_partes)
    lineas = [{
        "no_parte":     r["no_parte"],
        "piezas":       round(float(r["piezas"] or 0), 0),
        "cliente":      cliente_idx.get(r["no_parte"]),
        "fecha_ini":    str(r["fecha_ini"]),
        "fecha_fin":    str(r["fecha_fin"]),
        "rate_calidad": (tasa_idx.get(r["no_parte"]) or {}).get("rate"),
        "ubicacion":    ubicacion_idx.get(r["no_parte"]),
    } for r in rows]

    return {"nopedido": nopedido, "lineas": lineas}


@app.get("/v2/pedidos/clientes-lista")
def v2_pedidos_clientes_lista():
    """Clientes con actividad en fdbase, ordenados por piezas embarcadas —
    para el selector del panel de toneladas entregadas / % rechazo."""
    rows = run("""
        SELECT c.Cliente AS cliente, SUM(f.cant) AS piezas
        FROM piso_ia.fdbase f
        JOIN (
            SELECT NoParte, MAX(IdModelo) AS IdModelo
            FROM corex_test.modelos WHERE PesoKgs > 0
            GROUP BY NoParte
        ) best ON best.NoParte = f.no_parte
        JOIN corex_test.modelos m ON m.IdModelo = best.IdModelo
        JOIN corex_test.clientes c ON c.IdCliente = m.IdCliente
        GROUP BY c.Cliente
        ORDER BY piezas DESC
    """, {})
    return {"clientes": [r["cliente"] for r in rows]}


@app.get("/v2/pedidos/cliente-tonelaje")
def v2_pedidos_cliente_tonelaje(
    cliente: str = Query(...),
    desde: str = Query(default=None),
    hasta: str = Query(default=None),
    bucket: str = Query(default="mes"),
):
    """Toneladas entregadas (piso_ia.fdbase x corex_test.modelos.PesoKgs) y % de rechazo real de
    producción (cscmega_01rechazosbyidticket) para un cliente. Ventana y granularidad vienen del
    período activo en el resto del tab (Año/Trimestre → bucket 'mes'; Mes/Semana → bucket 'semana',
    para no perder la tendencia cuando la ventana es corta) — antes esta gráfica tenía una ventana
    fija de 12 meses independiente del filtro de abajo. Sin desde/hasta: fallback a los últimos 12
    meses anclados al MAX(fecha) real de fdbase (no "hoy", fdbase va por detrás).
    % rechazo queda en None donde no hay dato: producción congelada desde 2025-12-29, mientras que
    fdbase (embarques) sigue teniendo filas hasta marzo 2026."""
    from datetime import date, timedelta
    import calendar as cal_mod
    MESES = ['', 'Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']

    if desde and hasta:
        d_ini = date.fromisoformat(desde)
        d_fin = date.fromisoformat(hasta)
    else:
        row = run("SELECT MAX(fecha) AS mx FROM piso_ia.fdbase", {})
        max_date = row[0]["mx"] if row and row[0]["mx"] else date.today()
        end = date(max_date.year, max_date.month, 1)
        m, y = end.month - 11, end.year
        while m <= 0:
            m += 12
            y -= 1
        d_ini = date(y, m, 1)
        d_fin = date(end.year, end.month, cal_mod.monthrange(end.year, end.month)[1])

    # Resolver primero los No. Parte del cliente (tablas chicas modelos/clientes) y luego
    # filtrar fdbase/cscmega por IN(...) — evita joinear las tablas grandes contra clientes
    # por cada fila del rango pedido (esa versión tardaba ~13s por cliente).
    partes_rows = run("""
        SELECT best.NoParte AS no_parte, m.PesoKgs AS peso_kgs
        FROM (
            SELECT NoParte, MAX(IdModelo) AS IdModelo
            FROM corex_test.modelos WHERE PesoKgs > 0
            GROUP BY NoParte
        ) best
        JOIN corex_test.modelos m ON m.IdModelo = best.IdModelo
        JOIN corex_test.clientes c ON c.IdCliente = m.IdCliente
        WHERE c.Cliente = :cli
    """, {"cli": cliente})
    peso_idx = {r["no_parte"]: float(r["peso_kgs"] or 0) for r in partes_rows}
    no_partes = list(peso_idx.keys())

    es_semana = bucket == "semana"
    tons_key_sql = "DATE_SUB(fecha, INTERVAL WEEKDAY(fecha) DAY)" if es_semana else "fecha"
    rech_key_sql = "DATE_SUB(rb.u_Fecha, INTERVAL WEEKDAY(rb.u_Fecha) DAY)" if es_semana else "rb.u_Fecha"
    fmt = "%Y-%m-%d" if es_semana else "%Y-%m"

    tons_idx: Dict[str, float] = {}
    rech_idx: Dict[str, Tuple[int, int]] = {}
    if no_partes:
        placeholders = ",".join(f":p{i}" for i in range(len(no_partes)))
        params = {f"p{i}": p for i, p in enumerate(no_partes)}

        tons_rows = run(f"""
            SELECT DATE_FORMAT({tons_key_sql},'{fmt}') AS bk, no_parte, SUM(cant) AS piezas
            FROM piso_ia.fdbase
            WHERE no_parte IN ({placeholders}) AND fecha BETWEEN :d AND :h
            GROUP BY bk, no_parte
        """, {**params, "d": d_ini.isoformat(), "h": d_fin.isoformat()})
        for r in tons_rows:
            tons_idx[r["bk"]] = tons_idx.get(r["bk"], 0.0) + float(r["piezas"] or 0) * peso_idx.get(r["no_parte"], 0)
        tons_idx = {bk: v / 1000 for bk, v in tons_idx.items()}

        rech_rows = run(f"""
            SELECT DATE_FORMAT({rech_key_sql},'{fmt}') AS bk,
                   COUNT(*) AS n, SUM(rb.bRechazo) AS rec
            FROM cscmega_01rechazosbyidticket rb
            JOIN cscmega_01resultadoidticket rt ON rt.idTicket = rb.idTicket
            WHERE rt.No_Parte IN ({placeholders}) AND rb.u_Fecha BETWEEN :d AND :h
            GROUP BY bk
        """, {**params, "d": d_ini.isoformat() + " 00:00:00", "h": d_fin.isoformat() + " 23:59:59"})
        rech_idx = {r["bk"]: (int(r["n"] or 0), int(r["rec"] or 0)) for r in rech_rows}

    # Períodos a mostrar (claves + labels), cubriendo todo [d_ini, d_fin]
    periodos = []
    if es_semana:
        lunes = d_ini - timedelta(days=d_ini.weekday())
        while lunes <= d_fin:
            periodos.append((lunes.isoformat(), f"{lunes.day} {MESES[lunes.month]}"))
            lunes += timedelta(days=7)
    else:
        y, m = d_ini.year, d_ini.month
        while date(y, m, 1) <= d_fin:
            periodos.append((f"{y}-{m:02d}", f"{MESES[m]} {y}"))
            m += 1
            if m > 12:
                m = 1
                y += 1

    serie = []
    for bk, lbl in periodos:
        n, rec = rech_idx.get(bk, (0, 0))
        serie.append({
            "periodo":         bk,
            "lbl":             lbl,
            "tons_entregadas": round(tons_idx.get(bk, 0.0), 2),
            "pct_rechazo":     round(rec / n * 100, 1) if n else None,
        })

    return {"cliente": cliente, "bucket": bucket, "serie": serie}


# ══════════════════════════════════════════════════════════════════════════════
# PRODUCCIÓN — Tonelaje mensual
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/produccion/tonelaje-mensual")
def produccion_tonelaje_mensual(anios: str = Query(default="2024,2025")):
    """
    Tonelaje mensual con estimación de producto terminado vs rechazo.
    Método: peso_pieza = c_Kilos / total_piezas por colada.
    kg_ok = piezas_ok * peso_pieza  |  kg_rech = piezas_rech * peso_pieza
    """
    lista_anios = [a.strip() for a in anios.split(",") if a.strip().isdigit()]
    if not lista_anios:
        lista_anios = ["2025"]

    rows = run("""
        SELECT
            YEAR(cc.c_FechaInicial)   AS anio,
            MONTH(cc.c_FechaInicial)  AS mes,
            cc.u_Horno                AS horno,
            COUNT(DISTINCT cc.u_Colada) AS coladas,
            ROUND(SUM(cc.c_Kilos), 0)   AS kg_brutos,
            ROUND(AVG(cc.c_Kilos), 0)   AS kg_prom_colada,
            SUM(pz.total_piezas)        AS total_piezas,
            SUM(pz.piezas_ok)           AS piezas_ok,
            SUM(pz.piezas_rech)         AS piezas_rech,
            ROUND(SUM(
                CASE WHEN pz.total_piezas > 0
                     THEN cc.c_Kilos / pz.total_piezas * pz.piezas_ok
                     ELSE 0 END
            ), 0) AS kg_producto_ok,
            ROUND(SUM(
                CASE WHEN pz.total_piezas > 0
                     THEN cc.c_Kilos / pz.total_piezas * pz.piezas_rech
                     ELSE 0 END
            ), 0) AS kg_rechazo
        FROM cscmega_03coladacargametalica cc
        JOIN (
            SELECT u_Colada, u_Horno,
                   COUNT(*)            AS total_piezas,
                   SUM(1 - bRechazo)   AS piezas_ok,
                   SUM(bRechazo)       AS piezas_rech
            FROM cscmega_01rechazosbyidticket
            GROUP BY u_Colada, u_Horno
        ) pz ON pz.u_Colada = cc.u_Colada AND pz.u_Horno = cc.u_Horno
        WHERE YEAR(cc.c_FechaInicial) IN ({})
          AND cc.c_Kilos > 0
          AND cc.c_FechaInicial IS NOT NULL
        GROUP BY anio, mes, horno
        ORDER BY anio, mes, horno
    """.format(",".join(lista_anios)))

    from collections import defaultdict
    meses = defaultdict(lambda: {
        "coladas": 0, "kg_brutos": 0.0,
        "total_piezas": 0, "piezas_ok": 0, "piezas_rech": 0,
        "kg_producto_ok": 0.0, "kg_rechazo": 0.0,
        "por_horno": {}
    })
    for r in rows:
        key = "{}-{}".format(r["anio"], str(r["mes"]).zfill(2))
        m = meses[key]
        m["anio"] = r["anio"]
        m["mes"]  = r["mes"]
        m["coladas"]       += int(r["coladas"])
        m["kg_brutos"]     += float(r["kg_brutos"] or 0)
        m["total_piezas"]  += int(r["total_piezas"] or 0)
        m["piezas_ok"]     += int(r["piezas_ok"] or 0)
        m["piezas_rech"]   += int(r["piezas_rech"] or 0)
        m["kg_producto_ok"]+= float(r["kg_producto_ok"] or 0)
        m["kg_rechazo"]    += float(r["kg_rechazo"] or 0)
        m["por_horno"][str(r["horno"])] = {
            "coladas":        int(r["coladas"]),
            "kg_brutos":      float(r["kg_brutos"] or 0),
            "kg_producto_ok": float(r["kg_producto_ok"] or 0),
            "kg_rechazo":     float(r["kg_rechazo"] or 0),
            "kg_prom_colada": float(r["kg_prom_colada"] or 0),
        }

    resumen = []
    for m in sorted(meses.values(), key=lambda x: (x["anio"], x["mes"])):
        m["ton_brutos"]      = round(m["kg_brutos"] / 1000, 2)
        m["ton_producto_ok"] = round(m["kg_producto_ok"] / 1000, 2)
        m["ton_rechazo"]     = round(m["kg_rechazo"] / 1000, 2)
        m["pct_rechazo_kg"]  = round(m["kg_rechazo"] / m["kg_brutos"] * 100, 1) if m["kg_brutos"] else 0
        resumen.append(m)

    total_anual = {}
    for m in resumen:
        y = str(m["anio"])
        if y not in total_anual:
            total_anual[y] = {"ton_brutos": 0.0, "ton_producto_ok": 0.0, "ton_rechazo": 0.0}
        total_anual[y]["ton_brutos"]      = round(total_anual[y]["ton_brutos"]      + m["ton_brutos"], 2)
        total_anual[y]["ton_producto_ok"] = round(total_anual[y]["ton_producto_ok"] + m["ton_producto_ok"], 2)
        total_anual[y]["ton_rechazo"]     = round(total_anual[y]["ton_rechazo"]     + m["ton_rechazo"], 2)

    return {
        "metodo": "peso_pieza = c_Kilos / total_piezas por colada. kg_ok = piezas_ok * peso_pieza.",
        "total_por_anio": total_anual,
        "meses": resumen,
    }


# ══════════════════════════════════════════════════════════════════════════════
# AGENTE BETA — calidad / predictivo (v1, 2026-08-05)
# ══════════════════════════════════════════════════════════════════════════════
# Dos compuertas de datos, nunca mezcladas:
#   1. Criticidad histórica — gamamega_01_riesgodefectoparte. Confirmado
#      idéntica fila a fila (mismo z_defecto_parte con 10+ decimales) contra
#      TabulacionDeCriticidad_junio28_2026.xlsx (tabulación manual de Carlos
#      Cruz) — se decidió NO duplicarla en una tabla nueva, leer esta directo.
#      FlagCriticidadDefectoParte/FlagTendInterAnual son -1/0/1/NULL, no
#      binarios (713/575/87/6 filas resp.) — ver _riesgo_label().
#   2. Química en vivo — cscmega_05cquimicosbase (SpectroMAX). ETL congelado
#      desde 2025-12-29 (responsable: Jasso). Disponibilidad se calcula en
#      vivo contra MAX(u_Fecha) real, nunca hardcodeada ni simulada/inferida
#      cuando falta.

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


@app.get("/api/beta/evaluar")
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


@app.get("/api/beta/riesgo-alto")
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


# ══════════════════════════════════════════════════════════════════════════════
# AGENTE ALFA — ritmo real, cuello de botella y proyección de entrega, 100% lectura
# ══════════════════════════════════════════════════════════════════════════════
# Rediseño 2026-08-05 (JC): la v1 (PC-gate por colada) se retiró de aquí — esa
# señal ya vive en el tab "Alertas y Protocolos", era redundante con Agente Beta.
# Alfa ahora responde "¿vamos a cumplir la entrega de esta parte/cliente?" cruzando:
#   - ritmo real por etapa (cscmega_08ruta: FHrMOLD/FhrVACI/FHrDESM/FHrLIMP —
#     confirmados confiables ~93-99% dentro de 0-72h entre etapas consecutivas)
#   - proceso/cliente (corex_test.modelos.area + corex_test.clientes — AUTORITATIVO,
#     no depende del Excel del Plan de Producción)
#   - saldo pendiente (programa_produccion_detalle_referencial.sdo_final — NO
#     AUTORITATIVO, ver /api/programa; la tabla puede no existir todavía si el
#     DDL del Plan de Producción no se ha corrido — se degrada con gracia)

def _referencia_actual_flujo() -> str:
    """Ancla la ventana al último día con volumen real de coladas (>=5), no al
    MAX(c_FechaInicial) crudo — hay registros aislados posteriores al cierre
    real de datos (ej. colada 78830 el 2025-12-29, 9 días después de la última
    racha densa el 2025-12-20) que dejan la ventana vacía si se usan como ancla."""
    row = run("""
        SELECT MAX(c.c_FechaInicial) AS mx FROM cscmega_03coladacargametalica c
        JOIN (
            SELECT DATE(c_FechaInicial) AS dia
            FROM cscmega_03coladacargametalica
            WHERE c_IdColada > 0
            GROUP BY DATE(c_FechaInicial)
            HAVING COUNT(*) >= 5
        ) denso ON DATE(c.c_FechaInicial) = denso.dia
        WHERE c.c_IdColada > 0
    """, {})
    mx = row[0]["mx"] if row and row[0].get("mx") else None
    return mx.strftime("%Y-%m-%dT%H:%M") if mx else "2025-12-19T08:00"


def _resolver_proceso_cliente(no_partes: list) -> dict:
    """No_Parte -> {proceso (AF1/AF2/AF3, de corex_test.modelos.area), cliente}.
    ~1074/2107 modelos no tienen area asignada en el ERP -- se reporta None, no se infiere."""
    if not no_partes:
        return {}
    placeholders = ",".join(f":p{i}" for i in range(len(no_partes)))
    params = {f"p{i}": p for i, p in enumerate(no_partes)}
    rows = run(f"""
        SELECT m.NoParte AS no_parte, NULLIF(m.area, '') AS proceso, c.Cliente AS cliente
        FROM corex_test.modelos m
        LEFT JOIN corex_test.clientes c ON c.IdCliente = m.IdCliente
        WHERE m.NoParte IN ({placeholders})
    """, params)
    return {r["no_parte"]: {"proceso": r["proceso"], "cliente": r["cliente"]} for r in rows}


@app.get("/api/alfa/clientes")
def alfa_clientes():
    """Clientes con actividad de flujo real (cscmega_08ruta) en los últimos 90 días,
    ordenados por piezas -- para el selector de descubrimiento del tab Alfa (JC señaló
    que escribir el No_Parte a ciegas no era usable)."""
    referencia = _referencia_actual_flujo()
    ref_dt = datetime.fromisoformat(referencia)
    desde_dt = ref_dt - timedelta(days=90)
    rows = run("""
        SELECT c.Cliente AS cliente, COUNT(*) AS piezas
        FROM cscmega_08ruta r
        JOIN corex_test.modelos m ON m.NoParte = r.u_NoParte
        JOIN corex_test.clientes c ON c.IdCliente = m.IdCliente
        WHERE r.FHrLIMP BETWEEN :d AND :h
        GROUP BY c.Cliente
        ORDER BY piezas DESC
    """, {"d": desde_dt, "h": ref_dt})
    return {"clientes": [r["cliente"] for r in rows]}


@app.get("/api/alfa/partes-cliente")
def alfa_partes_cliente(cliente: str = Query(...), dias: int = Query(default=14, le=90)):
    """Partes de un cliente con ritmo real reciente -- puebla la lista clickeable
    que reemplaza la búsqueda a ciegas por No_Parte."""
    referencia = _referencia_actual_flujo()
    ref_dt = datetime.fromisoformat(referencia)
    desde_dt = ref_dt - timedelta(days=dias)
    rows = run("""
        SELECT r.u_NoParte AS no_parte, NULLIF(m.area, '') AS proceso, COUNT(*) AS piezas
        FROM cscmega_08ruta r
        JOIN corex_test.modelos m ON m.NoParte = r.u_NoParte
        JOIN corex_test.clientes c ON c.IdCliente = m.IdCliente
        WHERE c.Cliente = :cli AND r.FHrLIMP BETWEEN :d AND :h
        GROUP BY r.u_NoParte, m.area
        ORDER BY piezas DESC
    """, {"cli": cliente, "d": desde_dt, "h": ref_dt})
    return {
        "cliente": cliente,
        "dias": dias,
        "partes": [
            {
                "no_parte": r["no_parte"],
                "proceso": r["proceso"],
                "piezas": int(r["piezas"]),
                "ritmo_diario_prom": round(int(r["piezas"]) / dias, 2),
            }
            for r in rows
        ],
    }


def _ritmo_parte(no_parte: str, dias: int, referencia: str) -> dict:
    """Ritmo real de una parte: piezas terminadas (FHrLIMP, equivalente a bLIMP=1)
    por día en la ventana, y tiempo de ciclo real por etapa (promedio en horas,
    filtrado a transiciones de 0-72h -- mismo criterio que ya usa PC-5 para
    descartar timestamps con huecos de meses, que existen y son ~1-7% de los casos)."""
    ref_dt = datetime.fromisoformat(referencia)
    desde_dt = ref_dt - timedelta(days=dias)

    serie = run("""
        SELECT DATE(FHrLIMP) AS dia, COUNT(*) AS piezas
        FROM cscmega_08ruta
        WHERE u_NoParte = :np AND FHrLIMP BETWEEN :d AND :h
        GROUP BY DATE(FHrLIMP) ORDER BY dia
    """, {"np": no_parte, "d": desde_dt, "h": ref_dt})
    total = sum(int(r["piezas"]) for r in serie)

    ciclo = run("""
        SELECT
          ROUND(AVG(CASE WHEN TIMESTAMPDIFF(HOUR, FHrMOLD, FhrVACI) BETWEEN 0 AND 72
                     THEN TIMESTAMPDIFF(MINUTE, FHrMOLD, FhrVACI)/60.0 END), 1) AS h_molde_vaciado,
          ROUND(AVG(CASE WHEN TIMESTAMPDIFF(HOUR, FhrVACI, FHrDESM) BETWEEN 0 AND 72
                     THEN TIMESTAMPDIFF(MINUTE, FhrVACI, FHrDESM)/60.0 END), 1) AS h_vaciado_desmoldeo,
          ROUND(AVG(CASE WHEN TIMESTAMPDIFF(HOUR, FHrDESM, FHrLIMP) BETWEEN 0 AND 72
                     THEN TIMESTAMPDIFF(MINUTE, FHrDESM, FHrLIMP)/60.0 END), 1) AS h_desmoldeo_limpieza
        FROM cscmega_08ruta
        WHERE u_NoParte = :np AND FHrLIMP BETWEEN :d AND :h
    """, {"np": no_parte, "d": desde_dt, "h": ref_dt})
    c = ciclo[0] if ciclo else {}

    return {
        "referencia": referencia,
        "dias": dias,
        "serie_terminadas": [{"dia": str(r["dia"]), "piezas": int(r["piezas"])} for r in serie],
        "total_terminadas": total,
        "ritmo_diario_prom": round(total / dias, 2) if dias else None,
        "ciclo_horas": {
            "molde_a_vaciado":     float(c["h_molde_vaciado"])     if c.get("h_molde_vaciado")     is not None else None,
            "vaciado_a_desmoldeo": float(c["h_vaciado_desmoldeo"]) if c.get("h_vaciado_desmoldeo") is not None else None,
            "desmoldeo_a_limpieza": float(c["h_desmoldeo_limpieza"]) if c.get("h_desmoldeo_limpieza") is not None else None,
        },
    }


def _saldo_pendiente_parte(no_parte: str) -> Optional[dict]:
    """Saldo pendiente del Plan de Producción vigente para esta parte, del plan
    más reciente cargado. Devuelve None si las tablas no existen todavía (DDL
    sin ejecutar -- ver scripts/ddl_programa_produccion.sql) o si la parte no
    está en ningún plan cargado. SIEMPRE no autoritativo -- ver /api/programa."""
    try:
        rows = run("""
            SELECT d.sdo_final, d.kg_buenos, d.status, p.anio_mes
            FROM programa_produccion_detalle_referencial d
            JOIN programa_produccion_mensual p ON p.id = d.programa_id
            WHERE d.parte = :np
            ORDER BY p.anio_mes DESC, p.rev_no DESC
            LIMIT 1
        """, {"np": no_parte})
    except Exception:
        return None
    if not rows:
        return None
    r = rows[0]
    return {
        "sdo_final":   r["sdo_final"],
        "kg_buenos":   float(r["kg_buenos"]) if r.get("kg_buenos") is not None else None,
        "status":      r["status"],
        "anio_mes":    str(r["anio_mes"]),
        "autoritativo": False,
    }


@app.get("/api/alfa/evaluar")
def alfa_evaluar(no_parte: str = Query(...), dias: int = Query(default=14, le=90)):
    referencia = _referencia_actual_flujo()
    pc = _resolver_proceso_cliente([no_parte]).get(no_parte, {})
    ritmo = _ritmo_parte(no_parte, dias, referencia)
    saldo = _saldo_pendiente_parte(no_parte)

    proyeccion = None
    if saldo and saldo.get("sdo_final") and ritmo["ritmo_diario_prom"]:
        proyeccion = {
            "dias_estimados": math.ceil(saldo["sdo_final"] / ritmo["ritmo_diario_prom"]),
            "nota": "Estimado con el ritmo real reciente contra el saldo pendiente del "
                    "Plan de Producción -- el saldo NO es una fuente autoritativa.",
        }

    return {
        "no_parte":       no_parte,
        "proceso":        pc.get("proceso"),
        "cliente":        pc.get("cliente"),
        "ritmo":          ritmo,
        "saldo_pendiente": saldo,
        "proyeccion":     proyeccion,
    }


_CUELLO_UMBRAL_RATIO = 1.3  # heurística inicial (reciente >= 1.3x el baseline) -- ajustar con el piso

@app.get("/api/alfa/cuello-botella")
def alfa_cuello_botella(dias: int = Query(default=7, le=30)):
    """Compara el tiempo de ciclo real reciente (últimos `dias`) contra un
    baseline de las 4 ventanas anteriores, por proceso (AF1/AF2/AF3) y etapa.
    Sin concepto de 'turno' todavía -- no hay horarios de turno definidos en
    el código, queda pendiente si se quiere ese nivel de detalle."""
    referencia = _referencia_actual_flujo()
    ref_dt = datetime.fromisoformat(referencia)
    reciente_desde = ref_dt - timedelta(days=dias)
    baseline_desde = reciente_desde - timedelta(days=dias * 4)

    def _ciclo_por_proceso(desde_dt, hasta_dt):
        rows = run("""
            SELECT m.area AS proceso,
                   ROUND(AVG(CASE WHEN TIMESTAMPDIFF(HOUR, r.FHrMOLD, r.FhrVACI) BETWEEN 0 AND 72
                              THEN TIMESTAMPDIFF(MINUTE, r.FHrMOLD, r.FhrVACI)/60.0 END), 2) AS h_molde_vaciado,
                   ROUND(AVG(CASE WHEN TIMESTAMPDIFF(HOUR, r.FhrVACI, r.FHrDESM) BETWEEN 0 AND 72
                              THEN TIMESTAMPDIFF(MINUTE, r.FhrVACI, r.FHrDESM)/60.0 END), 2) AS h_vaciado_desmoldeo,
                   ROUND(AVG(CASE WHEN TIMESTAMPDIFF(HOUR, r.FHrDESM, r.FHrLIMP) BETWEEN 0 AND 72
                              THEN TIMESTAMPDIFF(MINUTE, r.FHrDESM, r.FHrLIMP)/60.0 END), 2) AS h_desmoldeo_limpieza,
                   COUNT(*) AS n
            FROM cscmega_08ruta r
            JOIN corex_test.modelos m ON m.NoParte = r.u_NoParte
            WHERE r.FHrLIMP BETWEEN :d AND :h AND m.area IN ('AF1','AF2','AF3')
            GROUP BY m.area
        """, {"d": desde_dt, "h": hasta_dt})
        return {r["proceso"]: r for r in rows}

    reciente = _ciclo_por_proceso(reciente_desde, ref_dt)
    baseline = _ciclo_por_proceso(baseline_desde, reciente_desde)

    etapas = [("molde_a_vaciado", "h_molde_vaciado"),
              ("vaciado_a_desmoldeo", "h_vaciado_desmoldeo"),
              ("desmoldeo_a_limpieza", "h_desmoldeo_limpieza")]
    cuellos = []
    for proceso in ("AF1", "AF2", "AF3"):
        r, b = reciente.get(proceso), baseline.get(proceso)
        if not r or not b or not r.get("n"):
            continue
        for etapa_label, col in etapas:
            hr, hb = r.get(col), b.get(col)
            if hr is None or hb is None or hb <= 0:
                continue
            ratio = round(float(hr) / float(hb), 2)
            if ratio >= _CUELLO_UMBRAL_RATIO:
                cuellos.append({
                    "proceso": proceso, "etapa": etapa_label,
                    "horas_reciente": float(hr), "horas_baseline": float(hb),
                    "ratio": ratio, "n_piezas_reciente": int(r["n"]),
                })
    cuellos.sort(key=lambda x: -x["ratio"])

    return {
        "referencia": referencia,
        "dias": dias,
        "ventana_baseline_dias": dias * 4,
        "umbral_ratio": _CUELLO_UMBRAL_RATIO,
        "cuellos_de_botella": cuellos,
    }


# ── Plan de Producción Mensual ────────────────────────────────────────────
# detalle_referencial viene de la hoja `resumen` del Excel, que el responsable
# confirmó que NO es fuente de verdad (zona de trabajo, sus totales internos
# no reconcilian entre sí). Se expone con "autoritativo": false explícito en
# cada fila para que ningún consumidor (dashboard, Agente Alfa/Beta) la use
# por accidente para tomar o justificar una decisión.
@app.get("/api/programa")
def programa_mensual(mes: str = Query(..., description="YYYY-MM, ej. 2026-08")):
    try:
        anio_mes = datetime.strptime(mes, "%Y-%m").date().replace(day=1)
    except ValueError:
        raise HTTPException(status_code=400, detail="mes debe tener formato YYYY-MM")

    encabezado_rows = run("""
        SELECT id, anio_mes, rev_no, fecha_rev, dias_habiles, ventas_ton, presup_ton,
               buenas_ton, vaciadas_ton, rech_int_pct, linea_ton, desarrollo_ton,
               linea_pct, desarrollo_pct, inv_total, archivo_origen, fecha_carga
        FROM programa_produccion_mensual
        WHERE anio_mes = :anio_mes
        ORDER BY rev_no DESC LIMIT 1
    """, {"anio_mes": anio_mes})

    if not encabezado_rows:
        raise HTTPException(status_code=404, detail=f"No hay plan cargado para {mes}")

    encabezado = encabezado_rows[0]
    programa_id = encabezado["id"]

    ritmos_por_proceso = run("""
        SELECT tipo_dia, proceso, moldes_dia, peso_prom_kg, kg_diarios
        FROM programa_ritmos_vaciado
        WHERE programa_id = :pid
        ORDER BY FIELD(tipo_dia, 'LV', 'SAB'), proceso
    """, {"pid": programa_id})

    ritmos_agregados = run("""
        SELECT tipo_dia, kg_vaciado_dia_con_ri, kg_diarios_buenos, coladas_diarias
        FROM programa_ritmos_agregados
        WHERE programa_id = :pid
        ORDER BY FIELD(tipo_dia, 'LV', 'SAB')
    """, {"pid": programa_id})

    detalle_referencial = run("""
        SELECT parte, sdo_final, peso_kg, kg_buenos, proceso, status, es_autoritativo
        FROM programa_produccion_detalle_referencial
        WHERE programa_id = :pid
        ORDER BY parte
    """, {"pid": programa_id})

    return {
        "encabezado": encabezado,
        "ritmos_por_proceso": ritmos_por_proceso,
        "ritmos_agregados": ritmos_agregados,
        "detalle_referencial": [
            {**fila, "autoritativo": False}
            for fila in detalle_referencial
        ],
    }


# ══════════════════════════════════════════════════════════════════════════════
# AGENTE ALFA — chat conversacional (tool-use), 2026-08-05
# ══════════════════════════════════════════════════════════════════════════════
# Agente REAL (LLM con tool-use) construido sobre los endpoints ya existentes de
# Alfa/Beta/Plan de Producción -- las tools son wrappers delgados que llaman
# directo a las funciones Python (mismo proceso, sin HTTP), nunca inventan datos.
# Regla dura del system prompt: solo puede afirmar lo que vino de un tool result.

@beta_tool
def evaluar_parte(no_parte: str, dias: int = 14) -> str:
    """Ritmo real, ciclo por etapa, proceso/cliente y proyección de entrega para una parte.

    Args:
        no_parte: Número de parte exacto (ej. "2C-2473-000"). Si no lo conoces, usa
            listar_clientes_con_actividad + partes_de_cliente para encontrarlo primero.
        dias: Ventana de días hacia atrás para calcular el ritmo real.
    """
    return json.dumps(alfa_evaluar(no_parte=no_parte, dias=dias), default=str)


@beta_tool
def cuello_de_botella(dias: int = 14) -> str:
    """Detecta si algún proceso (AF1/AF2/AF3) está más lento de lo normal en alguna etapa
    (Molde→Vaciado, Vaciado→Desmoldeo, Desmoldeo→Limpieza), comparando los últimos `dias`
    contra un baseline de las 4 ventanas anteriores. Lista vacía = sin cuello de botella.

    Args:
        dias: tamaño de la ventana reciente en días.
    """
    return json.dumps(alfa_cuello_botella(dias=dias), default=str)


@beta_tool
def listar_clientes_con_actividad() -> str:
    """Lista de clientes con actividad de producción real en los últimos 90 días,
    ordenados por piezas. Úsalo para encontrar el nombre exacto de un cliente antes
    de llamar a partes_de_cliente."""
    return json.dumps(alfa_clientes(), default=str)


@beta_tool
def partes_de_cliente(cliente: str, dias: int = 14) -> str:
    """Partes con actividad reciente de un cliente específico, con ritmo (piezas/día)
    y proceso. Úsalo para encontrar el No_Parte exacto de las partes de un cliente.

    Args:
        cliente: Nombre exacto del cliente (usar listar_clientes_con_actividad para verlo).
        dias: ventana en días.
    """
    return json.dumps(alfa_partes_cliente(cliente=cliente, dias=dias), default=str)


@beta_tool
def riesgo_calidad_parte(no_parte: str) -> str:
    """Criticidad histórica de rechazo y disponibilidad de compuerta química para una
    parte (Agente Beta) -- útil si preguntan por qué una parte tiene problemas de calidad.

    Args:
        no_parte: Número de parte exacto.
    """
    return json.dumps(beta_evaluar(no_parte=no_parte), default=str)


@beta_tool
def partes_riesgo_alto(limit: int = 20) -> str:
    """Lista de partes con criticidad histórica de rechazo alta (Agente Beta).

    Args:
        limit: máximo de partes a devolver.
    """
    return json.dumps(beta_riesgo_alto(limit=limit), default=str)


@beta_tool
def plan_produccion_mes(mes: str) -> str:
    """Plan de producción mensual: ritmos objetivo por proceso y saldo pendiente por
    parte -- el saldo NO es una fuente autoritativa, acláralo si lo usas en tu respuesta.
    Devuelve un mensaje de error si no hay plan cargado para ese mes.

    Args:
        mes: formato YYYY-MM, ej. "2026-08".
    """
    try:
        return json.dumps(programa_mensual(mes=mes), default=str)
    except HTTPException as e:
        return json.dumps({"error": e.detail})


# Tools compartidas por Alfa y Beta -- ambos agentes conversacionales pueden cruzar
# flujo/ritmo y calidad/criticidad cuando la pregunta lo amerita, sin duplicar definiciones.
_TORRE_CHAT_TOOLS = [
    evaluar_parte, cuello_de_botella, listar_clientes_con_actividad,
    partes_de_cliente, riesgo_calidad_parte, partes_riesgo_alto, plan_produccion_mes,
]

_REGLAS_GROUNDING = """
Reglas estrictas:
- Nunca inventes un número, fecha o nombre que no venga literalmente de un resultado de
  herramienta. Si no tienes el dato, dilo explícitamente -- no rellenes con una suposición.
- El saldo pendiente y la proyección de entrega vienen del Plan de Producción Mensual, que
  NO es una fuente autoritativa -- si los usas en tu respuesta, dilo claramente.
- Si preguntan por una parte y no sabes el No_Parte exacto, usa listar_clientes_con_actividad
  y partes_de_cliente para encontrarla en vez de preguntarle al usuario el número exacto.
- Responde en español, breve y directo, como si hablaras con un supervisor de piso -- sin
  relleno, sin repetir los datos crudos, solo la conclusión y el número que la respalda."""

_ALFA_CHAT_SYSTEM = """Eres el asistente del Agente Alfa de la Torre de Control FASA (fundición).
Respondes preguntas sobre ritmo de producción, cuellos de botella y proyección de entrega,
usando SOLO datos reales obtenidos con las herramientas disponibles. También tienes acceso a
las herramientas de calidad (Agente Beta) -- úsalas si la pregunta cruza a riesgo de rechazo.
""" + _REGLAS_GROUNDING

_BETA_CHAT_SYSTEM = """Eres el asistente del Agente Beta de la Torre de Control FASA (fundición).
Respondes preguntas sobre criticidad histórica de rechazo y disponibilidad de la compuerta
química de una parte, usando SOLO datos reales obtenidos con las herramientas disponibles.
También tienes acceso a las herramientas de flujo (Agente Alfa) -- úsalas si la pregunta cruza
a ritmo de producción, cuello de botella o entrega.
""" + _REGLAS_GROUNDING


class ChatRequest(BaseModel):
    pregunta: str
    historial: list = []  # [{"rol": "user"|"assistant", "texto": "..."}], opcional


def _correr_chat(system: str, req: ChatRequest) -> dict:
    client = anthropic.Anthropic()

    messages = [{"role": t["rol"], "content": t["texto"]} for t in req.historial]
    messages.append({"role": "user", "content": req.pregunta})

    runner = client.beta.messages.tool_runner(
        model="claude-opus-5",
        max_tokens=2048,
        system=system,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        tools=_TORRE_CHAT_TOOLS,
        messages=messages,
    )

    last_message = None
    herramientas_usadas = []
    for message in runner:
        last_message = message
        for block in message.content:
            if block.type == "tool_use":
                herramientas_usadas.append({"tool": block.name, "input": block.input})

    respuesta = ""
    if last_message:
        respuesta = next((b.text for b in last_message.content if b.type == "text"), "")

    return {"respuesta": respuesta, "herramientas_usadas": herramientas_usadas}


@app.post("/api/alfa/chat")
def alfa_chat(req: ChatRequest):
    return _correr_chat(_ALFA_CHAT_SYSTEM, req)


@app.post("/api/beta/chat")
def beta_chat(req: ChatRequest):
    return _correr_chat(_BETA_CHAT_SYSTEM, req)
