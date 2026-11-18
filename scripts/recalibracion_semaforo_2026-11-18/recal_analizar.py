# Recalibración del semáforo: lift (rechazo real / esperado por la mezcla de partes) por tramo del
# valor MEDIDO de cada PC, con IC 90 % por bootstrap de coladas. Total y solo defectos ligados al PC.
import json, random, sys
from pathlib import Path
sys.path.insert(0, r"C:\Users\Administrator\Documents\FASA\torre-de-control-fasa")
from backend.routers.control_resumen import DEFECTOS_POR_PC  # noqa: E402

C = json.loads(Path(__file__).with_name("recal_coladas.json").read_text(encoding="utf-8"))
tot, rec = {}, {}
for c in C:
    for s in c["skus"]:
        tot[s["sku"]] = tot.get(s["sku"], 0) + s["total"]; rec[s["sku"]] = rec.get(s["sku"], 0) + s["rechazos"]
tasa = {k: rec[k] / tot[k] for k in tot if tot[k]}
dfx = {}
for c in C:
    for d, p, n in c["defectos"]:
        dfx[(p, d)] = dfx.get((p, d), 0) + n
def tasa_def(defs):
    return {k: sum(v for (pp, d), v in dfx.items() if pp == k and d in defs) / tot[k] for k in tot if tot[k]}

def oe(c, defs=None, td=None):
    pz = sum(s["total"] for s in c["skus"])
    if defs is None:
        o = sum(s["rechazos"] for s in c["skus"]); e = sum(s["total"] * tasa.get(s["sku"], 0) for s in c["skus"])
    else:
        o = sum(n for d, p, n in c["defectos"] if d in defs); e = sum(s["total"] * td.get(s["sku"], 0) for s in c["skus"])
    return o, e, pz

random.seed(7)
def lift(cs, defs=None, td=None, B=400):
    v = [oe(c, defs, td) for c in cs]
    O, E = sum(x[0] for x in v), sum(x[1] for x in v)
    if not E or len(v) < 8:
        return None
    bs = []
    for _ in range(B):
        m = [v[random.randrange(len(v))] for _ in v]
        e = sum(x[1] for x in m)
        if e: bs.append(sum(x[0] for x in m) / e)
    bs.sort()
    return O / E, bs[int(.05 * len(bs))], bs[int(.95 * len(bs)) - 1], len(v), O

def fila(nombre, cs, defs=None):
    td = tasa_def(defs) if defs else None
    r = lift(cs, None)
    rd = lift(cs, defs, td) if defs else None
    f = lambda x: "  n<8" if not x else f"×{x[0]:.2f} [{x[1]:.2f}–{x[2]:.2f}]" + (" ▲" if x[1] > 1 else " ▼" if x[2] < 1 else "  ")
    print(f"  {nombre:<34} coladas {len(cs):>5} | total {f(r):<26} | ligados {f(rd) if defs else '':<26}")

def tramos(titulo, valor, cortes, defs, etiquetas=None):
    print(f"\n## {titulo}")
    xs = [(valor(c), c) for c in C]
    xs = [(v, c) for v, c in xs if v is not None]
    print(f"  (con dato: {len(xs)} de {len(C)})")
    bordes = [-1e18] + cortes + [1e18]
    for i in range(len(bordes) - 1):
        cs = [c for v, c in xs if bordes[i] <= v < bordes[i + 1]]
        et = etiquetas[i] if etiquetas else f"[{bordes[i]:g}, {bordes[i + 1]:g})"
        fila(et, cs, defs)

print(f"coladas {len(C)} · piezas {sum(tot.values())} · rechazo global {sum(rec.values()) / sum(tot.values()) * 100:.1f} %")

# ---- estado actual de cada PC
print("\n## Estado actual del semáforo")
for p in ("PC-1", "PC-2", "PC-3", "PC-4"):
    for e in ("VERDE", "AMBAR", "ROJO", "GRIS"):
        fila(f"{p} {e}", [c for c in C if c["estados"][p] == e], DEFECTOS_POR_PC[p])

# ---- PC-1 carga
def pc1_max_dev(c):
    xs = [abs(m["real"] - m["receta"]) / m["receta"] * 100 for m in c["carga"] if m["receta"] and m["receta"] > 5]
    return max(xs) if xs else None
tramos("PC-1 · peor desviación de un material (% de su receta)", pc1_max_dev, [1, 2, 5, 10, 20], DEFECTOS_POR_PC["PC-1"])
def pc1_kg_total(c):
    r = sum(m["receta"] for m in c["carga"]); x = sum(m["real"] for m in c["carga"])
    return abs(x - r) / r * 100 if r else None
tramos("PC-1 · desviación del total de la carga (%)", pc1_kg_total, [0.5, 1, 2, 5], DEFECTOS_POR_PC["PC-1"])
tramos("PC-1 · % retornos", lambda c: c["retornos"], [20, 30, 40, 50], DEFECTOS_POR_PC["PC-1"])

# ---- PC-2 química: desviación en múltiplos de la tolerancia, por elemento
for el in ("C", "Si", "Mg", "S", "P", "Mn"):
    def dev(c, el=el):
        q = c["quim"].get(el) or {}
        v, ce, tol = q.get("valor"), q.get("centro"), q.get("tol")
        if v is None or ce is None: return None
        if not tol:  # máximo (P): relativo al máximo
            return v / ce if ce else None
        return (v - ce) / tol
    if el == "P":
        tramos("PC-2 · P relativo a su máximo (valor/máx)", dev, [0.8, 1.0, 1.2, 1.5], DEFECTOS_POR_PC["PC-2"])
    else:
        tramos(f"PC-2 · {el}: (valor − centro) / tolerancia", dev, [-1.5, -1, -0.5, 0.5, 1, 1.5], DEFECTOS_POR_PC["PC-2"])
tramos("PC-2 · nº de elementos FAIL", lambda c: sum(1 for q in c["quim"].values() if q.get("estado") == "FAIL") if any(q.get("estado") for q in c["quim"].values()) else None, [1, 2, 3], DEFECTOS_POR_PC["PC-2"])

# ---- PC-3 ITACA
tramos("PC-3 · score ITACA (mínimo de alertas)", lambda c: c["itaca"]["score"], [30, 50, 70, 85], DEFECTOS_POR_PC["PC-3"])
for a in ("Porosity", "MacroShrinkage", "Cementite", "Nodularization", "InverseChill"):
    tramos(f"PC-3 · ITACA {a}", lambda c, a=a: c["itaca"]["alertas"].get(a), [30, 50, 70, 85], DEFECTOS_POR_PC["PC-3"])

# ---- PC-4 temperatura
def frac(k):
    return lambda c: (c["temp"][k] / c["temp"]["n_validos"]) if c["temp"].get("n_validos") else None
tramos("PC-4 · fracción de tickets FRÍOS (< mín)", frac("n_frio"), [0.001, 0.2, 0.34, 0.6], DEFECTOS_POR_PC["PC-4"])
tramos("PC-4 · fracción de tickets graves (frío o >15 °C arriba)", frac("n_grave"), [0.001, 0.2, 0.34, 0.6], DEFECTOS_POR_PC["PC-4"])
def t_rel(c):
    t = c["temp"]
    if not t.get("temp_avg") or not t.get("tmin") or not t.get("tmax"): return None
    return t["temp_avg"] - (t["tmin"] + t["tmax"]) / 2
tramos("PC-4 · temp. promedio − centro del rango de la parte (°C)", t_rel, [-15, -5, 5, 15, 25], DEFECTOS_POR_PC["PC-4"])
