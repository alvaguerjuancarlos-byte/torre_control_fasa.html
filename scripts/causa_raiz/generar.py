"""Genera backend/data/causa_raiz.json para la vista "Causa raíz" (Calidad del Producto).

Recalcula todo desde la BD (solo lectura): ~10 min. Corre solo cada lunes con la tarea programada de
Windows "TorreFASA - Causa raiz" (scripts/causa_raiz/recalcular_semanal.cmd, log en logs/). A mano:
    python scripts/causa_raiz/generar.py            # reconstruye la tabla por colada y el JSON
    python scripts/causa_raiz/generar.py --rapido   # reusa la tabla por colada ya guardada

Método (mismo del documento Hipotesis_Causa_Raiz_Top5_2026-10-30): O/E = rechazos observados /
esperados, con esperado = piezas de cada parte x tasa histórica de esa parte para el defecto. Así
una señal ("coladas con temperatura arriba del máximo") no se confunde con "ahí se vaciaron partes
difíciles". Las hipótesis (texto) viven en HIPOTESIS; las cifras salen de los datos cada vez.
"""
import sys, json, time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sqlalchemy import create_engine
from backend import core

# Sin límite de lectura, una conexión que la BD cierra a medio camino deja el script esperando para
# siempre (pasó el 2026-10-30: 20 min colgado). Con read_timeout la consulta falla y coladas.py
# reintenta el mes. Solo para este proceso; el servidor conserva su engine.
core.engine = create_engine(core.engine.url, pool_pre_ping=True,
                            connect_args={"read_timeout": 900, "write_timeout": 900})

from backend.core import run
from coladas import construir, norm_defecto, _defecto_sql
from arena import senales_arena

DATA = RAIZ / "backend" / "data"
TABLA = DATA / "cache" / "causa_raiz_coladas.json"
SALIDA = DATA / "causa_raiz.json"
MIN_ESPERADO = 3.0  # O/E con menos de 3 rechazos esperados no se reporta (ruido)


def _ita(k):
    return lambda c: (c.get("ita") or {}).get(k)


def _te(k):
    return lambda c: (c.get("ita_te") or {}).get(k)


def _pct_t_ok(c):
    return c["t_ok"][0] / c["t_ok"][1] if c.get("t_ok") and c["t_ok"][1] else None


def _min_horno(c):
    return c["min_horno"] if c.get("min_horno") and 0 < c["min_horno"] < 600 else None


# Señales de proceso por colada: (etiqueta, fuente, función -> True/False/None). None = sin dato.
SENALES = {
    "temp_sobre_max": ("Temperatura promedio arriba del máximo de la parte", "temperatura",
                       lambda c: None if not c.get("t_avg") or not c.get("tmax") else c["t_avg"] > c["tmax"]),
    "temp_bajo_min": ("Temperatura promedio abajo del mínimo de la parte", "temperatura",
                      lambda c: None if not c.get("t_avg") or not c.get("tmin") else c["t_avg"] < c["tmin"]),
    "temp_ventana_50": ("Menos de 50 % de las piezas dentro de la ventana de temperatura", "temperatura",
                        lambda c: None if _pct_t_ok(c) is None else _pct_t_ok(c) < 0.5),
    "horno_excede_50": ("Tiempo en horno excede su límite por más de 50 %", "horno",
                        lambda c: None if not c.get("min_horno") or not c.get("t_lim") else c["min_horno"] > c["t_lim"] * 1.5),
    "horno_66": ("Más de 66 min en horno", "horno",
                 lambda c: None if _min_horno(c) is None else _min_horno(c) > 66),
    "dem_falta_moldes": ("Demora por «Falta de moldes»", "demoras",
                         lambda c: None if c.get("demora") is None else (c.get("dem_desc") or "").startswith("Falta de Moldes")),
    "dem_juntan": ("Demora por «Se juntan las coladas»", "demoras",
                   lambda c: None if c.get("demora") is None else (c.get("dem_desc") or "").startswith("Se juntan las coladas")),
    "te_c_bajo": ("ITACA telurio: carbono < 3.45", "itaca",
                  lambda c: None if _te("calcC")(c) is None else _te("calcC")(c) < 3.45),
    "te_ceq_bajo": ("ITACA telurio: carbono equivalente < 4.02", "itaca",
                    lambda c: None if _te("calcCeq")(c) is None else _te("calcCeq")(c) < 4.02),
    "ita_reprueba": ("Dos o más copas ITACA (re-prueba)", "itaca",
                     lambda c: None if not c.get("ita_n") else c["ita_n"] >= 2),
    "ita_cementite_50": ("ITACA Cementite < 50 (riesgo de carburo)", "itaca",
                         lambda c: None if _ita("Cementite")(c) is None else _ita("Cementite")(c) < 50),
    "q_mn_alto": ("Manganeso alto (> 0.756)", "quimica",
                  lambda c: None if not c.get("q") or c["q"].get("Mn") is None else c["q"]["Mn"] > 0.756),
    "q_mg_alto": ("Magnesio alto (> 0.002)", "quimica",
                  lambda c: None if not c.get("q") or c["q"].get("Mg") is None else c["q"]["Mg"] > 0.002),
    "carga_fuera": ("Carga metálica con algún material fuera de tolerancia", "carga",
                    lambda c: None if c.get("carga_ok") is None else c["carga_ok"] < 100),
}

# Tarjetas: texto cualitativo (lo valida Calidad); las cifras las calcula este script.
HIPOTESIS = [
    {
        "clave": "contraccion", "nombre": "Contracción", "familia": "Micro / depresion", "confianza": "alta",
        "defectos": [{"clave": "contraccion", "nombre": "Rechupe + Depresión", "nd": ["RECHUPE", "DEPRESION", "MICRORECHUPE"]},
                     {"clave": "rechupe", "nombre": "Rechupe", "nd": ["RECHUPE"]},
                     {"clave": "depresion", "nombre": "Depresión", "nd": ["DEPRESION"]}],
        "senales": [("depresion", "temp_sobre_max"), ("depresion", "temp_ventana_50"), ("depresion", "horno_excede_50"),
                    ("depresion", "horno_66"), ("depresion", "dem_falta_moldes"), ("contraccion", "te_c_bajo"),
                    ("contraccion", "te_ceq_bajo"), ("depresion", "ita_reprueba")],
        "hipotesis": "El metal sale sobrecalentado y espera de más en el horno, muchas veces porque no hay moldes "
                     "listos. Con la espera pierde efecto el inoculante y el metal queda hipoeutéctico (carbono "
                     "equivalente bajo), con lo que la pieza contrae más al solidificar. En partes nuevas pesa además "
                     "el diseño de alimentación (mazarotas, cuellos, enfriaderas).",
        "notas": ["Rechupe y Depresión se analizan juntos: el registro alterna entre uno y otro sin criterio fijo "
                  "(ene–feb 2026 casi todo Rechupe, mar–abr casi todo Depresión).",
                  "La parte 42091101 tenía el 70 % de la Depresión de 2025 y en 2026 casi desapareció: documentar qué se le cambió."],
        "validar": ["Fusión: temperatura de vaciado real vs. ventana de cada parte, por turno.",
                    "Fusión y Moldeo: cuánto espera el metal cuando hay «falta de moldes» y si se re-inocula.",
                    "Ingeniería: sistema de alimentación de las partes en desarrollo con más rechupe.",
                    "Calidad: criterio único Rechupe vs. Depresión."],
    },
    {
        "clave": "sopladura", "nombre": "Sopladura", "familia": "Gases / porosidad", "confianza": "media",
        "defectos": [{"clave": "sopladura", "nombre": "Sopladura", "nd": ["SOPLADURA"]},
                     {"clave": "porosidad", "nombre": "Porosidad", "nd": ["POROSIDAD"]}],
        "senales": [("sopladura", "temp_sobre_max"), ("sopladura", "horno_excede_50"), ("sopladura", "q_mn_alto"),
                    ("porosidad", "temp_sobre_max"), ("porosidad", "q_mn_alto"), ("porosidad", "q_mg_alto")],
        "hipotesis": "Gases que salen de corazones y moldes en unas cuantas partes específicas: venteo insuficiente de "
                     "corazones, exceso o mala aplicación de pegamento, o pintura sin secar. El metal casi no explica "
                     "la sopladura (sus señales de proceso salen cerca de 1.0). La Porosidad, en cambio, sí se "
                     "relaciona con el metal: temperatura arriba del máximo y Mn/Mg altos.",
        "notas": ["Revisar efecto de temporada (humedad): en 2025 la sopladura subió en junio y septiembre.",
                  "Arena (2026): lo que mide el laboratorio no explica la sopladura de forma clara; solo hay pistas "
                  "débiles con el LOI de los corazones K300 y los finos de la cama fluidizada (ver sección Arena)."],
        "validar": ["Moldeo y Corazones: venteo, pegamento y secado de pintura en las partes con más sopladura.",
                    "Piso: registrar la causa detallada (17.xx) en cada sopladura durante 4 semanas.",
                    "Corazones: revisar LOI (contenido de resina) de los corazones de las partes con más sopladura."],
        "arena": {"defectos": ["sopladura", "porosidad"],
                  "mensual": ["LOI-K300", "LOI ARENA COLD BOX", "FINOS CAMA FLUIDIZADA"]},
    },
    {
        "clave": "molde", "nombre": "Caído y Tirado", "familia": "Arena / corazon", "confianza": "media",
        "defectos": [{"clave": "caido", "nombre": "Caído", "nd": ["CAIDO"]},
                     {"clave": "tirado", "nombre": "Tirado", "nd": ["TIRADO"]}],
        "senales": [("tirado", "dem_juntan"), ("tirado", "dem_falta_moldes"), ("tirado", "horno_excede_50"),
                    ("caido", "dem_juntan"), ("caido", "temp_sobre_max"), ("caido", "carga_fuera")],
        "hipotesis": "Caído: cierre y manejo del molde en AF3 o herramental. Las propiedades de arena que mide el "
                     "laboratorio no lo explican: dentro de cada mes, los días con arena fuera de límite o caliente "
                     "no tienen más Caído, y en octubre la arena se enfrió pero el Caído siguió alto. Tirado: "
                     "sujeción o contrapesos insuficientes y juntas de molde abiertas, sobre todo cuando la línea va "
                     "con prisa porque se juntan coladas. El metal no influye en ninguno de los dos.",
        "notas": ["El Caído subió en los meses de arena AF3 más caliente (jun–ago), pero eso solo no prueba causa: "
                  "la relación no aparece día a día dentro del mismo mes (ver sección Arena)."],
        "validar": ["Moldeo AF3: qué cambió desde junio de 2026 (personal, cierre, mantenimiento, herramental).",
                    "Moldeo AF3: revisar el proceso de la parte que más aporta al Caído.",
                    "Fusión y Moldeo: contrapesos y sujeción en los días con coladas juntas."],
        "arena": {"defectos": ["caido", "tirado"],
                  "mensual": ["TEMPERATURA ARENA AF3", "RESISTENCIA AF3 A LOS 15 MINUTOS",
                              "TIEMPO DE TRABAJO AF3 (Minutos y Segundos)"]},
    },
    {
        "clave": "llenado", "nombre": "Fierro frío y No lleno", "familia": "Llenado deficiente", "confianza": "alta",
        "defectos": [{"clave": "fierro_frio", "nombre": "Fierro frío", "nd": ["FIERRO FRIO"]},
                     {"clave": "no_lleno", "nombre": "No lleno", "nd": ["NO LLENO"]}],
        "senales": [("fierro_frio", "temp_bajo_min"), ("fierro_frio", "horno_excede_50"), ("fierro_frio", "ita_cementite_50"),
                    ("no_lleno", "ita_cementite_50"), ("no_lleno", "temp_bajo_min")],
        "hipotesis": "Fierro frío: las últimas piezas de cada olla se vacían ya frías, por vaciado lento o espera entre "
                     "moldes; afecta más a partes de pared delgada. No lleno no se relaciona con la temperatura: por "
                     "las causas que registra piso apunta a la cantidad de metal por olla y al sistema de llenado.",
        "notas": ["La evidencia más fuerte es a nivel pieza (temperatura con que se vació cada pieza): ver tabla de temperatura."],
        "validar": ["Fusión: temperatura de la última pieza de cada olla y tiempo entre primera y última pieza.",
                    "Fusión: cálculo de metal por olla en las partes con más No lleno."],
    },
    {
        "clave": "dimensional", "nombre": "Dimensional / maquinado", "familia": "Dimensional / maquinado", "confianza": "baja",
        "defectos": [{"clave": "dimensional", "nombre": "Familia dimensional / maquinado",
                      "nd": ["FUERA DE DIMENSIONES", "MAL MAQUINADO", "MARCAS MECANICAS", "MAL TERMINADO", "CRUZADO",
                             "HERRAMENTAL", "ACABADO SUPERFICIAL", "ESPESOR BAJO DE PIEZ", "MODELOS", "QUEBRADO", "CARGADO"]}],
        "senales": [("dimensional", "temp_bajo_min"), ("dimensional", "carga_fuera")],
        "hipotesis": "Sin hipótesis fuerte. Bajó a la mitad en 2026. Lo único llamativo es que una parte de las piezas "
                     "fuera de dimensiones y quebradas se vació abajo del mínimo de temperatura, pero son pocas piezas.",
        "notas": [],
        "validar": ["Calidad: revisar si el descenso de 2026 corresponde a una acción tomada."],
    },
]


def main(rapido=False):
    t0 = time.time()
    TABLA.parent.mkdir(parents=True, exist_ok=True)
    if rapido and TABLA.exists():
        C = json.load(open(TABLA, encoding="utf-8"))
    else:
        C = construir(str(TABLA))
    ids = [c["id"] for c in C]

    # piezas por colada y parte; tasa histórica por parte y defecto
    pp = defaultdict(dict)
    rp = defaultdict(lambda: defaultdict(int))
    for i in range(0, len(ids), 2000):
        lote = ",".join(map(str, ids[i:i + 2000]))
        for r in run(f"""SELECT IdColada, TRIM(no_parte) p, COUNT(DISTINCT IdTicket) n FROM betamega_aleks_idtickethistorial
                        WHERE IdColada IN ({lote}) GROUP BY 1,2""", {}):
            pp[r["IdColada"]][r["p"]] = int(r["n"])
        for r in run(f"""SELECT TRIM(a.no_parte) p, {_defecto_sql()} nd, COUNT(DISTINCT r.idticket) n
                        FROM rechazo r JOIN (SELECT DISTINCT IdTicket, no_parte FROM betamega_aleks_idtickethistorial
                            WHERE IdColada IN ({lote})) a ON a.IdTicket = r.idticket
                        WHERE r.tipo_rech='Producción' GROUP BY 1,2""", {}):
            rp[r["p"]][norm_defecto(r["nd"])] += int(r["n"])
    prod_p = defaultdict(int)
    for d in pp.values():
        for p, n in d.items():
            prod_p[p] += n
    mes_col = {c["id"]: c["mes"] for c in C}

    subdef = {d["clave"]: d for h in HIPOTESIS for d in h["defectos"]}
    for c in C:
        partes = pp.get(c["id"], {})
        c["_o"] = {k: sum(c["rech"].get(x, 0) for x in d["nd"]) for k, d in subdef.items()}
        c["_e"] = {k: sum(n * sum(rp[p].get(x, 0) for x in d["nd"]) / prod_p[p] for p, n in partes.items() if prod_p[p])
                   for k, d in subdef.items()}

    def oe(L, k):
        o = sum(c["_o"][k] for c in L)
        e = sum(c["_e"][k] for c in L)
        return {"oe": round(o / e, 2) if e >= MIN_ESPERADO else None, "obs": o, "esp": round(e, 1)}

    meses = sorted({c["mes"] for c in C})
    por_mes = defaultdict(list)
    for c in C:
        por_mes[c["mes"]].append(c)

    # piezas y rechazos por parte y año (para la tabla de partes)
    # moldeadas por parte y año de moldeo (misma base que el documento y el dashboard)
    pz_anio = {(str(r["y"]), r["p"]): int(r["n"]) for r in run("""
        SELECT YEAR(FHrMoldeo) y, TRIM(no_parte) p, COUNT(DISTINCT IdTicket) n
        FROM betamega_aleks_idtickethistorial WHERE FHrMoldeo >= :d GROUP BY 1,2""", {"d": meses[0] + "-01"})}
    rch_parte = run(f"""SELECT YEAR(r.fecha) y, TRIM(r.no_parte) p, {_defecto_sql()} nd, COUNT(*) n,
                         SUM(r.tipo='Desarrollo') dev
                       FROM rechazo r WHERE r.fecha >= :d AND r.tipo_rech='Producción' GROUP BY 1,2,3""",
                    {"d": meses[0] + "-01"})

    # registro de piso: causa genérica vs. detallada, responsable, área, causas detalladas
    piso = run(f"""SELECT {_defecto_sql()} nd, r.resp, r.area, r.causa, d.causaprimaria,
                     (d.causaprimaria IS NULL OR TRIM(d.causaprimaria) IN ('', 'NO DEFINIDO')) generica, COUNT(*) n
                   FROM rechazo r
                   LEFT JOIN (SELECT ident, MIN(causaprimaria) causaprimaria FROM piso_ia.defectos GROUP BY ident) d
                     ON d.ident = r.causa
                   WHERE r.fecha >= :d AND r.tipo_rech='Producción' GROUP BY 1,2,3,4,5,6""", {"d": meses[0] + "-01"})
    for r in piso:
        r["nd"] = norm_defecto(r["nd"])

    # temperatura de vaciado a nivel pieza: piezas buenas vs. rechazadas por defecto
    temp_pz = run(f"""
        SELECT x.nd, COUNT(*) n, ROUND(AVG(x.t - x.tmin), 1) sobre_min,
               ROUND(AVG(x.t > x.tmax) * 100, 1) pct_sobre_max, ROUND(AVG(x.t < x.tmin) * 100, 1) pct_bajo_min,
               MIN(x.f) desde, MAX(x.f) hasta
        FROM (SELECT v.vt_TemperaturaVaciado t, v.m_tmin tmin, v.m_tmax tmax, DATE(v.FHrVaciado) f,
                     CASE WHEN r.defecto IS NULL THEN 'OK' ELSE {_defecto_sql()} END nd
              FROM betamega_06_vaciado v
              LEFT JOIN rechazo r ON r.idticket = v.idTicket AND r.tipo_rech='Producción'
              WHERE v.FHrVaciado >= :d AND v.vt_TemperaturaVaciado BETWEEN 1100 AND 1600 AND v.m_tmin > 0) x
        GROUP BY x.nd HAVING n >= 80""", {"d": meses[0] + "-01"})
    temp_idx = {norm_defecto(r["nd"]): r for r in temp_pz}

    def cobertura(fuente):
        con = [c["mes"] for c in C if any(SENALES[s][2](c) is not None for s in SENALES if SENALES[s][1] == fuente)]
        cuenta = defaultdict(int)
        for m in con:
            cuenta[m] += 1
        buenos = [m for m in meses if cuenta[m] >= 0.3 * len(por_mes[m])]
        return {"desde": buenos[0] if buenos else None, "hasta": buenos[-1] if buenos else None,
                "meses_sin_dato": [m for m in meses if m not in buenos]}

    fuentes = {f: cobertura(f) for f in {s[1] for s in SENALES.values()}}

    # Arena (laboratorio) por día de moldeo; si falla, la vista sale sin esa sección en vez de no salir
    con_arena = [h for h in HIPOTESIS if h.get("arena")]
    try:
        arena_res, arena_meta = senales_arena({k: subdef[k]["nd"] for h in con_arena for k in h["arena"]["defectos"]})
        variables = sorted({v for h in con_arena for v in h["arena"]["mensual"]})
        arena_mes = defaultdict(dict)
        for r in run("""SELECT DATE_FORMAT(Fecha, '%Y-%m') m, TRIM(Variable) v,
                          AVG(CAST(REPLACE(Respuesta, ',', '') AS DECIMAL(12,4))) x,
                          AVG(CAST(REPLACE(Respuesta, ',', '') AS DECIMAL(12,4)) NOT BETWEEN LimiteInferior AND LimiteSuperior) * 100 fuera
                        FROM betamega_09_variablesdecontrol
                        WHERE Departamento='Laboratorio' AND TipoVariable='Cuantitativo' AND TRIM(Variable) IN :vs
                        GROUP BY 1, 2""", {"vs": tuple(variables)}):
            arena_mes[r["m"]][r["v"]] = {"prom": round(float(r["x"]), 2), "pct_fuera": round(float(r["fuera"] or 0))}
    except Exception as e:
        print("  arena ERR", str(e)[:200])
        arena_res, arena_meta, arena_mes = None, None, {}

    tarjetas = []
    for h in HIPOTESIS:
        defs = []
        for d in h["defectos"]:
            k = d["clave"]
            filas = [r for r in rch_parte if norm_defecto(r["nd"]) in d["nd"]]
            anual = []
            for y in sorted({m[:4] for m in meses}):
                n = sum(v for (yy, _), v in pz_anio.items() if yy == y)
                o = sum(int(r["n"]) for r in filas if str(r["y"]) == y)
                anual.append({"anio": y, "rechazos": o, "piezas": n, "tasa": round(o / n * 100, 2) if n else None})
            mensual = [{"mes": m, **oe(por_mes[m], k)} for m in meses]
            ult = max(a["anio"] for a in anual)
            tot_ult = sum(r["n"] for r in filas if str(r["y"]) == ult)
            agg = defaultdict(lambda: {"n": 0, "dev": 0})
            for r in filas:
                a = agg[(str(r["y"]), r["p"])]
                a["n"] += int(r["n"]); a["dev"] += int(r["dev"] or 0)
            partes = []
            for (y, p), a in sorted(agg.items(), key=lambda x: -x[1]["n"]):
                if y != ult or len(partes) >= 5:
                    continue
                prev = agg.get((str(int(ult) - 1), p), {"n": 0})
                mp, mprev = pz_anio.get((ult, p), 0), pz_anio.get((str(int(ult) - 1), p), 0)
                partes.append({"parte": p, "rechazos": a["n"], "pct_defecto": round(a["n"] / tot_ult * 100, 1) if tot_ult else None,
                               "moldeadas": mp, "tasa": round(a["n"] / mp * 100, 1) if mp else None,
                               "tasa_prev": round(prev["n"] / mprev * 100, 1) if mprev else None,
                               "desarrollo": a["dev"] > a["n"] / 2})
            ps = [r for r in piso if r["nd"] in d["nd"]]
            tot_ps = sum(r["n"] for r in ps)
            def top(campo, n=3):
                acc = defaultdict(int)
                for r in ps:
                    acc[(r[campo] or "—").strip() or "—"] += r["n"]
                return [{"valor": k2, "pct": round(v / tot_ps * 100)} for k2, v in sorted(acc.items(), key=lambda x: -x[1])[:n]]
            det = defaultdict(int)
            for r in ps:
                if not r["generica"]:
                    det[f'{r["causa"]} {r["causaprimaria"]}'.strip()] += r["n"]
            tp = [temp_idx[x] for x in d["nd"] if x in temp_idx]
            defs.append({
                "clave": k, "nombre": d["nombre"], "anual": anual, "mensual": mensual, "partes": partes,
                "piso": {"rechazos": tot_ps,
                         "pct_causa_generica": round(sum(r["n"] for r in ps if r["generica"]) / tot_ps * 100) if tot_ps else None,
                         "responsable": top("resp"), "area": top("area"),
                         "causas_detalladas": [{"causa": k2, "n": v} for k2, v in sorted(det.items(), key=lambda x: -x[1])[:4]]},
                "temp_pieza": ({"n": sum(int(t["n"]) for t in tp),
                                "pct_bajo_min": round(sum(float(t["pct_bajo_min"]) * int(t["n"]) for t in tp) / sum(int(t["n"]) for t in tp), 1),
                                "pct_sobre_max": round(sum(float(t["pct_sobre_max"]) * int(t["n"]) for t in tp) / sum(int(t["n"]) for t in tp), 1),
                                "sobre_min": round(sum(float(t["sobre_min"]) * int(t["n"]) for t in tp) / sum(int(t["n"]) for t in tp), 1)}
                               if tp else None),
            })
        senales = []
        for k, s in h["senales"]:
            etiqueta, fuente, fn = SENALES[s]
            L = [c for c in C if fn(c)]
            r = oe(L, k)
            senales.append({"defecto": subdef[k]["nombre"], "senal": s, "etiqueta": etiqueta, "fuente": fuente,
                            "coladas": len(L), **r})
        arena = None
        if h.get("arena") and arena_res is not None:
            ks = h["arena"]["defectos"]
            arena = {
                "meta": arena_meta,
                "por_defecto": [{"defecto": subdef[k]["nombre"], "senales": len(arena_res[k]),
                                 "fuertes": sum(1 for s in arena_res[k] if s["p"] < 0.002)} for k in ks],
                "senales": sorted(({**s, "defecto": subdef[k]["nombre"]} for k in ks for s in arena_res[k]),
                                  key=lambda s: s["p"])[:8],
                "variables": h["arena"]["mensual"],
                "mensual": [{"mes": m, "valores": arena_mes[m],
                             "oe": {subdef[k]["nombre"]: oe(por_mes[m], k)["oe"] for k in ks}}
                            for m in meses if m in arena_mes],
            }
        tarjetas.append({k: h[k] for k in ("clave", "nombre", "familia", "confianza", "hipotesis", "notas", "validar")}
                        | {"defectos": defs, "senales": senales, "arena": arena})

    ok = temp_idx.get("OK")
    gen = [r for r in piso]
    tot_gen = sum(r["n"] for r in gen)
    out = {
        "generado": datetime.now().isoformat(timespec="seconds"),
        "periodo": {"desde": meses[0], "hasta": meses[-1]},
        "n_coladas": len(C), "n_piezas": sum(c["n"] for c in C),
        "metodo": "O/E = rechazos observados / esperados según la mezcla de partes de esas coladas "
                  "(1.00 = lo normal para esas partes). Rechazo de producción del registro de piso (ia_fasa.rechazo).",
        "fuentes": fuentes,
        "rechazo_hasta": str(run("SELECT MAX(fecha) f FROM rechazo", {})[0]["f"]),
        "piso_pct_causa_generica": round(sum(r["n"] for r in gen if r["generica"]) / tot_gen * 100) if tot_gen else None,
        "temperatura_buenas": ({"n": int(ok["n"]), "pct_bajo_min": float(ok["pct_bajo_min"]),
                                "pct_sobre_max": float(ok["pct_sobre_max"]), "sobre_min": float(ok["sobre_min"]),
                                "desde": str(ok["desde"]), "hasta": str(ok["hasta"])} if ok else None),
        "tarjetas": tarjetas,
    }
    SALIDA.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"OK {SALIDA} · {len(C)} coladas · {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main(rapido="--rapido" in sys.argv)
