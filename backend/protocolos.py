"""
Protocolos de acción PROVISIONALES por punto de control (2026-11-08).

Hasta el 8-nov, P-01 a P-07 eran solo códigos en core.CARRILES, sin contenido: la Torre decía
"Activar P-03" sin decir qué hacer. JC aprobó usar estos protocolos de bajo riesgo mientras
Calidad valida las reglas (doc "Torre de Control FASA · Estado de los protocolos de acción por
punto de control", 8-nov-2026): confirmar el dato, contener las piezas y avisar; solo cambian el
proceso donde la evidencia ya es clara. NO son protocolos oficiales de Calidad -- cuando Calidad
los redacte se reemplaza este catálogo, la Torre no cambia.

PC-3 tiene dos variantes: en hierro nodular la copa ITACA sale en rojo en el 100 % de las
coladas (7 días al 8-nov) mientras se validan sus umbrales, así que ahí el rojo NO detiene el
horno: pide inspección reforzada. Nodular = clase con forma de grado ASTM A536 (65-45-12,
100-70-03, 80-55-06...); gris = el resto (30, 30C, 35 A,B...).

Tiempo = latencia objetivo que ya trae cada carril en core.CARRILES (minutos).
"""
import re
from typing import Optional

from backend.core import CARRILES

_LATENCIA = {c.id_pc: c.latencia_objetivo for c in CARRILES}
_CODIGO = {c.id_pc: c.protocolo for c in CARRILES}

PROTOCOLOS = {
    "PC-1": {
        "ambar": "Solo registrar.",
        "rojo": "Verificar báscula y captura. Si el horno aún no funde, corregir la carga; si ya fundió, esperar la química antes de ajustar.",
        "responsable": "Jefe de turno de fusión",
        "liberar": "Carga corregida o química en especificación",
    },
    "PC-2": {
        "ambar": "Repetir la muestra para confirmar.",
        "rojo": "No liberar el horno hasta un reanálisis en especificación; el ajuste con ferroaleación lo decide el metalurgista. Si ya se vació, identificar y retener las piezas de la colada.",
        "responsable": "Metalurgista y laboratorio",
        "liberar": "Reanálisis en especificación",
    },
    "PC-3": {
        "ambar": "Repetir la copa.",
        "rojo": "No liberar; reinocular y repetir la copa (porosidad, rechupe y cementita apuntan a inoculación y temperatura).",
        "responsable": "Metalurgista",
        "liberar": "Copa repetida ≥ 70",
    },
    "PC-3 nodular": {
        "ambar": "Registrar.",
        "rojo": "No detener: inspección reforzada de las piezas de la colada mientras Calidad valida los umbrales de ITACA para nodular.",
        "responsable": "Calidad",
        "tiempo": "antes de embarcar",
        "liberar": "Inspección OK",
    },
    "PC-4": {
        "ambar": "Avisar al vaciador y corregir en los siguientes moldes.",
        "rojo": "Frío: no vaciar piezas de pared delgada, recalentar. Más de 15 °C arriba: esperar a que baje. Piezas ya vaciadas frías: revisar fierro frío y no lleno.",
        "responsable": "Supervisor de vaciado",
        "liberar": "Temperatura en rango",
    },
    "PC-5": {
        "ambar": "Completar el registro de desmoldeo.",
        "rojo": "Sin protocolo de proceso: completar el registro de desmoldeo (hoy cubre pocas coladas).",
        "responsable": "Supervisor de área e IT",
        "liberar": None,
    },
    "PC-6": {
        "ambar": None,
        "rojo": "Buscar la causa con el panel de CSC (puesto y elemento por defecto); si la parte ya tenía riesgo alto según Agente Beta, abrir análisis de causa y vigilar sus siguientes coladas.",
        "responsable": "Calidad",
        "liberar": "Causa identificada",
    },
    "PC-7": {
        "ambar": "Completar el registro de limpieza.",
        "rojo": "Sin protocolo de proceso: completar el registro de la ruta de limpieza.",
        "responsable": "Supervisor de área e IT",
        "liberar": None,
    },
}

_NODULAR = re.compile(r"^\s*\d{2,3}-\d{2}-\d{2}")


def es_nodular(clase: Optional[str]) -> bool:
    return bool(clase and _NODULAR.match(clase))


def catalogo() -> dict:
    """Catálogo completo con código P-0X y tiempo objetivo, para el frontend."""
    out = {}
    for k, p in PROTOCOLOS.items():
        pc = k.split(" ")[0]
        lat = _LATENCIA.get(pc)
        out[k] = {**p, "codigo": _CODIGO.get(pc), "pc": pc,
                  "tiempo": p.get("tiempo") or (f"{lat} min" if lat else None)}
    return {"provisional": True,
            "nota": "Protocolos provisionales aprobados el 8-nov-2026 mientras Calidad valida las reglas.",
            "protocolos": out}


def protocolo(id_pc: str, estado: str, clase: Optional[str] = None) -> Optional[dict]:
    """La acción que toca para un PC en ÁMBAR o ROJO (None en VERDE/GRIS o sin acción)."""
    if estado not in ("AMBAR", "ROJO"):
        return None
    clave = "PC-3 nodular" if id_pc == "PC-3" and es_nodular(clase) else id_pc
    p = catalogo()["protocolos"].get(clave)
    if not p:
        return None
    accion = p["rojo"] if estado == "ROJO" else p["ambar"]
    if not accion:
        return None
    return {"clave": clave, "codigo": p["codigo"], "accion": accion, "responsable": p["responsable"],
            "tiempo": p["tiempo"], "liberar": p["liberar"] if estado == "ROJO" else None}
