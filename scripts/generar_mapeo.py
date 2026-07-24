#!/usr/bin/env python3
"""
Genera mapeo_ia_fasa.xlsx — diagnóstico campo a campo de los 5 paneles
vs columnas disponibles en ia_fasa.
"""
import os
from pathlib import Path
import pandas as pd
from sqlalchemy import create_engine, text

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

HOST   = os.environ["DB_HOST"]
PORT   = int(os.environ.get("DB_PORT", 3306))
USER   = os.environ["DB_USER"]
PWD    = os.environ["DB_PWD"]
SCHEMA = os.environ.get("DB_SCHEMA", "ia_fasa")
OUTFILE = Path(__file__).parent.parent / "mapeo_ia_fasa.xlsx"

eng = create_engine(
    f"mysql+pymysql://{USER}:{PWD}@{HOST}:{PORT}/{SCHEMA}?charset=utf8mb4",
    pool_pre_ping=True
)

# ── Mapeo definido a mano (campo del panel → fuente en ia_fasa) ──────────────
MAPEO = [
    # panel, campo_panel, tabla_fuente, columna_fuente, estado, nota
    # PANEL 1 — Gestión de Producción
    ("1 - Gestión de Producción", "Coladas del turno (idTicket)",       "cscmega_00_tickets",               "idTicket",               "OK",           ""),
    ("1 - Gestión de Producción", "Número de colada (u_Colada)",        "cscmega_03coladacargametalica",     "u_Colada",               "OK",           ""),
    ("1 - Gestión de Producción", "Horno",                               "cscmega_03coladacargametalica",     "u_Horno",                "OK",           ""),
    ("1 - Gestión de Producción", "No. de parte",                        "cscmega_00_tickets",               "no_parte",               "OK",           ""),
    ("1 - Gestión de Producción", "Año",                                 "cscmega_00_tickets",               "anio",                   "OK",           ""),
    ("1 - Gestión de Producción", "Flag de rechazo (bRechazo)",         "cscmega_01rechazosbyidticket",      "bRechazo",               "OK",           "Solo flag 0/1"),
    ("1 - Gestión de Producción", "Nombre del defecto",                  "cscmega_01resultadoidticket",      "nomDefecto",             "OK",           ""),
    ("1 - Gestión de Producción", "Código de defecto",                   "cscmega_01resultadoidticket",      "cveDefecto",             "OK",           ""),
    ("1 - Gestión de Producción", "Días de rechazo",                     "cscmega_01resultadoidticket",      "DiasRechazo",            "OK",           ""),
    ("1 - Gestión de Producción", "Fecha-hora del ticket",               "cscmega_01resultadoidticket",      "u_FechaHr",              "OK",           ""),
    ("1 - Gestión de Producción", "Tiempo moldeo (FHrMOLD)",             "cscmega_08ruta",                   "FHrMOLD",                "OK",           ""),
    ("1 - Gestión de Producción", "Tiempo vaciado (FhrVACI)",            "cscmega_08ruta",                   "FhrVACI",                "OK",           ""),
    ("1 - Gestión de Producción", "Tiempo limpieza (FHrLIMP)",           "cscmega_08ruta",                   "FHrLIMP",                "OK",           ""),
    ("1 - Gestión de Producción", "Tiempo desmoldeo (FHrDESM)",          "cscmega_08ruta",                   "FHrDESM",                "OK",           ""),
    ("1 - Gestión de Producción", "% Rechazo turno (KPI calculado)",     "cscmega_01rechazosbyidticket",     "bRechazo (AVG)",          "CALCULAR",     "AVG(bRechazo)*100 por turno"),
    ("1 - Gestión de Producción", "Predicción de rechazo IA",            "[SIN FUENTE]",                     "[SIN FUENTE]",           "SIN FUENTE",   "Requiere modelo ML externo"),

    # PANEL 2 — Detalle de Colada (química LIMS)
    ("2 - Detalle de Colada",     "IdColada (c_IdColada)",               "cscmega_03coladacargametalica",    "c_IdColada",             "OK",           ""),
    ("2 - Detalle de Colada",     "Fecha inicio colada",                 "cscmega_03coladacargametalica",    "c_FechaInicial",         "OK",           ""),
    ("2 - Detalle de Colada",     "Fecha final colada",                  "cscmega_03coladacargametalica",    "c_FechaFinal",           "OK",           ""),
    ("2 - Detalle de Colada",     "Fecha inicio vaciado",                "cscmega_03coladacargametalica",    "c_FechaInicioVaciado1",  "OK",           ""),
    ("2 - Detalle de Colada",     "Kilos colada",                        "cscmega_03coladacargametalica",    "c_Kilos",                "OK",           ""),
    ("2 - Detalle de Colada",     "Tiempo de fusión",                    "cscmega_03coladacargametalica",    "c_TiempoFundicion",      "OK",           ""),
    ("2 - Detalle de Colada",     "Folio COREX",                         "cscmega_03coladacargametalica",    "c_Folio",                "OK",           ""),
    ("2 - Detalle de Colada",     "Temperatura vaciado",                 "cscmega_06modelosvaciado",         "vt_TemperaturaVaciado",  "OK*",          "Outliers detectados (max 14153, min 21) — filtrar rango 1100-1600°C"),
    ("2 - Detalle de Colada",     "Cumplimiento rango temperatura",      "cscmega_06modelosvaciado",         "bCumplimientoTemperaturaRango", "OK",    ""),
    ("2 - Detalle de Colada",     "Temperatura OK flag",                 "cscmega_06modelosvaciado",         "bCumplimientoTemperaturaOk", "OK",       ""),
    ("2 - Detalle de Colada",     "Química Base — %C",                   "cscmega_05cquimicosbase",          "aB_C",                   "OK*",          "42% de registros tienen valor (SpectroMAX)"),
    ("2 - Detalle de Colada",     "Química Base — %Si",                  "cscmega_05cquimicosbase",          "aB_Si",                  "OK*",          "42% de registros tienen valor"),
    ("2 - Detalle de Colada",     "Química Base — %Mn",                  "cscmega_05cquimicosbase",          "aB_Mn",                  "OK*",          "42% de registros tienen valor"),
    ("2 - Detalle de Colada",     "Química Base — %S",                   "cscmega_05cquimicosbase",          "aB_S",                   "OK*",          "42% de registros tienen valor"),
    ("2 - Detalle de Colada",     "Química Base — %P",                   "cscmega_05cquimicosbase",          "aB_P",                   "OK*",          "42% de registros tienen valor"),
    ("2 - Detalle de Colada",     "Química Base — %Mg",                  "cscmega_05cquimicosbase",          "aB_Mg",                  "OK*",          "42% de registros tienen valor"),
    ("2 - Detalle de Colada",     "Química Final — %C",                  "cscmega_05bquimicosfinal",         "aF_C",                   "OK*",          "SpectroMAX"),
    ("2 - Detalle de Colada",     "Química Final — %Si",                 "cscmega_05bquimicosfinal",         "aF_Si",                  "OK*",          "SpectroMAX"),
    ("2 - Detalle de Colada",     "Química Final — %Mg",                 "cscmega_05bquimicosfinal",         "aF_Mg",                  "OK*",          "SpectroMAX"),
    ("2 - Detalle de Colada",     "CE% (equivalente de carbono)",        "[CALCULAR]",                       "aB_C + aB_Si/3 + aB_P/3","CALCULAR",    "Fórmula estándar fundición"),
    ("2 - Detalle de Colada",     "Cementite (ITACA)",                   "[SIN FUENTE]",                     "[SIN FUENTE]",           "SIN FUENTE",   "No está en ia_fasa — buscar en corex_test"),
    ("2 - Detalle de Colada",     "Nodularization (ITACA)",              "[SIN FUENTE]",                     "[SIN FUENTE]",           "SIN FUENTE",   "No está en ia_fasa — buscar en corex_test"),
    ("2 - Detalle de Colada",     "Porosity (ITACA)",                    "[SIN FUENTE]",                     "[SIN FUENTE]",           "SIN FUENTE",   "No está en ia_fasa — buscar en corex_test"),
    ("2 - Detalle de Colada",     "IGQ (ITACA)",                         "[SIN FUENTE]",                     "[SIN FUENTE]",           "SIN FUENTE",   "No está en ia_fasa — buscar en corex_test"),
    ("2 - Detalle de Colada",     "Clase ITACA (cls_ClaseITACA)",        "cscmega_04clase",                  "cls_ClaseITACA",         "OK",           "Clase de material, no resultado de microestructura"),
    ("2 - Detalle de Colada",     "Inoculante en horno",                 "cscmega_03coladacargametalica",    "c_InoculanteEnHornos",   "OK",           ""),

    # PANEL 3 — Agentes IA
    ("3 - Agentes IA",            "Agente Alfa — cadencia de coladas",   "cscmega_08ruta",                   "FhrVACI / FHrMOLD",      "OK",           "Calculado a partir de timestamps"),
    ("3 - Agentes IA",            "Agente Alfa — fading time",           "cscmega_03coladacargametalica",    "c_FechaInoculante1/c_FechaInicioVaciado1","CALCULAR","Diferencia entre inoculación y vaciado"),
    ("3 - Agentes IA",            "Agente Alfa — temperatura vaciado RT","cscmega_06modelosvaciado",         "vt_TemperaturaVaciado",  "OK*",          "Outliers presentes"),
    ("3 - Agentes IA",            "Agente Beta — CE% por colada",        "[CALCULAR]",                       "aB_C + aB_Si/3",         "CALCULAR",     ""),
    ("3 - Agentes IA",            "Agente Beta — tasa de rechazo",       "cscmega_01rechazosbyidticket",     "bRechazo",               "OK",           ""),
    ("3 - Agentes IA",            "Agente Beta — LOI arena",             "[SIN FUENTE]",                     "[SIN FUENTE]",           "SIN FUENTE",   "Proceso arena química, no registrado en ia_fasa"),
    ("3 - Agentes IA",            "Agente Beta — ADV arena",             "[SIN FUENTE]",                     "[SIN FUENTE]",           "SIN FUENTE",   "No registrado en ia_fasa"),
    ("3 - Agentes IA",            "Agente Beta — pH arena",              "[SIN FUENTE]",                     "[SIN FUENTE]",           "SIN FUENTE",   "No registrado en ia_fasa"),
    ("3 - Agentes IA",            "Predicción rechazo (modelo ML)",      "[SIN FUENTE]",                     "[SIN FUENTE]",           "SIN FUENTE",   "Requiere modelo entrenado externo"),

    # PANEL 4 — Protocolos
    ("4 - Protocolos de acción",  "Catálogo de protocolos",              "[ESTÁTICO]",                       "[ESTÁTICO]",             "ESTÁTICO",     "No requiere BD — hardcodeado en HTML"),
    ("4 - Protocolos de acción",  "Responsables por protocolo",          "[ESTÁTICO]",                       "[ESTÁTICO]",             "ESTÁTICO",     ""),
    ("4 - Protocolos de acción",  "Pasos del protocolo",                 "[ESTÁTICO]",                       "[ESTÁTICO]",             "ESTÁTICO",     ""),

    # PANEL 5 — Simulación en vivo
    ("5 - Simulación en vivo",    "Scrap turno (sensor)",                "cscmega_01rechazosbyidticket",     "bRechazo (AVG)",          "CALCULAR",     "Requiere filtro por turno activo"),
    ("5 - Simulación en vivo",    "Temperatura horno en tiempo real",    "[SIN FUENTE]",                     "[SIN FUENTE]",           "SIN FUENTE",   "No hay sensor RT en ia_fasa"),
    ("5 - Simulación en vivo",    "CE% en tiempo real",                  "cscmega_05cquimicosbase",          "aB_C / aB_Si",           "OK*",          "Última muestra SpectroMAX por colada activa"),
    ("5 - Simulación en vivo",    "LOI arena en tiempo real",            "[SIN FUENTE]",                     "[SIN FUENTE]",           "SIN FUENTE",   "No registrado"),
]

cols = ["Panel", "Campo del panel", "Tabla fuente", "Columna fuente", "Estado", "Nota / Brecha"]
df_mapeo = pd.DataFrame(MAPEO, columns=cols)

# ── Estadísticas de cobertura ────────────────────────────────────────────────
total = len(df_mapeo)
ok    = df_mapeo["Estado"].str.startswith("OK").sum()
calc  = (df_mapeo["Estado"] == "CALCULAR").sum()
static = (df_mapeo["Estado"] == "ESTÁTICO").sum()
sin_f = (df_mapeo["Estado"] == "SIN FUENTE").sum()

resumen = pd.DataFrame([
    ["Total campos mapeados",       total],
    ["OK — fuente directa en ia_fasa", ok],
    ["CALCULAR — derivable de ia_fasa", calc],
    ["ESTÁTICO — no requiere BD",    static],
    ["SIN FUENTE — brecha",         sin_f],
    ["Cobertura ia_fasa (%)",       f"{round((ok+calc)/total*100,1)}%"],
], columns=["Métrica", "Valor"])

# ── Exportar ─────────────────────────────────────────────────────────────────
with pd.ExcelWriter(OUTFILE, engine="openpyxl") as xw:
    df_mapeo.to_excel(xw, sheet_name="mapeo_paneles", index=False)
    resumen.to_excel(xw, sheet_name="resumen", index=False)

print(f"Exportado: {OUTFILE}")
print(resumen.to_string(index=False))
