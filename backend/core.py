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
    pool_size=5,
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


# ── Cache mensual de alfamega: carga única al arrancar ────────────────────────
# alfamega no tiene índice en FHrVaciado → cada scan toma ~5s.
# Precargamos todos los meses (2023-2024) para que los endpoints sean O(1).
_ALFAMEGA_MONTHLY: dict = {}

def _preload_alfamega():
    global _ALFAMEGA_MONTHLY
    try:
        rows = run("""
            SELECT DATE_FORMAT(FHrVaciado,'%Y-%m') AS mk,
                   COUNT(*) AS n, SUM(bRechazo) AS rec
            FROM alfamega_01_rechazosbyidticket
            GROUP BY mk
        """, {})
        _ALFAMEGA_MONTHLY = {r["mk"]: {"n": int(r["n"] or 0), "rec": int(r["rec"] or 0)}
                              for r in rows}
        print(f"[alfamega] cache mensual: {len(_ALFAMEGA_MONTHLY)} meses")
    except Exception as e:
        print(f"[alfamega] preload falló ({e}) — lookups al vuelo")

_preload_alfamega()


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
        "PC-1", "kg cargados por material vs receta",
        "ia_fasa.cscmega_03coladacargametalica.c_Kilos (kg total — sin desglose por material)",
        None, "inicio_fusion",
        "kg real por componente vs receta programada", "P-01", 15, "gris_sin_dato",
    ),
    CarrilControl(
        "PC-2", "análisis químico — elementos clave en especificación",
        "ia_fasa.cscmega_05aquimicoscumplimiento — flags _F via u_Colada + u_Horno",
        VentanaControl("completitud", 100.0, 80.0, "pct"),
        "fin_fusion",
        "% elementos clave (C,Si,Mg,S,P,Mn) con flag _F = pass ; 100%→VERDE ; ≥80%→ÁMBAR ; <80%→ROJO",
        "P-02", 10, "activo",
    ),
    CarrilControl(
        "PC-3", "inoculación / fading",
        "sin fuente integrada en ia_fasa",
        None, "inicio_vaciado",
        "tiempo desde inoculación ≤ ventana de fading por aleación", "P-03", 5, "gris_sin_dato",
    ),
    CarrilControl(
        "PC-4", "temperatura de vaciado (% tickets en rango)",
        "ia_fasa.alfamega_06_vaciado.vt_TemperaturaVaciado + bCumplimientoTemperaturaOk",
        VentanaControl("completitud", 95.0, 80.0, "pct"),
        "inicio_vaciado",
        "% tickets con bCumplimientoTemperaturaOk = 1 ≥ 95 % → VERDE ; ≥ 80 % → ÁMBAR ; < 80 % → ROJO",
        "P-04", 5, "activo",
    ),
    CarrilControl(
        "PC-5", "desmoldeo oportuno (% piezas >= umbral por parte)",
        "cscmega_08ruta.FHrMOLD + FHrDESM x corex_test.modelos.TiempoDesmoldeo",
        VentanaControl("completitud", 95.0, 80.0, "pct"),
        "fin_vaciado",
        "% piezas con tiempo_molde >= TiempoDesmoldeo por NoParte ; ≥95%→VERDE ; ≥80%→ÁMBAR ; <80%→ROJO",
        "P-05", 30, "activo",
    ),
    CarrilControl(
        "PC-6", "% rechazo del lote (qué, no causa metalúrgica)",
        "ia_fasa.cscmega_01rechazosbyidticket.bRechazo",
        VentanaControl("porcentaje_max", 7.0, None, "pct"),
        "fin_vaciado",
        "pct_rechazo < 7 % → VERDE ; ≥ 7 % → ROJO", "P-06", 60, "activo",
    ),
    CarrilControl(
        "PC-7", "cierre de colada — piezas con limpieza registrada",
        "ia_fasa.cscmega_08ruta.bLIMP — ventana: ≥95% piezas O ≤5 días desde vaciado",
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
        "motivo_gris":  None,
        "protocolo":    carril.protocolo if estado == "ROJO" else None,
        "detalle":      {},
    }


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
        FROM alfamega_06_vaciado
        WHERE c_IdColada IN ({id_list})
          AND vt_TemperaturaVaciado IS NOT NULL
          AND vt_TemperaturaVaciado > 0
        GROUP BY c_IdColada
    """, {})
    temp_idx = {t["c_IdColada"]: t for t in temp_raw}

    # PC-2: batch química — último análisis SpectroMAX BASE por (u_Colada, u_Horno)
    # bC_F IS NOT NULL filtra a filas SpectroMAX; filas copa/ITACA tienen todos los flags NULL
    # NOTA: análisis BASE (pre-inoculación). Mg siempre FAIL en BASE → excluido del score.
    #       Pendiente: usar análisis FINAL cuando haya cobertura suficiente de aB_Calidad='FINAL'.
    uid_list = ",".join(str(c["u_colada"]) for c in coladas) or "0"
    quim_batch = run(f"""
        SELECT q.u_Colada, q.u_Horno,
               q.bC_F, q.bSi_F, q.bMg_F, q.bS_F, q.bP_F, q.bMn_F
        FROM cscmega_05aquimicoscumplimiento q
        INNER JOIN (
            SELECT u_Colada, u_Horno, MAX(u_Fecha) AS max_fecha
            FROM cscmega_05aquimicoscumplimiento
            WHERE u_Colada IN ({uid_list})
              AND bC_F IS NOT NULL
            GROUP BY u_Colada, u_Horno
        ) lat ON q.u_Colada = lat.u_Colada
             AND q.u_Horno  = lat.u_Horno
             AND q.u_Fecha  = lat.max_fecha
        GROUP BY q.u_Colada, q.u_Horno
    """, {})
    quim_idx = {(r["u_Colada"], r["u_Horno"]): r for r in quim_batch}

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

    pc2 = next(c for c in CARRILES if c.id_pc == "PC-2")
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

        qq       = quim_idx.get((uid, col["horno"]), {})
        _QKEYS   = ["bC_F", "bSi_F", "bS_F", "bP_F", "bMn_F"]  # Mg excluido (BASE pre-inoculación)
        _q_total = sum(1 for k in _QKEYS if qq.get(k) is not None)
        _q_pass  = sum(1 for k in _QKEYS if qq.get(k) is not None and str(qq[k]).endswith(")1"))
        pct_quim = round(_q_pass / _q_total * 100.0, 1) if _q_total else None

        mo5 = molde5_idx.get(cid, {})
        con_umbral5 = int(mo5.get("con_umbral") or 0)
        cumple5     = int(mo5.get("cumple") or 0)
        pct_cumple5 = round(cumple5 / con_umbral5 * 100.0, 1) if con_umbral5 else None

        ev2 = evaluar_carril(pc2, pct_quim,    None,                    cid)
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
            "PC-1": "GRIS", "PC-2": ev2["estado"], "PC-3": "GRIS",
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
    esté vivo."""
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
