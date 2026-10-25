"""
Control de Arenas (2026-10-25) -- panel de planta en Control del Proceso, NO un octavo PC.

Fuente: ia_fasa.betamega_09_variablesdecontrol (creada por IT el 13-oct-2026, viva), captura
del Laboratorio de arenas desde ene-2026: AFS, LOI, finos, pH, ADV, humedad, resistencias,
tiempos de trabajo/desmoldeo y temperatura por línea, cada registro con su LimiteInferior/
LimiteSuperior y el MotivoNoCumplido/AccionesCorrectivas que captura el laboratorista.

Por qué panel de planta y no PC por colada (decisión de JC, opción A): la arena se controla
por línea y tipo de arena, no por colada. Ligarla a una colada exige la línea de moldeo de
cada pieza, y corex_test.modelos.area solo la trae para ~38 % de lo producido en oct-2026
(mapamoldes.LineaPanel está atrasada: id máx 29k vs 59k en los tickets). Un PC-8 saldría
gris en ~60 % de las piezas.

Regla (misma escala que PC-1/PC-2): por grupo, la última medición de cada variable dentro
de la ventana; % en límite = 100 → VERDE, ≥ 80 → ÁMBAR, < 80 → ROJO; sin medición → GRIS.
Solo Departamento='Laboratorio' (Terminado/Fusión/AF capturados por operadores traen Puesto
en texto libre y cobertura de días; fuera de alcance).

Límites "por revisar" (no cuentan para el color, se muestran): AFS con límite 48-54 (el real
anda en 40-43 → ~99 % fuera; la cama fluidizada usa 37-47 y sí cuenta) y la granulometría
"% RETENIDO MALLA" (malla 14 sale 100 % fuera). Se identifican por el límite, no por el
nombre: en cuanto Calidad corrija el límite en Corex la variable vuelve a contar sola. LOI SÍ
cuenta aunque salga 40-60 % fuera (puede ser exceso real de aglutinante). Solicitud a
Calidad pendiente en el reporte a IT del 13-oct.
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Query

from backend.core import run

router = APIRouter(tags=["arenas"])

# (grupo, patrón en Variable o Proceso) -- en orden: el tipo de arena gana sobre la línea
# ("LOI TOLVA AF3" es AF3; "AFS CAMA FLUIDIZADA" es cama aunque el formulario sea de AF3).
_GRUPOS = [
    ("Cama fluidizada",      "CAMA FLUIDIZADA"),
    ("Arena mecánica",       "MECANICA"),
    ("Arena térmica",        "TERMICA"),
    ("Cold box",             "COLD BOX"),
    ("Corazones K300",       "K300"),
    ("Autofraguante AF3",    "AF3"),
    ("Autofraguante AF2",    "AF2"),
    ("Granulometría",        "RETENIDO"),
]
_ORDEN = [g for g, _ in _GRUPOS] + ["Otros"]


def _grupo(variable: str, proceso: str) -> str:
    v, p = variable.upper(), (proceso or "").upper()
    for nombre, patron in _GRUPOS:
        if patron in v:
            return nombre
    for nombre, patron in _GRUPOS:
        if patron in p:
            return nombre
    return "Otros"


def _num(s) -> Optional[float]:
    try:
        return float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _limite_por_revisar(variable: str, lo: Optional[float], hi: Optional[float]) -> bool:
    v = variable.upper()
    return (v.startswith("AFS") and lo == 48 and hi == 54) or "RETENIDO MALLA" in v or v.startswith("% RETENIDO")


def _estado(pct: Optional[float]) -> str:
    if pct is None:
        return "GRIS"
    return "VERDE" if pct >= 100 else "AMBAR" if pct >= 80 else "ROJO"


@router.get("/v3/gestion/arenas")
def control_arenas(
    referencia: Optional[str] = Query(default=None),
    horas: int = Query(default=48),
):
    ref = datetime.fromisoformat(referencia) if referencia else datetime.now()
    desde = ref - timedelta(hours=horas)
    rows = run("""
        SELECT Proceso, Fecha, TRIM(Variable) AS Variable, Respuesta, LimiteInferior, LimiteSuperior,
               MotivoNoCumplido, AccionesCorrectivas
        FROM betamega_09_variablesdecontrol
        WHERE Departamento = 'Laboratorio' AND TipoVariable = 'Cuantitativo'
          AND Fecha BETWEEN :d AND :h
        ORDER BY Fecha
    """, {"d": desde, "h": ref})

    grupos: dict = {}
    for r in rows:
        valor, lo, hi = _num(r["Respuesta"]), _num(r["LimiteInferior"]), _num(r["LimiteSuperior"])
        if valor is None:
            continue
        g = grupos.setdefault(_grupo(r["Variable"], r["Proceso"]),
                              {"ultimas": {}, "n_mediciones": 0, "n_fuera": 0})
        por_revisar = _limite_por_revisar(r["Variable"], lo, hi)
        en_limite = None if lo is None or hi is None else lo <= valor <= hi
        if not por_revisar and en_limite is not None:
            g["n_mediciones"] += 1
            g["n_fuera"] += 0 if en_limite else 1
        # ORDER BY Fecha: la última escritura por variable es la medición más reciente
        g["ultimas"][r["Variable"]] = {
            "variable": r["Variable"], "valor": valor, "lim_inf": lo, "lim_sup": hi,
            "en_limite": en_limite, "limite_por_revisar": por_revisar,
            "fecha": str(r["Fecha"]),
            "motivo": (r["MotivoNoCumplido"] or "").strip() or None,
            "acciones": (r["AccionesCorrectivas"] or "").strip(". ") or None,
        }

    out = []
    for nombre in _ORDEN:
        g = grupos.get(nombre)
        if not g:
            continue
        vars_ = sorted(g["ultimas"].values(),
                       key=lambda x: (x["limite_por_revisar"], x["en_limite"] is not False, x["variable"]))
        evaluables = [x for x in vars_ if not x["limite_por_revisar"] and x["en_limite"] is not None]
        n_ok = sum(1 for x in evaluables if x["en_limite"])
        pct = round(n_ok / len(evaluables) * 100.0, 1) if evaluables else None
        out.append({
            "grupo": nombre, "estado": _estado(pct), "pct_en_limite": pct,
            "n_variables": len(evaluables), "n_ok": n_ok,
            "n_mediciones": g["n_mediciones"], "n_fuera_ventana": g["n_fuera"],
            "ultima": max(x["fecha"] for x in vars_),
            "variables": vars_,
        })
    return {
        "referencia": ref.isoformat(timespec="minutes"), "horas": horas, "grupos": out,
        "nota": "Por grupo: última medición de cada variable en la ventana. AFS con límite 48-54 y "
                "granulometría (% retenido) se muestran como 'límite por revisar' y no cuentan para el color.",
    }
