"""
Causa raíz (2026-10-30) -- vista dentro de Calidad del Producto.

Las cifras NO se calculan aquí: las precalcula scripts/causa_raiz/generar.py (~4 min, una vez por
semana) en backend/data/causa_raiz.json. Cruza 15k+ coladas contra química, ITACA, temperatura,
horno y demoras, demasiado pesado para cada clic. Este router solo sirve ese JSON y guarda el
estado de cada hipótesis que captura Calidad (por validar / confirmada / descartada + nota) en
backend/data/causa_raiz_estado.json -- archivo local, la BD es de solo lectura.
"""
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["causa-raiz"])

_DATA = Path(__file__).resolve().parents[1] / "data"
_RESUMEN = _DATA / "causa_raiz.json"
_ESTADO = _DATA / "causa_raiz_estado.json"
_ESTADOS = ("por_validar", "confirmada", "descartada")
_LOCK = threading.Lock()
_CACHE = {"mtime": None, "data": None}


def _resumen() -> dict:
    if not _RESUMEN.exists():
        raise HTTPException(503, "Aún no se ha generado el análisis: correr python scripts/causa_raiz/generar.py")
    mtime = _RESUMEN.stat().st_mtime
    if _CACHE["mtime"] != mtime:
        _CACHE["data"] = json.loads(_RESUMEN.read_text(encoding="utf-8"))
        _CACHE["mtime"] = mtime
    return _CACHE["data"]


def _leer_estado() -> dict:
    if not _ESTADO.exists():
        return {}
    try:
        return json.loads(_ESTADO.read_text(encoding="utf-8"))
    except ValueError:
        return {}


@router.get("/v3/calidad/causa-raiz")
def causa_raiz():
    datos = _resumen()
    estado = _leer_estado()
    tarjetas = [{**t, "estado": estado.get(t["clave"], {"estado": "por_validar"})} for t in datos["tarjetas"]]
    return {**datos, "tarjetas": tarjetas}


class EstadoIn(BaseModel):
    clave: str
    estado: str
    nota: Optional[str] = None
    autor: Optional[str] = None


@router.post("/v3/calidad/causa-raiz/estado")
def guardar_estado(body: EstadoIn):
    if body.estado not in _ESTADOS:
        raise HTTPException(400, f"estado debe ser uno de {', '.join(_ESTADOS)}")
    if body.clave not in {t["clave"] for t in _resumen()["tarjetas"]}:
        raise HTTPException(404, f"hipótesis desconocida: {body.clave}")
    with _LOCK:
        estado = _leer_estado()
        anterior = estado.get(body.clave)
        historial = (anterior or {}).get("historial", [])
        if anterior:
            historial = (historial + [{k: anterior.get(k) for k in ("estado", "nota", "autor", "fecha")}])[-20:]
        estado[body.clave] = {
            "estado": body.estado,
            "nota": (body.nota or "").strip()[:2000] or None,
            "autor": (body.autor or "").strip()[:80] or None,
            "fecha": datetime.now().isoformat(timespec="seconds"),
            "historial": historial,
        }
        tmp = _ESTADO.with_suffix(".tmp")
        tmp.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_ESTADO)
    return estado[body.clave]
