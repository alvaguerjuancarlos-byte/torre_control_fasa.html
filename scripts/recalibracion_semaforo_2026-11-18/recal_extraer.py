# Recalibración del semáforo (JC, 18-nov-2026): extrae coladas con su rechazo ya registrado y los
# valores MEDIDOS de cada PC (no solo el color), para buscar umbrales que separen rechazo.
# Solo lectura. Guarda recal_coladas.json junto a este script.
import sys, json, time
from datetime import datetime, timedelta
from pathlib import Path
REPO = Path(r"C:\Users\Administrator\Documents\FASA\torre-de-control-fasa")
sys.path.insert(0, str(REPO))
from backend import core  # noqa: E402

DESDE_DIAS, SEMANAS = 12, 12
ref = datetime.now() - timedelta(days=DESDE_DIAS)
d0 = ref - timedelta(weeks=SEMANAS)
out = []
t0 = time.time()
# por bloques de 1 semana: la consulta de vaciado no tiene índice
for k in range(SEMANAS):
    a = d0 + timedelta(weeks=k)
    b = a + timedelta(weeks=1)
    cols = core.coladas_en_ventana(a.strftime("%Y-%m-%d %H:%M:%S"), b.strftime("%Y-%m-%d %H:%M:%S"), limite=None)
    if not cols:
        continue
    datos = core.datos_pcs_coladas(cols)
    no_conf = core.meses_no_confiables_cache(a.strftime("%Y-%m-%d %H:%M:%S"), b.strftime("%Y-%m-%d %H:%M:%S"))
    for col in cols:
        e = core.evaluar_pcs_colada(col, datos, no_conf)
        evs, rs = e["evs"], e["resumen"]
        if evs["PC-6"]["estado"] == "GRIS" or not rs["skus"]:
            continue
        comps = e["comps_carga"]
        q = e["quim"] or {}
        quim = {}
        for elem, pref in core._QUIM_ELEMS:
            v, c, tol = q.get(elem), q.get(f"{pref}_F"), q.get(f"{pref}_Fl")
            quim[elem] = {"valor": float(v) if v is not None else None, "centro": float(c) if c is not None else None,
                          "tol": float(tol) if tol is not None else None, "estado": e["quim_elem"].get(elem)}
        it = e["itaca"] or {}
        t = e["temp"] or {}
        out.append({
            "id": col["id_colada"], "horno": col["horno"], "fecha": str(col["inicio_fusion"]),
            "estados": {p: evs[p]["estado"] for p in ("PC-1", "PC-2", "PC-3", "PC-4")},
            "skus": rs["skus"], "defectos": [[d, p, n] for (d, p), n in rs["combos_defecto"]],
            "carga": [{"desc": c["descripcion"] or c["clave"], "tipo": c["tipo"], "receta": c["kg_receta"], "real": c["kg_real"],
                       "tol": c["tol_kg"]} for c in comps],
            "quim": quim, "clase": q.get("clase"),
            "itaca": {"score": it.get("score"), "alertas": it.get("alertas") or {}, "info": it.get("informativas") or {}, "te": it.get("te") or {}},
            "temp": {k: (float(t[k]) if t.get(k) is not None else None) for k in ("n_validos", "n_tol", "n_leve", "n_grave", "n_frio", "temp_avg", "tmin", "tmax")},
            "retornos": e["carga"].get("pct_retornos"),
        })
    print(f"semana {k + 1}/{SEMANAS} {a:%d-%b}: {len(cols)} coladas · acumuladas {len(out)} · {time.time() - t0:.0f} s", flush=True)

Path(__file__).with_name("recal_coladas.json").write_text(json.dumps(out, default=str), encoding="utf-8")
print("listo", len(out))
