"""
AGENTE ALFA/BETA — chat conversacional (tool-use), 2026-08-05
"""
# Agente REAL (LLM con tool-use) construido sobre los endpoints ya existentes de
# Alfa/Beta/Plan de Producción -- las tools son wrappers delgados que llaman
# directo a las funciones Python (mismo proceso, sin HTTP), nunca inventan datos.
# Regla dura del system prompt: solo puede afirmar lo que vino de un tool result.

import json
import anthropic
from anthropic import beta_tool
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.routers.alfa import (
    alfa_evaluar, alfa_cuello_botella, alfa_clientes, alfa_partes_cliente, programa_mensual,
)
from backend.routers.beta import beta_evaluar, beta_riesgo_alto

router = APIRouter(tags=["chat"])


@beta_tool
def evaluar_parte(no_parte: str, dias: int = 14) -> str:
    """Ritmo real, ciclo por etapa, proceso/cliente y proyección de entrega para una parte.

    Args:
        no_parte: Número de parte exacto (ej. "2C-2473-000"). Si no lo conoces, usa
            listar_clientes_con_actividad + partes_de_cliente para encontrarlo primero.
        dias: Ventana de días hacia atrás para calcular el ritmo real.
    """
    return json.dumps(alfa_evaluar(no_parte=no_parte, dias=dias), default=str)


@beta_tool
def cuello_de_botella(dias: int = 14) -> str:
    """Detecta si algún proceso (AF1/AF2/AF3) está más lento de lo normal en alguna etapa
    (Molde→Vaciado, Vaciado→Desmoldeo, Desmoldeo→Limpieza), comparando los últimos `dias`
    contra un baseline de las 4 ventanas anteriores. Lista vacía = sin cuello de botella.

    Args:
        dias: tamaño de la ventana reciente en días.
    """
    return json.dumps(alfa_cuello_botella(dias=dias), default=str)


@beta_tool
def listar_clientes_con_actividad() -> str:
    """Lista de clientes con actividad de producción real en los últimos 90 días,
    ordenados por piezas. Úsalo para encontrar el nombre exacto de un cliente antes
    de llamar a partes_de_cliente."""
    return json.dumps(alfa_clientes(), default=str)


@beta_tool
def partes_de_cliente(cliente: str, dias: int = 14) -> str:
    """Partes con actividad reciente de un cliente específico, con ritmo (piezas/día)
    y proceso. Úsalo para encontrar el No_Parte exacto de las partes de un cliente.

    Args:
        cliente: Nombre exacto del cliente (usar listar_clientes_con_actividad para verlo).
        dias: ventana en días.
    """
    return json.dumps(alfa_partes_cliente(cliente=cliente, dias=dias), default=str)


@beta_tool
def riesgo_calidad_parte(no_parte: str) -> str:
    """Criticidad histórica de rechazo y disponibilidad de compuerta química para una
    parte (Agente Beta) -- útil si preguntan por qué una parte tiene problemas de calidad.

    Args:
        no_parte: Número de parte exacto.
    """
    return json.dumps(beta_evaluar(no_parte=no_parte), default=str)


@beta_tool
def partes_riesgo_alto(limit: int = 20) -> str:
    """Lista de partes con criticidad histórica de rechazo alta (Agente Beta).

    Args:
        limit: máximo de partes a devolver.
    """
    return json.dumps(beta_riesgo_alto(limit=limit), default=str)


@beta_tool
def plan_produccion_mes(mes: str) -> str:
    """Plan de producción mensual: ritmos objetivo por proceso y saldo pendiente por
    parte -- el saldo NO es una fuente autoritativa, acláralo si lo usas en tu respuesta.
    Devuelve un mensaje de error si no hay plan cargado para ese mes.

    Args:
        mes: formato YYYY-MM, ej. "2026-08".
    """
    try:
        return json.dumps(programa_mensual(mes=mes), default=str)
    except HTTPException as e:
        return json.dumps({"error": e.detail})


# Tools compartidas por Alfa y Beta -- ambos agentes conversacionales pueden cruzar
# flujo/ritmo y calidad/criticidad cuando la pregunta lo amerita, sin duplicar definiciones.
_TORRE_CHAT_TOOLS = [
    evaluar_parte, cuello_de_botella, listar_clientes_con_actividad,
    partes_de_cliente, riesgo_calidad_parte, partes_riesgo_alto, plan_produccion_mes,
]

_REGLAS_GROUNDING = """
Reglas estrictas:
- Nunca inventes un número, fecha o nombre que no venga literalmente de un resultado de
  herramienta. Si no tienes el dato, dilo explícitamente -- no rellenes con una suposición.
- El saldo pendiente y la proyección de entrega vienen del Plan de Producción Mensual, que
  NO es una fuente autoritativa -- si los usas en tu respuesta, dilo claramente.
- Si preguntan por una parte y no sabes el No_Parte exacto, usa listar_clientes_con_actividad
  y partes_de_cliente para encontrarla en vez de preguntarle al usuario el número exacto.
- Responde en español, breve y directo, como si hablaras con un supervisor de piso -- sin
  relleno, sin repetir los datos crudos, solo la conclusión y el número que la respalda."""

_ALFA_CHAT_SYSTEM = """Eres el asistente del Agente Alfa de la Torre de Control FASA (fundición).
Respondes preguntas sobre ritmo de producción, cuellos de botella y proyección de entrega,
usando SOLO datos reales obtenidos con las herramientas disponibles. También tienes acceso a
las herramientas de calidad (Agente Beta) -- úsalas si la pregunta cruza a riesgo de rechazo.
""" + _REGLAS_GROUNDING

_BETA_CHAT_SYSTEM = """Eres el asistente del Agente Beta de la Torre de Control FASA (fundición).
Respondes preguntas sobre criticidad histórica de rechazo y disponibilidad de la compuerta
química de una parte, usando SOLO datos reales obtenidos con las herramientas disponibles.
También tienes acceso a las herramientas de flujo (Agente Alfa) -- úsalas si la pregunta cruza
a ritmo de producción, cuello de botella o entrega.
""" + _REGLAS_GROUNDING


class ChatRequest(BaseModel):
    pregunta: str
    historial: list = []  # [{"rol": "user"|"assistant", "texto": "..."}], opcional


def _correr_chat(system: str, req: ChatRequest) -> dict:
    client = anthropic.Anthropic()

    messages = [{"role": t["rol"], "content": t["texto"]} for t in req.historial]
    messages.append({"role": "user", "content": req.pregunta})

    runner = client.beta.messages.tool_runner(
        model="claude-opus-5",
        max_tokens=2048,
        system=system,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        tools=_TORRE_CHAT_TOOLS,
        messages=messages,
    )

    last_message = None
    herramientas_usadas = []
    for message in runner:
        last_message = message
        for block in message.content:
            if block.type == "tool_use":
                herramientas_usadas.append({"tool": block.name, "input": block.input})

    respuesta = ""
    if last_message:
        respuesta = next((b.text for b in last_message.content if b.type == "text"), "")

    return {"respuesta": respuesta, "herramientas_usadas": herramientas_usadas}


@router.post("/api/alfa/chat")
def alfa_chat(req: ChatRequest):
    return _correr_chat(_ALFA_CHAT_SYSTEM, req)


@router.post("/api/beta/chat")
def beta_chat(req: ChatRequest):
    return _correr_chat(_BETA_CHAT_SYSTEM, req)
