"""
¿A qué clientes les estamos fallando? (2026-11-08) -- tablero de la pestaña Clientes · Pedidos · Partes.

Pedido de JC: ver a qué clientes les fallamos más por rechazo, con la misma lógica de los tableros de
Control del Proceso y Calidad. La pestaña mostraba un % de rechazo por cliente que era la tasa
histórica de sus partes, mezclando lo que el cliente nunca ve con lo que sí le llega.

La base de rechazo SÍ distingue (betamega_01_rechazosbyidticket.IntExt, vía core.RECHAZO_PZAS_SQL):
  - EXTERNO ("Ext") = la pieza le llegó al cliente y la devolvió -> falla VISIBLE. Validado el
    8-nov: hay devoluciones todos los meses (70-220), defectos coherentes (sopladura, rechupe,
    caído) y llegan en promedio 61 días después del vaciado, como una devolución real.
    % DEVUELTO POR COHORTE (2026-11-09): de las piezas FUNDIDAS en la ventana que salieron buenas
    (vaciadas - rechazo interno), cuántas devolvió el cliente. Antes era devoluciones por fecha en
    que regresaron / piezas facturadas en la ventana: dos poblaciones distintas por el desfase de
    ~2 meses, y con ventanas cortas un cliente salía con más devuelto que embarcado (JC: Carrier
    24 de 27 en 6 meses, CNH 8 de 0). Validado con X62008071010 (Ingersoll), 2026: 260 vaciadas,
    109 internas -> 151 buenas (fdbase: 150 embarcadas) y 97 devueltas = 64 %. Los últimos ~2 meses
    siguen acumulando devoluciones y salen bajos.
  - INTERNO ("Int") = se detectó en planta -> falla OCULTA: el cliente no la ve, pero cuesta y
    obliga a fundir de más para surtir. Se cuenta por fecha de vaciado.

Por cliente (corex_test.modelos + clientes, main._resolver_clientes):
  - embarcado = piezas y toneladas facturadas en ia_fasa.fdbase en la ventana (volumen e
    importancia del cliente);
  - % devolución = devueltas / buenas de la misma cohorte (ver arriba);
  - costo de no calidad = (devueltas + rechazo interno) x peso x costo de venta por tonelada de
    Finanzas -- fdbase no trae precio, así que no hay venta en pesos por cliente;
  - piezas a fundir por pieza buena = 1 / (1 - tasa interna): presión oculta sobre capacidad;
  - partes con señal fuerte hoy (Resumen de control, 7 días): alerta temprana de lo que puede
    llegarle mal.
Tendencia = % devolución contra la ventana anterior del mismo largo. No hay fecha compromiso
en fdbase: la puntualidad de entrega no se puede medir todavía.

Devoluciones esperadas (2026-11-09, JC: ligar la lista de pedidos con el tablero): la devolución
llega ~2 meses después, así que lo embarcado en los últimos DIAS_EN_CAMINO días todavía puede
regresar. Esperadas = piezas embarcadas en ese lapso x tasa de devolución de la parte en la
ventana (si la parte tiene < MIN_EMB_TASA piezas embarcadas, la del cliente). Es una estimación
para anticiparse con el cliente, no un conteo.

/v3/clientes/ficha: la ficha única del cliente -- sus pedidos recientes (fdbase) con las partes
que ya devuelve, la señal fuerte de hoy y las devoluciones esperadas por pedido, y su tendencia
mensual (toneladas, % devuelto, % interno).
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
DIAS_EN_CAMINO = 60
MIN_EMB_TASA = 30


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
                     WHERE bRechazo = 1 AND IntExt = 'Ext' AND FHrVaciado >= :d AND FHrVaciado < :h
                     GROUP BY TRIM(No_Parte), nomDefecto""", {"d": d, "h": h}):
        x = g(r["np"]); n = int(r["n"] or 0); x["devueltas"] += n
        df = (r["df"] or "SIN DEFECTO").strip()
        x["defectos_ext"][df] = x["defectos_ext"].get(df, 0) + n
    return out


def _embarques_recientes(dias: int) -> dict:
    """{no_parte: piezas embarcadas en los últimos `dias`} (sin renglones de recargo -SURCH)."""
    d = (date.today() - timedelta(days=dias)).isoformat()
    return {r["np"]: int(r["n"] or 0) for r in run("""
        SELECT TRIM(no_parte) AS np, SUM(cant) AS n FROM fdbase
        WHERE fecha >= :d AND UPPER(no_parte) NOT LIKE '%SURCH%' GROUP BY TRIM(no_parte)""", {"d": d})}


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
    reciente = _embarques_recientes(DIAS_EN_CAMINO)
    clientes = []
    for c, r in A.items():
        if not (r["embarcadas"] or r["devueltas"]):
            continue
        buenas = r["vaciadas"] - r["rech_int"]
        pct_dev = round(r["devueltas"] / buenas * 100, 2) if buenas >= 20 else None
        tasa_int = r["rech_int"] / r["vaciadas"] if r["vaciadas"] else None
        b = B.get(c) or {}
        b_buenas = b.get("vaciadas", 0) - b.get("rech_int", 0)
        pct_ant = round(b["devueltas"] / b_buenas * 100, 2) if b_buenas >= 50 else None
        cambio = round(pct_dev - pct_ant, 2) if pct_dev is not None and pct_ant is not None and buenas >= 50 else None
        partes = sorted(r["partes"].values(), key=lambda p: (-p["devueltas"], -p["rech_int"]))
        tasa_cli = (r["devueltas"] / buenas) if buenas > 0 else 0
        esperadas = 0.0
        for p in partes:
            pb = p["vaciadas"] - p["rech_int"]
            p["tasa_devolucion"] = round(min(p["devueltas"] / pb if pb >= MIN_EMB_TASA else tasa_cli, 1) * 100, 2)
            p["embarcadas_en_camino"] = reciente.get(p["no_parte"], 0)
            p["dev_esperadas"] = round(p["embarcadas_en_camino"] * p["tasa_devolucion"] / 100, 1)
            esperadas += p["dev_esperadas"]
        clientes.append({
            "cliente": c, "piezas_embarcadas": r["embarcadas"], "ton_embarcadas": round(r["ton_embarcadas"], 1),
            "piezas_buenas": buenas,
            "devueltas": r["devueltas"], "pct_devolucion": pct_dev,
            "pct_devolucion_anterior": pct_ant, "cambio_pp": cambio,
            "tendencia": None if cambio is None else "empeora" if cambio >= 0.5 else "mejora" if cambio <= -0.5 else "estable",
            "rech_interno": r["rech_int"], "pct_interno": round(tasa_int * 100, 1) if tasa_int is not None else None,
            "fundir_por_pieza_buena": round(1 / (1 - tasa_int), 2) if tasa_int is not None and tasa_int < 1 else None,
            "contencion": round(r["rech_int"] / (r["rech_int"] + r["devueltas"]) * 100, 1) if (r["rech_int"] + r["devueltas"]) >= 10 else None,
            "ppm": round(r["devueltas"] / buenas * 1e6) if buenas >= 100 else None,
            "ton_no_calidad": round(r["ton_no_calidad"], 1),
            "costo_usd": round(r["ton_no_calidad"] * costo_t) if costo_t else None,
            "defectos_ext": sorted(({"defecto": k, "piezas": v} for k, v in r["defectos_ext"].items()), key=lambda x: -x["piezas"])[:5],
            "partes_senal_fuerte": [p["no_parte"] for p in partes if p["senal_fuerte_7d"]],
            "dev_esperadas": round(esperadas),
            "tasas_partes": {p["no_parte"]: p["tasa_devolucion"] for p in partes},
            "partes": partes[:15],
        })
    clientes.sort(key=lambda x: (-(x["devueltas"] or 0), -(x["costo_usd"] or 0)))
    ton = sum(c["ton_embarcadas"] for c in clientes)
    med_ton = sorted(c["ton_embarcadas"] for c in clientes)[len(clientes) // 2] if clientes else 0
    pz = sum(c["piezas_embarcadas"] for c in clientes)
    dev = sum(c["devueltas"] for c in clientes)
    bu = sum(max(c["piezas_buenas"], 0) for c in clientes)
    g_dev = round(dev / bu * 100, 2) if bu else None
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
            "devoluciones_esperadas": sum(c["dev_esperadas"] for c in clientes),
            "dias_en_camino": DIAS_EN_CAMINO,
        },
        "clientes": clientes,
        "notas": "Devolución = rechazo EXTERNO (betamega_01.IntExt='Ext'). % devuelto = de las piezas fundidas en la "
                 "ventana que salieron buenas, cuántas devolvió el cliente (misma población); los últimos ~2 meses "
                 "siguen acumulando devoluciones. fdbase no trae precio ni fecha compromiso.",
    }
    for k in [k for k in _CACHE if k[1] != clave[1]]:
        _CACHE.pop(k, None)
    _CACHE[clave] = res
    return res


@router.get("/v3/clientes/ficha")
def cliente_ficha(cliente: str = Query(...), ventana: int = Query(default=12, ge=3, le=24),
                  dias_pedidos: int = Query(default=90, ge=14, le=365)):
    """Ficha única del cliente: pedidos recientes con riesgo por parte y tendencia mensual."""
    tablero = clientes_fallas(ventana=ventana)
    c = next((x for x in tablero["clientes"] if x["cliente"] == cliente), None)
    tasas = (c or {}).get("tasas_partes") or {}
    tasa_cli = ((c or {}).get("pct_devolucion") or 0)
    senal = set((c or {}).get("partes_senal_fuerte") or [])
    partes = [r["np"] for r in run("""
        SELECT DISTINCT TRIM(m.NoParte) AS np FROM corex_test.modelos m
        JOIN corex_test.clientes cl ON cl.IdCliente = m.IdCliente WHERE cl.Cliente = :c""", {"c": cliente})]
    if not partes:
        return {"cliente": cliente, "pedidos": [], "tendencia": [], "motivo": "sin partes ligadas al cliente en Corex"}
    ph = ",".join(f":p{i}" for i in range(len(partes)))
    prm = {f"p{i}": p for i, p in enumerate(partes)}
    peso = _peso_partes()

    # Pedidos recientes: fdbase por pedido y parte
    d_ped = (date.today() - timedelta(days=dias_pedidos)).isoformat()
    camino = date.today() - timedelta(days=DIAS_EN_CAMINO)
    ped: dict = {}
    for r in run(f"""SELECT nopedido, TRIM(no_parte) AS np, MIN(fecha) AS f0, MAX(fecha) AS f1, SUM(cant) AS n,
                            SUM(CASE WHEN fecha >= :cam THEN cant ELSE 0 END) AS n_cam
                     FROM fdbase WHERE fecha >= :d AND UPPER(no_parte) NOT LIKE '%SURCH%' AND TRIM(no_parte) IN ({ph})
                     GROUP BY nopedido, TRIM(no_parte)""", {**prm, "d": d_ped, "cam": camino.isoformat()}):
        p = ped.setdefault(r["nopedido"], {"nopedido": r["nopedido"], "fecha_ini": str(r["f0"])[:10],
                                          "fecha_fin": str(r["f1"])[:10], "piezas": 0, "dev_esperadas": 0.0, "partes": []})
        p["fecha_ini"] = min(p["fecha_ini"], str(r["f0"])[:10]); p["fecha_fin"] = max(p["fecha_fin"], str(r["f1"])[:10])
        n, n_cam = int(r["n"] or 0), int(r["n_cam"] or 0)
        tasa = tasas.get(r["np"], tasa_cli)
        esp = round(n_cam * tasa / 100, 1)
        p["piezas"] += n; p["dev_esperadas"] += esp
        p["partes"].append({"no_parte": r["np"], "piezas": n, "tasa_devolucion": tasa,
                            "en_camino": n_cam, "dev_esperadas": esp, "senal_fuerte": r["np"] in senal})
    pedidos = sorted(ped.values(), key=lambda x: x["fecha_fin"], reverse=True)
    for p in pedidos:
        p["dev_esperadas"] = round(p["dev_esperadas"], 1)
        p["partes"].sort(key=lambda x: (-x["dev_esperadas"], -x["piezas"]))
        p["en_riesgo"] = any(x["senal_fuerte"] or x["tasa_devolucion"] >= 5 for x in p["partes"])

    # Tendencia mensual: toneladas embarcadas, % devuelto (por mes de devolución) y % interno
    desde = _meses_atras(date.today(), ventana - 1).isoformat()
    meses: dict = {}

    def m_(k):
        return meses.setdefault(k, {"mes": k, "piezas": 0, "ton": 0.0, "devueltas": 0, "vaciadas": 0, "rech_int": 0})
    for r in run(f"""SELECT DATE_FORMAT(fecha,'%Y-%m') AS m, TRIM(no_parte) AS np, SUM(cant) AS n FROM fdbase
                     WHERE fecha >= :d AND UPPER(no_parte) NOT LIKE '%SURCH%' AND TRIM(no_parte) IN ({ph})
                     GROUP BY m, TRIM(no_parte)""", {**prm, "d": desde}):
        x = m_(r["m"]); x["piezas"] += int(r["n"] or 0); x["ton"] += int(r["n"] or 0) * peso.get(r["np"], 0) / 1000
    for r in run(f"""SELECT DATE_FORMAT(FHrVaciado,'%Y-%m') AS m, COUNT(*) AS n FROM {RECHAZO_PZAS_SQL} x
                     WHERE bRechazo = 1 AND IntExt = 'Ext' AND FHrVaciado >= :d AND TRIM(No_Parte) IN ({ph})
                     GROUP BY m""", {**prm, "d": desde}):
        m_(r["m"])["devueltas"] += int(r["n"] or 0)
    for r in run(f"""SELECT DATE_FORMAT(FHrVaciado,'%Y-%m') AS m, SUM(pz) AS pz, SUM(bRechazo = 1 AND IntExt = 'Int') AS ri
                     FROM {RECHAZO_PZAS_SQL} x WHERE FHrVaciado >= :d AND TRIM(No_Parte) IN ({ph}) GROUP BY m""",
                 {**prm, "d": desde}):
        x = m_(r["m"]); x["vaciadas"] += int(r["pz"] or 0); x["rech_int"] += int(r["ri"] or 0)
    tendencia = []
    for k in sorted(meses):
        x = meses[k]
        x["ton"] = round(x["ton"], 1)
        buenas = x["vaciadas"] - x["rech_int"]
        x["pct_devuelto"] = round(x["devueltas"] / buenas * 100, 1) if buenas >= 20 else None
        # las devoluciones de lo fundido en los últimos ~2 meses todavía no llegan completas
        x["incompleto"] = k >= _meses_atras(date.today(), 2).strftime("%Y-%m")
        # mes en curso sin % interno: el rechazo se captura días después y saldría falsamente bajo
        x["pct_interno"] = (round(x["rech_int"] / x["vaciadas"] * 100, 1)
                            if x["vaciadas"] >= 20 and k != date.today().strftime("%Y-%m") else None)
        tendencia.append(x)
    return {"cliente": cliente, "dias_pedidos": dias_pedidos, "dias_en_camino": DIAS_EN_CAMINO,
            "pedidos": pedidos[:40], "n_pedidos": len(pedidos),
            "dev_esperadas": round(sum(p["dev_esperadas"] for p in pedidos), 1), "tendencia": tendencia,
            "nota": "Devoluciones esperadas = piezas embarcadas en los últimos 60 días x tasa de devolución de la "
                    "parte con este cliente (estimación). % devuelto por mes = de las piezas fundidas ese mes que "
                    "salieron buenas, cuántas devolvió el cliente (meses con >= 20); los últimos 2 meses siguen "
                    "acumulando devoluciones."}


# ══════════════════════════════════════════════════════════════════════════════
# Aseguramiento de calidad (2026-11-09): ¿qué tanto evitamos que le lleguen defectos al cliente?
# ══════════════════════════════════════════════════════════════════════════════
# Pedido de JC: un indicador de la capacidad de prevenir que piezas defectuosas lleguen al cliente
# (además del costo, cuesta reputación), en porcentajes, no en costo. Indicadores estándar:
#   - EFECTIVIDAD DE CONTENCIÓN = defectos atrapados en planta / (atrapados + devueltos por el
#     cliente). Al 9-nov (12 m): 83.9 % -> de cada 100 defectos, 16 se le escapan al cliente.
#   - PPM de cliente = devueltas por millón de piezas buenas enviadas (el idioma de los clientes).
#   - % de piezas que llegan bien = (buenas - devueltas) / buenas.
# Misma cohorte que el tablero de clientes (piezas FUNDIDAS en la ventana): la contención de los
# últimos ~2 meses sale inflada porque sus devoluciones todavía no llegan (se marcan incompletos).
# META_CONTENCION es propuesta de JC/MindBridge mientras Calidad fija la suya: verde >= 95 %,
# ámbar 85-95 %, rojo < 85 %.
META_CONTENCION = 95.0
AMBAR_CONTENCION = 85.0


def _semaforo(c):
    if c is None:
        return "GRIS"
    return "VERDE" if c >= META_CONTENCION else "AMBAR" if c >= AMBAR_CONTENCION else "ROJO"


@router.get("/v3/clientes/aseguramiento")
def aseguramiento_calidad(ventana: int = Query(default=12, ge=3, le=24)):
    clave = ("aseg", ventana, int(time.time() // _TTL))
    if clave in _CACHE:
        return _CACHE[clave]
    hoy = date.today()
    desde = _meses_atras(hoy, ventana - 1)
    d, h = desde.isoformat(), (hoy + timedelta(days=1)).isoformat()
    tot = run(f"""SELECT SUM(pz) AS pz, SUM(bRechazo = 1 AND IntExt = 'Int') AS i, SUM(bRechazo = 1 AND IntExt = 'Ext') AS e
                  FROM {RECHAZO_PZAS_SQL} x WHERE FHrVaciado >= :d AND FHrVaciado < :h""", {"d": d, "h": h})[0]
    pz, i, e = int(tot["pz"] or 0), int(tot["i"] or 0), int(tot["e"] or 0)
    buenas = pz - i
    cont = round(i / (i + e) * 100, 1) if (i + e) else None

    defectos = []
    for r in run(f"""SELECT nomDefecto AS df, SUM(IntExt = 'Int') AS i, SUM(IntExt = 'Ext') AS e
                     FROM {RECHAZO_PZAS_SQL} x WHERE bRechazo = 1 AND FHrVaciado >= :d AND FHrVaciado < :h
                     GROUP BY nomDefecto""", {"d": d, "h": h}):
        di, de = int(r["i"] or 0), int(r["e"] or 0)
        if di + de < 20:
            continue
        defectos.append({"defecto": (r["df"] or "SIN DEFECTO").strip(), "internos": di, "escapes": de,
                         "contencion": round(di / (di + de) * 100, 1),
                         "pct_de_escapes": round(de / e * 100, 1) if e else None})
    defectos.sort(key=lambda x: -x["escapes"])

    meses = []
    desde_t = _meses_atras(hoy, max(ventana, 12) - 1).isoformat()
    lim_inc = _meses_atras(hoy, 2).strftime("%Y-%m")
    for r in run(f"""SELECT DATE_FORMAT(FHrVaciado,'%Y-%m') AS m, SUM(pz) AS pz,
                            SUM(bRechazo = 1 AND IntExt = 'Int') AS i, SUM(bRechazo = 1 AND IntExt = 'Ext') AS e
                     FROM {RECHAZO_PZAS_SQL} x WHERE FHrVaciado >= :d AND FHrVaciado < :h GROUP BY m ORDER BY m""",
                 {"d": desde_t, "h": h}):
        mi, me, mp = int(r["i"] or 0), int(r["e"] or 0), int(r["pz"] or 0)
        mb = mp - mi
        meses.append({"mes": r["m"], "internos": mi, "escapes": me,
                      "contencion": round(mi / (mi + me) * 100, 1) if (mi + me) >= 20 else None,
                      "ppm": round(me / mb * 1e6) if mb >= 100 else None,
                      "incompleto": r["m"] >= lim_inc})

    fallas = clientes_fallas(ventana=ventana)
    por_cliente = sorted(({"cliente": c["cliente"], "contencion": c["contencion"], "ppm": c["ppm"],
                           "internos": c["rech_interno"], "escapes": c["devueltas"],
                           "semaforo": _semaforo(c["contencion"])}
                          for c in fallas["clientes"] if c["contencion"] is not None),
                         key=lambda x: (x["contencion"], -x["escapes"]))
    res = {
        "ventana": {"desde": d, "hasta": hoy.isoformat()},
        "meta": {"verde": META_CONTENCION, "ambar": AMBAR_CONTENCION,
                 "nota": "propuesta mientras Calidad fija la meta oficial"},
        "totales": {"vaciadas": pz, "internos": i, "escapes": e, "buenas": buenas,
                    "contencion": cont, "semaforo": _semaforo(cont),
                    "ppm": round(e / buenas * 1e6) if buenas else None,
                    "llegan_bien": round((buenas - e) / buenas * 100, 2) if buenas else None},
        "por_defecto": defectos[:12], "tendencia": meses, "por_cliente": por_cliente,
        "nota": "Contención = defectos atrapados en planta / (atrapados + devueltos por el cliente), de las piezas "
                "fundidas en la ventana. Los últimos ~2 meses salen inflados: sus devoluciones todavía no llegan.",
    }
    _CACHE[clave] = res
    return res
