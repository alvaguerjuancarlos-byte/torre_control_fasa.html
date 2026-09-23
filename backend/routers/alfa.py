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
"""

import math
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from backend.core import run, _referencia_actual_flujo
from backend import plan_lector

router = APIRouter(tags=["alfa"])


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
def alfa_clientes():
    """Clientes con actividad de flujo real (betamega_08_ticketrutacritica) en los
    últimos 90 días, ordenados por piezas -- para el selector de descubrimiento del
    tab Alfa (JC señaló que escribir el No_Parte a ciegas no era usable)."""
    referencia = _referencia_actual_flujo()
    ref_dt = datetime.fromisoformat(referencia)
    desde_dt = ref_dt - timedelta(days=90)
    rows = run("""
        SELECT c.Cliente AS cliente, COUNT(*) AS piezas
        FROM betamega_08_ticketrutacritica r
        JOIN corex_test.modelos m ON m.NoParte = r.No_Parte
        JOIN corex_test.clientes c ON c.IdCliente = m.IdCliente
        WHERE r.FHrLIMP BETWEEN :d AND :h
        GROUP BY c.Cliente
        ORDER BY piezas DESC
    """, {"d": desde_dt, "h": ref_dt})
    return {"clientes": [r["cliente"] for r in rows]}


@router.get("/api/alfa/partes-cliente")
def alfa_partes_cliente(cliente: str = Query(...), dias: int = Query(default=14, le=90)):
    """Partes de un cliente con ritmo real reciente -- puebla la lista clickeable
    que reemplaza la búsqueda a ciegas por No_Parte."""
    referencia = _referencia_actual_flujo()
    ref_dt = datetime.fromisoformat(referencia)
    desde_dt = ref_dt - timedelta(days=dias)
    rows = run("""
        SELECT r.No_Parte AS no_parte, NULLIF(m.area, '') AS proceso, COUNT(*) AS piezas
        FROM betamega_08_ticketrutacritica r
        JOIN corex_test.modelos m ON m.NoParte = r.No_Parte
        JOIN corex_test.clientes c ON c.IdCliente = m.IdCliente
        WHERE c.Cliente = :cli AND r.FHrLIMP BETWEEN :d AND :h
        GROUP BY r.No_Parte, m.area
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
        FROM betamega_08_ticketrutacritica
        WHERE No_Parte = :np AND FHrLIMP BETWEEN :d AND :h
        GROUP BY DATE(FHrLIMP) ORDER BY dia
    """, {"np": no_parte, "d": desde_dt, "h": ref_dt})
    total = sum(int(r["piezas"]) for r in serie)

    ciclo = run("""
        SELECT
          ROUND(AVG(CASE WHEN TIMESTAMPDIFF(HOUR, FHrMOLD, FHrVACI) BETWEEN 0 AND 72
                     THEN TIMESTAMPDIFF(MINUTE, FHrMOLD, FHrVACI)/60.0 END), 1) AS h_molde_vaciado,
          ROUND(AVG(CASE WHEN TIMESTAMPDIFF(HOUR, FHrVACI, FHrDESM) BETWEEN 0 AND 72
                     THEN TIMESTAMPDIFF(MINUTE, FHrVACI, FHrDESM)/60.0 END), 1) AS h_vaciado_desmoldeo,
          ROUND(AVG(CASE WHEN TIMESTAMPDIFF(HOUR, FHrDESM, FHrLIMP) BETWEEN 0 AND 72
                     THEN TIMESTAMPDIFF(MINUTE, FHrDESM, FHrLIMP)/60.0 END), 1) AS h_desmoldeo_limpieza
        FROM betamega_08_ticketrutacritica
        WHERE No_Parte = :np AND FHrLIMP BETWEEN :d AND :h
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


@router.get("/api/alfa/evaluar")
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

@router.get("/api/alfa/cuello-botella")
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
                   ROUND(AVG(CASE WHEN TIMESTAMPDIFF(HOUR, r.FHrMOLD, r.FHrVACI) BETWEEN 0 AND 72
                              THEN TIMESTAMPDIFF(MINUTE, r.FHrMOLD, r.FHrVACI)/60.0 END), 2) AS h_molde_vaciado,
                   ROUND(AVG(CASE WHEN TIMESTAMPDIFF(HOUR, r.FHrVACI, r.FHrDESM) BETWEEN 0 AND 72
                              THEN TIMESTAMPDIFF(MINUTE, r.FHrVACI, r.FHrDESM)/60.0 END), 2) AS h_vaciado_desmoldeo,
                   ROUND(AVG(CASE WHEN TIMESTAMPDIFF(HOUR, r.FHrDESM, r.FHrLIMP) BETWEEN 0 AND 72
                              THEN TIMESTAMPDIFF(MINUTE, r.FHrDESM, r.FHrLIMP)/60.0 END), 2) AS h_desmoldeo_limpieza,
                   COUNT(*) AS n
            FROM betamega_08_ticketrutacritica r
            JOIN corex_test.modelos m ON m.NoParte = r.No_Parte
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

def _obtener_plan(mes: str) -> Optional[dict]:
    """BD primero (programa_produccion_*, hoy vacías -- sapiens es solo-lectura,
    el DDL nunca se corrió); si no hay nada, cae a leer el Excel en vivo vía
    plan_lector. Devuelve None si no hay plan en ningún lado para ese mes."""
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

    return plan_lector.leer_plan(mes)


@router.get("/api/programa")
def programa_mensual(mes: str = Query(..., description="YYYY-MM, ej. 2026-08")):
    plan = _obtener_plan(mes)
    if not plan:
        raise HTTPException(status_code=404, detail=f"No hay plan cargado ni archivo Excel para {mes}")
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
    """Piezas terminadas/día reciente por parte, en batch -- mismo criterio que
    _ritmo_parte() de Alfa (FHrLIMP, ver arriba), sin hacer N queries
    individuales para las ~138 partes del plan."""
    if not no_partes:
        return {}
    ref_dt = datetime.fromisoformat(referencia)
    desde_dt = ref_dt - timedelta(days=dias)
    placeholders = ",".join(f":p{i}" for i in range(len(no_partes)))
    params = {f"p{i}": p for i, p in enumerate(no_partes)}
    params.update({"d": desde_dt, "h": ref_dt})
    rows = run(f"""
        SELECT No_Parte AS no_parte, COUNT(*) AS piezas
        FROM betamega_08_ticketrutacritica
        WHERE No_Parte IN ({placeholders}) AND FHrLIMP BETWEEN :d AND :h
        GROUP BY No_Parte
    """, params)
    return {r["no_parte"]: round(int(r["piezas"]) / dias, 2) for r in rows}


@router.get("/api/programa/seguimiento")
def programa_seguimiento(mes: str = Query(..., description="YYYY-MM, ej. 2026-08"),
                          dias: int = Query(default=14, le=90)):
    """Plan + pronóstico de rechazo aplicado (producción neta esperada) + ritmo
    real reciente por parte (seguimiento/cumplimiento) -- une las 3 piezas que
    JC pidió: tener un plan, dar seguimiento, evaluar cumplimiento."""
    plan = _obtener_plan(mes)
    if not plan:
        raise HTTPException(status_code=404, detail=f"No hay plan cargado ni archivo Excel para {mes}")

    partes = [f["parte"] for f in plan["detalle_referencial"]]
    referencia = _referencia_actual_flujo()
    tasas = _tasa_rechazo_2025_batch(partes)
    ritmos_reales = _ritmo_real_partes_batch(partes, dias, referencia)

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
        },
        "ritmo_real_por_proceso": ritmo_real_por_proceso,
        "detalle": detalle,
    }
