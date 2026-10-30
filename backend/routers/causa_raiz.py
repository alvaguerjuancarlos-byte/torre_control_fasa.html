"""
Causa raíz (2026-10-30) -- vista dentro de Calidad del Producto.

Las cifras NO se calculan en cada consulta: las precalcula scripts/causa_raiz/generar.py (~8 min)
en backend/data/causa_raiz.json. Cruza 15k+ coladas contra química, ITACA, temperatura, horno,
demoras y arena, demasiado pesado para cada clic. Este router sirve ese JSON y guarda el estado de
cada hipótesis que captura Calidad (por validar / confirmada / descartada + nota) en
backend/data/causa_raiz_estado.json -- archivo local, la BD es de solo lectura.

Recálculo semanal: lo dispara el propio servidor (en esta máquina Windows no deja registrar tareas
programadas, ni como administrador). Un hilo revisa cada 30 min; toca recalcular si ya pasó el
lunes 05:30 de esta semana y el JSON es anterior, o si el JSON tiene más de 7 días (el servidor
estuvo apagado el lunes). Corre generar.py en un PROCESO aparte y con prioridad baja, para no
cargarle memoria ni CPU al servidor; log en logs/causa_raiz_semanal.log.
"""
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["causa-raiz"])

_RAIZ = Path(__file__).resolve().parents[2]
_DATA = Path(__file__).resolve().parents[1] / "data"
_RESUMEN = _DATA / "causa_raiz.json"
_ESTADO = _DATA / "causa_raiz_estado.json"
_ESTADOS = ("por_validar", "confirmada", "descartada")
_LOCK = threading.Lock()
_CACHE = {"mtime": None, "data": None}

_LOG = _RAIZ / "logs" / "causa_raiz_semanal.log"
_CANDADO = _DATA / "cache" / "recalculo.lock"     # evita dos recálculos a la vez (p. ej. dos servidores)
_RECALCULO = {"corriendo": False, "inicio": None, "fin": None, "codigo": None}
_HORA_SEMANAL = (0, 5, 30)        # lunes 05:30
_REVISAR_CADA_SEG = 30 * 60
_ESPERA_ARRANQUE_SEG = 10 * 60    # deja terminar el precalentado de main.py antes de pegarle a la BD
_CANDADO_VENCE_SEG = 3 * 3600


def _ultimo_corte(ahora: datetime) -> datetime:
    """Lunes 05:30 más reciente que ya pasó."""
    dia, h, m = _HORA_SEMANAL
    corte = (ahora - timedelta(days=(ahora.weekday() - dia) % 7)).replace(hour=h, minute=m, second=0, microsecond=0)
    return corte if corte <= ahora else corte - timedelta(days=7)


def _generado() -> Optional[datetime]:
    try:
        return datetime.fromisoformat(json.loads(_RESUMEN.read_text(encoding="utf-8"))["generado"])
    except Exception:
        return None


def _toca_recalcular(ahora: datetime) -> bool:
    gen = _generado()
    return gen is None or gen < _ultimo_corte(ahora) or ahora - gen > timedelta(days=7)


def _tomar_candado() -> bool:
    _CANDADO.parent.mkdir(parents=True, exist_ok=True)
    if _CANDADO.exists() and time.time() - _CANDADO.stat().st_mtime < _CANDADO_VENCE_SEG:
        return False
    _CANDADO.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _recalcular():
    """Corre generar.py en un proceso aparte (prioridad baja) y espera a que termine."""
    if not _tomar_candado():
        return
    _RECALCULO.update(corriendo=True, inicio=datetime.now().isoformat(timespec="seconds"), fin=None, codigo=None)
    try:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG, "a", encoding="utf-8") as log:
            log.write(f"==== {datetime.now():%Y-%m-%d %H:%M:%S} inicio (automático, servidor)\n")
            log.flush()
            flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
            p = subprocess.Popen([sys.executable, str(_RAIZ / "scripts" / "causa_raiz" / "generar.py")],
                                 cwd=str(_RAIZ), stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
                                 env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            codigo = p.wait()
            log.write(f"==== {datetime.now():%Y-%m-%d %H:%M:%S} fin (código {codigo})\n")
        _RECALCULO.update(codigo=codigo)
    except Exception as e:
        _RECALCULO.update(codigo=f"error: {e}")
    finally:
        _RECALCULO.update(corriendo=False, fin=datetime.now().isoformat(timespec="seconds"))
        _CANDADO.unlink(missing_ok=True)


def _programador():
    time.sleep(_ESPERA_ARRANQUE_SEG)
    while True:
        try:
            if not _RECALCULO["corriendo"] and _toca_recalcular(datetime.now()):
                _recalcular()
        except Exception:
            pass  # un error en la revisión no debe tumbar el hilo; se reintenta en la siguiente vuelta
        time.sleep(_REVISAR_CADA_SEG)


@router.on_event("startup")
def _iniciar_programador():
    threading.Thread(target=_programador, daemon=True, name="causa-raiz-semanal").start()


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
    ahora = datetime.now()
    proximo = _ultimo_corte(ahora) + timedelta(days=7)
    return {**datos, "tarjetas": tarjetas,
            "recalculo": {**_RECALCULO, "proximo": proximo.isoformat(timespec="minutes")}}


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
