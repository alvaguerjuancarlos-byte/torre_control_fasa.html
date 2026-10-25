"""
Costo de no-calidad mensual — para la card "Rendimiento Metálico · Tonelaje" del tab Ejecutivo.

Origen: costos/build_costo_calidad.py (script de discovery/validación fuera de este repo, en
C:\\Users\\Administrator\\Documents\\FASA\\costos), corrido y validado con JC el 2026-09-05/06.
Este endpoint es la versión productiva de la Sección "Paso 6" de ese script — mismas fuentes,
mismas correcciones ya aplicadas ahí:

- Rechazo con costo: piso_ia.rechazo, SOLO tipo_rech='Producción' -- Maquinado/Prueba ya
  absorbieron costo adicional (fundición completa + maquinado) no desglosado en el archivo de
  costeo, se excluyen en vez de aplicarles el mismo factor por error.
- Peso unitario: corex_test.modelos.PesoKgs (MAX por NoParte trimmeado -- ~7% de las filas tienen
  NoParte duplicado con peso distinto, ninguna aparece en rechazo real, no bloqueante).
- Producción total 2025 (para % de rechazo y costo de producción, no solo costo de no-calidad):
  ia_fasa.cscmega_01rechazosbyidticket (idTicket único, verificado sin duplicados) x
  cscmega_00_ticketsproducidos (mapa idTicket -> no_parte) x PesoKgs.
  NUNCA piso_ia.idtickets -- se probó y sobre-cuenta ~2.3x (101,028 filas vs. 44,041 idTicket
  reales en 2025), lo que hacía ver el % de rechazo en ~6% cuando el real es ~13.5%. Encontrado
  porque JC notó que no cuadraba con lo que recordaba (~11%) ni con "% Rechazo Int." del Excel de
  costeo (11%-19% mensual, hoja "Costos 2025").
- 2026 SIN producción total verificada: cscmega_* está congelada desde 2025-12-29 (misma familia
  que alimenta /produccion/tonelaje-mensual). Se expone ton_producidas/pct_rechazo/
  costo_produccion_usd en null para esos meses en vez de reusar idtickets o inventar un número --
  el frontend debe pintar esos meses con un placeholder explícito ("sin dato"), nunca en cero.
- Costo/ton: tabla de referencia manual ("Costos Tonelada Graficas 2026.xlsx", hojas "Costos
  2025"/"Costos 2026", fila "Total Costo Ventas") -- mismo criterio ya validado con JC en chat
  para 2026 y confirmado válido para 2025 (misma hoja/layout, 12 meses completos). No hay fuente
  automática todavía; actualizar este dict a mano cuando el archivo de costeo se refresque.

El join contra corex_test.modelos con TRIM(NoParte) en el ON se cuelga si se hace en SQL contra
las ~44k filas de cscmega (probado, >2 min sin resultado -- no puede usar índice con TRIM()) --
por eso el peso se resuelve en Python contra un dict, no en un JOIN de SQL.
"""

import calendar

from fastapi import APIRouter

from backend.core import run, meses_rechazo_no_confiables

# 2026 en adelante (JC 2026-10-25, "que 2026 sea congruente; 2025 me puedo quedar con el anual"):
# piso_ia.rechazo se congeló el 9-mar-2026, así que 2026 salía solo con ene-feb y no había cómo
# completarlo con el mismo método. Para TODO 2026 el rechazo con costo sale de
# betamega_01_rechazosbyidticket: rechazo interno (IntExt='Int') asignado al mes de FechaRechazo
# -- mismo criterio de fecha que piso_ia. Diferencia conocida con 2025: b01 no trae tipo_rech, así
# que incluye Maquinado/Prueba (~7 % de las toneladas en 2025: 1,201.8 t todos los tipos vs
# 1,119.2 t solo Producción; b01 Int cuadra con piso todos los tipos al 0.02 %). Solo meses con
# rechazo confiable (core.meses_rechazo_no_confiables): desde jul-2026 el registro está detenido.
ANIO_RECHAZO_B01 = 2026

router = APIRouter(prefix="/produccion", tags=["costo-calidad"])

COSTO_TON_USD = {
    2025: {
        1: 2493.60, 2: 2430.79, 3: 2304.18, 4: 2095.63,
        5: 2424.96, 6: 2246.34, 7: 1944.78, 8: 2625.55,
        9: 2447.36, 10: 2443.07, 11: 2423.48, 12: 2649.02,
    },
    2026: {
        1: 2274.98, 2: 2269.38, 3: 2453.99, 4: 2241.37,
        5: 2573.91, 6: 2296.96, 7: 2466.73, 8: 2322.60,
    },
}


def _mapa_peso() -> dict:
    rows = run("""
        SELECT TRIM(NoParte) AS no_parte, MAX(PesoKgs) AS peso
        FROM corex_test.modelos
        WHERE PesoKgs > 0
        GROUP BY TRIM(NoParte)
    """)
    return {r["no_parte"]: float(r["peso"]) for r in rows}


def _ton_producidas_por_mes(peso_map: dict) -> dict:
    """MIGRACIÓN (2026-10-11): IT borró cscmega_01rechazosbyidticket y
    cscmega_00_ticketsproducidos. Ahora betamega_01_rechazosbyidticket (idTicket único,
    trae No_Parte directo, 2023 -> hoy). Ojo: cuenta ~50.1k tickets en 2025 contra los
    44,041 validados con la fuente vieja (la misma diferencia aparece en betamega_03), así
    que el % de rechazo de 2025 baja respecto a lo validado el 2026-09-06. Ya no se puede
    conciliar ticket por ticket porque la fuente vieja no existe."""
    rows = run("""
        SELECT YEAR(FHrVaciado) AS anio, MONTH(FHrVaciado) AS mes, No_Parte AS no_parte
        FROM ia_fasa.betamega_01_rechazosbyidticket
        WHERE FHrVaciado >= '2025-01-01'
    """)
    ton_mes = {}
    for r in rows:
        peso = peso_map.get((r["no_parte"] or "").strip())
        if peso is None:
            continue
        clave = (r["anio"], r["mes"])
        ton_mes[clave] = ton_mes.get(clave, 0.0) + peso / 1000
    return ton_mes


def _ton_rechazadas_b01_por_mes(peso_map: dict) -> dict:
    """Rechazo interno de betamega_01 por mes de FechaRechazo, desde ANIO_RECHAZO_B01."""
    rows = run("""
        SELECT YEAR(FechaRechazo) AS anio, MONTH(FechaRechazo) AS mes, No_Parte AS no_parte,
               COUNT(*) AS piezas
        FROM ia_fasa.betamega_01_rechazosbyidticket
        WHERE bRechazo = 1 AND IntExt = 'Int' AND FechaRechazo >= :d
        GROUP BY YEAR(FechaRechazo), MONTH(FechaRechazo), No_Parte
    """, {"d": f"{ANIO_RECHAZO_B01}-01-01"})
    ton_mes = {}
    for r in rows:
        peso = peso_map.get((r["no_parte"] or "").strip())
        if peso is None:
            continue
        clave = (r["anio"], r["mes"])
        ton_mes[clave] = ton_mes.get(clave, 0.0) + int(r["piezas"]) * peso / 1000
    return ton_mes


def _ton_rechazadas_por_mes() -> dict:
    rows = run("""
        SELECT YEAR(r.fecha) AS anio, MONTH(r.fecha) AS mes, r.tipo_rech AS tipo_rech,
               COUNT(*) AS piezas, m.PesoKgs AS peso_kg
        FROM piso_ia.rechazo r
        JOIN (
            SELECT TRIM(NoParte) AS NoParte, MAX(PesoKgs) AS PesoKgs
            FROM corex_test.modelos WHERE PesoKgs > 0 GROUP BY TRIM(NoParte)
        ) m ON m.NoParte = TRIM(r.no_parte)
        WHERE r.fecha >= '2025-01-01'
        GROUP BY YEAR(r.fecha), MONTH(r.fecha), r.tipo_rech, m.PesoKgs
    """)
    ton_mes = {}
    for r in rows:
        if r["tipo_rech"] == "Producción":
            clave = (r["anio"], r["mes"])
            ton_mes[clave] = ton_mes.get(clave, 0.0) + r["piezas"] * float(r["peso_kg"]) / 1000
    return ton_mes


@router.get("/costo-no-calidad-mensual")
def costo_no_calidad_mensual():
    peso_map = _mapa_peso()
    ton_prod_mes = _ton_producidas_por_mes(peso_map)
    # Hasta 2025: piso_ia (solo tipo Producción). Desde 2026: betamega_01 (ver ANIO_RECHAZO_B01).
    ton_rech_mes = {k: v for k, v in _ton_rechazadas_por_mes().items() if k[0] < ANIO_RECHAZO_B01}
    no_conf = set(meses_rechazo_no_confiables(f"{ANIO_RECHAZO_B01}-01-01 00:00:00", "2099-12-31 23:59:59"))
    ton_rech_mes.update({k: v for k, v in _ton_rechazadas_b01_por_mes(peso_map).items()
                         if f"{k[0]}-{k[1]:02d}" not in no_conf})
    # piso_ia.rechazo puede cortarse a media mes (hoy: 2026-03-09) mientras la producción
    # sigue completa -- ese mes saldría con un % artificialmente bajo, así que se omite.
    corte = run("SELECT MAX(fecha) AS mx FROM piso_ia.rechazo")[0]["mx"]

    meses = []
    for (anio, mes), tr in sorted(ton_rech_mes.items()):
        costo_ton = COSTO_TON_USD.get(anio, {}).get(mes)
        if costo_ton is None:
            continue  # sin referencia de costo/ton para este mes todavía (gray lane)
        if anio < ANIO_RECHAZO_B01 and corte and (anio, mes) >= (corte.year, corte.month) \
                and corte.day < calendar.monthrange(anio, mes)[1]:
            continue  # rechazo de piso_ia incompleto para este mes

        tp = ton_prod_mes.get((anio, mes))
        costo_prod = tp * costo_ton if tp is not None else None
        costo_noc = tr * costo_ton

        meses.append({
            "anio": anio,
            "mes": mes,
            "ton_producidas": round(tp, 2) if tp is not None else None,
            "ton_rechazadas": round(tr, 2),
            "pct_rechazo": round(tr / tp * 100, 2) if tp else None,
            "costo_ton_ref_usd": costo_ton,
            "costo_produccion_usd": round(costo_prod, 2) if costo_prod is not None else None,
            "costo_no_calidad_usd": round(costo_noc, 2),
            "pct_no_calidad": round(costo_noc / costo_prod * 100, 2) if costo_prod else None,
            "metodo_rechazo": "betamega_01_interno" if anio >= ANIO_RECHAZO_B01 else "piso_ia_produccion",
        })

    return {
        "meses": meses,
        "nota": "Producción total desde betamega_01_rechazosbyidticket (cscmega_* fue borrada "
                "por IT en 2026-10). Toneladas rechazadas: hasta 2025 piso_ia.rechazo (tipo Producción); "
                "desde 2026 rechazo interno de betamega_01 por fecha de rechazo (incluye maquinado/prueba), "
                "solo meses con rechazo confiable.",
    }
