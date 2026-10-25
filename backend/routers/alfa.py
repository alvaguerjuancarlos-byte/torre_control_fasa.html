"""
AGENTE ALFA — ritmo real, cuello de botella y proyección de entrega, 100% lectura
Rediseño 2026-08-05 (JC): la v1 (PC-gate por colada) se retiró de aquí — esa
señal ya vive en el tab "Alertas y Protocolos", era redundante con Agente Beta.
Alfa ahora responde "¿vamos a cumplir la entrega de esta parte/cliente?" cruzando:
  - ritmo real por etapa (betamega_08_ticketrutacritica: FHrMOLD/FHrVACI/FHrDESM/FHrLIMP —
    migrado 2026-09-23 desde cscmega_08ruta, congelada desde dic-2025; mismos
    umbrales de confiabilidad ~93-99% dentro de 0-72h entre etapas consecutivas
    ya validados para la tabla vieja, no re-verificados en la nueva)
  - proceso/cliente (corex_test.modelos.area + corex_test.clientes — AUTORITATIVO,
    no depende del Excel del Plan de Producción)
  - saldo pendiente (programa_produccion_detalle_referencial.sdo_final — NO
    AUTORITATIVO, ver /api/programa; la tabla puede no existir todavía si el
    DDL del Plan de Producción no se ha corrido — se degrada con gracia)

Incluye también Plan de Producción Mensual — comparte _referencia_actual_flujo
con Alfa y estaban co-ubicados en el main.py original.

MIGRACIÓN 2026-10-25 — conteos a piezas VACIADAS: betamega_08_ticketrutacritica casi dejó de
registrar (solo ~9 % de lo vaciado desde el 12-oct aparece ahí; FHrDESM en cero desde la
semana 40). Ritmo, clientes con actividad y partes de un cliente cuentan ahora piezas de
betamega_00_ticketsconvaciadodirecto (completa). Ciclo molde→vaciado = FHrVaciado (vdir) −
FHrMoldeo (betamega_aleks_idtickethistorial), unidos por ticket (mediana ~3.8 h en oct-2026).
Vaciado→desmoldeo y desmoldeo→limpieza solo existen en betamega_08: se siguen calculando ahí
pero con su muestra (n) a la vista y None si no alcanza UMBRAL_PIEZAS_RITMO_CONFIABLE --
nunca un promedio de 3 piezas presentado como ciclo de planta.
"""

import math
import time
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from backend.core import run, _referencia_actual_flujo, historial_rechazo_partes, tasa_shrunk
from backend import plan_lector, plan_corex

router = APIRouter(tags=["alfa"])

# 2026-10-03: selector de horizonte de análisis (Alfa/Beta) -- ver backend/routers/chat.py
# HORIZONTES. Un ritmo calculado sobre muy pocas piezas no es representativo y antes se
# devolvía igual, sin aviso (ej. ritmo_diario_prom=0.4 por 1 pieza en 14 días se leía como
# "va lento" cuando en realidad es "casi sin muestra").
UMBRAL_PIEZAS_RITMO_CONFIABLE = 5

# Ciclo molde→vaciado por ticket (vdir ⋈ aleks): el join tarda 10-45 s por mes (sin índices
# útiles, BD de producción compartida). Caché por DÍA de vaciado: un día cerrado no cambia y
# se guarda (tope _MV_MAX_DIAS); el día en curso se relee a los 30 min. Así el cuello de
# botella (ventana + 4 ventanas de baseline) y el ritmo por parte reutilizan lo ya leído en
# vez de volver a barrer la tabla en cada consulta.
_MV_DIA: dict = {}
_MV_TTL_HOY_SEG = 1800
_MV_MAX_DIAS = 200


def _molde_vaciado(desde_dt: datetime, hasta_dt: datetime) -> list:
    """[(no_parte, horas molde→vaciado)] de piezas vaciadas en la ventana, transiciones 0-72 h."""
    hoy = datetime.now().date()
    dias = [desde_dt.date() + timedelta(days=i) for i in range((hasta_dt.date() - desde_dt.date()).days + 1)]
    faltan = [d for d in dias if d not in _MV_DIA
              or (d >= hoy and time.time() - _MV_DIA[d][0] > _MV_TTL_HOY_SEG)]
    if faltan:
        rows = run("""
            SELECT TRIM(v.no_parte) AS no_parte, v.FHrVaciado AS ts,
                   TIMESTAMPDIFF(MINUTE, a.FHrMoldeo, v.FHrVaciado) / 60.0 AS h
            FROM betamega_00_ticketsconvaciadodirecto v
            JOIN betamega_aleks_idtickethistorial a ON a.IdTicket = v.idTicket
            WHERE v.FHrVaciado BETWEEN :d AND :h
              AND TIMESTAMPDIFF(HOUR, a.FHrMoldeo, v.FHrVaciado) BETWEEN 0 AND 72
        """, {"d": datetime.combine(min(faltan), datetime.min.time()),
              "h": datetime.combine(max(faltan), datetime.max.time())})
        por_dia: dict = {d: [] for d in faltan}
        for r in rows:
            if r["h"] is not None and r["ts"].date() in por_dia:
                por_dia[r["ts"].date()].append((r["no_parte"], r["ts"], float(r["h"])))
        for d, lst in por_dia.items():
            _MV_DIA[d] = (time.time(), lst)
        for d in sorted(_MV_DIA)[:-_MV_MAX_DIAS]:
            _MV_DIA.pop(d, None)
    return [(p, h) for d in dias for p, ts, h in _MV_DIA.get(d, (0, []))[1] if desde_dt <= ts <= hasta_dt]


def precalentar_alfa():
    """Llena la caché molde→vaciado para la ventana más usada (cuello de botella, 7 + 28 días)."""
    ref = datetime.fromisoformat(_referencia_actual_flujo())
    _molde_vaciado(ref - timedelta(days=35), ref)


def _prom(vals: list) -> Optional[float]:
    return round(sum(vals) / len(vals), 2) if len(vals) >= UMBRAL_PIEZAS_RITMO_CONFIABLE else None


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


@router.get("/api/alfa/clientes")
def alfa_clientes(dias: int = Query(default=90, le=90)):
    """Clientes con piezas vaciadas en los últimos `dias` días (default 90), ordenados
    por piezas -- para el selector de descubrimiento del tab Alfa (JC señaló que escribir
    el No_Parte a ciegas no era usable). Tope de 90 días, mismo criterio que
    partes_de_cliente/evaluar."""
    referencia = _referencia_actual_flujo()
    ref_dt = datetime.fromisoformat(referencia)
    desde_dt = ref_dt - timedelta(days=dias)
    rows = run("""
        SELECT c.Cliente AS cliente, SUM(v.n) AS piezas
        FROM (SELECT TRIM(no_parte) AS no_parte, COUNT(*) AS n
              FROM betamega_00_ticketsconvaciadodirecto
              WHERE FHrVaciado BETWEEN :d AND :h GROUP BY TRIM(no_parte)) v
        JOIN (SELECT NoParte, MAX(IdCliente) AS IdCliente FROM corex_test.modelos GROUP BY NoParte) m
          ON m.NoParte = v.no_parte
        JOIN corex_test.clientes c ON c.IdCliente = m.IdCliente
        GROUP BY c.Cliente
        ORDER BY piezas DESC
    """, {"d": desde_dt, "h": ref_dt})
    return {"clientes": [r["cliente"] for r in rows], "referencia": referencia, "dias": dias}


@router.get("/api/alfa/partes-cliente")
def alfa_partes_cliente(cliente: str = Query(...), dias: int = Query(default=14, le=90)):
    """Partes de un cliente con piezas vaciadas recientes -- puebla la lista clickeable
    que reemplaza la búsqueda a ciegas por No_Parte."""
    referencia = _referencia_actual_flujo()
    ref_dt = datetime.fromisoformat(referencia)
    desde_dt = ref_dt - timedelta(days=dias)
    rows = run("""
        SELECT v.no_parte, NULLIF(m.area, '') AS proceso, v.n AS piezas
        FROM (SELECT TRIM(no_parte) AS no_parte, COUNT(*) AS n
              FROM betamega_00_ticketsconvaciadodirecto
              WHERE FHrVaciado BETWEEN :d AND :h GROUP BY TRIM(no_parte)) v
        JOIN (SELECT NoParte, MAX(IdCliente) AS IdCliente, MAX(area) AS area
              FROM corex_test.modelos GROUP BY NoParte) m ON m.NoParte = v.no_parte
        JOIN corex_test.clientes c ON c.IdCliente = m.IdCliente
        WHERE c.Cliente = :cli
        ORDER BY piezas DESC
    """, {"cli": cliente, "d": desde_dt, "h": ref_dt})
    # modelos.area vacío en ~60 % de lo producido: se completa con programa3dias (plan_corex)
    procesos = plan_corex._proceso_partes([r["no_parte"] for r in rows if not r["proceso"]])
    return {
        "cliente": cliente,
        "dias": dias,
        "referencia": referencia,
        "partes": [
            {
                "no_parte": r["no_parte"],
                "proceso": r["proceso"] or procesos.get(r["no_parte"]),
                "piezas": int(r["piezas"]),
                "ritmo_diario_prom": round(int(r["piezas"]) / dias, 2),
            }
            for r in rows
        ],
    }


def _ritmo_parte(no_parte: str, dias: int, referencia: str) -> dict:
    """Ritmo real de una parte: piezas VACIADAS por día en la ventana (ver MIGRACIÓN
    2026-10-25 arriba), y tiempo de ciclo real por etapa (promedio en horas, transiciones
    de 0-72 h -- mismo criterio que PC-5 para descartar timestamps con huecos de meses).
    Cada etapa trae su muestra en ciclo_muestra; con menos de UMBRAL_PIEZAS_RITMO_CONFIABLE
    el promedio sale None."""
    ref_dt = datetime.fromisoformat(referencia)
    desde_dt = ref_dt - timedelta(days=dias)
    np_ = no_parte.strip()

    serie = run("""
        SELECT DATE(FHrVaciado) AS dia, COUNT(*) AS piezas
        FROM betamega_00_ticketsconvaciadodirecto
        WHERE TRIM(no_parte) = :np AND FHrVaciado BETWEEN :d AND :h
        GROUP BY DATE(FHrVaciado) ORDER BY dia
    """, {"np": np_, "d": desde_dt, "h": ref_dt})
    total = sum(int(r["piezas"]) for r in serie)

    mv = [h for p, h in _molde_vaciado(desde_dt, ref_dt) if p == np_]
    # Etapas posteriores al vaciado: solo betamega_08 las registra (muestra chica, ver arriba)
    b08 = run("""
        SELECT
          TIMESTAMPDIFF(MINUTE, FHrVACI, FHrDESM)/60.0 AS h_vd,
          TIMESTAMPDIFF(MINUTE, FHrDESM, FHrLIMP)/60.0 AS h_dl
        FROM betamega_08_ticketrutacritica
        WHERE No_Parte = :np AND (FHrDESM BETWEEN :d AND :h OR FHrLIMP BETWEEN :d AND :h)
    """, {"np": no_parte, "d": desde_dt, "h": ref_dt})
    vd = [float(r["h_vd"]) for r in b08 if r["h_vd"] is not None and 0 <= float(r["h_vd"]) <= 72]
    dl = [float(r["h_dl"]) for r in b08 if r["h_dl"] is not None and 0 <= float(r["h_dl"]) <= 72]

    return {
        "referencia": referencia,
        "dias": dias,
        "serie_vaciadas": [{"dia": str(r["dia"]), "piezas": int(r["piezas"])} for r in serie],
        "total_vaciadas": total,
        "muestra_suficiente": total >= UMBRAL_PIEZAS_RITMO_CONFIABLE,
        "ritmo_diario_prom": round(total / dias, 2) if dias else None,
        "ciclo_horas": {
            "molde_a_vaciado":      _prom(mv),
            "vaciado_a_desmoldeo":  _prom(vd),
            "desmoldeo_a_limpieza": _prom(dl),
        },
        "ciclo_muestra": {"molde_a_vaciado": len(mv), "vaciado_a_desmoldeo": len(vd), "desmoldeo_a_limpieza": len(dl)},
        "nota_ciclo": "Desmoldeo y limpieza solo se registran en betamega_08_ticketrutacritica, que hoy "
                      "captura ~9 % de las piezas; sin muestra suficiente salen sin dato.",
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
        rows = []  # tablas programa_produccion_* no existen
    if not rows:
        return plan_corex.saldo_parte_corex(no_parte)  # plan capturado en Corex (2026-10)
    r = rows[0]
    return {
        "sdo_final":   r["sdo_final"],
        "kg_buenos":   float(r["kg_buenos"]) if r.get("kg_buenos") is not None else None,
        "status":      r["status"],
        "anio_mes":    str(r["anio_mes"]),
        "autoritativo": False,
    }


@router.get("/api/alfa/evaluar")
def alfa_evaluar(no_parte: str = Query(...), dias: int = Query(default=14, le=90)):
    referencia = _referencia_actual_flujo()
    pc = _resolver_proceso_cliente([no_parte]).get(no_parte, {})
    if not pc.get("proceso"):  # modelos.area vacío: se completa con programa3dias (plan_corex)
        pc = {**pc, "proceso": plan_corex._proceso_partes([no_parte]).get(no_parte)}
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
_CUELLO_MIN_MUESTRA = 20    # piezas por (proceso, etapa) en cada ventana para comparar promedios

@router.get("/api/alfa/cuello-botella")
def alfa_cuello_botella(dias: int = Query(default=7, le=30)):
    """Compara el tiempo de ciclo real reciente (últimos `dias`) contra un
    baseline de las 4 ventanas anteriores, por proceso (AF1/AF2/AF3) y etapa.
    Sin concepto de 'turno' todavía -- no hay horarios de turno definidos en
    el código, queda pendiente si se quiere ese nivel de detalle.

    2026-10-25: molde→vaciado sale de vdir ⋈ aleks (completa); vaciado→desmoldeo y
    desmoldeo→limpieza de betamega_08 (~9 % de las piezas). Cada (proceso, etapa) exige
    _CUELLO_MIN_MUESTRA piezas en las dos ventanas; si no, va a etapas_sin_dato en vez de
    comparar dos promedios de 3 piezas. Proceso = plan_corex._proceso_partes (modelos.area
    completado con programa3dias: modelos.area solo cubre ~38 % de lo producido)."""
    referencia = _referencia_actual_flujo()
    ref_dt = datetime.fromisoformat(referencia)
    reciente_desde = ref_dt - timedelta(days=dias)
    baseline_desde = reciente_desde - timedelta(days=dias * 4)

    def _muestras(desde_dt, hasta_dt) -> list:
        """[(no_parte, etapa, horas)]"""
        b08 = run("""
            SELECT No_Parte AS no_parte,
                   CASE WHEN FHrDESM BETWEEN :d AND :h THEN TIMESTAMPDIFF(MINUTE, FHrVACI, FHrDESM)/60.0 END AS h_vd,
                   CASE WHEN FHrLIMP BETWEEN :d AND :h THEN TIMESTAMPDIFF(MINUTE, FHrDESM, FHrLIMP)/60.0 END AS h_dl
            FROM betamega_08_ticketrutacritica
            WHERE FHrDESM BETWEEN :d AND :h OR FHrLIMP BETWEEN :d AND :h
        """, {"d": desde_dt, "h": hasta_dt})
        muestras = [(p, "molde_a_vaciado", h) for p, h in _molde_vaciado(desde_dt, hasta_dt)]
        for r in b08:
            for etapa, col in (("vaciado_a_desmoldeo", "h_vd"), ("desmoldeo_a_limpieza", "h_dl")):
                if r[col] is not None and 0 <= float(r[col]) <= 72:
                    muestras.append(((r["no_parte"] or "").strip(), etapa, float(r[col])))
        return muestras

    m_rec, m_base = _muestras(reciente_desde, ref_dt), _muestras(baseline_desde, reciente_desde)
    procesos = plan_corex._proceso_partes(sorted({p for p, _, _ in m_rec + m_base}))

    def _por_proceso(muestras) -> dict:
        """{(proceso, etapa): [horas]}"""
        out: dict = {}
        for p, etapa, h in muestras:
            proc = procesos.get(p)
            if proc in ("AF1", "AF2", "AF3"):
                out.setdefault((proc, etapa), []).append(h)
        return out

    reciente, baseline = _por_proceso(m_rec), _por_proceso(m_base)

    cuellos, etapas_sin_dato = [], []
    for proceso in ("AF1", "AF2", "AF3"):
        for etapa in ("molde_a_vaciado", "vaciado_a_desmoldeo", "desmoldeo_a_limpieza"):
            r, b = reciente.get((proceso, etapa), []), baseline.get((proceso, etapa), [])
            if len(r) < _CUELLO_MIN_MUESTRA or len(b) < _CUELLO_MIN_MUESTRA:
                etapas_sin_dato.append({"proceso": proceso, "etapa": etapa,
                                        "n_reciente": len(r), "n_baseline": len(b)})
                continue
            hr, hb = sum(r) / len(r), sum(b) / len(b)
            if hb <= 0:
                continue
            ratio = round(hr / hb, 2)
            if ratio >= _CUELLO_UMBRAL_RATIO:
                cuellos.append({
                    "proceso": proceso, "etapa": etapa,
                    "horas_reciente": round(hr, 2), "horas_baseline": round(hb, 2),
                    "ratio": ratio, "n_piezas_reciente": len(r),
                })
    cuellos.sort(key=lambda x: -x["ratio"])
    sin_dato_proc = [p for p in ("AF1", "AF2", "AF3")
                     if sum(1 for e in etapas_sin_dato if e["proceso"] == p) == 3]

    return {
        "referencia": referencia,
        "dias": dias,
        "ventana_baseline_dias": dias * 4,
        "umbral_ratio": _CUELLO_UMBRAL_RATIO,
        "min_muestra": _CUELLO_MIN_MUESTRA,
        "cuellos_de_botella": cuellos,
        "procesos_sin_dato": sin_dato_proc,
        "etapas_sin_dato": etapas_sin_dato,
        "nota": "Molde→vaciado con todas las piezas vaciadas; desmoldeo y limpieza solo con lo que "
                "registra betamega_08 (~9 % de las piezas desde oct-2026).",
    }


# ── Plan de Producción Mensual ────────────────────────────────────────────
# detalle_referencial viene de la hoja `resumen` del Excel, que el responsable
# confirmó que NO es fuente de verdad (zona de trabajo, sus totales internos
# no reconcilian entre sí). Se expone con "autoritativo": false explícito en
# cada fila para que ningún consumidor (dashboard, Agente Alfa/Beta) la use
# por accidente para tomar o justificar una decisión.

def _obtener_plan(mes: str) -> Optional[dict]:
    """BD primero (programa_produccion_*, hoy vacías -- sapiens es solo-lectura,
    el DDL nunca se corrió); luego el plan capturado en Corex
    (gamamega_01_programaperiodo, fuente principal desde oct-2026, ver
    plan_corex.py); el Excel en vivo (plan_lector) queda solo como respaldo para
    meses que no estén en Corex. None si no hay plan en ningún lado."""
    try:
        anio_mes = datetime.strptime(mes, "%Y-%m").date().replace(day=1)
        encabezado_rows = run("""
            SELECT id, anio_mes, rev_no, fecha_rev, dias_habiles, ventas_ton, presup_ton,
                   buenas_ton, vaciadas_ton, rech_int_pct, linea_ton, desarrollo_ton,
                   linea_pct, desarrollo_pct, inv_total, archivo_origen, fecha_carga
            FROM programa_produccion_mensual
            WHERE anio_mes = :anio_mes
            ORDER BY rev_no DESC LIMIT 1
        """, {"anio_mes": anio_mes})
    except Exception:
        encabezado_rows = []  # tablas no existen todavía

    if encabezado_rows:
        encabezado = encabezado_rows[0]
        programa_id = encabezado["id"]
        ritmos_por_proceso = run("""
            SELECT tipo_dia, proceso, moldes_dia, peso_prom_kg, kg_diarios
            FROM programa_ritmos_vaciado WHERE programa_id = :pid
            ORDER BY FIELD(tipo_dia, 'LV', 'SAB'), proceso
        """, {"pid": programa_id})
        ritmos_agregados = run("""
            SELECT tipo_dia, kg_vaciado_dia_con_ri, kg_diarios_buenos, coladas_diarias
            FROM programa_ritmos_agregados WHERE programa_id = :pid
            ORDER BY FIELD(tipo_dia, 'LV', 'SAB')
        """, {"pid": programa_id})
        detalle_referencial = run("""
            SELECT parte, sdo_final, peso_kg, kg_buenos, proceso, status, es_autoritativo
            FROM programa_produccion_detalle_referencial WHERE programa_id = :pid
            ORDER BY parte
        """, {"pid": programa_id})
        return {
            "fuente": "bd",
            "encabezado": encabezado,
            "ritmos_por_proceso": ritmos_por_proceso,
            "ritmos_agregados": ritmos_agregados,
            "detalle_referencial": [{**f, "autoritativo": False} for f in detalle_referencial],
        }

    return plan_corex.leer_plan_corex(mes) or plan_lector.leer_plan(mes)


@router.get("/api/programa")
def programa_mensual(mes: str = Query(..., description="YYYY-MM, ej. 2026-08")):
    plan = _obtener_plan(mes)
    if not plan:
        raise HTTPException(status_code=404, detail=f"No hay plan para {mes} en Corex ni archivo Excel")
    return plan


def _tasa_rechazo_2025_batch(no_partes: list) -> dict:
    """Tasa de rechazo esperada por parte -- suma de PrcRchz2025 por cada fila de
    defecto en gamamega_01_riesgodefectoparte. Mismo campo/concepto que ya usa
    /v2/gestion/pronostico-programa (pronóstico histórico), reusado aquí para
    proyectar la producción neta esperada del plan en vez de producción real."""
    if not no_partes:
        return {}
    placeholders = ",".join(f":p{i}" for i in range(len(no_partes)))
    params = {f"p{i}": p for i, p in enumerate(no_partes)}
    rows = run(f"""
        SELECT No_Parte AS no_parte, SUM(COALESCE(PrcRchz2025, 0)) AS tasa
        FROM gamamega_01_riesgodefectoparte
        WHERE No_Parte IN ({placeholders})
        GROUP BY No_Parte
    """, params)
    return {r["no_parte"]: float(r["tasa"] or 0) for r in rows}


def _ritmo_real_partes_batch(no_partes: list, dias: int, referencia: str) -> dict:
    """Piezas VACIADAS/día reciente por parte, en batch (seguimiento mensual y Riesgo del Plan).

    Cambio 2026-10-25: antes contaba piezas terminadas (betamega_08.FHrLIMP, mismo criterio
    que _ritmo_parte() de Alfa), pero esa tabla hoy solo registra una fracción de lo que se
    produce -- en 14 días: 256 piezas de 14 partes contra 1,893 vaciadas de 123 partes en
    betamega_00_ticketsconvaciadodirecto (completa jul-oct). Con FHrLIMP casi todo el plan
    salía "sin ritmo" y los días estimados se inflaban ~7x. Vaciado es también el criterio del
    plan semanal (routers/pronostico_semanal.py) y de _ritmo_parte() (ver MIGRACIÓN arriba)."""
    if not no_partes:
        return {}
    ref_dt = datetime.fromisoformat(referencia)
    desde_dt = ref_dt - timedelta(days=dias)
    placeholders = ",".join(f":p{i}" for i in range(len(no_partes)))
    params = {f"p{i}": p.strip() for i, p in enumerate(no_partes)}
    params.update({"d": desde_dt, "h": ref_dt})
    rows = run(f"""
        SELECT TRIM(no_parte) AS no_parte, COUNT(*) AS piezas
        FROM betamega_00_ticketsconvaciadodirecto
        WHERE TRIM(no_parte) IN ({placeholders}) AND FHrVaciado BETWEEN :d AND :h
        GROUP BY TRIM(no_parte)
    """, params)
    por_parte = {r["no_parte"]: round(int(r["piezas"]) / dias, 2) for r in rows}
    # Devuelve con la llave original del plan (puede traer espacios al final)
    return {p: por_parte[p.strip()] for p in no_partes if p.strip() in por_parte}


@router.get("/api/programa/seguimiento")
def programa_seguimiento(mes: str = Query(..., description="YYYY-MM, ej. 2026-08"),
                          dias: int = Query(default=14, le=90)):
    """Plan + pronóstico de rechazo aplicado (producción neta esperada) + ritmo
    real reciente por parte (seguimiento/cumplimiento) -- une las 3 piezas que
    JC pidió: tener un plan, dar seguimiento, evaluar cumplimiento."""
    plan = _obtener_plan(mes)
    if not plan:
        raise HTTPException(status_code=404, detail=f"No hay plan para {mes} en Corex ni archivo Excel")

    partes = [f["parte"] for f in plan["detalle_referencial"]]
    referencia = _referencia_actual_flujo()
    tasas = _tasa_rechazo_2025_batch(partes)
    ritmos_reales = _ritmo_real_partes_batch(partes, dias, referencia)

    # Partes sin fila en gamamega_01_riesgodefectoparte (partes nuevas o sin rechazo
    # tabulado en 2025): antes quedaban fuera del neto pero dentro de kg_buenos, así que
    # "producción neta esperada" parecía un rechazo mucho mayor al real (oct-2026: 15
    # partes / 74 t). Se completan con la tasa histórica shrunk de los últimos 12 meses
    # (mismo método que /api/riesgo-plan); sin historial, cae a la tasa global.
    faltan = [p for p in partes if p not in tasas]
    origen_tasa = {p: "gamamega_2025" for p in tasas}
    if faltan:
        ref_dt = datetime.fromisoformat(referencia)
        hist = historial_rechazo_partes((ref_dt - timedelta(days=365)).strftime("%Y-%m-%d 00:00:00"),
                                        ref_dt.strftime("%Y-%m-%d 23:59:59"))
        tot_n = sum(v["n"] for v in hist.values())
        global_rate = (sum(v["rechazos"] for v in hist.values()) / tot_n) if tot_n else 0.15
        for p in faltan:
            h = hist.get(p, {"n": 0, "rechazos": 0})
            rate_pct, _ = tasa_shrunk(h["n"], h["rechazos"], global_rate, k=75)
            tasas[p] = rate_pct / 100
            origen_tasa[p] = "historial_12m" if h["n"] else "global"

    detalle = []
    kg_buenos_total = 0.0
    kg_neto_total = 0.0
    for fila in plan["detalle_referencial"]:
        parte = fila["parte"]
        kg_buenos = fila.get("kg_buenos")
        tasa = tasas.get(parte)
        kg_neto = round(float(kg_buenos) * (1 - tasa), 1) if (kg_buenos is not None and tasa is not None) else None

        if kg_buenos is not None:
            kg_buenos_total += float(kg_buenos)
        if kg_neto is not None:
            kg_neto_total += kg_neto

        ritmo = ritmos_reales.get(parte)
        sdo_final = fila.get("sdo_final")
        dias_estimados = math.ceil(sdo_final / ritmo) if (ritmo and ritmo > 0 and sdo_final) else None

        detalle.append({
            "parte":                       parte,
            "proceso":                     fila.get("proceso"),
            "status":                      fila.get("status"),
            "sdo_final":                   sdo_final,
            "peso_kg":                     fila.get("peso_kg"),
            "kg_buenos_plan":              kg_buenos,
            "tasa_rechazo_esperada_pct":   round(tasa * 100, 2) if tasa is not None else None,
            "origen_tasa":                 origen_tasa.get(parte),
            "kg_neto_esperado":            kg_neto,
            "ritmo_real_pzas_dia":         ritmo,
            "dias_estimados_saldo":        dias_estimados,
        })

    ritmo_real_por_proceso: dict = {}
    for fila in detalle:
        if fila["proceso"] and fila["ritmo_real_pzas_dia"]:
            ritmo_real_por_proceso[fila["proceso"]] = round(
                ritmo_real_por_proceso.get(fila["proceso"], 0) + fila["ritmo_real_pzas_dia"], 2)

    return {
        "mes":                mes,
        "fuente":             plan["fuente"],
        "dias_ventana_ritmo": dias,
        "encabezado":         plan["encabezado"],
        "ritmos_objetivo":    plan["ritmos_por_proceso"],
        "ritmos_agregados":   plan["ritmos_agregados"],
        "resumen": {
            "kg_buenos_plan_total":   round(kg_buenos_total, 1),
            "kg_neto_esperado_total": round(kg_neto_total, 1),
            "partes_con_ritmo_real":  sum(1 for f in detalle if f["ritmo_real_pzas_dia"]),
            "partes_totales":         len(detalle),
            "partes_tasa_complementada": sum(1 for f in detalle if f["origen_tasa"] in ("historial_12m", "global")),
        },
        "ritmo_real_por_proceso": ritmo_real_por_proceso,
        "detalle": detalle,
    }
