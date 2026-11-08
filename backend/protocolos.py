"""
Protocolos de acción PROVISIONALES por punto de control (2026-11-08).

Hasta el 8-nov, P-01 a P-07 eran solo códigos en core.CARRILES, sin contenido: la Torre decía
"Activar P-03" sin decir qué hacer. JC aprobó usar estos protocolos de bajo riesgo mientras
Calidad valida las reglas (doc "Torre de Control FASA · Estado de los protocolos de acción por
punto de control", 8-nov-2026): confirmar el dato, contener las piezas y avisar; solo cambian el
proceso donde la evidencia ya es clara. NO son protocolos oficiales de Calidad -- cuando Calidad
los redacte se reemplaza este catálogo, la Torre no cambia.

Anclados al Plan de Control de CSC (mismo día, decisión de JC): ninguna tabla trae un "plan de
reacción", pero los Excel "Var Proceso" (backend/plan_control_lector.py) dicen por variable quién
mide, quién controla, el estándar (TBL-OPE-IIP-00x, FOR-OPE-FUS-009...) y la ventana de decisión.
Cada protocolo lista sus variables y el responsable sale de "Controlar" en vivo; si el Excel no
trae la variable, queda el respaldo escrito aquí. Las acciones remiten al estándar oficial en
vez de inventar el cómo; el contenido de los instructivos TBL/INS/FOR no está en el sistema.

Variantes nodular (PC-2 y PC-3): en hierro nodular la copa ITACA sale en rojo en el 100 % de
las coladas (7 días al 8-nov) y la química trae Mg y S bajos. El Plan de Control fija 13 min
máximo de vida del tratamiento nodular (OPE-FUS-022): pasado ese tiempo el Mg se desvanece, que
es lo que se observa -- por eso el protocolo nodular empieza por verificar ese tiempo y el
FeSiMg en molde (OPE-AF2-016), y en PC-3 no detiene el horno mientras se validan los umbrales.
Nodular = clase con forma de grado ASTM A536 (65-45-12, 100-70-03, 80-55-06...).

Tiempo = latencia objetivo de cada carril en core.CARRILES (minutos).
"""
import re
import time
from typing import Optional

from backend.core import CARRILES

_LATENCIA = {c.id_pc: c.latencia_objetivo for c in CARRILES}
_CODIGO = {c.id_pc: c.protocolo for c in CARRILES}

_VERIFICA_NODULAR = ("Verificar primero el tiempo de tratamiento nodular (OPE-FUS-022, máx. 13 min hasta terminar "
                     "de vaciar) y el FeSiMg en molde (OPE-AF2-016).")

PROTOCOLOS = {
    "PC-1": {
        "variables": ["OPE-FUS-005"],
        "ambar": "Solo registrar.",
        "rojo": "Verificar báscula y captura contra la receta de Corex. Si el horno aún no funde, corregir la carga; si ya fundió, esperar la química antes de ajustar.",
        "responsable": "Preparador de cargas",
        "liberar": "Carga corregida o química en especificación",
    },
    "PC-2": {
        "variables": ["LAB-FUS-002", "OPE-FUS-010"],
        "ambar": "Repetir la muestra para confirmar.",
        "rojo": "No liberar el horno hasta un reanálisis en especificación; ajustar según TBL-OPE-IIP-001/002/003. Si ya se vació, identificar y retener las piezas de la colada.",
        "responsable": "Operador de metal líquido",
        "liberar": "Reanálisis en especificación",
    },
    "PC-2 nodular": {
        "variables": ["LAB-FUS-002", "OPE-FUS-022", "OPE-AF2-016"],
        "ambar": "Repetir la muestra para confirmar. " + _VERIFICA_NODULAR,
        "rojo": _VERIFICA_NODULAR + " No liberar hasta un reanálisis en especificación (TBL-OPE-IIP-001/002/003); si ya se vació, retener las piezas de la colada.",
        "responsable": "Operador de metal líquido",
        "liberar": "Reanálisis en especificación",
    },
    "PC-3": {
        "variables": ["OPE-FUS-010", "OPE-FUS-021"],
        "ambar": "Repetir la copa.",
        "rojo": "No liberar; reinocular según TBL-OPE-IIP-004/005, revisar la vida de inoculación (OPE-FUS-021, máx. 13 min) y repetir la copa.",
        "responsable": "Operador de metal líquido",
        "liberar": "Copa repetida ≥ 70",
    },
    "PC-3 nodular": {
        "variables": ["OPE-FUS-010", "OPE-FUS-022", "OPE-AF2-016"],
        "ambar": "Registrar. " + _VERIFICA_NODULAR,
        "rojo": "No detener: " + _VERIFICA_NODULAR[0].lower() + _VERIFICA_NODULAR[1:] + " Inspección reforzada de las piezas de la colada mientras Calidad valida los umbrales de ITACA para nodular.",
        "responsable": "Operador de metal líquido",
        "tambien": "Calidad (inspección)",
        "tiempo": "antes de embarcar",
        "liberar": "Inspección OK",
    },
    "PC-4": {
        "variables": ["OPE-FUS-019", "OPE-FUS-018"],
        "ambar": "Corregir la temperatura en los siguientes moldes según la etiqueta FOR-OPE-FUS-009.",
        "rojo": "Frío: no vaciar piezas de pared delgada, recalentar. Más de 15 °C arriba: esperar a que baje. Rango de la etiqueta FOR-OPE-FUS-009. Piezas ya vaciadas frías: revisar fierro frío y no lleno.",
        "responsable": "Vaciador",
        "liberar": "Temperatura en rango",
    },
    "PC-5": {
        "variables": [],
        "ambar": "Completar el registro de desmoldeo.",
        "rojo": "Sin protocolo de proceso: completar el registro de desmoldeo (hoy cubre pocas coladas).",
        "responsable": "Supervisor de área e IT",
        "liberar": None,
    },
    "PC-6": {
        "variables": [],
        "ambar": None,
        "rojo": "Buscar la causa con el panel de CSC (puesto y elemento por defecto); si la parte ya tenía riesgo alto según Agente Beta, abrir análisis de causa y vigilar sus siguientes coladas.",
        "responsable": "Calidad",
        "liberar": "Causa identificada",
    },
    "PC-7": {
        "variables": [],
        "ambar": "Completar el registro de limpieza.",
        "rojo": "Sin protocolo de proceso: completar el registro de la ruta de limpieza.",
        "responsable": "Supervisor de área e IT",
        "liberar": None,
    },
}

_NODULAR = re.compile(r"^\s*\d{2,3}-\d{2}-\d{2}")
_PLAN_CACHE: dict = {}
_PLAN_TTL_SEG = 600  # los Excel se leen de disco; las alertas piden el catálogo una vez por alerta


def es_nodular(clase: Optional[str]) -> bool:
    return bool(clase and _NODULAR.match(clase))


def _plan_por_variable() -> dict:
    hit = _PLAN_CACHE.get("t")
    if hit and time.time() - hit < _PLAN_TTL_SEG:
        return _PLAN_CACHE["vars"]
    try:
        from backend.plan_control_lector import leer_plan_control
        vs = (leer_plan_control() or {}).get("variables") or []
    except Exception:  # sin carpeta o Excel ilegible: el catálogo sigue con sus respaldos
        vs = []
    limpio = lambda s: " ".join(str(s or "").split()) or None
    _PLAN_CACHE["vars"] = {v["variable_id"]: {
        "id": v["variable_id"], "proceso": v.get("proceso"), "variable": limpio(v.get("variable")),
        "mide": limpio(v.get("responsable_medir")), "controla": limpio(v.get("responsable_controlar")),
        "estandar": limpio(v.get("estandar")), "ventana": limpio(v.get("ventana_decision")),
    } for v in vs if v.get("variable_id")}
    _PLAN_CACHE["t"] = time.time()
    return _PLAN_CACHE["vars"]


def catalogo() -> dict:
    """Catálogo completo con código P-0X, tiempo objetivo y variables del Plan de Control."""
    plan = _plan_por_variable()
    out = {}
    for k, p in PROTOCOLOS.items():
        pc = k.split(" ")[0]
        lat = _LATENCIA.get(pc)
        variables = [plan[v] for v in p["variables"] if v in plan]
        # responsable oficial: quien "Controla" la primera variable en el Plan de Control
        resp = next((v["controla"] for v in variables if v.get("controla")), None) or p["responsable"]
        if p.get("tambien"):
            resp += " y " + p["tambien"]
        out[k] = {**p, "clave": k, "codigo": _CODIGO.get(pc), "pc": pc, "responsable": resp,
                  "plan_control": variables,
                  "tiempo": p.get("tiempo") or (f"{lat} min" if lat else None)}
    return {"provisional": True,
            "nota": "Protocolos provisionales aprobados el 8-nov-2026 mientras Calidad valida las reglas; "
                    "responsables, variables y estándares del Plan de Control de CSC.",
            "protocolos": out}


def protocolo(id_pc: str, estado: str, clase: Optional[str] = None) -> Optional[dict]:
    """La acción que toca para un PC en ÁMBAR o ROJO (None en VERDE/GRIS o sin acción)."""
    if estado not in ("AMBAR", "ROJO"):
        return None
    cat = catalogo()["protocolos"]
    clave = f"{id_pc} nodular" if es_nodular(clase) and f"{id_pc} nodular" in cat else id_pc
    p = cat.get(clave)
    if not p:
        return None
    accion = p["rojo"] if estado == "ROJO" else p["ambar"]
    if not accion:
        return None
    return {"clave": clave, "codigo": p["codigo"], "accion": accion, "responsable": p["responsable"],
            "tiempo": p["tiempo"], "liberar": p["liberar"] if estado == "ROJO" else None,
            "variables": [v["id"] for v in p["plan_control"]]}
