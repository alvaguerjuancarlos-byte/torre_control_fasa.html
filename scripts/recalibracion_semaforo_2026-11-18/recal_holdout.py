import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
exec(Path(__file__).with_name("recal_analizar.py").read_text(encoding="utf-8").split("print(f\"coladas {len(C)}")[0])
C_all = sorted(C, key=lambda c: c["fecha"])
mitad = len(C_all) // 2
partes = {"1a mitad (" + C_all[0]["fecha"][:10] + " a " + C_all[mitad - 1]["fecha"][:10] + ")": C_all[:mitad],
          "2a mitad (" + C_all[mitad]["fecha"][:10] + " a " + C_all[-1]["fecha"][:10] + ")": C_all[mitad:]}
def q(el, c):
    x = c["quim"].get(el) or {}
    return (x["valor"] - x["centro"]) / x["tol"] if x.get("valor") is not None and x.get("centro") is not None and x.get("tol") else None
fr = lambda c: (c["temp"]["n_frio"] / c["temp"]["n_validos"]) if c["temp"].get("n_validos") else None
def pc1_kg_total(c):
    r = sum(m['receta'] for m in c['carga']); x = sum(m['real'] for m in c['carga'])
    return abs(x - r) / r * 100 if r else None
cand = {
  "PC-4 algún ticket frío": (lambda c: fr(c) is not None and fr(c) > 0, lambda c: fr(c) == 0, "PC-4"),
  "PC-4 caliente >15 °C (sin fríos)": (lambda c: fr(c) == 0 and c["temp"].get("n_grave", 0) > 0, lambda c: c["temp"].get("n_validos") and c["temp"].get("n_grave", 0) == 0 and not c["temp"].get("n_leve"), "PC-4"),
  "PC-2 S > centro + 1 tol": (lambda c: (q("S", c) or -9) > 1, lambda c: q("S", c) is not None and abs(q("S", c)) <= 1, "PC-2"),
  "PC-2 Si > centro + 0.5 tol": (lambda c: (q("Si", c) or -9) > 0.5, lambda c: q("Si", c) is not None and abs(q("Si", c)) <= 0.5, "PC-2"),
  "PC-1 carga total desviada ≥ 0.5 %": (lambda c: (pc1_kg_total(c) or 0) >= 0.5, lambda c: pc1_kg_total(c) is not None and pc1_kg_total(c) < 0.5, "PC-1"),
  "PC-3 ITACA Porosity < 30": (lambda c: (c["itaca"]["alertas"].get("Porosity") if c["itaca"]["alertas"].get("Porosity") is not None else 999) < 30, lambda c: (c["itaca"]["alertas"].get("Porosity") or 0) >= 70, "PC-3"),
  "PC-3 ITACA score < 30": (lambda c: c["itaca"]["score"] is not None and c["itaca"]["score"] < 30, lambda c: c["itaca"]["score"] is not None and c["itaca"]["score"] >= 70, "PC-3"),
}
for nombre, (si, no, pc) in cand.items():
    print(f"\n## {nombre}")
    for et, cs in partes.items():
        globals()["C"] = cs  # tasas por parte dentro de cada mitad
        tot.clear(); rec.clear(); dfx.clear()
        for c in cs:
            for s in c["skus"]:
                tot[s["sku"]] = tot.get(s["sku"], 0) + s["total"]; rec[s["sku"]] = rec.get(s["sku"], 0) + s["rechazos"]
            for d, p, n in c["defectos"]:
                dfx[(p, d)] = dfx.get((p, d), 0) + n
        tasa.clear(); tasa.update({k: rec[k] / tot[k] for k in tot if tot[k]})
        print(f" {et}")
        fila("  señal", [c for c in cs if si(c)], DEFECTOS_POR_PC[pc])
        fila("  referencia (en rango)", [c for c in cs if no(c)], DEFECTOS_POR_PC[pc])
