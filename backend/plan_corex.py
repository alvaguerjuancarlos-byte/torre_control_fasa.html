"""
Plan de Producción Mensual leído de Corex -- ia_fasa.gamamega_01_programaperiodo.

Desde oct-2026 el plan se captura en Corex (compromisos #5/#6 de la minuta del
30-sep-2026) y JC confirmó que ya no hace falta el Excel. Esta es la fuente
principal; plan_lector.py (Excel) queda solo como respaldo para meses que no
estén en Corex.

Qué trae y qué no:
- `Periodo` es int: 4 dígitos = mes (YYMM, ej. 2610 = oct-2026); 6 dígitos =
  semana (YYYYWW, ej. 202641). Aquí solo se leen los mensuales.
- Por parte trae nPzasProg y KgsPzasProgAsProgProd. Esos kilos NO son
  piezas × PesoKgs del modelo: en varias partes vienen ~6-12% arriba (ej.
  42091101: 176.96 kg/pza contra 158 de corex_test.modelos), sin documentar
  todavía qué incluyen. Para kg_buenos se usa piezas × PesoKgs (mismo criterio
  que la hoja `resumen` del Excel, con la que coincide en las partes sin esa
  diferencia) y los kilos de Corex se exponen aparte como kg_prog_corex.
- NO trae el encabezado del Excel (ventas, presupuesto, buenas/vaciadas,
  % rechazo interno, línea/desarrollo, inventario) ni los ritmos objetivo por
  proceso. Se buscó en corex_test/ia_fasa/piso_ia por nombre de tabla y de
  columna (2026-10-11) sin encontrarlos -- salen en None/[] y el frontend los
  marca "no capturado en Corex", nunca se infieren.
- Proceso (AF1/AF2/AF3): corex_test.modelos.area está vacío para ~la mitad de
  las partes; se completa con el área más reciente de gamamega_01_programa3dias
  (programa diario real) -- con eso cubre el 100% de las partes de oct-2026.
- Comparado contra el Excel de agosto 2026: 137/138 partes en común, 127 con
  piezas idénticas; el resto difiere poco (Corex casi siempre más alto).
"""
import calendar
from datetime import date
from typing import Optional

from backend.core import run


def _periodo_mensual(mes: str) -> Optional[int]:
    try:
        anio, m = (int(x) for x in mes.split("-"))
    except ValueError:
        return None
    return (anio % 100) * 100 + m


def _dias_habiles(anio: int, mes: int) -> int:
    """Lunes a sábado del mes (el plan de FASA cuenta sábados -- agosto 2026 = 26
    en el Excel, igual que este cálculo). No descuenta festivos: no hay
    calendario de festivos en la BD."""
    _, ultimo = calendar.monthrange(anio, mes)
    return sum(1 for d in range(1, ultimo + 1) if date(anio, mes, d).weekday() < 6)


def _proceso_partes(no_partes: list) -> dict:
    if not no_partes:
        return {}
    ph = ",".join(f":p{i}" for i in range(len(no_partes)))
    params = {f"p{i}": p for i, p in enumerate(no_partes)}
    out = {r["no_parte"]: r["area"] for r in run(f"""
        SELECT NoParte AS no_parte, MAX(area) AS area
        FROM corex_test.modelos
        WHERE NoParte IN ({ph}) AND area IN ('AF1','AF2','AF3')
        GROUP BY NoParte
    """, params)}
    faltan = [p for p in no_partes if p not in out]
    if faltan:
        ph = ",".join(f":p{i}" for i in range(len(faltan)))
        params = {f"p{i}": p for i, p in enumerate(faltan)}
        for r in run(f"""
            SELECT d.no_parte, d.area
            FROM gamamega_01_programa3dias d
            JOIN (SELECT no_parte, MAX(fecha) AS f FROM gamamega_01_programa3dias
                  WHERE no_parte IN ({ph}) AND area IN ('AF1','AF2','AF3') GROUP BY no_parte) u
              ON u.no_parte = d.no_parte AND u.f = d.fecha
            WHERE d.area IN ('AF1','AF2','AF3')
        """, params):
            out.setdefault(r["no_parte"], r["area"])
    return out


def leer_plan_corex(mes: str) -> Optional[dict]:
    """Mismo shape que plan_lector.leer_plan(), con fuente='corex'. None si el
    mes no está capturado en Corex."""
    periodo = _periodo_mensual(mes)
    if periodo is None:
        return None
    rows = run("""
        SELECT p.No_Parte AS parte, p.nPzasProg AS piezas,
               p.KgsPzasProgAsProgProd AS kg_corex, pm.peso AS peso_modelo
        FROM gamamega_01_programaperiodo p
        LEFT JOIN (SELECT NoParte, MAX(PesoKgs) AS peso FROM corex_test.modelos
                   WHERE PesoKgs > 0 GROUP BY NoParte) pm ON pm.NoParte = p.No_Parte
        WHERE p.Periodo = :per
        ORDER BY p.No_Parte
    """, {"per": periodo})
    if not rows:
        return None

    procesos = _proceso_partes([r["parte"] for r in rows])
    detalle = []
    for r in rows:
        piezas = int(r["piezas"] or 0)
        kg_corex = float(r["kg_corex"] or 0)
        peso = float(r["peso_modelo"]) if r["peso_modelo"] else (kg_corex / piezas if piezas else None)
        detalle.append({
            "parte":         r["parte"],
            "sdo_final":     piezas,
            "peso_kg":       round(peso, 2) if peso is not None else None,
            "kg_buenos":     round(piezas * peso, 1) if peso is not None else None,
            "kg_prog_corex": round(kg_corex, 1),
            "proceso":       procesos.get(r["parte"]),
            "status":        None,
            "autoritativo":  False,
        })

    anio, m = (int(x) for x in mes.split("-"))
    encabezado = {
        "anio_mes":         f"{anio}-{m:02d}-01",
        "periodo_corex":    periodo,
        "dias_habiles":     _dias_habiles(anio, m),
        "partes":           len(detalle),
        "piezas_plan":      sum(f["sdo_final"] for f in detalle),
        "ton_plan":         round(sum(f["kg_buenos"] or 0 for f in detalle) / 1000, 1),
        "ton_prog_corex":   round(sum(f["kg_prog_corex"] for f in detalle) / 1000, 1),
        # No capturados en Corex (ver docstring del módulo) -- nunca se infieren.
        "rev_no": None, "fecha_rev": None, "ventas_ton": None, "presup_ton": None,
        "buenas_ton": None, "vaciadas_ton": None, "rech_int_pct": None,
        "linea_ton": None, "desarrollo_ton": None, "linea_pct": None,
        "desarrollo_pct": None, "inv_total": None, "archivo_origen": None,
    }
    return {
        "fuente":              "corex",
        "encabezado":          encabezado,
        "ritmos_por_proceso":  [],
        "ritmos_agregados":    [],
        "detalle_referencial": detalle,
    }


def saldo_parte_corex(no_parte: str) -> Optional[dict]:
    """Piezas programadas de la parte en el plan mensual más reciente de Corex
    que la incluya (para la proyección del Agente Alfa)."""
    rows = run("""
        SELECT Periodo, nPzasProg, KgsPzasProgAsProgProd
        FROM gamamega_01_programaperiodo
        WHERE No_Parte = :np AND Periodo < 10000
        ORDER BY Periodo DESC LIMIT 1
    """, {"np": no_parte})
    if not rows:
        return None
    r = rows[0]
    per = int(r["Periodo"])
    return {
        "sdo_final":    int(r["nPzasProg"] or 0),
        "kg_buenos":    float(r["KgsPzasProgAsProgProd"] or 0),
        "status":       None,
        "anio_mes":     f"20{per // 100:02d}-{per % 100:02d}-01",
        "fuente":       "corex",
        "autoritativo": False,
    }
