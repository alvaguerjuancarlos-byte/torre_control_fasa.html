"""
Resumen de Control del Proceso (2026-11-08) -- tablero arriba de la pestaña.

Pedido de JC: "tengo muchos datos y no me dice nada". La pestaña ya evalúa los 7 PCs por colada
(core.evaluar_pcs_colada), pero solo los mostraba colada por colada. Este endpoint responde
tres preguntas sobre el universo de la ventana:

  1. ¿Cómo estamos?  coladas y piezas analizadas, cuántas limpias / con ámbar / con rojo y las
     piezas expuestas (piezas de coladas en ROJO de producto).
  2. ¿Dónde?         por PC, coladas y piezas en VERDE / ÁMBAR / ROJO / GRIS, más el motivo
     de gris más común (para que el gris no se esconda).
  3. Retornos        incumplimiento del Plan de Control (≤ 30 % de la carga), aparte: no
     colorea la colada (ver core.PC1_RETORNOS_MAX_PCT).

Gravedad de la colada (decisión de JC, 2026-11-08): la define el peor estado entre los PCs de
RIESGO DE PRODUCTO (PC-1 carga, PC-2 química, PC-3 ITACA, PC-4 temperatura). Los PCs de
PCs de DESPUÉS DEL VACIADO (PC-5 desmoldeo, PC-6 rechazo = resultado, PC-7 cierre) se cuentan
aparte: llegan días después y dependen de que se registre el proceso (una limpieza sin
registrar no es un hierro fuera de especificación). Una colada sin ningún PC de
producto evaluable cuenta como "sin dato", no como limpia.

Mismos datos y misma caché que la lista de coladas (core._evaluar_coladas_ventana), así el
tablero y la matriz de abajo nunca se contradicen.
"""
import re
from collections import Counter
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query

from backend.core import CARRILES, PC1_RETORNOS_MAX_PCT, _evaluar_coladas_ventana

router = APIRouter()

PCS_PRODUCTO = ["PC-1", "PC-2", "PC-3", "PC-4"]
PCS_REGISTRO = ["PC-5", "PC-6", "PC-7"]
_ORDEN = {"ROJO": 3, "AMBAR": 2, "VERDE": 1, "GRIS": 0}


def _peor(estados: list) -> str:
    return max(estados, key=lambda e: _ORDEN.get(e, 0)) if estados else "GRIS"


@router.get("/v3/gestion/resumen-control")
def resumen_control(referencia: Optional[str] = Query(default=None), horas: int = Query(default=48)):
    if referencia is None:
        referencia = datetime.now().strftime("%Y-%m-%dT%H:%M")
    coladas = _evaluar_coladas_ventana(referencia, horas)
    carril = {c.id_pc: c for c in CARRILES}

    gravedad = Counter()
    piezas_grav = Counter()
    grav = {}  # por id_colada; no se escribe en las coladas porque vienen de la caché compartida
    for c in coladas:
        g = _peor([c["estados"][p] for p in PCS_PRODUCTO])
        grav[c["id_colada"]] = g
        gravedad[g] += 1
        piezas_grav[g] += c["piezas"] or 0

    pcs = []
    for p in PCS_PRODUCTO + PCS_REGISTRO:
        col, pz = Counter(), Counter()
        motivos = Counter()
        for c in coladas:
            e = c["estados"][p]
            col[e] += 1
            pz[e] += c["piezas"] or 0
            if e == "GRIS":
                m = (c.get("motivos_gris") or {}).get(p) or "sin dato"
                # "registrado en 0 de 4 piezas de la colada" -> texto general, para agrupar
                m = re.sub(r"registrad[oa] en \d+ de \d+ piezas de la colada",
                           "registrado en menos de la mitad de las piezas de la colada", m)
                motivos[m] += 1
        evaluables = col["VERDE"] + col["AMBAR"] + col["ROJO"]
        pcs.append({
            "id_pc": p,
            "grupo": "producto" if p in PCS_PRODUCTO else "registro",
            "señal": carril[p].señal,
            "regla": carril[p].logica_de_estado,
            "coladas": {k: col[k] for k in ("VERDE", "AMBAR", "ROJO", "GRIS")},
            "piezas": {k: pz[k] for k in ("VERDE", "AMBAR", "ROJO", "GRIS")},
            "cobertura_pct": round(evaluables / len(coladas) * 100, 1) if coladas else None,
            "motivo_gris": motivos.most_common(1)[0][0] if motivos else None,
            "motivo_gris_coladas": motivos.most_common(1)[0][1] if motivos else 0,
        })

    ret = [c["pct_retornos"] for c in coladas if c.get("pct_retornos") is not None]
    peores = sorted((c for c in coladas if grav[c["id_colada"]] == "ROJO"),
                    key=lambda c: (-sum(c["estados"][p] == "ROJO" for p in PCS_PRODUCTO), -(c["piezas"] or 0)))
    return {
        "referencia": referencia, "horas": horas,
        "universo": {"coladas": len(coladas), "piezas": sum(c["piezas"] or 0 for c in coladas)},
        "gravedad": {
            "coladas": {k: gravedad[k] for k in ("VERDE", "AMBAR", "ROJO", "GRIS")},
            "piezas": {k: piezas_grav[k] for k in ("VERDE", "AMBAR", "ROJO", "GRIS")},
            "regla": "peor estado entre PC-1 a PC-4 (riesgo de producto); PC-5 a PC-7 se cuentan aparte",
        },
        "pcs": pcs,
        "retornos": {
            "limite_pct": PC1_RETORNOS_MAX_PCT,
            "coladas_con_dato": len(ret),
            "coladas_sobre_limite": sum(1 for x in ret if x > PC1_RETORNOS_MAX_PCT),
            "mediana_pct": sorted(ret)[len(ret) // 2] if ret else None,
        },
        "atender": [
            {"id_colada": c["id_colada"], "u_colada": c["u_colada"], "horno": c["horno"],
             "inicio_fusion": c["inicio_fusion"], "piezas": c["piezas"],
             "rojos": [p for p in PCS_PRODUCTO if c["estados"][p] == "ROJO"],
             "skus": [s["sku"] for s in (c.get("skus") or [])][:3]}
            for c in peores[:5]
        ],
    }
