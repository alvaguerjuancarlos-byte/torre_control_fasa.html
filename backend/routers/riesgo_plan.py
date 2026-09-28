"""
AGENTE DE RIESGO DEL PLAN — combina compromiso mensual (parte/cliente/volumen)
con la historia de rechazo de cada parte para disparar medidas proactivas antes
de que el rechazo pegue en piso. Pedido directo de JC (2026-09-27): "el punto de
partida es el plan de producción mensual... analizando la historia de ese número
de parte se pueden obtener el pronóstico de producción y % de rechazo... ¿eso qué
dispara? ¿qué medidas proactivas me puedes sugerir?" — este módulo combina las 5
medidas acordadas en un solo endpoint, en vez de 5 tabs/llamadas sueltas:

  1. Alerta de faltante   — compromiso (sdo_final, piezas) vs. neto esperado
  2. Prioridad por impacto — piezas_en_riesgo = sdo_final × tasa (no % crudo)
  3. Refuerzo preventivo   — protocolo sugerido por defecto dominante histórico
  4. Colchón de producción — piezas extra a correr para seguir netando el compromiso
  5. Secuenciación         — orden_sugerido explícito por impacto

No es un reemplazo de /api/programa/seguimiento (alfa.py) -- ese endpoint usa la
tasa fija PrcRchz2025 y no tiene cliente ni clasificación de riesgo; aquí se usa
la tasa shrunk bayesiana (core.historial_rechazo_partes + core.tasa_shrunk, misma
lógica que /v2/gestion/riesgo-partes) porque es más estable cuando el histórico de
una parte es corto, y se le suma la dimensión de cliente que el plan no trae.

Reusa deliberadamente varios helpers "privados" de otros routers en vez de
duplicar sus queries (_resolver_proceso_cliente y _ritmo_real_partes_batch de
alfa.py, _riesgo_lote_partes de beta.py) -- es la única excepción documentada a
"los routers no se importan entre sí": la alternativa era reescribir 3 queries
batch no triviales. core.py sigue sin importar de ningún router.

El plan mensual (plan_lector.leer_plan) es SIEMPRE no autoritativo (Excel, zona
de trabajo cuyos totales no reconcilian) -- se propaga autoritativo=False igual
que en el resto del dashboard.
"""

import math
from datetime import date, datetime, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from backend.core import _referencia_actual_flujo, historial_rechazo_partes, tasa_shrunk, _buscar_protocolos
from backend.routers.alfa import _obtener_plan, _resolver_proceso_cliente, _ritmo_real_partes_batch
from backend.routers.beta import _riesgo_lote_partes

router = APIRouter(tags=["riesgo-plan"])

UMBRAL_ROJO_PCT = 15.0   # mismo criterio que pedBadgeCalidad()/chipFlujoHtml() en el frontend
UMBRAL_AMBAR_PCT = 7.0
VENTANAS_VALIDAS = (6, 12, 18, 24)


def _nivel_riesgo(rate_pct: float) -> str:
    if rate_pct >= UMBRAL_ROJO_PCT:
        return "rojo"
    if rate_pct >= UMBRAL_AMBAR_PCT:
        return "ambar"
    return "verde"


def _mes_actual() -> str:
    return date.today().strftime("%Y-%m")


@router.get("/api/riesgo-plan")
def riesgo_plan(
    mes: str = Query(default=None, description="YYYY-MM, ej. 2026-09. Default: mes actual"),
    ventana: int = Query(default=12, description="Meses de historia para la tasa shrunk (6/12/18/24)"),
    dias_ritmo: int = Query(default=14, le=90, description="Ventana de días para el ritmo real reciente"),
):
    mes = mes or _mes_actual()
    if ventana not in VENTANAS_VALIDAS:
        ventana = 12

    plan = _obtener_plan(mes)
    if not plan:
        raise HTTPException(status_code=404, detail=f"No hay plan cargado ni archivo Excel para {mes}")

    filas_plan = plan["detalle_referencial"]
    partes = [f["parte"] for f in filas_plan]

    # ── Tasa shrunk por parte (misma ventana/lógica que /v2/gestion/riesgo-partes) ──
    hasta = datetime.utcnow().date()
    desde = date(hasta.year, hasta.month, 1)
    for _ in range(ventana):
        desde = date(desde.year - 1, 12, 1) if desde.month == 1 else date(desde.year, desde.month - 1, 1)
    d_ini = desde.isoformat() + " 00:00:00"
    d_fin = hasta.isoformat() + " 23:59:59"
    agg = historial_rechazo_partes(d_ini, d_fin)

    total_n   = sum(v["n"] for v in agg.values())
    total_rec = sum(v["rechazos"] for v in agg.values())
    global_rate = (total_rec / total_n) if total_n else 0.15

    # ── Cliente, ritmo real, defecto dominante -- en batch, no por parte ──
    clientes = _resolver_proceso_cliente(partes)
    referencia = _referencia_actual_flujo()
    ritmos = _ritmo_real_partes_batch(partes, dias_ritmo, referencia)
    riesgo_hist = _riesgo_lote_partes(partes)

    defectos_unicos = sorted({v["defecto_dominante"] for v in riesgo_hist.values() if v.get("defecto_dominante")})
    protocolos_por_defecto: dict = {}
    for p in _buscar_protocolos(defectos_unicos):
        protocolos_por_defecto.setdefault(p["defecto"], []).append(p)

    detalle = []
    for fila in filas_plan:
        parte = fila["parte"]
        sdo_final = fila.get("sdo_final") or 0
        kg_buenos = fila.get("kg_buenos")

        h = agg.get(parte, {"n": 0, "rechazos": 0})
        rate_pct, shrink_w = tasa_shrunk(h["n"], h["rechazos"], global_rate, k=75)
        rate = rate_pct / 100

        piezas_en_riesgo = round(sdo_final * rate, 1) if sdo_final else 0.0
        buffer_sugerido = math.ceil(sdo_final * rate / (1 - rate)) if (sdo_final and rate < 1) else None
        kg_neto_esperado = round(float(kg_buenos) * (1 - rate), 1) if kg_buenos is not None else None
        nivel = _nivel_riesgo(rate_pct)

        cli = clientes.get(parte, {})
        ritmo = ritmos.get(parte)
        dias_estimados = math.ceil(sdo_final / ritmo) if (ritmo and ritmo > 0 and sdo_final) else None

        rh = riesgo_hist.get(parte, {})
        defecto_dominante = rh.get("defecto_dominante")

        detalle.append({
            "parte":                 parte,
            "cliente":               cli.get("cliente"),
            "proceso":               fila.get("proceso") or cli.get("proceso"),
            "status":                fila.get("status"),
            "sdo_final":             sdo_final,
            "kg_buenos_plan":        kg_buenos,
            "n_periodo":             h["n"],
            "rate_shrunk_pct":       rate_pct,
            "shrink_w":              shrink_w,
            "nivel_riesgo":          nivel,
            "piezas_en_riesgo":      piezas_en_riesgo,
            "alerta_faltante":       nivel in ("rojo", "ambar") and piezas_en_riesgo >= 1,
            "buffer_sugerido_piezas": buffer_sugerido,
            "kg_neto_esperado":      kg_neto_esperado,
            "ritmo_real_pzas_dia":   ritmo,
            "dias_estimados_saldo":  dias_estimados,
            "defecto_dominante":     defecto_dominante,
            "riesgo_historico_label": rh.get("riesgo_label"),
            "protocolos_sugeridos":  protocolos_por_defecto.get(defecto_dominante, []) if nivel in ("rojo", "ambar") else [],
            "autoritativo":          False,
        })

    detalle.sort(key=lambda x: x["piezas_en_riesgo"], reverse=True)
    for i, fila in enumerate(detalle, start=1):
        fila["orden_sugerido"] = i

    por_cliente: dict = {}
    for fila in detalle:
        cli = fila["cliente"] or "Sin cliente"
        acc = por_cliente.setdefault(cli, {"cliente": cli, "piezas_comprometidas": 0, "piezas_en_riesgo": 0.0, "partes_rojo": 0})
        acc["piezas_comprometidas"] += fila["sdo_final"]
        acc["piezas_en_riesgo"]     += fila["piezas_en_riesgo"]
        if fila["nivel_riesgo"] == "rojo":
            acc["partes_rojo"] += 1
    for acc in por_cliente.values():
        acc["piezas_en_riesgo"] = round(acc["piezas_en_riesgo"], 1)

    return {
        "mes":              mes,
        "fuente_plan":      plan["fuente"],
        "ventana_meses":    ventana,
        "ventana_historial": {"desde": d_ini[:10], "hasta": d_fin[:10]},
        "global_rate_pct":  round(global_rate * 100, 1),
        "kpis": {
            "piezas_comprometidas_total": sum(f["sdo_final"] for f in detalle),
            "piezas_en_riesgo_total":     round(sum(f["piezas_en_riesgo"] for f in detalle), 1),
            "partes_rojo":                sum(1 for f in detalle if f["nivel_riesgo"] == "rojo"),
            "partes_ambar":               sum(1 for f in detalle if f["nivel_riesgo"] == "ambar"),
            "clientes_afectados":         sum(1 for c in por_cliente.values() if c["partes_rojo"] > 0),
        },
        "por_cliente": sorted(por_cliente.values(), key=lambda x: x["piezas_en_riesgo"], reverse=True),
        "detalle":     detalle,
    }
