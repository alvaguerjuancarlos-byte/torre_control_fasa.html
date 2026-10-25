"""
Torre de Control FASA — infraestructura compartida entre routers.

Contiene SOLO lo que se usa desde más de un dominio (verificado por grep de
call-sites antes de mover nada, no por intuición):
  - Conexión a BD (engine, run(), rango())
  - Mapeo de familias de defecto (FAMILIA_SQL)
  - Infraestructura de los 7 Puntos de Control (PC-1..PC-7): VentanaControl,
    CarrilControl, CARRILES, evaluar_carril(), evaluar_pc7()
  - _evaluar_coladas_ventana() — usado por V3 Gestión, Alertas/Protocolos y
    Agente Beta (criticas)
  - _buscar_protocolos() + _REDISENO_COMBOS — mismos tres consumidores
  - _referencia_actual_flujo() — usado por Agente Alfa y Plan de Producción

Nada en este módulo importa de backend.routers.* — los routers importan de
aquí, nunca al revés (evita import circular).
"""

import os
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple, List
from dataclasses import dataclass

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
    # pool_size=5 (default anterior) se quedaba corto: solo el tab Ejecutivo dispara
    # ~6-8 requests concurrentes que pegan a BD (resumen-anual, cierre-ciclo,
    # partes-scatter, pronostico, tonelaje-mensual, costo-no-calidad-mensual), y
    # con Flujo/Alertas/Pedidos sincronizados desde hoy (eSincronizarFechas())
    # pueden sumarse mas en simultaneo. La 6a+ request esperaba una conexion libre
    # -- si coincidia con pronostico (lento por ml_score() en loop, ver migracion
    # 2026-09-23) el bloqueo se sentia como "no responde" (JC, 2026-09-24: Tonelaje
    # se quedaba en "Cargando..." indefinido en Trimestre/Mes -- en realidad
    # pasaba tambien en Año, solo que con timing distinto). max_overflow da
    # holgura extra en picos sin mantener conexiones ociosas todo el tiempo.
    pool_size=15,
    max_overflow=10,
)


def run(sql: str, params: dict = None):
    with engine.connect() as cx:
        result = cx.execute(text(sql), params or {})
        keys = result.keys()
        return [dict(zip(keys, row)) for row in result.fetchall()]


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


# ══════════════════════════════════════════════════════════════════════════════
# TORRE V3 — Contrato de Visibilidad (los 7 Puntos de Control)
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
        "PC-1", "carga al horno — kg por material vs receta",
        "ia_fasa.betamega_00_cargasmetalicas (KgsReal vs KgsReceta por material)",
        VentanaControl("completitud", 100.0, 80.0, "pct"),
        "inicio_fusion",
        "% materiales en tolerancia (|real−receta| ≤ máx(0.5 % receta, resolución de báscula: "
        "1 kg metálicos / 0.01 kg ferroaleaciones)) ; 100 %→VERDE ; ≥80 %→ÁMBAR ; <80 %→ROJO",
        "P-01", 15, "activo",
    ),
    CarrilControl(
        "PC-2", "análisis químico — elementos clave en especificación",
        "corex_test.vistaanalisisquimicoparaitaca vía betamega_02_pks_idticketsidmuestras, spec de corex_test.clases",
        VentanaControl("completitud", 100.0, 80.0, "pct"),
        "fin_fusion",
        "% elementos clave (C,Si,Mg,S,P,Mn) con flag _F = pass ; 100%→VERDE ; ≥80%→ÁMBAR ; <80%→ROJO",
        "P-02", 10, "activo",
    ),
    CarrilControl(
        "PC-3", "calidad metalúrgica — análisis térmico ITACA (copa final antes de liberar)",
        "ia_fasa.itaca_acqresults (alertas 0-100, copa de análisis), ligada a la colada por hora",
        VentanaControl("completitud", 70.0, 50.0, "pct"),
        "fin_fusion",
        "peor alerta entre Porosity, MacroShrinkage, Cementite, Nodularization, InverseChill "
        "(100 = sin riesgo) ; ≥70→VERDE ; ≥50→ÁMBAR ; <50→ROJO",
        "P-03", 10, "activo",
    ),
    CarrilControl(
        "PC-4", "temperatura de vaciado (% tickets en rango)",
        "ia_fasa.betamega_06_vaciado.vt_TemperaturaVaciado + bCumplimientoTemperaturaOk",
        VentanaControl("completitud", 95.0, 80.0, "pct"),
        "inicio_vaciado",
        "% tickets con bCumplimientoTemperaturaOk = 1 ≥ 95 % → VERDE ; ≥ 80 % → ÁMBAR ; < 80 % → ROJO",
        "P-04", 5, "activo",
    ),
    CarrilControl(
        "PC-5", "desmoldeo oportuno (% piezas >= umbral por parte)",
        "betamega_08_ticketrutacritica.FHrMOLD + FHrDESM x corex_test.modelos.TiempoDesmoldeo",
        VentanaControl("completitud", 95.0, 80.0, "pct"),
        "fin_vaciado",
        "% piezas con tiempo_molde >= TiempoDesmoldeo por NoParte ; ≥95%→VERDE ; ≥80%→ÁMBAR ; <80%→ROJO",
        "P-05", 30, "activo",
    ),
    CarrilControl(
        "PC-6", "% rechazo del lote (qué, no causa metalúrgica)",
        "ia_fasa.betamega_03_coladasproceso.bRechazo",
        VentanaControl("porcentaje_max", 7.0, None, "pct"),
        "fin_vaciado",
        "pct_rechazo < 7 % → VERDE ; ≥ 7 % → ROJO", "P-06", 60, "activo",
    ),
    CarrilControl(
        "PC-7", "cierre de colada — piezas con limpieza registrada",
        "ia_fasa.betamega_08_ticketrutacritica.bLIMP — ventana: ≥95% piezas O ≤5 días desde vaciado",
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
        "latencia_objetivo": carril.latencia_objetivo,
        "motivo_gris":  None,
        "protocolo":    carril.protocolo if estado == "ROJO" else None,
        "detalle":      {},
    }


# Un mes con muestra grande y CERO rechazos es estadísticamente imposible (meses sanos traen
# 7-22%): significa que bRechazo no se está marcando (betamega_02_pks_ticketcorrida detenida
# desde 2026-07-09), no que la calidad fue perfecta. Regla compartida por Agente Beta
# (tendencia mensual), simulaciones y pronóstico -- dinámica, sin fechas hardcodeadas: en
# cuanto IT reactive el pipeline los meses vuelven a evaluarse solos.
UMBRAL_PIEZAS_MES_CONFIABLE = 200


def meses_rechazo_no_confiables(d: str, h: str) -> list:
    """Meses 'YYYY-MM' con producción en [d, h] cuyo rechazo no es evaluable
    (menos de UMBRAL_PIEZAS_MES_CONFIABLE piezas, o cero rechazos)."""
    rows = run("""
        SELECT DATE_FORMAT(FHrVaciado, '%Y-%m') AS mes, COUNT(*) AS n, SUM(bRechazo) AS rec
        FROM betamega_01_rechazosbyidticket
        WHERE FHrVaciado BETWEEN :d AND :h
        GROUP BY mes
    """, {"d": d, "h": h})
    return sorted(r["mes"] for r in rows
                  if int(r["n"] or 0) < UMBRAL_PIEZAS_MES_CONFIABLE or not int(r["rec"] or 0))


# PC-1: carga al horno (2026-10-13). Fuente betamega_00_cargasmetalicas, viva y validada
# contra corex_test.coladasdetalle (kg reales idénticos por colada). NO se usa su
# bFueraTolerancia: con PrcTolerancia=0.5 % (metálicos) / 0 (ferroaleaciones) y la
# receta escalada con decimales contra una báscula que pesa en kg enteros, marca
# diferencias de redondeo (75.73 vs 75 kg = "fuera") -- 62 % de coladas saldrían ROJO
# sin problema real. Regla elegida por JC: tolerancia = máx(0.5 % de la receta,
# resolución de báscula). Con ella: ~71 % VERDE, 27 % ÁMBAR, 2 % ROJO (rojos = 5-7 kg
# de diferencia en cargas de ~600 kg). La tolerancia real de ferroaleaciones sigue
# pendiente de Calidad (solicitud #10 del reporte a IT del 13-oct). La tabla trae
# duplicados exactos (colada 84420 x10) -- por eso el DISTINCT.
PC1_TOL_PCT = 0.005
PC1_RESOLUCION_KG = {"metalicos": 1.0, "ferroaleaciones": 0.01}


def _pc1_resolucion(tipo: str) -> float:
    return PC1_RESOLUCION_KG["ferroaleaciones" if (tipo or "").lower().startswith("ferro")
                             else "metalicos"]


def cargas_por_colada(ids: list) -> Dict[int, list]:
    """Materiales de la carga (receta vs real) por IdColada, ya evaluados contra la tolerancia de PC-1."""
    ids = [int(i) for i in ids if i]
    if not ids:
        return {}
    rows = run(f"""
        SELECT DISTINCT IdColada, Tipo, Clave, Descripcion, KgsReceta, KgsReal
        FROM betamega_00_cargasmetalicas
        WHERE IdColada IN ({",".join(str(i) for i in ids)})
    """, {})
    out: Dict[int, list] = {}
    for r in rows:
        receta, real = float(r["KgsReceta"] or 0), float(r["KgsReal"] or 0)
        tol = max(PC1_TOL_PCT * receta, _pc1_resolucion(r["Tipo"]))
        out.setdefault(r["IdColada"], []).append({
            "tipo": r["Tipo"], "clave": r["Clave"],
            "descripcion": (r["Descripcion"] or "").strip(),
            "kg_receta": round(receta, 2), "kg_real": round(real, 2),
            "dif_kg": round(real - receta, 2) + 0.0, "tol_kg": round(tol, 2),  # +0.0: evita "-0.0"
            "en_tolerancia": abs(real - receta) <= tol,
        })
    for comps in out.values():
        comps.sort(key=lambda c: (c["en_tolerancia"], -abs(c["dif_kg"])))
    return out


def resumen_carga(comps: list) -> dict:
    """% de materiales en tolerancia + totales de la carga; pct None si la colada no tiene carga registrada."""
    n = len(comps)
    n_ok = sum(1 for c in comps if c["en_tolerancia"])
    return {
        "n_materiales": n, "n_ok": n_ok,
        "pct_ok": round(n_ok / n * 100.0, 1) if n else None,
        "kg_receta": round(sum(c["kg_receta"] for c in comps), 1),
        "kg_real": round(sum(c["kg_real"] for c in comps), 1),
    }


# PC-3: análisis térmico ITACA (2026-10-25). itaca_acqresults es formato largo (una fila por
# código) sin índices ni llave a colada: una muestra = (aResDate, aResNum), aResNum es un
# consecutivo que se reinicia cada día. Cada análisis son dos copas a segundos de distancia:
# la de telurio (solidifica blanca, da calcC/calcSi/calcCeq) y la de análisis (trae los
# ALERT de metalurgia). Plan de Control OPE-FUS-010: "por copa, cada colada, Ítaca".
#
# La liga betamega_02_pks_idticketsidmuestras.idItaca está en ~0.6 % de tickets -- inútil.
# Se liga por HORA: la copa se toma 10-15 min antes de liberar (validado oct-2026), así que
# a cada copa le toca la colada ya en horno que se libera primero después de ella (≤40 min).
# Cubre ~85 % de coladas. Con varias copas, la última es el veredicto (las previas son
# re-pruebas tras corregir). Con dos hornos abiertos a la vez la asignación puede ser
# ambigua (~7 %) -- se marca, no se oculta.
#
# ALERT: 100 = sin riesgo, validado contra rechazo real abr-jun 2026 (750 coladas: Porosity
# baja → rechupe 4.2 % vs 0.2 % alta). Regla elegida por JC (opción A): peor de las 5
# alertas de PC3_ALERTAS ; ~65 % VERDE / 14 % ÁMBAR / 21 % ROJO en oct-2026. GraphiteExpansion
# y HypoEutecticHyper viven en otra escala (medianas 50 y 21): se muestran, no califican.
# Límites PROVISIONALES: el estándar real (TBL-OPE-IIP-001/002/003) no está en la BD.
# El fading de 13 min (OPE-FUS-021) NO es medible: FHrVaciado es hora de registro en lote.
PC3_ALERTAS = ["Porosity", "MacroShrinkage", "Cementite", "Nodularization", "InverseChill"]
PC3_INFO = ["GraphiteExpansion", "HypoEutecticHyper"]
_PC3_TE = ["calcC", "calcSi", "calcCeq"]
_PC3_VENTANA_MIN = 40

# itaca_acqresults no tiene índices (8.9M filas): cualquier consulta es barrido completo,
# 6-16 s. Caché por aResDate: un día cerrado ya no cambia (se guarda indefinido, tope de
# días); el día en curso se relee cada 5 min (llega ~1 copa cada 10 min).
_ITACA_DIA_CACHE: Dict[object, tuple] = {}
_ITACA_HOY_TTL_SEG = 300
_ITACA_MAX_DIAS = 120


def _itaca_copas(d1, d2) -> list:
    """Copas ITACA (análisis y telurio) pivoteadas, de aResDate d1 a d2, ordenadas por hora."""
    hoy = datetime.now().date()
    dias = [d1 + timedelta(days=i) for i in range((d2 - d1).days + 1)]
    faltan = [d for d in dias if d not in _ITACA_DIA_CACHE
              or (d >= hoy and time.time() - _ITACA_DIA_CACHE[d][0] > _ITACA_HOY_TTL_SEG)]
    if faltan:
        codigos = PC3_ALERTAS + PC3_INFO + _PC3_TE
        piv = ",".join(f"MAX(CASE WHEN aResCode='{k}' THEN aResValue END) AS `{k}`" for k in codigos)
        rows = run(f"""
            SELECT aResDate, aResNum, MIN(DataIns) AS ts, MAX(aResCode = 'Cementite') AS es_analisis, {piv}
            FROM itaca_acqresults
            WHERE aResDate IN ({",".join("'" + d.isoformat() + "'" for d in faltan)})
            GROUP BY aResDate, aResNum
            HAVING MAX(aResCode IN ('Cementite', 'calcC')) = 1
        """, {})
        por_dia: Dict[object, list] = {d: [] for d in faltan}
        for r in rows:
            k = r["aResDate"].date() if isinstance(r["aResDate"], datetime) else r["aResDate"]
            por_dia.setdefault(k, []).append(r)
        for d, lst in por_dia.items():
            _ITACA_DIA_CACHE[d] = (time.time(), lst)
        for d in sorted(_ITACA_DIA_CACHE)[:-_ITACA_MAX_DIAS]:
            _ITACA_DIA_CACHE.pop(d, None)
    return sorted((c for d in dias for c in _ITACA_DIA_CACHE.get(d, (0, []))[1]), key=lambda c: c["ts"])


def itaca_por_coladas(ids: list) -> Dict[int, dict]:
    """Copa ITACA final (y nº de re-pruebas) por idColada, ligada por hora contra betamega_00_coladaprocesodemoras."""
    ids = [int(i) for i in ids if i]
    if not ids:
        return {}
    propias = run(f"""
        SELECT idColada, FechaInicial, FechaLiberado FROM betamega_00_coladaprocesodemoras
        WHERE idColada IN ({",".join(str(i) for i in ids)}) AND FechaLiberado IS NOT NULL
    """, {})
    if not propias:
        return {}
    lo = min(r["FechaInicial"] or r["FechaLiberado"] for r in propias) - timedelta(minutes=_PC3_VENTANA_MIN)
    hi = max(r["FechaLiberado"] for r in propias)
    # Las coladas del otro horno compiten por las mismas copas: se asigna contra todas.
    todas = run("""
        SELECT idColada, FechaInicial, FechaLiberado FROM betamega_00_coladaprocesodemoras
        WHERE FechaLiberado BETWEEN :lo AND :hi2 AND FechaInicial IS NOT NULL
    """, {"lo": lo, "hi2": hi + timedelta(hours=2)})
    copas = [c for c in _itaca_copas(lo.date(), hi.date()) if lo <= c["ts"] <= hi]
    te = [c for c in copas if not c["es_analisis"]]
    margen = timedelta(minutes=5)
    asignadas: Dict[int, list] = {}
    for c in copas:
        if not c["es_analisis"]:
            continue
        cand = [x for x in todas if x["FechaInicial"] - margen <= c["ts"] <= x["FechaLiberado"]
                and (x["FechaLiberado"] - c["ts"]).total_seconds() <= _PC3_VENTANA_MIN * 60]
        if cand:
            best = min(cand, key=lambda x: x["FechaLiberado"])
            asignadas.setdefault(best["idColada"], []).append((c, len(cand) > 1))
    out = {}
    for cid in ids:
        lst = asignadas.get(cid)
        if not lst:
            continue
        c, ambigua = lst[-1]
        par = min((t for t in te if abs((t["ts"] - c["ts"]).total_seconds()) <= 120),
                  key=lambda t: abs((t["ts"] - c["ts"]).total_seconds()), default=None)
        alertas = {k: round(float(c[k]), 1) for k in PC3_ALERTAS if c[k] is not None}
        out[cid] = {
            "ts": c["ts"],
            "n_copas": len(lst),
            "asignacion_ambigua": ambigua,
            "alertas": alertas,
            "informativas": {k: round(float(c[k]), 1) for k in PC3_INFO if c[k] is not None},
            "te": {k: round(float(par[k]), 2) for k in _PC3_TE if par and par[k] is not None},
            "score": min(alertas.values()) if alertas else None,
            "peor": min(alertas, key=alertas.get) if alertas else None,
        }
    return out


# PC-2: columnas de la cadena viva de química (betamega_02_pks_idticketsidmuestras ->
# corex_test.vistaanalisisquimicoparaitaca -> corex_test.clases). El elemento "C" de la
# vista mapea a Ct_* en clases (C_* no existe; Ce_* es Carbono Equivalente, otro campo).
QUIM_SELECT_SQL = """
    v.C, v.Si, v.Mg, v.S, v.P, v.Mn,
    STR_TO_DATE(v.AnalysisTime, '%Y%m%d %H:%i:%s') AS AnalysisTime,
    cl.Clave AS clase,
    cl.Ct_F, cl.Ct_Fl, cl.Ct_Fx,
    cl.Si_F, cl.Si_Fl, cl.Si_Fx,
    cl.Mg_F, cl.Mg_Fl, cl.Mg_Fx,
    cl.S_F,  cl.S_Fl,  cl.S_Fx,
    cl.P_F,  cl.P_Fl,  cl.P_Fx,
    cl.Mn_F, cl.Mn_Fl, cl.Mn_Fx
"""
_QUIM_ELEMS = [("C", "Ct"), ("Si", "Si"), ("Mg", "Mg"), ("S", "S"), ("P", "P"), ("Mn", "Mn")]


def evaluar_quimica(q: dict) -> dict:
    """PASS/FAIL por elemento contra la ventana F±Fl de su clase; None si no aplica (Fx!='S')."""
    if not q:
        return {}
    out = {}
    for elem, pref in _QUIM_ELEMS:
        valor, centro = q.get(elem), q.get(f"{pref}_F")
        tol, aplica = q.get(f"{pref}_Fl"), q.get(f"{pref}_Fx")
        if aplica != "S" or valor is None or centro is None:
            out[elem] = None
        else:
            lo, hi = float(centro) - float(tol or 0), float(centro) + float(tol or 0)
            out[elem] = "PASS" if lo <= float(valor) <= hi else "FAIL"
    return out


def _evaluar_coladas_ventana(referencia: str, horas: int) -> list:
    """
    Evalúa los 7 carriles (PC-1..PC-7) para cada colada en la ventana [referencia-horas, referencia].
    Compartida por /v3/gestion/coladas, /v3/gestion/alertas y /api/beta/criticas para
    que los tres usen exactamente la misma lógica de evaluación (evita que diverjan).

    MIGRACIÓN (2026-09-23): fuente cambiada de cscmega_03coladacargametalica/
    cscmega_01rechazosbyidticket/cscmega_08ruta (las 3 congeladas desde 2025-12-29/
    dic-2025) a betamega_03_coladasproceso/betamega_08_ticketrutacritica (vivas hasta
    hoy). betamega_03 ya trae idTicket+bRechazo+No_Parte en la misma fila (a diferencia
    de cscmega_03+cscmega_01 que requerían join), así que ya no hace falta la tabla de
    rechazo aparte -- solo betamega_08 para las columnas de etapa (bMOLD/bVACI/bDESM/bLIMP).

    Detalle importante: betamega_03 tiene dos partes con distinta frescura -- los campos
    de TICKET (idTicket, FHrVaciado, bRechazo, No_Parte) están vivos hasta hoy, pero los
    de ENCABEZADO DE COLADA (Kilos, Status, FechaInicial, FechaLiberado) dependen de otra
    tabla fuente que se quedó pegada en 2026-08-17 -- para coladas más recientes que esa
    fecha esos 4 campos salen NULL. Por eso la ventana se ancla en FHrVaciado (vivo), no
    en FechaInicial, y inicio_fusion/fin_fusion caen a MIN/MAX(FHrVaciado) cuando
    FechaInicial/FechaLiberado son NULL (aproximación pedida por JC, sin marcarla aparte
    en la UI -- ver conversación 2026-09-23).

    Las llaves de los índices por colada (rec_idx/cierre_idx/molde5_idx/etapa_idx/sku_idx)
    se cambiaron de u_Colada a id_colada (c_IdColada): u_Colada es un consecutivo que se
    recicla (ver nota de Tonelaje en 2026-09-06), c_IdColada es el identificador real único
    y betamega_03 lo trae en cada fila -- evita el riesgo de mezclar dos coladas reales que
    compartan número de colada dentro de la misma ventana.
    """
    ref_dt = datetime.fromisoformat(referencia)
    desde_dt = ref_dt - timedelta(hours=horas)
    d = desde_dt.strftime("%Y-%m-%d %H:%M:%S")
    h = ref_dt.strftime("%Y-%m-%d %H:%M:%S")

    coladas = run("""
        SELECT c_IdColada AS id_colada,
               MAX(Colada) AS u_colada,
               MAX(Horno)  AS horno,
               COALESCE(MIN(FechaInicial), MIN(FHrVaciado))  AS inicio_fusion,
               COALESCE(MAX(FechaLiberado), MAX(FHrVaciado)) AS fin_fusion,
               MAX(Kilos)  AS kilos,
               MAX(Status) AS status
        FROM betamega_03_coladasproceso
        WHERE FHrVaciado BETWEEN :d AND :h
          AND c_IdColada > 0
        GROUP BY c_IdColada
        ORDER BY COALESCE(MIN(FechaInicial), MIN(FHrVaciado)) DESC
        LIMIT 60
    """, {"d": d, "h": h})

    if not coladas:
        return []

    id_list = ",".join(str(c["id_colada"]) for c in coladas if c["id_colada"]) or "0"
    u_list = ",".join(str(c["u_colada"]) for c in coladas if c["u_colada"]) or "0"

    rec_raw = run(f"""
        SELECT c_IdColada AS id_colada,
               COUNT(*) AS total,
               SUM(bRechazo) AS rechazos,
               ROUND(AVG(bRechazo)*100, 1) AS pct_rechazo,
               MAX(FHrVaciado) AS ts_ultimo
        FROM betamega_03_coladasproceso
        WHERE c_IdColada IN ({id_list})
        GROUP BY c_IdColada
    """, {})
    rec_idx = {r["id_colada"]: r for r in rec_raw}

    cierre_raw = run(f"""
        SELECT b3.c_IdColada AS id_colada,
               COUNT(*) AS total,
               SUM(b8.bLIMP) AS terminadas,
               ROUND(SUM(b8.bLIMP)/COUNT(*)*100, 1) AS pct_cierre
        FROM betamega_03_coladasproceso b3
        JOIN betamega_08_ticketrutacritica b8 ON b8.idTicket = b3.idTicket
        WHERE b3.c_IdColada IN ({id_list})
        GROUP BY b3.c_IdColada
    """, {})
    cierre_idx = {r["id_colada"]: r for r in cierre_raw}
    temp_raw = run(f"""
        SELECT c_IdColada,
               COUNT(*) AS n_tickets,
               SUM(bCumplimientoTemperaturaOk) AS n_ok,
               ROUND(AVG(CASE WHEN vt_TemperaturaVaciado BETWEEN 1100 AND 1600
                         THEN vt_TemperaturaVaciado END)) AS temp_avg,
               MIN(FHrVaciado) AS ts_primer_vaci
        FROM betamega_06_vaciado
        WHERE c_IdColada IN ({id_list})
          AND vt_TemperaturaVaciado IS NOT NULL
          AND vt_TemperaturaVaciado > 0
        GROUP BY c_IdColada
    """, {})
    temp_idx = {t["c_IdColada"]: t for t in temp_raw}

    # PC-2: batch química -- misma cadena viva que /v3/gestion/colada/{id}/carriles
    # (migrada 2026-10-01). cscmega_05aquimicoscumplimiento, la fuente vieja, fue borrada
    # de ia_fasa por IT (detectado 2026-10-10). Se toma el análisis más reciente por colada,
    # igual que el detalle, para que lista y detalle den el mismo color.
    quim_batch = run(f"""
        SELECT rb.c_IdColada AS id_colada, {QUIM_SELECT_SQL}
        FROM betamega_03_coladasproceso rb
        JOIN betamega_02_pks_idticketsidmuestras pm ON pm.IdTicket = rb.idTicket
        JOIN corex_test.vistaanalisisquimicoparaitaca v ON v.AnalysisNum = pm.IdAnalisisQuimico
        JOIN corex_test.clases cl ON cl.IdClase = rb.cm_idClase
        WHERE rb.c_IdColada IN ({id_list}) AND pm.IdAnalisisQuimico IS NOT NULL
    """, {})
    quim_idx = {}
    for q in quim_batch:
        prev = quim_idx.get(q["id_colada"])
        if prev is None or (q["AnalysisTime"] and (not prev["AnalysisTime"] or q["AnalysisTime"] > prev["AnalysisTime"])):
            quim_idx[q["id_colada"]] = q

    # PC-5 batch: tiempo en molde vs TiempoDesmoldeo por NoParte
    molde5_batch = run(f"""
        SELECT b3.c_IdColada AS id_colada,
               SUM(CASE WHEN b8.FHrMOLD IS NOT NULL AND b8.FHrDESM IS NOT NULL
                        AND m.TiempoDesmoldeo > 0
                   THEN 1 ELSE 0 END) AS con_umbral,
               SUM(CASE WHEN b8.FHrMOLD IS NOT NULL AND b8.FHrDESM IS NOT NULL
                        AND m.TiempoDesmoldeo > 0
                        AND TIMESTAMPDIFF(MINUTE, b8.FHrMOLD, b8.FHrDESM) / 60.0 >= m.TiempoDesmoldeo
                   THEN 1 ELSE 0 END) AS cumple
        FROM betamega_03_coladasproceso b3
        JOIN betamega_08_ticketrutacritica b8 ON b8.idTicket = b3.idTicket
        LEFT JOIN (
            SELECT NoParte, MAX(IdModelo) AS IdModelo
            FROM corex_test.modelos WHERE TiempoDesmoldeo > 0 GROUP BY NoParte
        ) best ON best.NoParte = b8.No_Parte
        LEFT JOIN corex_test.modelos m ON m.IdModelo = best.IdModelo
        WHERE b3.c_IdColada IN ({id_list})
          AND (b8.FHrMOLD IS NULL OR b8.FHrDESM IS NULL
               OR TIMESTAMPDIFF(HOUR, b8.FHrMOLD, b8.FHrDESM) BETWEEN 0 AND 72)
        GROUP BY b3.c_IdColada
    """, {})
    molde5_idx = {r["id_colada"]: r for r in molde5_batch}

    # Desglose por No. Parte (para el toggle "Por Colada / Por No. Parte" del Nivel 1)
    # betamega_03 ya trae No_Parte+bRechazo por ticket, no hace falta join para esto.
    sku_batch = run(f"""
        SELECT c_IdColada AS id_colada, No_Parte AS sku,
               COUNT(*) AS total, SUM(bRechazo) AS rechazos
        FROM betamega_03_coladasproceso
        WHERE c_IdColada IN ({id_list})
        GROUP BY c_IdColada, No_Parte
    """, {})
    sku_idx = {}
    for r in sku_batch:
        sku_idx.setdefault(r["id_colada"], []).append({
            "sku": r["sku"] or "—",
            "total": int(r["total"] or 0),
            "rechazos": int(r["rechazos"] or 0),
        })

    # Etapa de flujo (mismo criterio que /piso/flujo): basada en la pieza más
    # retrasada de la colada, vía flags bMOLD/bVACI/bDESM/bLIMP de betamega_08.
    etapa_batch = run(f"""
        SELECT b3.c_IdColada AS id_colada,
               SUM(b8.bMOLD) AS n_mold, SUM(b8.bVACI) AS n_vaci,
               SUM(b8.bDESM) AS n_desm, SUM(b8.bLIMP) AS n_limp
        FROM betamega_03_coladasproceso b3
        JOIN betamega_08_ticketrutacritica b8 ON b8.idTicket = b3.idTicket
        WHERE b3.c_IdColada IN ({id_list})
        GROUP BY b3.c_IdColada
    """, {})
    etapa_idx = {r["id_colada"]: r for r in etapa_batch}

    carga_idx = cargas_por_colada([c["id_colada"] for c in coladas])
    itaca_idx = itaca_por_coladas([c["id_colada"] for c in coladas])

    pc1 = next(c for c in CARRILES if c.id_pc == "PC-1")
    pc2 = next(c for c in CARRILES if c.id_pc == "PC-2")
    pc3 = next(c for c in CARRILES if c.id_pc == "PC-3")
    pc4 = next(c for c in CARRILES if c.id_pc == "PC-4")
    pc5 = next(c for c in CARRILES if c.id_pc == "PC-5")
    pc6 = next(c for c in CARRILES if c.id_pc == "PC-6")
    pc7 = next(c for c in CARRILES if c.id_pc == "PC-7")

    resultado = []
    for col in coladas:
        uid = col["u_colada"]
        cid = col["id_colada"]

        r   = rec_idx.get(cid, {})
        ci  = cierre_idx.get(cid, {})
        tr  = temp_idx.get(cid, {})

        pct_rec  = float(r["pct_rechazo"]) if r.get("total") else None
        pct_ci   = float(ci["pct_cierre"]) if ci.get("total") else None
        n_t      = int(tr.get("n_tickets") or 0)
        n_ok_t   = int(tr.get("n_ok") or 0)
        pct_temp = round(n_ok_t / n_t * 100.0, 1) if n_t else None

        q_elem   = evaluar_quimica(quim_idx.get(cid, {}))
        _q_total = sum(1 for v in q_elem.values() if v is not None)
        _q_pass  = sum(1 for v in q_elem.values() if v == "PASS")
        pct_quim = round(_q_pass / _q_total * 100.0, 1) if _q_total else None

        mo5 = molde5_idx.get(cid, {})
        con_umbral5 = int(mo5.get("con_umbral") or 0)
        cumple5     = int(mo5.get("cumple") or 0)
        pct_cumple5 = round(cumple5 / con_umbral5 * 100.0, 1) if con_umbral5 else None

        carga = resumen_carga(carga_idx.get(cid, []))
        ev1 = evaluar_carril(pc1, carga["pct_ok"], col["inicio_fusion"], cid)
        ev2 = evaluar_carril(pc2, pct_quim,    None,                    cid)
        it  = itaca_idx.get(cid, {})
        ev3 = evaluar_carril(pc3, it.get("score"), it.get("ts"),         cid)
        ev4 = evaluar_carril(pc4, pct_temp,    tr.get("ts_primer_vaci"), cid)
        ev5 = evaluar_carril(pc5, pct_cumple5, None,                    cid)
        ev6 = evaluar_carril(pc6, pct_rec,     r.get("ts_ultimo"),       cid)
        ev7 = evaluar_pc7(pct_ci, col["inicio_fusion"], cid)

        et = etapa_idx.get(cid, {})
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
            "PC-1": ev1["estado"], "PC-2": ev2["estado"], "PC-3": ev3["estado"],
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
            "pct_carga_ok":  carga["pct_ok"],
            "estados":       estados,
            "etapa":         etapa,
            "tiene_alerta":  tiene_alerta,
            "skus":          sku_idx.get(cid, []),
            "latencia_pc4_seg": ev4.get("latencia_seg"),
            "latencia_pc6_seg": ev6.get("latencia_seg"),
        })

    return resultado


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
    (gamamega_02_recomendacionescontrol, tabla chica — ~254 filas por período,
    se trae completa y se filtra en Python; DefectoCritico es CSV sin espacio
    garantizado). Compartido por /v3/gestion/alertas, /v3/gestion/colada/{id}/carriles
    (PC-6) y /api/beta/criticas para que los tres usen exactamente la misma lógica.

    Solo se usa el período más reciente (MAX(Periodo)) — la tabla acumula
    períodos mensuales (ej. 2607, 2608) sin sobrescribir el anterior, y ~39/254
    puntos de control cambian mes a mes (partes críticas distintas, puntos que
    entran/salen). Sin este filtro se mezclaban recomendaciones vigentes con
    obsoletas del mes previo. Hallazgo 2026-08-07, JC confirmó aplicar el fix.
    """
    if not defectos_unicos:
        return []
    puente = run("""
        SELECT Puesto, Proceso, ElementoControl, DefectoCritico, NoParteCritica
        FROM gamamega_02_recomendacionescontrol
        WHERE Periodo = (SELECT MAX(Periodo) FROM gamamega_02_recomendacionescontrol)
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


def historial_rechazo_partes(d_ini: str, d_fin: str) -> dict:
    """
    Agrega n/rechazos por No. Parte en [d_ini, d_fin] (strings 'YYYY-MM-DD HH:MM:SS'),
    fusionando dos fuentes por fecha de corte (2025-01-01) -- mismo patrón que la
    MIGRACIÓN 2026-09-23 de _evaluar_coladas_ventana(): la porción >= PIVOT viene de
    betamega_03_coladasproceso (viva hasta hoy, trae No_Parte+bRechazo+FHrVaciado en
    la misma fila); la porción < PIVOT de alfamega_01_rechazosbyidticket (histórico,
    no requiere fuente viva).

    Extraído de /v2/gestion/riesgo-partes (main.py), que hasta el 2026-09-27 seguía
    apuntando a cscmega_01rechazosbyidticket/cscmega_01resultadoidticket para la
    porción "reciente" -- esas tablas están congeladas desde 2025-12-29 (mismo
    congelamiento que ya se había corregido en _evaluar_coladas_ventana), así que
    "reciente" en realidad tenía tope en dic-2025 sin avisar. Compartido ahora por
    /v2/gestion/riesgo-partes y /api/riesgo-plan para que ninguno vuelva a divergir
    de la fuente viva.

    ACTUALIZACIÓN 2026-10-11: alfamega_01 (la rama < 2025) fue borrada por IT; betamega_03
    cubre 2023 -> hoy, así que queda una sola consulta. Se excluyen los meses sin rechazo
    confiable (meses_rechazo_no_confiables) -- sus ceros falsos bajaban la tasa de cada parte.
    """
    excluir = meses_rechazo_no_confiables(d_ini, d_fin)
    params = {"d": d_ini, "h": d_fin, **{f"m{i}": m for i, m in enumerate(excluir)}}
    filtro = ("AND DATE_FORMAT(FHrVaciado, '%Y-%m') NOT IN ("
              + ",".join(f":m{i}" for i in range(len(excluir))) + ")") if excluir else ""
    rows = run(f"""
        SELECT No_Parte AS no_parte, COUNT(*) AS n, SUM(bRechazo) AS rechazos
        FROM betamega_03_coladasproceso
        WHERE FHrVaciado BETWEEN :d AND :h
          AND No_Parte IS NOT NULL AND No_Parte != ''
          {filtro}
        GROUP BY No_Parte
    """, params)
    return {r["no_parte"]: {"n": int(r["n"] or 0), "rechazos": int(r["rechazos"] or 0)} for r in rows}


def tasa_shrunk(n: int, rechazos: int, global_rate: float, k: int = 75) -> tuple:
    """Shrinkage Bayesiano de la tasa de rechazo hacia la tasa global -- k=75 =>
    a n=k piezas el peso de la tasa propia de la parte es 50% (n=200 => 73%,
    n=500 => 87%). Devuelve (tasa_pct redondeada, peso_propio redondeado)."""
    if not n:
        return round(global_rate * 100, 1), 0.0
    w = n / (n + k)
    rate = (w * (rechazos / n) + (1 - w) * global_rate) * 100
    return round(rate, 1), round(w, 2)


def _referencia_actual_flujo() -> str:
    """Ancla la ventana al último día con volumen real de coladas (>=5), no al
    MAX(FHrVaciado) crudo — hay registros aislados posteriores al cierre
    real de datos que dejan la ventana vacía si se usan como ancla.
    Compartida por Agente Alfa y Plan de Producción.

    MIGRACIÓN (2026-09-23): de cscmega_03coladacargametalica (congelada desde
    2025-12-29 -- esta función SIEMPRE devolvía ~2025-12-19/20, sin importar la
    fecha real, porque esa era la fuente) a betamega_03_coladasproceso (viva
    hasta hoy). Ancla en FHrVaciado, no en FechaInicial -- mismo motivo que
    _evaluar_coladas_ventana() arriba: el encabezado de colada de betamega_03
    (FechaInicial/Kilos/Status) está pegado en 2026-08-17 aunque el ticket-level
    esté vivo.

    2026-10-25: ancla en el último día con >= 5 piezas VACIADAS de
    betamega_00_ticketsconvaciadodirecto (completa), porque Alfa, Beta y el seguimiento del plan
    ya cuentan vaciadas -- y betamega_03 tiene el hueco de cobertura jul-oct. Se busca solo en
    los últimos 30 días (0.7 s; un MAX() sobre toda la tabla, sin índice en FHrVaciado, tarda
    ~12 s, y la consulta vieja sobre betamega_03 ~15 s). Si no hay vaciado reciente se usa la
    lógica anterior. Caché de 10 min: la llaman todos los endpoints de Alfa/Beta/Plan y en frío
    la BD de producción tarda hasta ~13 s en contestarla."""
    hit = _REF_FLUJO_CACHE.get("ref")
    if hit and time.time() - hit[0] < _REF_FLUJO_TTL_SEG:
        return hit[1]
    ref = _referencia_actual_flujo_bd()
    _REF_FLUJO_CACHE["ref"] = (time.time(), ref)
    return ref


_REF_FLUJO_CACHE: Dict[str, tuple] = {}
_REF_FLUJO_TTL_SEG = 600


def _referencia_actual_flujo_bd() -> str:
    dias = run("""
        SELECT DATE(FHrVaciado) AS dia, COUNT(*) AS n, MAX(FHrVaciado) AS mx
        FROM betamega_00_ticketsconvaciadodirecto
        WHERE FHrVaciado >= NOW() - INTERVAL 30 DAY
        GROUP BY DATE(FHrVaciado)
    """, {})
    densos = [d["mx"] for d in dias if int(d["n"] or 0) >= 5 and d["mx"]]
    if densos:
        return max(densos).strftime("%Y-%m-%dT%H:%M")
    row = run("""
        SELECT MAX(b.FHrVaciado) AS mx FROM betamega_03_coladasproceso b
        JOIN (
            SELECT DATE(FHrVaciado) AS dia
            FROM betamega_03_coladasproceso
            WHERE c_IdColada > 0
            GROUP BY DATE(FHrVaciado)
            HAVING COUNT(*) >= 5
        ) denso ON DATE(b.FHrVaciado) = denso.dia
        WHERE b.c_IdColada > 0
    """, {})
    mx = row[0]["mx"] if row and row[0].get("mx") else None
    return mx.strftime("%Y-%m-%dT%H:%M") if mx else "2025-12-19T08:00"
