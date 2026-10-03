"""
AGENTE ALFA/BETA — chat conversacional (tool-use), 2026-08-05
"""
# Agente REAL (LLM con tool-use) construido sobre los endpoints ya existentes de
# Alfa/Beta/Plan de Producción -- las tools son wrappers delgados que llaman
# directo a las funciones Python (mismo proceso, sin HTTP), nunca inventan datos.
# Regla dura del system prompt: solo puede afirmar lo que vino de un tool result.

import json
from typing import Optional
import anthropic
from anthropic import beta_tool
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.routers.alfa import (
    alfa_evaluar, alfa_cuello_botella, alfa_clientes, alfa_partes_cliente, programa_mensual,
)
from backend.routers.beta import beta_evaluar, beta_riesgo_alto, tendencia_rechazo_mensual

router = APIRouter(tags=["chat"])

# Selector de horizonte de análisis (2026-10-03, JC): un botón único en cada pestaña
# (Mes actual/Trimestre/Semestre/Año) que el frontend manda como ChatRequest.periodo.
# Única fuente de verdad del mapeo horizonte -> parámetros de herramienta; el frontend
# (frontend/torre_v4.html, const PERIODOS_JS) replica el mismo literal -- no hay paso de
# build en este proyecto para compartirlo de verdad, así que si se cambia uno hay que
# cambiar el otro a mano.
HORIZONTES = {
    "mes":       {"label": "mes actual", "dias": 30,  "meses_baseline": 1},
    "trimestre": {"label": "trimestre",  "dias": 90,  "meses_baseline": 3},
    "semestre":  {"label": "semestre",   "dias": 180, "meses_baseline": 6},
    "anio":      {"label": "año",        "dias": 365, "meses_baseline": 12},
}


@beta_tool
def evaluar_parte(no_parte: str, dias: int = 14) -> str:
    """Ritmo real, ciclo por etapa, proceso/cliente y proyección de entrega para una parte.
    Trae "muestra_suficiente" (bool) -- si viene false, la pieza/ventana tiene muy pocas
    piezas terminadas para ser representativa: dilo y recomienda ampliar el horizonte
    (mes->trimestre->semestre->año), y si el horizonte pedido no era ya el máximo, considera
    volver a llamar esta misma tool con un `dias` mayor en el mismo turno. TOPE TÉCNICO: 90
    días -- si el horizonte seleccionado pide más (semestre/año), usa dias=90 y acláralo.

    Args:
        no_parte: Número de parte exacto (ej. "2C-2473-000"). Si no lo conoces, usa
            listar_clientes_con_actividad + partes_de_cliente para encontrarlo primero.
        dias: Ventana de días hacia atrás para calcular el ritmo real (máximo 90).
    """
    return json.dumps(alfa_evaluar(no_parte=no_parte, dias=dias), default=str)


@beta_tool
def cuello_de_botella(dias: int = 14) -> str:
    """Detecta si algún proceso (AF1/AF2/AF3) está más lento de lo normal en alguna etapa
    (Molde→Vaciado, Vaciado→Desmoldeo, Desmoldeo→Limpieza), comparando los últimos `dias`
    contra un baseline de las 4 ventanas anteriores. Lista vacía = sin cuello de botella.
    Trae "procesos_sin_dato" (lista, ej. ["AF2"]) -- un proceso ahí NO significa que esté
    sano, significa que no hay datos suficientes en la ventana para evaluarlo: dilo
    explícito, no lo omitas en silencio. TOPE TÉCNICO: 30 días -- si el horizonte
    seleccionado pide más, usa dias=30 y acláralo, no finjas una ventana mayor.

    Args:
        dias: tamaño de la ventana reciente en días (máximo 30).
    """
    return json.dumps(alfa_cuello_botella(dias=dias), default=str)


@beta_tool
def listar_clientes_con_actividad(dias: int = 90) -> str:
    """Lista de clientes con actividad de producción real en los últimos `dias` días,
    ordenados por piezas. Úsalo para encontrar el nombre exacto de un cliente antes
    de llamar a partes_de_cliente. TOPE TÉCNICO: 90 días.

    Args:
        dias: ventana en días (máximo 90).
    """
    return json.dumps(alfa_clientes(dias=dias), default=str)


@beta_tool
def partes_de_cliente(cliente: str, dias: int = 14) -> str:
    """Partes con actividad reciente de un cliente específico, con ritmo (piezas/día)
    y proceso. Úsalo para encontrar el No_Parte exacto de las partes de un cliente.
    TOPE TÉCNICO: 90 días.

    Args:
        cliente: Nombre exacto del cliente (usar listar_clientes_con_actividad para verlo).
        dias: ventana en días (máximo 90).
    """
    return json.dumps(alfa_partes_cliente(cliente=cliente, dias=dias), default=str)


@beta_tool
def riesgo_calidad_parte(no_parte: str) -> str:
    """Criticidad histórica de rechazo y disponibilidad de compuerta química para una
    parte (Agente Beta) -- útil si preguntan por qué una parte tiene problemas de calidad.
    El resultado trae un campo "periodo" (dentro de compuerta_criticidad) que dice
    exactamente qué años cubre -- siempre repítelo literal, no digas solo "histórico"
    o "sin corte de período" sin más, el usuario quiere saber qué años son.

    Args:
        no_parte: Número de parte exacto.
    """
    return json.dumps(beta_evaluar(no_parte=no_parte), default=str)


@beta_tool
def partes_riesgo_alto(limit: int = 20) -> str:
    """Lista de partes con criticidad histórica de rechazo alta (Agente Beta).
    El resultado trae un campo "periodo" que dice exactamente qué años cubre --
    siempre repítelo literal, no digas solo "histórico" o "sin corte de período"
    sin más, el usuario quiere saber qué años son.

    Args:
        limit: máximo de partes a devolver.
    """
    return json.dumps(beta_riesgo_alto(limit=limit), default=str)


@beta_tool
def tendencia_mensual_rechazo(mes: str, meses_baseline: int = 3) -> str:
    """Compara la tasa de rechazo de cada parte en un mes contra su promedio de los
    meses anteriores, para encontrar qué parte empeoró más ese mes (mayor tendencia de
    rechazo) -- úsala cuando pregunten por tendencia/evolución mensual, no para
    criticidad histórica general (para eso usa riesgo_calidad_parte/partes_riesgo_alto).

    Puede devolver "confiable": false (con "partes" vacío) si el mes evaluado o el
    período de comparación no tienen veredicto de rechazo confiable en la base de
    datos -- en ese caso usa el "motivo" que trae la respuesta tal cual para explicarle
    al usuario por qué, NUNCA reportes 0% como si fuera un buen resultado ni inventes
    un número para rellenar. Recomienda ampliar `meses_baseline` (siguiente horizonte:
    mes->trimestre->semestre->año) y, si el horizonte pedido no era ya el máximo (año =
    meses_baseline 12), considera volver a llamar esta misma tool con un baseline mayor
    en el mismo turno en vez de solo sugerirlo.

    Args:
        mes: formato YYYY-MM, ej. "2026-09".
        meses_baseline: cuántos meses anteriores usar como referencia (default 3).
    """
    return json.dumps(tendencia_rechazo_mensual(mes=mes, meses_baseline=meses_baseline), default=str)


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
    tendencia_mensual_rechazo,
]

_REGLAS_GROUNDING = """
Reglas estrictas:
- Nunca inventes un número, fecha o nombre que no venga literalmente de un resultado de
  herramienta. Si no tienes el dato, dilo explícitamente -- no rellenes con una suposición.
- El saldo pendiente y la proyección de entrega vienen del Plan de Producción Mensual, que
  NO es una fuente autoritativa -- si los usas en tu respuesta, dilo claramente.
- Si preguntan por una parte y no sabes el No_Parte exacto, usa listar_clientes_con_actividad
  y partes_de_cliente para encontrarla en vez de preguntarle al usuario el número exacto.
- Cuando un resultado de herramienta traiga un campo de fecha o período (p.ej. `referencia`,
  `ultima_actualizacion`, `mes`), SIEMPRE declara esa fecha en tu respuesta -- el usuario no
  tiene forma de saber a qué corte corresponde un número si tú no se lo dices. Dilo en una
  frase corta y natural, no como nota aparte (ej. "con datos hasta el 9 de marzo" o "información
  histórica acumulada, sin corte de fecha" si la herramienta no filtra por período).
- Si una herramienta devuelve que la muestra es insuficiente para el horizonte actual
  (`muestra_suficiente: false`, `confiable: false`, o un proceso en `procesos_sin_dato`), dilo
  y recomienda ampliar al siguiente horizonte (mes→trimestre→semestre→año) -- si el horizonte
  seleccionado no era ya el máximo (año), puedes volver a llamar la misma herramienta con el
  siguiente horizonte en el mismo turno para dar la respuesta directa en vez de solo sugerirla.
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
    periodo: Optional[str] = None  # clave de HORIZONTES ("mes"/"trimestre"/"semestre"/"anio")


def _system_con_horizonte(system: str, periodo: Optional[str]) -> str:
    h = HORIZONTES.get(periodo)
    if not h:
        return system
    return system + f"""

Horizonte de análisis seleccionado por el usuario: {h['label']} (~{h['dias']} días / últimos
{h['meses_baseline']} meses). Úsalo como default en los parámetros `dias`/`meses_baseline` de
las herramientas que los acepten, salvo que la pregunta pida otra cosa explícita (ej. "el mes
pasado" o un No_Parte puntual con su propio horizonte implícito en la pregunta). Si una
herramienta tiene un tope técnico menor al horizonte pedido (cuello_de_botella: 30 días;
evaluar_parte/partes_de_cliente/listar_clientes_con_actividad: 90 días), usa ese tope y
acláralo -- no finjas que aplicaste el horizonte completo. Criticidad histórica
(riesgo_calidad_parte/partes_riesgo_alto) y disponibilidad de compuerta química NO se ven
afectadas por este selector bajo ninguna circunstancia -- si preguntan algo de esas dos con un
horizonte seleccionado, acláralo también en vez de ignorar la pregunta sobre el horizonte."""


def _correr_chat(system: str, req: ChatRequest) -> dict:
    client = anthropic.Anthropic()
    system = _system_con_horizonte(system, req.periodo)

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
