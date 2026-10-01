"""
Plan de Control por proceso -- expone en vivo los Excel "Var Proceso <ÁREA>.xlsx"
que CSC comparte por correo (ver plan_control_lector.py para el detalle de por
qué es Excel y no una tabla de BD). Primera carga: 2026-09-30, pedida por JC
("son varios excels... la idea es que estén vivos").
"""
from typing import Optional
from fastapi import APIRouter, Query

from backend.plan_control_lector import leer_plan_control

router = APIRouter(tags=["plan-control"])


@router.get("/api/plan-control")
def plan_control(
    proceso: Optional[str] = Query(default=None, description="Filtra por proceso, ej. AF2, ARENAS, FUSION"),
):
    return leer_plan_control(proceso)
