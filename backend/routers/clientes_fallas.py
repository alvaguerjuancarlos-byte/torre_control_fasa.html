"""
¿A qué clientes les estamos fallando? (2026-11-08) -- tablero de la pestaña Clientes · Pedidos · Partes.

Pedido de JC: ver a qué clientes les fallamos más por rechazo, con la misma lógica de los tableros de
Control del Proceso y Calidad. La pestaña mostraba un % de rechazo por cliente que era la tasa
histórica de sus partes, mezclando lo que el cliente nunca ve con lo que sí le llega.

La base de rechazo SÍ distingue (betamega_01_rechazosbyidticket.IntExt, vía core.RECHAZO_PZAS_SQL):
  - EXTERNO ("Ext") = la pieza le llegó al cliente y la devolvió -> falla VISIBLE. Validado el
    8-nov: hay devoluciones todos los meses (70-220), defectos coherentes (sopladura, rechupe,
    caído) y llegan en promedio 61 días después del vaciado, como una devolución real. Se cuenta
    por FechaRechazo (cuándo la devolvió) dentro de la ventana.
  - INTERNO ("Int") = se detectó en planta -> falla OCULTA: el cliente no la ve, pero cuesta y
    obliga a fundir de más para surtir. Se cuenta por fecha de vaciado.

Por cliente (corex_test.modelos + clientes, main._resolver_clientes):
  - embarcado = piezas y toneladas facturadas en ia_fasa.fdbase en la ventana;
  - % devolución = piezas devueltas / piezas embarcadas en la misma ventana (aproximado: fdbase
    no trae llave a la pieza devuelta y la devolución llega ~2 meses después);
  - costo de no calidad = (devueltas + rechazo interno) x peso x costo de venta por tonelada de
    Finanzas -- fdbase no trae precio, así que no hay venta en pesos por cliente;
  - piezas a fundir por pieza buena = 1 / (1 - tasa interna): presión oculta sobre capacidad;
  - partes con señal fuerte hoy (Resumen de control, 7 días): alerta temprana de lo que puede
    llegarle mal.
Tendencia = % devolución contra la ventana anterior del mismo largo. No hay fecha compromiso
en fdbase: la puntualidad de entrega no se puede medir todavía.
"""
import time
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Query

from backend.core import RECHAZO_PZAS_SQL, run
from backend.routers.costo_calidad import COSTO_TON_USD

router = APIRouter()
_CACHE: dict = {}
_TTL = 1800


def _meses_atras(d: date, n: int) -> date:
    y, m = d.year, d.month - n
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def _costo_ton(desde: date, hasta: date) -> Optional[float]:
    vals, y, m = [], desde.year, desde.month
    while (y, m) <= (hasta.year, hasta.month):
        v = (COSTO_TON_USD.get(y) or {}).get(m)
        if v:
            vals.append(v)
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return round(sum(vals) / len(vals), 2) if vals else None


def _por_parte(desde: date, hasta: date) -> dict:
    """{no_parte: {embarcadas, vaciadas, rech_int, devueltas, defectos_ext{}}} en [desde, hasta]."""
    d, h = desde.isoformat(), (hasta + timedelta(days=1)).isoformat()
    out: dict = {}

    def g(np_):
        return out.setdefault(np_, {"embarcadas": 0, "vaciadas": 0, "rech_int": 0, "devueltas": 0, "defectos_ext": {}})
    # sin los renglones de recargo "-SURCH": son cargos en la factura, no piezas (104 renglones y
    # 607 "piezas" dic-2025 a nov-2026; inflaban lo embarcado de Ingersoll Rand)
    for r in run("""SELECT TRIM(no_parte) AS np, SUM(cant) AS n FROM fdbase
                    WHERE fecha >= :d AND fecha < :h AND UPPER(no_parte) NOT LIKE '%SURCH%'
                    GROUP BY TRIM(no_parte)""", {"d": d, "h": h}):
        g(r["np"])["embarcadas"] += int(r["n"] or 0)
    for r in run(f"""SELECT TRIM(No_Parte) AS np, SUM(pz) AS pz, SUM(bRechazo = 1 AND IntExt = 'Int') AS ri
                     FROM {RECHAZO_PZAS_SQL} x WHERE FHrVaciado >= :d AND FHrVaciado < :h
                     GROUP BY TRIM(No_Parte)""", {"d": d, "h": h}):
        x = g(r["np"]); x["vaciadas"] += int(r["pz"] or 0); x["rech_int"] += int(r["ri"] or 0)
    for r in run(f"""SELECT TRIM(No_Parte) AS np, nomDefecto AS df, COUNT(*) AS n
                     FROM {RECHAZO_PZAS_SQL} x
                     WHERE bRechazo = 1 AND IntExt = 'Ext' AND FechaRechazo >= :d AND FechaRechazo < :h
                     GROUP BY TRIM(No_Parte), nomDefecto""", {"d": d, "h": h}):
        x = g(r["np"]); n = int(r["n"] or 0); x["devueltas"] += n
        df = (r["df"] or "SIN DEFECTO").strip()
        x["defectos_ext"][df] = x["defectos_ext"].get(df, 0) + n
    return out


def _peso_partes() -> dict:
    return {r["np"]: float(r["p"]) for r in run("""SELECT TRIM(NoParte) AS np, MAX(PesoKgs) AS p
                                                    FROM ia_fasa.modelos WHERE PesoKgs > 0 GROUP BY TRIM(NoParte)""", {})}


def _partes_senal_fuerte() -> dict:
    """{no_parte: n coladas con señal fuerte en 7 días} -- del Resumen de control (misma caché)."""
    try:
        from backend.core import _evaluar_coladas_ventana
        from backend.routers.control_resumen import PCS_PRODUCTO, _peor, senales_beta
        cs = _evaluar_coladas_ventana(datetime.now().strftime("%Y-%m-%dT%H:%M"), 168)
        grav = {c["id_colada"]: _peor([c["estados"][p] for p in PCS_PRODUCTO]) for c in cs}
        out: dict = {}
        for x in senales_beta(cs, grav).values():
            if x["senal"] == "fuerte":
                for p in x["partes_riesgo"]:
                    out[p["no_parte"]] = out.get(p["no_parte"], 0) + 1
        return out
    except Exception:
        return {}


@router.get("/v3/clientes/fallas")
def clientes_fallas(ventana: int = Query(default=12, ge=3, le=24)):
    from backend.main import _resolver_clientes  # diferido: main importa este router
    clave = (ventana, int(time.time() // _TTL))
    if clave in _CACHE:
        return _CACHE[clave]
    hoy = date.today()
    desde = _meses_atras(hoy, ventana - 1)
    desde_ant, hasta_ant = _meses_atras(desde, ventana), desde - timedelta(days=1)
    act, ant = _por_parte(desde, hoy), _por_parte(desde_ant, hasta_ant)
    peso, costo_t = _peso_partes(), _costo_ton(desde, hoy)
    senal = _partes_senal_fuerte()
    cli = _resolver_clientes(sorted(set(act) | set(ant)))

    def acumula(base: dict) -> dict:
        res: dict = {}
        for np_, x in base.items():
            c = cli.get(np_) or "Sin cliente resuelto"
            r = res.setdefault(c, {"embarcadas": 0, "ton_embarcadas": 0.0, "vaciadas": 0, "rech_int": 0,
                                   "devueltas": 0, "ton_no_calidad": 0.0, "defectos_ext": {}, "partes": {}})
            w = peso.get(np_, 0) / 1000
            r["embarcadas"] += x["embarcadas"]; r["ton_embarcadas"] += x["embarcadas"] * w
            r["vaciadas"] += x["vaciadas"]; r["rech_int"] += x["rech_int"]; r["devueltas"] += x["devueltas"]
            r["ton_no_calidad"] += (x["rech_int"] + x["devueltas"]) * w
            for df, n in x["defectos_ext"].items():
                r["defectos_ext"][df] = r["defectos_ext"].get(df, 0) + n
            if x["embarcadas"] or x["devueltas"] or x["rech_int"]:
                r["partes"][np_] = {"no_parte": np_, "embarcadas": x["embarcadas"], "devueltas": x["devueltas"],
                                    "vaciadas": x["vaciadas"], "rech_int": x["rech_int"],
                                    "senal_fuerte_7d": senal.get(np_, 0),
                                    "defecto_ext": max(x["defectos_ext"], key=x["defectos_ext"].get) if x["defectos_ext"] else None}
        return res

    A, B = acumula(act), acumula(ant)
    clientes = []
    for c, r in A.items():
        if not (r["embarcadas"] or r["devueltas"]):
            continue
        pct_dev = round(r["devueltas"] / r["embarcadas"] * 100, 2) if r["embarcadas"] else None
        tasa_int = r["rech_int"] / r["vaciadas"] if r["vaciadas"] else None
        b = B.get(c) or {}
        pct_ant = round(b["devueltas"] / b["embarcadas"] * 100, 2) if b.get("embarcadas", 0) >= 50 else None
        cambio = round(pct_dev - pct_ant, 2) if pct_dev is not None and pct_ant is not None and r["embarcadas"] >= 50 else None
        partes = sorted(r["partes"].values(), key=lambda p: (-p["devueltas"], -p["rech_int"]))
        clientes.append({
            "cliente": c, "piezas_embarcadas": r["embarcadas"], "ton_embarcadas": round(r["ton_embarcadas"], 1),
            "devueltas": r["devueltas"], "pct_devolucion": pct_dev,
            "pct_devolucion_anterior": pct_ant, "cambio_pp": cambio,
            "tendencia": None if cambio is None else "empeora" if cambio >= 0.5 else "mejora" if cambio <= -0.5 else "estable",
            "rech_interno": r["rech_int"], "pct_interno": round(tasa_int * 100, 1) if tasa_int is not None else None,
            "fundir_por_pieza_buena": round(1 / (1 - tasa_int), 2) if tasa_int is not None and tasa_int < 1 else None,
            "ton_no_calidad": round(r["ton_no_calidad"], 1),
            "costo_usd": round(r["ton_no_calidad"] * costo_t) if costo_t else None,
            "defectos_ext": sorted(({"defecto": k, "piezas": v} for k, v in r["defectos_ext"].items()), key=lambda x: -x["piezas"])[:5],
            "partes_senal_fuerte": [p["no_parte"] for p in partes if p["senal_fuerte_7d"]],
            "partes": partes[:15],
        })
    clientes.sort(key=lambda x: (-(x["devueltas"] or 0), -(x["costo_usd"] or 0)))
    ton = sum(c["ton_embarcadas"] for c in clientes)
    med_ton = sorted(c["ton_embarcadas"] for c in clientes)[len(clientes) // 2] if clientes else 0
    pz = sum(c["piezas_embarcadas"] for c in clientes)
    dev = sum(c["devueltas"] for c in clientes)
    g_dev = round(dev / pz * 100, 2) if pz else None
    for c in clientes:
        grande = c["ton_embarcadas"] >= med_ton
        alto = (c["pct_devolucion"] or 0) >= (g_dev or 0)
        c["cuadrante"] = "critico" if grande and alto else "atencion" if alto else "cuidar" if grande else "estable"
    res = {
        "ventana": {"desde": desde.isoformat(), "hasta": hoy.isoformat()},
        "ventana_anterior": {"desde": desde_ant.isoformat(), "hasta": hasta_ant.isoformat()},
        "costo_ton_usd": costo_t, "mediana_ton": round(med_ton, 1),
        "totales": {
            "clientes": len(clientes), "piezas_embarcadas": pz, "ton_embarcadas": round(ton, 1),
            "devueltas": dev, "pct_devolucion": g_dev,
            "clientes_con_devolucion": sum(1 for c in clientes if c["devueltas"]),
            "rech_interno": sum(c["rech_interno"] for c in clientes),
            "costo_usd": sum(c["costo_usd"] or 0 for c in clientes) if costo_t else None,
            "criticos": sum(1 for c in clientes if c["cuadrante"] == "critico"),
            "con_senal_fuerte": sum(1 for c in clientes if c["partes_senal_fuerte"]),
        },
        "clientes": clientes,
        "notas": "Devolución = rechazo EXTERNO (betamega_01.IntExt='Ext') por fecha de rechazo; interno por fecha de "
                 "vaciado. fdbase no trae precio ni fecha compromiso: costo con $/t de Finanzas y sin puntualidad.",
    }
    for k in [k for k in _CACHE if k[1] != clave[1]]:
        _CACHE.pop(k, None)
    _CACHE[clave] = res
    return res
