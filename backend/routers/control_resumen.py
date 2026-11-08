"""
Resumen de Control del Proceso (2026-11-08) -- tablero arriba de la pestaña.

Pedido de JC: "tengo muchos datos y no me dice nada". La pestaña ya evalúa los 7 PCs por colada
(core.evaluar_pcs_colada), pero solo los mostraba colada por colada. Este endpoint responde
tres preguntas sobre el universo de la ventana:

  1. ¿Cómo estamos?  coladas y piezas analizadas, cuántas limpias / con ámbar / con rojo y las
     piezas expuestas (piezas de coladas en ROJO de producto).
  2. ¿Dónde?         por PC, coladas y piezas en VERDE / ÁMBAR / ROJO / GRIS, más el motivo
     de gris más común (para que el gris no se esconda).
  2b. Señal fuerte  (Agente Beta automático) coladas en rojo de producto cuyas partes ya
     rechazaban históricamente; ordena "atender primero". Ver senales_beta().
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
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Query

from backend.core import CARRILES, PC1_RETORNOS_MAX_PCT, _evaluar_coladas_ventana
from backend.protocolos import catalogo as _catalogo_protocolos
from backend.routers.beta import _riesgo_lote_partes

router = APIRouter()

PCS_PRODUCTO = ["PC-1", "PC-2", "PC-3", "PC-4"]
PCS_REGISTRO = ["PC-5", "PC-6", "PC-7"]
_ORDEN = {"ROJO": 3, "AMBAR": 2, "VERDE": 1, "GRIS": 0}


def _peor(estados: list) -> str:
    return max(estados, key=lambda e: _ORDEN.get(e, 0)) if estados else "GRIS"


# Señal fuerte (2026-11-08, Agente Beta automático): una colada fuera de control HOY en una parte
# que YA rechaza por el defecto que ese PC provoca es lo primero que hay que atender -- la idea de
# /api/beta/criticas (PC-6 rojo ∩ riesgo alto), pero con PC-1 a PC-4, que se conocen al momento y
# no ~11 días después. Historia de la parte = compuerta 1 de Beta (gamamega_01_riesgodefectoparte,
# 2023+2025): defecto dominante y % de rechazo. Sin LLM: reglas y datos, instantáneo y gratis.
#
# Por qué no basta FlagCriticidadDefectoParte: marca concentración de un defecto (z), no rechazo
# alto -- 60 de 66 partes de la semana al 8-nov lo traían (hasta con 0.4 % de rechazo) y 52 de 61
# coladas rojas salían "fuertes". Con defecto ligado al PC + rechazo ≥ 3 % quedan 19 de 61.
#   fuerte = PC de producto en ROJO + una parte cuyo defecto dominante está ligado a ese PC
#            (DEFECTOS_POR_PC) y que rechaza ≥ SENAL_PCT_MIN % históricamente
#   media  = ROJO sin esa coincidencia, o ÁMBAR con ella
#   baja   = ÁMBAR sin ella
# DEFECTOS_POR_PC es criterio metalúrgico PROPUESTO (temperatura -> fierro frío / no lleno;
# ITACA -> sopladura, porosidad, rechupe, microestructura...), pendiente de validar con Calidad.
# NOTA PARA CALIDAD (8-nov, JC: se queda hasta que Calidad decida): química -> SOPLADURA es la liga
# más dudosa; la sopladura suele venir del molde o del inoculante más que de la composición
# (lo señaló el propio Agente Beta al analizar la colada 161). Hoy hace que C-AD626LG (12 % por
# sopladura) salga como señal fuerte cada vez que su química sale roja. Pregunta 7 del doc de
# protocolos.
_SENAL_ORDEN = {"fuerte": 0, "media": 1, "baja": 2}
SENAL_PCT_MIN = 3.0
DEFECTOS_POR_PC = {
    "PC-1": {"SOPLADURA", "CARBON LUSTROSO"},
    "PC-2": {"MICROSETRUCTURA", "PROP  MEC Y QUIMICA", "FISURADO", "QUEBRADO", "SOPLADURA",
             "POROSIDAD", "CARBON LUSTROSO"},
    "PC-3": {"SOPLADURA", "POROSIDAD", "RECHUPE", "DEPRESION", "MICROSETRUCTURA",
             "PROP  MEC Y QUIMICA", "FISURADO", "QUEBRADO"},
    "PC-4": {"FIERRO FRIO", "GOTA DE FIERRO FRIO", "NO LLENO", "PENETRADO"},
}


def senales_beta(coladas: list, grav: dict) -> dict:
    """{id_colada: {senal, partes_riesgo[{no_parte, piezas, defecto, pct}], piezas_riesgo}} para
    las coladas en ÁMBAR/ROJO de producto. Una sola consulta para todas las partes."""
    fuera = [c for c in coladas if grav.get(c["id_colada"]) in ("ROJO", "AMBAR")]
    partes = sorted({s["sku"] for c in fuera for s in (c.get("skus") or []) if s.get("sku")})
    try:
        riesgo = _riesgo_lote_partes(partes)
    except Exception:  # sin la tabla de criticidad: el resumen sigue, solo sin señal
        riesgo = {}
    out = {}
    for c in fuera:
        g = grav[c["id_colada"]]
        pcs = [p for p in DEFECTOS_POR_PC if c["estados"].get(p) == g]  # los PCs que dan la gravedad
        altas = []
        for s_ in (c.get("skus") or []):
            h = riesgo.get(s_["sku"]) or {}
            ligados = [p for p in pcs if h.get("defecto_dominante") in DEFECTOS_POR_PC[p]]
            if ligados and h.get("pct_rchz_total", 0) * 100 >= SENAL_PCT_MIN:
                altas.append({"no_parte": s_["sku"], "piezas": s_["total"], "defecto": h["defecto_dominante"],
                              "pct": round(h["pct_rchz_total"] * 100, 1), "pc": ligados})
        senal = "fuerte" if g == "ROJO" and altas else "media" if g == "ROJO" or altas else "baja"
        out[c["id_colada"]] = {"senal": senal, "partes_riesgo": altas,
                               "piezas_riesgo": sum(a["piezas"] for a in altas)}
    return out


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
    sen = senales_beta(coladas, grav)
    # "Atender primero" por señal de Beta: fuerte antes que media, y dentro, más piezas de partes
    # que ya rechazaban; después más PCs en rojo y más piezas.
    peores = sorted((c for c in coladas if c["id_colada"] in sen and sen[c["id_colada"]]["senal"] != "baja"),
                    key=lambda c: (_SENAL_ORDEN[sen[c["id_colada"]]["senal"]], -sen[c["id_colada"]]["piezas_riesgo"],
                                   -sum(c["estados"][p] == "ROJO" for p in PCS_PRODUCTO), -(c["piezas"] or 0)))
    fuertes = [c for c in coladas if sen.get(c["id_colada"], {}).get("senal") == "fuerte"]
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
        "senal_fuerte": {
            "coladas": len(fuertes),
            "piezas": sum(c["piezas"] or 0 for c in fuertes),
            "piezas_partes_riesgo": sum(sen[c["id_colada"]]["piezas_riesgo"] for c in fuertes),
            "regla": f"colada en rojo (PC-1 a PC-4) con una parte que ya rechaza ≥ {SENAL_PCT_MIN:g} % por el defecto ligado a ese PC (Agente Beta)",
        },
        "atender": [
            {"id_colada": c["id_colada"], "u_colada": c["u_colada"], "horno": c["horno"],
             "inicio_fusion": c["inicio_fusion"], "piezas": c["piezas"], "clase": c.get("clase"),
             "rojos": [p for p in PCS_PRODUCTO if c["estados"][p] == "ROJO"],
             "ambar": [p for p in PCS_PRODUCTO if c["estados"][p] == "AMBAR"],
             "skus": [s["sku"] for s in (c.get("skus") or [])][:3],
             **sen[c["id_colada"]]}
            for c in peores[:5]
        ],
    }


@router.get("/v3/gestion/resumen-control/pc/{id_pc}")
def resumen_control_pc(id_pc: str, referencia: Optional[str] = Query(default=None), horas: int = Query(default=48)):
    """
    Desglose de un PC (2026-11-08), lo que se abre al dar clic en su renglón del resumen:

      - pareto:  qué lo saca de VERDE (core.causas_pcs: material, elemento alto/bajo, alerta
                 ITACA, frío/caliente, defecto), en cuántas coladas y piezas, cuántas en ROJO;
      - patron:  horno x día (coladas evaluables, ámbar, rojo) y por clase de hierro, para ver si
                 el problema es un horno, un día o todo;
      - coladas: las coladas en ÁMBAR/ROJO de ese PC con su causa, peor primero.

    Mismas coladas y caché que el resumen. PCs sin causas desglosables (PC-5, PC-7) devuelven el
    patrón y la lista sin Pareto.
    """
    id_pc = id_pc.upper()
    if referencia is None:
        referencia = datetime.now().strftime("%Y-%m-%dT%H:%M")
    coladas = _evaluar_coladas_ventana(referencia, horas)

    pareto: dict = {}
    hornos: dict = {}
    clases: dict = {}
    dias = set()
    fuera = []
    for c in coladas:
        e = c["estados"].get(id_pc, "GRIS")
        if e == "GRIS":
            continue
        dia = str(c["inicio_fusion"])[:10]
        dias.add(dia)
        h = hornos.setdefault(str(c["horno"] or "?"), {})
        cel = h.setdefault(dia, {"evaluables": 0, "AMBAR": 0, "ROJO": 0})
        cel["evaluables"] += 1
        k = clases.setdefault(c.get("clase") or "sin clase", {"evaluables": 0, "AMBAR": 0, "ROJO": 0})
        k["evaluables"] += 1
        if e == "VERDE":
            continue
        cel[e] += 1
        k[e] += 1
        causas = (c.get("causas") or {}).get(id_pc) or []
        for etiqueta in {x["causa"] for x in causas}:
            p = pareto.setdefault(etiqueta, {"causa": etiqueta, "coladas": 0, "rojo": 0, "piezas": 0})
            p["coladas"] += 1
            p["rojo"] += e == "ROJO"
            p["piezas"] += c["piezas"] or 0
        fuera.append({
            "_c": c,
            "id_colada": c["id_colada"], "u_colada": c["u_colada"], "horno": c["horno"],
            "inicio_fusion": c["inicio_fusion"], "clase": c.get("clase"), "estado": e,
            "piezas": c["piezas"], "causas": causas,
            "skus": [s["sku"] for s in (c.get("skus") or [])][:3],
        })
    gpc = {x["id_colada"]: _peor([x["_c"]["estados"][p] for p in PCS_PRODUCTO]) for x in fuera}
    sen = senales_beta([x.pop("_c") for x in fuera], gpc)
    for x in fuera:
        x.update(sen.get(x["id_colada"]) or {"senal": None, "partes_riesgo": [], "piezas_riesgo": 0})
    fuera.sort(key=lambda x: (x["estado"] != "ROJO", _SENAL_ORDEN.get(x["senal"], 3), -(x["piezas"] or 0)))
    return {
        "referencia": referencia, "horas": horas, "id_pc": id_pc,
        "pareto": sorted(pareto.values(), key=lambda p: (-p["coladas"], -p["rojo"])),
        "patron": {
            "dias": sorted(dias),
            "hornos": {h: v for h, v in sorted(hornos.items())},
            "clases": sorted(({"clase": k, **v} for k, v in clases.items()), key=lambda x: -(x["AMBAR"] + x["ROJO"])),
        },
        "coladas": fuera,
        # protocolo provisional del PC (PC-3 trae también la variante nodular)
        "protocolos": [p for p in _catalogo_protocolos()["protocolos"].values() if p["pc"] == id_pc],
    }


@router.get("/v3/gestion/protocolos-provisionales")
def protocolos_provisionales():
    """Catálogo de protocolos provisionales por PC (backend/protocolos.py) para el detalle de colada."""
    return _catalogo_protocolos()


# ══════════════════════════════════════════════════════════════════════════════
# Lectura del turno (2026-11-08): tres frases de Agente Beta arriba del resumen
# ══════════════════════════════════════════════════════════════════════════════
# Una sola llamada al modelo (sin tools) con los datos ya calculados del resumen y del desglose
# del PC que más rojos da -- el modelo solo redacta, no busca datos, y no puede citar un número
# que no venga en el JSON. Caché por ventana en bloques de 30 min: aunque varias personas abran
# la pestaña, el costo es ~una llamada cada media hora por ventana usada.
_LECTURA_CACHE: dict = {}
_LECTURA_TTL_SEG = 1800

_LECTURA_SYSTEM = """Eres el Agente Beta de la Torre de Control FASA (fundición de hierro gris y nodular).
Escribes la LECTURA DEL TURNO para el jefe de turno: exactamente tres frases en español, cada una
de máximo 35 palabras, directas, como se le habla a un supervisor de piso:
  1. "que_paso": cómo está el control del proceso en la ventana (con sus números).
  2. "por_que_importa": dónde está el problema (punto de control, causa, clase u horno) y por qué
     importa (piezas expuestas, partes que ya rechazan).
  3. "que_atender": qué atender primero (las coladas de "atender" con señal fuerte, con su número
     de colada -- el campo "colada" -- y horno) y la acción del protocolo provisional si viene en
     los datos.
Reglas duras: usa SOLO números, coladas, partes y causas que vengan literalmente en el JSON; no
inventes acciones ni cantidades; si mencionas una acción, di que el protocolo es provisional. Si
el rojo dominante es de hierro nodular en PC-3, di que sus umbrales están pendientes de Calidad.
Responde SOLO un objeto JSON con las claves que_paso, por_que_importa, que_atender."""


@router.get("/v3/gestion/resumen-control/lectura")
def lectura_turno(referencia: Optional[str] = Query(default=None), horas: int = Query(default=48),
                  forzar: bool = Query(default=False)):
    """Lectura del turno de Agente Beta (3 frases) sobre el Resumen de control de la ventana."""
    import json
    import time as _t
    ahora = datetime.now()
    clave = (horas, int(_t.time() // _LECTURA_TTL_SEG))
    if not forzar and clave in _LECTURA_CACHE:
        return _LECTURA_CACHE[clave]
    res = resumen_control(referencia=referencia, horas=horas)
    if not res["universo"]["coladas"]:
        return {"disponible": False, "motivo": "sin coladas en la ventana"}
    for p in res["pcs"]:
        p.pop("regla", None)
    top = max((p for p in res["pcs"] if p["grupo"] == "producto"), key=lambda p: p["coladas"]["ROJO"])
    det = resumen_control_pc(id_pc=top["id_pc"], referencia=referencia, horas=horas)
    datos = {
        "ventana_horas": horas, "corte": res["referencia"],
        "universo": res["universo"], "gravedad": res["gravedad"], "pcs": res["pcs"],
        "senal_fuerte": res.get("senal_fuerte"), "retornos": res["retornos"],
        # sin id_colada: el modelo citaba el id interno (86821) en vez del número que ve el turno
        "atender": [{("colada" if k == "u_colada" else k): v for k, v in a.items() if k != "id_colada"}
                    for a in res["atender"]],
        "pc_con_mas_rojos": {"id_pc": top["id_pc"], "pareto": det["pareto"][:5],
                             "clases": det["patron"]["clases"][:5],
                             "hornos": {h: {"evaluables": sum(c["evaluables"] for c in v.values()),
                                            "rojo": sum(c["ROJO"] for c in v.values())}
                                        for h, v in det["patron"]["hornos"].items()},
                             "protocolos": [{k: p[k] for k in ("clave", "rojo", "responsable", "tiempo")}
                                            for p in det.get("protocolos", [])]},
    }
    try:
        import anthropic
        msg = anthropic.Anthropic().messages.create(
            model="claude-opus-5", max_tokens=1500, system=_LECTURA_SYSTEM,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": json.dumps(datos, ensure_ascii=False, default=str)}])
        texto = next(b.text for b in msg.content if b.type == "text").strip()
        texto = texto[texto.find("{"): texto.rfind("}") + 1]
        frases = json.loads(texto)
    except Exception as e:  # sin API, sin crédito o respuesta no JSON: el tablero sigue sin la lectura
        return {"disponible": False, "motivo": f"no se pudo generar la lectura ({type(e).__name__})"}
    out = {"disponible": True, "generada": ahora.strftime("%Y-%m-%d %H:%M"), "horas": horas,
           "frases": {k: frases.get(k) for k in ("que_paso", "por_que_importa", "que_atender")}}
    for k in [k for k in _LECTURA_CACHE if k[1] != clave[1]]:
        _LECTURA_CACHE.pop(k, None)
    _LECTURA_CACHE[clave] = out
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Validación del semáforo (2026-11-08): ¿las coladas en rojo rechazaron más?
# ══════════════════════════════════════════════════════════════════════════════
# El rechazo se captura ~11 días después del vaciado, así que se evalúan las coladas de hace
# `desde_dias` a `desde_dias + semanas*7` días (default 12 a 26). Para cada PC se compara el
# rechazo REAL de sus coladas en ROJO / ÁMBAR / VERDE contra el ESPERADO por la mezcla de partes
# (tasa de cada parte en todas las coladas de la ventana): lift = real / esperado. Así no se
# confunde "este PC predice rechazo" con "las coladas rojas eran de partes difíciles" -- la misma
# prueba que se usó el 8-nov para calibrar PC-4 y descartar los retornos. Solo coladas con PC-6
# evaluable (rechazo registrado en >= 50 % de sus piezas). Caché de 1 h.
_VALIDACION_CACHE: dict = {}


@router.get("/v3/gestion/resumen-control/validacion")
def validacion_semaforo(semanas: int = Query(default=2, ge=1, le=6), desde_dias: int = Query(default=12, ge=7, le=60)):
    import time as _t
    clave = (semanas, desde_dias, int(_t.time() // 3600))
    if clave in _VALIDACION_CACHE:
        return _VALIDACION_CACHE[clave]
    ref_dt = datetime.now() - timedelta(days=desde_dias)
    ref = ref_dt.strftime("%Y-%m-%dT%H:%M")
    coladas = [c for c in _evaluar_coladas_ventana(ref, semanas * 7 * 24)
               if c["estados"].get("PC-6") != "GRIS" and c.get("skus")]
    tot, rec = {}, {}
    for c in coladas:
        for s_ in c["skus"]:
            tot[s_["sku"]] = tot.get(s_["sku"], 0) + (s_.get("total") or 0)
            rec[s_["sku"]] = rec.get(s_["sku"], 0) + (s_.get("rechazos") or 0)
    tasa = {k: rec[k] / tot[k] for k in tot if tot[k]}
    # rechazo por (parte, defecto), de las causas de PC-6 (top 5 combinaciones por colada)
    dfx: dict = {}
    for c in coladas:
        for x in (c.get("causas") or {}).get("PC-6", []):
            dfx[(x["parte"], x["causa"])] = dfx.get((x["parte"], x["causa"]), 0) + x["piezas"]

    def grupo_defectos(cs, defectos):
        """Lo mismo que grupo() pero contando solo los rechazos por los defectos ligados al PC
        (DEFECTOS_POR_PC): prueba más justa, el rechazo total trae defectos de moldeo que ningún PC
        de fusión puede predecir."""
        tasa_d = {k: sum(v for (pp, d), v in dfx.items() if pp == k and d in defectos) / tot[k] for k in tot if tot[k]}
        piezas = sum(s_["total"] or 0 for c in cs for s_ in c["skus"])
        real = sum(x["piezas"] for c in cs for x in (c.get("causas") or {}).get("PC-6", []) if x["causa"] in defectos)
        esp = sum((s_["total"] or 0) * tasa_d.get(s_["sku"], 0) for c in cs for s_ in c["skus"])
        return {"rechazo_real_pct": round(real / piezas * 100, 2) if piezas else None,
                "rechazo_esperado_pct": round(esp / piezas * 100, 2) if piezas else None,
                "lift": round(real / esp, 2) if esp else None}

    def grupo(cs):
        piezas = sum(s_["total"] or 0 for c in cs for s_ in c["skus"])
        real = sum(s_.get("rechazos") or 0 for c in cs for s_ in c["skus"])
        esp = sum((s_["total"] or 0) * tasa.get(s_["sku"], 0) for c in cs for s_ in c["skus"])
        return {"coladas": len(cs), "piezas": piezas,
                "rechazo_real_pct": round(real / piezas * 100, 1) if piezas else None,
                "rechazo_esperado_pct": round(esp / piezas * 100, 1) if piezas else None,
                "lift": round(real / esp, 2) if esp else None,
                "muestra_suficiente": len(cs) >= 5 and piezas >= 30}

    pcs = []
    for p in PCS_PRODUCTO:
        g = {e: grupo([c for c in coladas if c["estados"][p] == e]) for e in ("ROJO", "AMBAR", "VERDE")}
        r, v = g["ROJO"], g["VERDE"]
        if not (r["muestra_suficiente"] and v["muestra_suficiente"]) or r["lift"] is None or v["lift"] is None:
            veredicto = "sin muestra suficiente"
        elif r["lift"] >= 1.15 and r["lift"] - v["lift"] >= 0.15:
            veredicto = "predice rechazo"
        elif r["lift"] <= 1.05:
            veredicto = "no separa: el rojo no rechazó más que lo esperado por sus partes"
        else:
            veredicto = "señal débil"
        ligados = {e: grupo_defectos([c for c in coladas if c["estados"][p] == e], DEFECTOS_POR_PC[p])
                   for e in ("ROJO", "VERDE")}
        pcs.append({"id_pc": p, "estados": g, "veredicto": veredicto,
                    "defectos_ligados": {"defectos": sorted(DEFECTOS_POR_PC[p]), **ligados}})

    grav = {c["id_colada"]: _peor([c["estados"][p] for p in PCS_PRODUCTO]) for c in coladas}
    sen = senales_beta(coladas, grav)
    ids_fuertes = {cid for cid, x in sen.items() if x["senal"] == "fuerte"}
    out = {
        "ventana": {"desde": (ref_dt - timedelta(weeks=semanas)).strftime("%Y-%m-%d"),
                    "hasta": ref_dt.strftime("%Y-%m-%d"), "coladas_evaluadas": len(coladas),
                    "nota": f"coladas de hace {desde_dias} a {desde_dias + semanas * 7} días, con su rechazo ya registrado"},
        "pcs": pcs,
        "senal_fuerte": {
            "fuerte": grupo([c for c in coladas if c["id_colada"] in ids_fuertes]),
            "rojo_sin_senal": grupo([c for c in coladas if grav[c["id_colada"]] == "ROJO" and c["id_colada"] not in ids_fuertes]),
            "limpias": grupo([c for c in coladas if grav[c["id_colada"]] == "VERDE"])},
        "metodo": "lift = rechazo real / rechazo esperado por la mezcla de partes (tasa de cada parte en la misma ventana)",
    }
    _VALIDACION_CACHE.clear()
    _VALIDACION_CACHE[clave] = out
    return out
