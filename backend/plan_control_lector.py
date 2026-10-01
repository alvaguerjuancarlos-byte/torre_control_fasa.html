"""
Lector del Plan de Control por proceso, directo de los Excel "Var Proceso
<ÁREA>.xlsx" -- mismo criterio que plan_lector.py (Plan de Producción Mensual):
sin tabla en ia_fasa/corex_test confirmada como viva y pareada con estos
estándares (ver auditoría 2026-09-29/30 e investigación 2026-10-01 sobre
corex_test.variablesproceso* -- la familia "vieja" está congelada desde
2026-03-11 y no se pudo confirmar cuál tabla "nueva" la reemplaza, pendiente
de que Jasso confirme). Mientras tanto, el Excel que CSC actualiza y comparte
por correo (ver MINUTA TORRE DE CONTROL 30092026.docx, compromiso #2) es la
fuente más confiable disponible -- se lee en vivo, nunca se escribe nada.

Primera carga: 2026-09-30 (9 archivos: 8 Var Proceso *.xlsx + la minuta, que
no se parsea aquí). Si llega una actualización, basta con reemplazar los
archivos en PLAN_CONTROL_DIR -- este lector siempre refleja lo que haya en
disco en el momento de la consulta, no cachea entre requests.
"""
import re
from pathlib import Path
from typing import Optional

import openpyxl

PLAN_CONTROL_DIR = Path(
    r"C:\Users\Administrator\Documents\FASA\plan de control actualizado sep 2026 y minuta de torre de control"
)

# "Var Proceso AF2.xlsx" -> "AF2" ; "Var Proceso SOLDADURA, EMPAQUE y PINTURA.xlsx" -> "SOLDADURA, EMPAQUE Y PINTURA"
_PREFIJO = re.compile(r"^Var Proceso\s+(.+)\.xlsx$", re.I)

# "OPE-AF2-001 - Identificación No. Parte" -> ("OPE-AF2-001", "Identificación No. Parte")
_VARIABLE_RE = re.compile(r"^([A-Z]+-[A-Z0-9]+-\d+)\s*-\s*(.*)$", re.S)


def _split_variable(valor) -> tuple:
    s = str(valor or "").strip()
    m = _VARIABLE_RE.match(s)
    if m:
        return m.group(1), m.group(2).strip()
    return None, s


def _archivos() -> list:
    if not PLAN_CONTROL_DIR.is_dir():
        return []
    return sorted(
        p for p in PLAN_CONTROL_DIR.glob("Var Proceso *.xlsx")
        if not p.name.startswith("~$")
    )


def _leer_archivo(path: Path) -> list:
    proceso = _PREFIJO.match(path.name)
    proceso = proceso.group(1).strip().upper() if proceso else path.stem.upper()

    wb = openpyxl.load_workbook(path, data_only=True)
    if "VAR_PRO" not in wb.sheetnames:
        return []
    ws = wb["VAR_PRO"]

    filas = []
    # Fila 1: encabezados ; fila 2: subencabezados Medir/Controlar bajo "Responsable" ;
    # datos desde la fila 3. Columnas: A=Área B=Variable C=Frecuencia D=Equipo
    # E=Responsable-Medir F=Responsable-Controlar G=Estándar H=Ventana I=Notas (opcional).
    for row in ws.iter_rows(min_row=3, values_only=True):
        area_control = row[0]
        if area_control in (None, ""):
            continue
        variable_id, variable_desc = _split_variable(row[1])
        filas.append({
            "proceso":               proceso,
            "area_control":          str(area_control).strip(),
            "variable_id":           variable_id,
            "variable":              variable_desc,
            "frecuencia_medicion":   str(row[2]).strip() if row[2] else None,
            "equipo_medicion":       str(row[3]).strip() if row[3] else None,
            "responsable_medir":     str(row[4]).strip() if row[4] else None,
            "responsable_controlar": str(row[5]).strip() if row[5] else None,
            "estandar":              str(row[6]).strip() if row[6] else None,
            "ventana_decision":      str(row[7]).strip() if len(row) > 7 and row[7] else None,
            "notas":                 str(row[8]).strip() if len(row) > 8 and row[8] else None,
        })
    return filas


def leer_plan_control(proceso: Optional[str] = None) -> dict:
    """Devuelve {fuente, archivos, procesos, variables}. `proceso` (opcional) filtra
    por el nombre derivado del archivo (ej. 'AF2', 'ARENAS'), case-insensitive.
    None/[] si la carpeta no existe o no hay archivos -- nunca lanza por eso,
    es responsabilidad del caller decidir qué hacer con una lista vacía."""
    archivos = _archivos()
    variables = []
    for path in archivos:
        variables.extend(_leer_archivo(path))

    if proceso:
        proceso_up = proceso.strip().upper()
        variables = [v for v in variables if v["proceso"] == proceso_up]

    procesos = sorted({v["proceso"] for v in variables})
    return {
        "fuente":    "excel_vivo",
        "carpeta":   str(PLAN_CONTROL_DIR),
        "archivos":  [p.name for p in archivos],
        "procesos":  procesos,
        "variables": variables,
    }
