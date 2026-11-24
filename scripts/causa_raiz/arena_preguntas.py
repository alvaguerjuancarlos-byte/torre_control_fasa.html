"""Datos de apoyo para las preguntas a Calidad sobre arena (24-nov-2026). Solo lectura.
Para cada señal de arena.py que se lleva a Calidad: límites de Corex, cómo se reparte el valor real,
cuántas mediciones por día, % de días fuera, y si el O/E se sostiene en las dos mitades del año
(mismo criterio que pc8_validacion.py: ene-may vs ago-nov).
"""
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from backend.core import run
from arena import datos_dia, datos_arena, _desestacionalizar

SENALES = [  # (defecto, nomDefectos, variable, lado, corte)
    ("Rechupe", ["RECHUPE", "DEPRESION", "MICRORECHUPE"], "RESISTENCIA AF3 A LOS 15 MINUTOS", "alto", 77.4),
    ("Rechupe", ["RECHUPE", "DEPRESION", "MICRORECHUPE"], "TIEMPO DE TRABAJO AF3 (Minutos y Segundos)", "alto", 2.47),
    ("Rechupe", ["RECHUPE", "DEPRESION", "MICRORECHUPE"], "AFS ARENA MECANICA", "bajo", 41.3),
    ("Tierra suelta", ["TIERRA SUELTA"], "TEMPERATURA DE LA ARENA EN AF2 AL 1.2% DE RESINA", "alto", 32.0),
    ("Tierra suelta", ["TIERRA SUELTA"], "FINOS ARENA TERMICA", "bajo", 0.621),
    ("Corazón", ["CORAZON DEFECTUOSO", "CORAZON MAL COLOCADO"], "RESISTENCIA AF2 A LOS 15 MINUTOS", "bajo", 68.0),
    ("Sopladura", ["SOPLADURA"], "AFS ARENA TERMICA", "bajo", 39.7),
]
MITADES = [("ene-may", "2026-01-01", "2026-06-01"), ("ago-nov", "2026-08-01", "2026-12-01")]

if __name__ == "__main__":
    obs, esp, pzs = datos_dia({s[0]: s[1] for s in SENALES})
    arena, lim = datos_arena()
    rows = run("""SELECT TRIM(Variable) v, DATE(Fecha) d, COUNT(*) n
                  FROM betamega_09_variablesdecontrol WHERE Departamento='Laboratorio' AND Fecha >= '2026-01-01'
                  GROUP BY TRIM(Variable), DATE(Fecha)""", {})
    nmed = defaultdict(list)
    for r in rows:
        nmed[r["v"]].append(r["n"])
    for defecto, _, var, lado, corte in SENALES:
        A = arena.get(var, {})
        dias = sorted(d for d in A if d in pzs)
        x = np.array([A[d]["prom"] for d in dias])
        fuera = [A[d]["fuera"] for d in dias if A[d]["fuera"] is not None]
        lo, hi = lim.get(var, (None, None))
        print(f"\n## {defecto} · {var} · {lado} {corte}")
        print(f"   límites Corex {lo}-{hi} · días con dato {len(dias)} · mediciones/día mediana {np.median(nmed[var]) if nmed[var] else 0:.0f}"
              f" · días fuera {100 * sum(fuera) / len(fuera):.0f} % de {len(fuera)}" if fuera else f"   límites {lo}-{hi} · días {len(dias)} · sin evaluar límite")
        print(f"   valor diario p10 {np.quantile(x, .1):.2f} · mediana {np.median(x):.2f} · p90 {np.quantile(x, .9):.2f}")
        meses = defaultdict(list)
        for d, v in zip(dias, x):
            meses[d[:7]].append(v)
        print("   mediana por mes: " + " · ".join(f"{m[5:]} {np.median(v):.1f}" for m, v in sorted(meses.items())))
        for nombre, a, b in MITADES:
            dd = [d for d in dias if a <= d < b]
            if len(dd) < 15:
                print(f"   {nombre}: pocos días ({len(dd)})"); continue
            o = np.array([obs[defecto][d] for d in dd]); e = np.array([esp[defecto][d] for d in dd])
            e = _desestacionalizar(o, e, dd)
            v = np.array([A[d]["prom"] for d in dd])
            m = v > corte if lado == "alto" else v < corte
            if m.sum() < 5 or (~m).sum() < 5:
                print(f"   {nombre}: grupo muy chico ({m.sum()} días)"); continue
            print(f"   {nombre}: O/E grupo {o[m].sum() / e[m].sum():.2f} ({int(o[m].sum())} rech, {m.sum()} días) vs resto {o[~m].sum() / e[~m].sum():.2f}")
