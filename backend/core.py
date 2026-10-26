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
import re
import threading
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
    motivo_sin_dato: Optional[str] = None,
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
        return _gris(motivo_sin_dato or "sin dato en el período evaluado")
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


# ══════════════════════════════════════════════════════════════════════════════
# COLADAS DESDE FUENTES COMPLETAS (2026-10-26)
# ══════════════════════════════════════════════════════════════════════════════
# Control del Proceso armaba la lista de coladas y casi todos sus PCs sobre
# betamega_03_coladasproceso, que desde jul-2026 trae ~1 % de los tickets (ticketsorigen
# sin liga a vaciado, auditoría 13-oct): ago-oct salían ~20 coladas al mes contra ~700
# reales. Ahora:
#   - Colada y sus piezas: betamega_aleks_idtickethistorial (ticket -> IdColada, completa;
#     mismo conteo de piezas que b03 en 168/188 coladas de may-2026, en el resto aleks trae
#     más) + hora de vaciado de betamega_00_ticketsconvaciadodirecto.
#   - Encabezado (fusión, kilos, horno): betamega_00_coladaprocesodemoras. Su Horno124 es la
#     misma numeración (1/2/4) que vdir.Horno y que el Note del espectrómetro (su Horno no:
#     1/2/3). La colada entra a la ventana por FechaLiberado; sin fila en demoras (hueco
#     18-ago -> 29-sep) por su primer vaciado.
#   - PC-2: análisis FINAL ligado por el Note del SpectroMAX, ver quimica_por_coladas().
#   - PC-4/5/6/7 siguen en las únicas fuentes que existen (b06 temperatura, b08 desmoldeo y
#     limpieza, b01 rechazo), pero solo califican si cubren al menos COBERTURA_MIN_PCT de las
#     piezas de la colada; si no, GRIS con la muestra a la vista. Un color calculado con 1 de
#     7 piezas no representa a la colada.
#   - PC-6 además queda GRIS en meses de rechazo no confiable (meses_rechazo_no_confiables):
#     bRechazo=0 ahí no significa "sin rechazo".
COBERTURA_MIN_PCT = 50.0

_NOCONF_CACHE: Dict[Tuple[str, str], tuple] = {}
_NOCONF_TTL_SEG = 3600


def meses_no_confiables_cache(d: str, h: str) -> set:
    """meses_rechazo_no_confiables() de los meses que toca [d, h], con caché de 1 h (~3 s en frío)."""
    k = (d[:7], h[:7])
    hit = _NOCONF_CACHE.get(k)
    if hit and time.time() - hit[0] < _NOCONF_TTL_SEG:
        return hit[1]
    meses = set(meses_rechazo_no_confiables(d[:7] + "-01 00:00:00", h))
    _NOCONF_CACHE[k] = (time.time(), meses)
    return meses


def _pct(a, b):
    return round(a / b * 100.0, 1) if b else None


def _ids_sql(ids) -> str:
    return ",".join(str(int(i)) for i in ids) or "0"


def coladas_en_ventana(d: str, h: str, limite: int = 60) -> list:
    """Coladas liberadas en [d, h] (o, sin fila en demoras, con primer vaciado en [d, h]), más recientes primero."""
    vac = run("""
        SELECT a.IdColada AS id, MIN(v.FHrVaciado) AS vac_ini, MAX(v.FHrVaciado) AS vac_fin,
               MAX(v.Horno) AS horno, MAX(v.Colada) AS u_colada
        FROM betamega_00_ticketsconvaciadodirecto v
        JOIN betamega_aleks_idtickethistorial a ON a.IdTicket = v.idTicket
        WHERE v.FHrVaciado BETWEEN :d AND :h AND a.IdColada > 0
        GROUP BY a.IdColada
    """, {"d": d, "h": h})
    lib = run("""
        SELECT idColada FROM betamega_00_coladaprocesodemoras
        WHERE FechaLiberado BETWEEN :d AND :h AND idColada > 0
    """, {"d": d, "h": h})
    ids = {r["id"] for r in vac} | {r["idColada"] for r in lib}
    if not ids:
        return []
    dem = {r["idColada"]: r for r in run(f"""
        SELECT idColada, Colada, Horno124, Kilos, Status, FechaInicial, FechaLiberado
        FROM betamega_00_coladaprocesodemoras WHERE idColada IN ({_ids_sql(ids)})
    """, {})}
    vac_idx = {r["id"]: r for r in vac}
    dt_d, dt_h = datetime.fromisoformat(d), datetime.fromisoformat(h)
    out = []
    for cid in ids:
        dm, vc = dem.get(cid) or {}, vac_idx.get(cid) or {}
        ancla = dm.get("FechaLiberado") or vc.get("vac_ini")
        if ancla is None or not (dt_d <= ancla <= dt_h):
            continue
        out.append({
            "id_colada": cid,
            "u_colada": dm.get("Colada") or vc.get("u_colada"),
            "horno": dm.get("Horno124") or vc.get("horno"),
            "inicio_fusion": dm.get("FechaInicial") or vc.get("vac_ini"),
            "fin_fusion": dm.get("FechaLiberado") or vc.get("vac_fin"),
            "kilos": dm.get("Kilos"),
            "status": dm.get("Status"),
        })
    out.sort(key=lambda c: c["inicio_fusion"], reverse=True)
    return out[:limite]


def colada_por_id(id_colada: int) -> Optional[dict]:
    """Encabezado de una colada (mismo formato que coladas_en_ventana) por IdColada."""
    dm = run("""
        SELECT idColada, Colada, Horno124, Kilos, Status, FechaInicial, FechaLiberado
        FROM betamega_00_coladaprocesodemoras WHERE idColada = :id LIMIT 1
    """, {"id": id_colada})
    dm = dm[0] if dm else {}
    vc = run("""
        SELECT MIN(v.FHrVaciado) AS vac_ini, MAX(v.FHrVaciado) AS vac_fin,
               MAX(v.Horno) AS horno, MAX(v.Colada) AS u_colada, COUNT(a.IdTicket) AS n
        FROM betamega_aleks_idtickethistorial a
        LEFT JOIN betamega_00_ticketsconvaciadodirecto v ON v.idTicket = a.IdTicket
        WHERE a.IdColada = :id
    """, {"id": id_colada})
    vc = vc[0] if vc and vc[0].get("n") else {}
    if not dm and not vc:
        return None
    return {
        "id_colada": id_colada,
        "u_colada": dm.get("Colada") or vc.get("u_colada"),
        "horno": dm.get("Horno124") or vc.get("horno"),
        "inicio_fusion": dm.get("FechaInicial") or vc.get("vac_ini"),
        "fin_fusion": dm.get("FechaLiberado") or vc.get("vac_fin"),
        "kilos": dm.get("Kilos"),
        "status": dm.get("Status"),
    }


def piezas_de_coladas(ids: list) -> Dict[int, list]:
    """Piezas (tickets) por IdColada con su vaciado, rechazo (b01) y ruta (b08); en_b01/en_b08 marcan si la fila existe."""
    ids = [int(i) for i in ids if i]
    if not ids:
        return {}
    tk = run(f"""
        SELECT IdTicket, IdColada, IdClase, TRIM(no_parte) AS no_parte
        FROM betamega_aleks_idtickethistorial WHERE IdColada IN ({_ids_sql(ids)})
    """, {})
    if not tk:
        return {}
    tl = ",".join("'" + str(t["IdTicket"]).replace("'", "") + "'" for t in tk)
    vac = {r["idTicket"]: r["FHrVaciado"] for r in run(f"""
        SELECT idTicket, FHrVaciado FROM betamega_00_ticketsconvaciadodirecto WHERE idTicket IN ({tl})
    """, {})}
    rec = {r["idTicket"]: r for r in run(f"""
        SELECT idTicket, bRechazo, nomDefecto FROM betamega_01_rechazosbyidticket WHERE idTicket IN ({tl})
    """, {})}
    ruta = {r["idTicket"]: r for r in run(f"""
        SELECT idTicket, bMOLD, bVACI, bDESM, bLIMP, FHrMOLD, FHrDESM, FHrLIMP
        FROM betamega_08_ticketrutacritica WHERE idTicket IN ({tl})
    """, {})}
    out: Dict[int, list] = {}
    for t in tk:
        k = t["IdTicket"]
        r, ru = rec.get(k), ruta.get(k)
        out.setdefault(t["IdColada"], []).append({
            "idTicket": k, "no_parte": t["no_parte"] or "—", "id_clase": t["IdClase"],
            "ts_vaciado": vac.get(k),
            "en_b01": r is not None,
            "bRechazo": int(r["bRechazo"] or 0) if r else 0,
            "nomDefecto": r["nomDefecto"] if r else None,
            "en_b08": ru is not None,
            **{c: int((ru or {}).get(c) or 0) for c in ("bMOLD", "bVACI", "bDESM", "bLIMP")},
            **{c: (ru or {}).get(c) for c in ("FHrMOLD", "FHrDESM", "FHrLIMP")},
        })
    return out


def tiempo_desmoldeo_partes(partes) -> Dict[str, float]:
    """TiempoDesmoldeo (h) por No. Parte, del modelo más reciente en corex_test.modelos que lo tenga."""
    partes = sorted({p for p in partes if p and p != "—"})
    if not partes:
        return {}
    params = {f"p{i}": p for i, p in enumerate(partes)}
    rows = run(f"""
        SELECT TRIM(m.NoParte) AS no_parte, m.TiempoDesmoldeo AS td
        FROM corex_test.modelos m
        JOIN (SELECT NoParte, MAX(IdModelo) AS IdModelo FROM corex_test.modelos
              WHERE TiempoDesmoldeo > 0 GROUP BY NoParte) best ON best.IdModelo = m.IdModelo
        WHERE TRIM(m.NoParte) IN ({",".join(":" + k for k in params)})
    """, params)
    return {r["no_parte"]: float(r["td"]) for r in rows}


def temperatura_por_coladas(ids: list) -> Dict[int, dict]:
    """PC-4: tickets con temperatura de vaciado (betamega_06) por colada."""
    ids = [int(i) for i in ids if i]
    if not ids:
        return {}
    return {r["id_colada"]: r for r in run(f"""
        SELECT c_IdColada AS id_colada, COUNT(*) AS n_tickets,
               SUM(bCumplimientoTemperaturaOk) AS n_ok,
               ROUND(AVG(CASE WHEN vt_TemperaturaVaciado BETWEEN 1100 AND 1600
                         THEN vt_TemperaturaVaciado END)) AS temp_avg,
               MIN(m_tmin) AS tmin, MAX(m_tmax) AS tmax,
               MIN(FHrVaciado) AS ts_primer_vaci
        FROM betamega_06_vaciado
        WHERE c_IdColada IN ({_ids_sql(ids)})
          AND vt_TemperaturaVaciado IS NOT NULL AND vt_TemperaturaVaciado > 0
        GROUP BY c_IdColada
    """, {})}


def resumen_piezas(piezas: list, td_parte: Dict[str, float]) -> dict:
    """Métricas por colada a partir de piezas_de_coladas(): rechazo (PC-6), desmoldeo (PC-5),
    cierre (PC-7), etapa, SKUs. Cada métrica trae su base (piezas con la fila fuente)."""
    n = len(piezas)
    b01 = [p for p in piezas if p["en_b01"]]
    b08 = [p for p in piezas if p["en_b08"]]
    rech = sum(p["bRechazo"] for p in b01)
    combos: Dict[tuple, int] = {}
    for p in b01:
        if p["bRechazo"] and p["nomDefecto"]:
            k = (p["nomDefecto"], p["no_parte"])
            combos[k] = combos.get(k, 0) + 1
    tiempos = []
    for p in b08:
        if p["FHrMOLD"] and p["FHrDESM"]:
            hrs = (p["FHrDESM"] - p["FHrMOLD"]).total_seconds() / 3600.0
            if 0 <= hrs <= 72:
                tiempos.append((hrs, td_parte.get(p["no_parte"])))
    con_umbral = [(x, td) for x, td in tiempos if td]
    cumple = sum(1 for x, td in con_umbral if x >= td)
    terminadas = sum(p["bLIMP"] for p in b08)
    skus: Dict[str, dict] = {}
    for p in piezas:
        s = skus.setdefault(p["no_parte"], {"sku": p["no_parte"], "total": 0, "rechazos": 0})
        s["total"] += 1
        s["rechazos"] += p["bRechazo"]
    ts_vac = [p["ts_vaciado"] for p in piezas if p["ts_vaciado"]]
    ts_limp = [p["FHrLIMP"] for p in b08 if p["FHrLIMP"]]
    return {
        "piezas": n,
        "vaciadas": len(ts_vac),
        "ts_primer_vaciado": min(ts_vac) if ts_vac else None,
        "ts_ultimo_vaciado": max(ts_vac) if ts_vac else None,
        "n_rechazo": len(b01),
        "rechazos": rech, "pct_rechazo": _pct(rech, len(b01)),
        "combos_defecto": sorted(combos.items(), key=lambda kv: -kv[1]),
        "n_ruta": len(b08),
        "terminadas": terminadas, "pct_cierre": _pct(terminadas, len(b08)),
        "ts_ultimo_limp": max(ts_limp) if ts_limp else None,
        "n_desmoldeo": len(tiempos),
        "con_umbral": len(con_umbral), "cumple_desm": cumple,
        "pct_cumple_desm": _pct(cumple, len(con_umbral)),
        "avg_h": round(sum(x for x, _ in tiempos) / len(tiempos), 2) if tiempos else None,
        "min_h": round(min(x for x, _ in tiempos), 2) if tiempos else None,
        "max_h": round(max(x for x, _ in tiempos), 2) if tiempos else None,
        "n_desm_ruta": sum(p["bDESM"] for p in b08),
        "skus": sorted(skus.values(), key=lambda s: -s["total"]),
    }


# PC-2 (2026-10-26): la liga oficial idTicket -> IdAnalisisQuimico
# (betamega_02_pks_idticketsidmuestras) viene de la misma familia derivada con el hueco
# jul-oct (21 de 516 coladas en oct-2026). La vista del SpectroMAX sí está viva, y el
# operador escribe en Note "<aaaammddhhmmss>FINAL<colada><clase>F1<horno>" (ej.
# "...FINAL303100-70-03F14" = colada 303, clase 100-70-03, horno 4). Se liga por
# (horno, número de colada) contra las coladas evaluadas, a ±_PC2_MARGEN_H de su liberación
# (el número se recicla, pero tarda semanas; la hora del análisis no sirve sola: a veces se
# capturan varios FINAL en lote horas después). Validado en may-2026 contra la liga oficial:
# mismo análisis en 344 de 346 coladas. Solo FINAL: BASE es antes de ajustar/inocular (Mg
# 0.001 en nodular) y saldría FAIL siempre. Se prefiere el FINAL "de colada" (sufijo F) sobre
# el de pieza/probeta (sufijo P/PC), y entre ellos el más reciente (re-análisis tras ajuste).
# El spec sale de corex_test.clases por la clase de las piezas (aleks.IdClase).
_PC2_MARGEN_H = 36
_RX_NOTE_FINAL = re.compile(r"^\d{14}FINAL(.+)$")
_RX_NOTE_HORNO = re.compile(r"[BF]1(\d)$")
_CLASES_CACHE: Dict[str, object] = {}


def _clases() -> dict:
    """corex_test.clases completa (spec de química por IdClase), caché de 1 h."""
    hit = _CLASES_CACHE.get("t")
    if hit and time.time() - hit < 3600:
        return _CLASES_CACHE["por_id"]
    cols = ",".join(f"{p}_F, {p}_Fl, {p}_Fx" for _, p in _QUIM_ELEMS)
    rows = run(f"SELECT IdClase, Clave, {cols} FROM corex_test.clases", {})
    _CLASES_CACHE["por_id"] = {r["IdClase"]: r for r in rows}
    _CLASES_CACHE["claves"] = sorted({(r["Clave"] or "").strip() for r in rows
                                      if (r["Clave"] or "").strip()[:1].isdigit()}, key=len, reverse=True)
    _CLASES_CACHE["t"] = time.time()
    return _CLASES_CACHE["por_id"]


def quimica_por_coladas(coladas: list, clase_por_colada: Dict[int, int]) -> Dict[int, dict]:
    """Análisis FINAL del SpectroMAX por colada (dict listo para evaluar_quimica), ligado por el Note."""
    cols = [c for c in coladas if c.get("u_colada") and c.get("horno") and c.get("fin_fusion")]
    if not cols:
        return {}
    clases = _clases()
    claves = _CLASES_CACHE["claves"]
    margen = timedelta(hours=_PC2_MARGEN_H)
    lo = min(c["inicio_fusion"] or c["fin_fusion"] for c in cols) - margen
    hi = max(c["fin_fusion"] for c in cols) + margen
    rows = run("""
        SELECT AnalysisNum, Note, SourceCode, C, Si, Mg, S, P, Mn,
               STR_TO_DATE(AnalysisTime, '%Y%m%d %H:%i:%s') AS AnalysisTime
        FROM corex_test.vistaanalisisquimicoparaitaca
        WHERE AnalysisTime BETWEEN :lo AND :hi AND Note LIKE '%FINAL%'
    """, {"lo": lo.strftime("%Y%m%d %H:%M:%S"), "hi": hi.strftime("%Y%m%d %H:%M:%S")})
    idx: Dict[tuple, list] = {}
    for c in cols:
        idx.setdefault((int(c["horno"]), int(c["u_colada"])), []).append(c)
    mejor: Dict[int, tuple] = {}
    for a in rows:
        m = _RX_NOTE_FINAL.match((a["Note"] or "").strip())
        if not m or not a["AnalysisTime"]:
            continue
        cuerpo = m.group(1)
        mh = _RX_NOTE_HORNO.search(cuerpo)
        sc = (a["SourceCode"] or "").strip()
        hornos = [int(mh.group(1))] if mh else ([int(sc)] if sc.isdigit() else [1, 2, 4])
        cand = []
        for k in range(1, 5):
            num, resto = cuerpo[:k], cuerpo[k:]
            if not num.isdigit():
                break
            clave = next((cl for cl in claves if resto.startswith(cl)), None)
            if clave is None:
                continue
            for hh in hornos:
                for c in idx.get((hh, int(num)), []):
                    if abs((a["AnalysisTime"] - c["fin_fusion"]).total_seconds()) <= margen.total_seconds():
                        cand.append((c, clave, resto[len(clave):]))
        if not cand:
            continue
        c, clave, sufijo = min(cand, key=lambda x: abs((a["AnalysisTime"] - x[0]["fin_fusion"]).total_seconds()))
        de_colada = sufijo[:1].upper() == "F"
        orden = (de_colada, a["AnalysisTime"])
        prev = mejor.get(c["id_colada"])
        if prev is None or orden > prev[0]:
            mejor[c["id_colada"]] = (orden, a, clave, de_colada)
    out = {}
    for cid, (_, a, clave_note, de_colada) in mejor.items():
        spec = clases.get(clase_por_colada.get(cid)) or {}
        out[cid] = {
            **{k: a[k] for k in ("C", "Si", "Mg", "S", "P", "Mn")},
            **{k: v for k, v in spec.items() if k not in ("IdClase", "Clave")},
            "AnalysisTime": a["AnalysisTime"],
            "AnalysisNum": a["AnalysisNum"],
            "note": (a["Note"] or "").strip(),
            "clase": (spec.get("Clave") or "").strip() or None,
            "clase_note": clave_note,
            "tipo_analisis": "colada" if de_colada else "pieza/probeta",
        }
    return out


def clase_dominante(piezas: list):
    cuenta: Dict[int, int] = {}
    for p in piezas:
        if p["id_clase"]:
            cuenta[p["id_clase"]] = cuenta.get(p["id_clase"], 0) + 1
    return max(cuenta, key=cuenta.get) if cuenta else None


def _motivo_cobertura(que: str, n_fuente: int, n: int, fuente: str) -> Optional[str]:
    """Motivo GRIS si la fuente cubre menos de COBERTURA_MIN_PCT de las piezas; None si alcanza."""
    if n and n_fuente / n * 100.0 >= COBERTURA_MIN_PCT:
        return None
    return (f"{que} registrado en {n_fuente} de {n} piezas de la colada ({fuente}); "
            f"se califica con ≥{COBERTURA_MIN_PCT:.0f} % de las piezas")


def datos_pcs_coladas(coladas: list) -> dict:
    """Todas las consultas en lote que necesita evaluar_pcs_colada() para una lista de coladas."""
    ids = [c["id_colada"] for c in coladas]
    piezas = piezas_de_coladas(ids)
    clase = {cid: clase_dominante(ps) for cid, ps in piezas.items()}
    return {
        "piezas": piezas,
        "td_parte": tiempo_desmoldeo_partes(p["no_parte"] for ps in piezas.values() for p in ps),
        "temp": temperatura_por_coladas(ids),
        "quim": quimica_por_coladas(coladas, clase),
        "carga": cargas_por_colada(ids),
        "itaca": itaca_por_coladas(ids),
    }


def evaluar_pcs_colada(col: dict, datos: dict, no_conf: set) -> dict:
    """Los 7 PCs de una colada. Compartida por la lista (_evaluar_coladas_ventana) y el detalle
    (/v3/gestion/colada/{id}/carriles) para que los dos den el mismo color."""
    cid = col["id_colada"]
    rs = resumen_piezas(datos["piezas"].get(cid, []), datos["td_parte"])
    n = rs["piezas"]
    pc = {c.id_pc: c for c in CARRILES}

    comps = datos["carga"].get(cid, [])
    carga = resumen_carga(comps)
    ev1 = evaluar_carril(pc["PC-1"], carga["pct_ok"], col["inicio_fusion"], cid,
                         "sin carga registrada para esta colada (betamega_00_cargasmetalicas)")

    q = datos["quim"].get(cid, {})
    q_elem = evaluar_quimica(q)
    _q_tot = sum(1 for v in q_elem.values() if v is not None)
    pct_quim = _pct(sum(1 for v in q_elem.values() if v == "PASS"), _q_tot)
    ev2 = evaluar_carril(pc["PC-2"], pct_quim, q.get("AnalysisTime"), cid,
                         "sin análisis FINAL del SpectroMAX con el número de colada y horno en el Note"
                         if not q else "la clase de la colada no define elementos a evaluar")

    it = datos["itaca"].get(cid, {})
    ev3 = evaluar_carril(pc["PC-3"], it.get("score"), it.get("ts"), cid,
                         "sin copa ITACA en los 40 min previos a liberar (o colada sin hora de liberación)")

    tr = datos["temp"].get(cid, {})
    n_t = int(tr.get("n_tickets") or 0)
    pct_temp = _pct(int(tr.get("n_ok") or 0), n_t)
    m4 = _motivo_cobertura("temperatura de vaciado", n_t, n, "betamega_06_vaciado")
    ev4 = evaluar_carril(pc["PC-4"], None if m4 else pct_temp, tr.get("ts_primer_vaci"), cid, m4)

    m5 = _motivo_cobertura("desmoldeo", rs["n_desmoldeo"], n, "betamega_08_ticketrutacritica")
    ev5 = evaluar_carril(pc["PC-5"], None if m5 else rs["pct_cumple_desm"], None, cid,
                         m5 or "las partes de la colada no tienen TiempoDesmoldeo en Corex")

    ref6 = rs["ts_ultimo_vaciado"] or col["fin_fusion"]
    mes6 = ref6.strftime("%Y-%m") if isinstance(ref6, datetime) else None
    if mes6 in no_conf:
        m6 = (f"rechazo no confiable en {mes6}: desde el 9-jul-2026 las piezas nuevas no se marcan "
              f"(betamega_02_pks_ticketcorrida detenida); 0 % aquí no significa sin rechazo")
    else:
        m6 = _motivo_cobertura("rechazo", rs["n_rechazo"], n, "betamega_01_rechazosbyidticket")
    ev6 = evaluar_carril(pc["PC-6"], None if m6 else rs["pct_rechazo"], rs["ts_ultimo_vaciado"], cid, m6)

    m7 = _motivo_cobertura("ruta de limpieza", rs["n_ruta"], n, "betamega_08_ticketrutacritica")
    if m7:
        ev7 = evaluar_carril(pc["PC-7"], None, None, cid, m7)
    else:
        ev7 = evaluar_pc7(rs["pct_cierre"], col["inicio_fusion"], cid)

    if not n:
        etapa = "Sin datos"
    elif m7:
        etapa = "Vaciada · sin registro posterior"
    elif rs["terminadas"] >= rs["n_ruta"]:
        etapa = "Terminado"
    elif rs["terminadas"] > 0:
        etapa = "En limpieza"
    elif rs["n_desm_ruta"] > 0:
        etapa = "En desmoldeo"
    else:
        etapa = "En vaciado"

    return {
        "evs": {"PC-1": ev1, "PC-2": ev2, "PC-3": ev3, "PC-4": ev4,
                "PC-5": ev5, "PC-6": ev6, "PC-7": ev7},
        "resumen": rs, "carga": carga, "comps_carga": comps,
        "quim": q, "quim_elem": q_elem, "itaca": it,
        "temp": tr, "etapa": etapa,
    }


def _evaluar_coladas_ventana(referencia: str, horas: int) -> list:
    """
    Evalúa los 7 carriles (PC-1..PC-7) para cada colada liberada en la ventana
    [referencia-horas, referencia]. Compartida por /v3/gestion/coladas, /v3/gestion/alertas y
    /api/beta/criticas para que los tres usen exactamente la misma lógica de evaluación.

    2026-10-26: deja betamega_03 (hueco jul-oct, ~1 % de tickets) y arma coladas y piezas desde
    fuentes completas -- ver "COLADAS DESDE FUENTES COMPLETAS" arriba. Historia anterior:
    2026-09-23 cscmega_* -> betamega_03/08 (llaves por c_IdColada, no u_Colada, que se recicla).
    """
    # Caché corta: Control del Proceso pide /coladas y /alertas con la misma ventana a la vez
    # (~10 s cada evaluación, la consulta de vaciado no tiene índice). El candado por ventana
    # hace que la segunda espere a la primera en vez de repetir las consultas.
    key = (referencia, horas)
    with _VENTANA_LOCKS_GUARD:
        lock = _VENTANA_LOCKS.setdefault(key, threading.Lock())
    with lock:
        hit = _VENTANA_CACHE.get(key)
        if hit and time.time() - hit[0] < _VENTANA_TTL_SEG:
            return hit[1]
        res = _evaluar_coladas_ventana_bd(referencia, horas)
        _VENTANA_CACHE[key] = (time.time(), res)
        for k in [k for k, v in _VENTANA_CACHE.items() if time.time() - v[0] > _VENTANA_TTL_SEG]:
            _VENTANA_CACHE.pop(k, None)
        return res


_VENTANA_CACHE: Dict[tuple, tuple] = {}
_VENTANA_LOCKS: Dict[tuple, "threading.Lock"] = {}
_VENTANA_LOCKS_GUARD = threading.Lock()
_VENTANA_TTL_SEG = 120


def _evaluar_coladas_ventana_bd(referencia: str, horas: int) -> list:
    ref_dt = datetime.fromisoformat(referencia)
    d = (ref_dt - timedelta(hours=horas)).strftime("%Y-%m-%d %H:%M:%S")
    h = ref_dt.strftime("%Y-%m-%d %H:%M:%S")

    coladas = coladas_en_ventana(d, h)
    if not coladas:
        return []
    datos = datos_pcs_coladas(coladas)
    no_conf = meses_no_confiables_cache(d, h)

    resultado = []
    for col in coladas:
        e = evaluar_pcs_colada(col, datos, no_conf)
        evs, rs = e["evs"], e["resumen"]
        estados = {k: v["estado"] for k, v in evs.items()}
        resultado.append({
            "id_colada":     col["id_colada"],
            "u_colada":      col["u_colada"],
            "horno":         col["horno"],
            "inicio_fusion": str(col["inicio_fusion"] or ""),
            "fin_fusion":    str(col["fin_fusion"] or ""),
            "kilos":         float(col["kilos"] or 0) or e["carga"]["kg_real"],
            "status":        col["status"],
            "piezas":        rs["piezas"],
            "rechazos":      rs["rechazos"],
            "pct_rechazo":   evs["PC-6"]["valor"],
            "pct_cierre":    evs["PC-7"]["valor"],
            "temp_avg":      int(e["temp"]["temp_avg"]) if e["temp"].get("temp_avg") else None,
            "pct_temp_ok":   evs["PC-4"]["valor"],
            "pct_carga_ok":  e["carga"]["pct_ok"],
            "estados":       estados,
            "etapa":         e["etapa"],
            "tiene_alerta":  any(v in ("ROJO", "AMBAR") for v in estados.values()),
            "skus":          rs["skus"],
            "latencia_pc4_seg": evs["PC-4"].get("latencia_seg"),
            "latencia_pc6_seg": evs["PC-6"].get("latencia_seg"),
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
