"""Revisión de las partes con espera corta molde -> vaciado (< 2 h), 23-nov-2026.
Preguntas: ¿qué partes son, desde cuándo, qué proceso (AF2/AF3), y su tirado + corazón es mayor cuando se
vacían en < 2 h que cuando esperan más (misma parte contra sí misma)? Solo lectura; imprime y guarda JSON.
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from backend.core import run
from backend.plan_corex import _proceso_partes
from coladas import norm_defecto, _defecto_sql

DESDE = "2026-01-01"
LIGADOS = {"TIRADO", "CORAZON DEFECTUOSO", "CORAZON MAL COLOCADO"}
REF = datetime(2026, 11, 23)
RECIENTE = REF - timedelta(days=14)


def cargar():
    mo = run("""SELECT IdTicket t, TRIM(MAX(no_parte)) p, MIN(FHrMoldeo) m
                FROM betamega_aleks_idtickethistorial WHERE FHrMoldeo >= :d GROUP BY IdTicket""", {"d": DESDE})
    va = run("""SELECT idTicket t, MIN(FHrVaciado) v FROM betamega_00_ticketsconvaciadodirecto
                WHERE FHrVaciado >= :d GROUP BY idTicket""", {"d": DESDE})
    rz = run(f"""SELECT r.idticket t, {_defecto_sql()} nd FROM rechazo r
                 WHERE r.tipo_rech='Producción' AND r.fecha >= :d""", {"d": DESDE})
    vac = {str(r["t"]): r["v"] for r in va}
    dfs = defaultdict(set)
    for r in rz:
        dfs[str(r["t"])].add(norm_defecto(r["nd"]))
    out = []
    for r in mo:
        t, v = str(r["t"]), vac.get(str(r["t"]))
        if r["m"] is None or v is None:
            continue
        h = (v - r["m"]).total_seconds() / 3600
        if 0 < h < 336:
            out.append({"p": r["p"], "m": r["m"], "v": v, "h": h, "lig": bool(dfs.get(t, set()) & LIGADOS), "rech": bool(dfs.get(t))})
    return out


if __name__ == "__main__":
    pz = cargar()
    # semanas (lunes) de toda la planta
    sem = defaultdict(lambda: [0, 0])
    for x in pz:
        w = (x["v"] - timedelta(days=x["v"].weekday())).strftime("%Y-%m-%d")
        sem[w][0] += 1; sem[w][1] += x["h"] < 2
    semanas = [{"semana": w, "piezas": n, "pct_corta": round(c / n * 100, 1)} for w, (n, c) in sorted(sem.items()) if n >= 100]
    print("semanas:", [(s["semana"][5:], s["pct_corta"]) for s in semanas])
    # por parte
    P = defaultdict(list)
    for x in pz:
        P[x["p"]].append(x)
    rec = [(p, [x for x in L if x["v"] >= RECIENTE]) for p, L in P.items()]
    top = sorted(((p, sum(x["h"] < 2 for x in L)) for p, L in rec), key=lambda r: -r[1])[:12]
    proc = _proceso_partes([p for p, _ in top]) if top else {}
    partes = []
    for p, n_c in top:
        L = P[p]; R = [x for x in L if x["v"] >= RECIENTE]
        meses = defaultdict(lambda: [0, 0])
        for x in L:
            k = x["v"].strftime("%Y-%m"); meses[k][0] += 1; meses[k][1] += x["h"] < 2
        cor = [x for x in L if x["h"] < 2]; resto = [x for x in L if x["h"] >= 2]
        horas = defaultdict(int)
        for x in R:
            if x["h"] < 2:
                horas[x["v"].hour] += 1
        partes.append({"parte": p, "proceso": proc.get(p) if isinstance(proc.get(p), str) else (proc.get(p) or {}).get("proceso") if isinstance(proc.get(p), dict) else proc.get(p),
                       "piezas_2026": len(L), "pct_corta_2026": round(len(cor) / len(L) * 100, 1),
                       "piezas_14d": len(R), "corta_14d": n_c, "pct_corta_14d": round(n_c / len(R) * 100, 1) if R else None,
                       "mediana_h_14d": round(sorted(x["h"] for x in R)[len(R) // 2], 1) if R else None,
                       "meses": {k: round(c / n * 100) for k, (n, c) in sorted(meses.items()) if n >= 10},
                       "lig_corta": [sum(x["lig"] for x in cor), len(cor)], "lig_resto": [sum(x["lig"] for x in resto), len(resto)],
                       "rech_corta": [sum(x["rech"] for x in cor), len(cor)], "rech_resto": [sum(x["rech"] for x in resto), len(resto)],
                       "horas_vaciado_corta_14d": dict(sorted(horas.items()))})
    for r in partes:
        lc, lr = r["lig_corta"], r["lig_resto"]
        print(f"{r['parte']:<16} {str(r['proceso']):<6} 14d {r['corta_14d']}/{r['piezas_14d']} ({r['pct_corta_14d']}%) med {r['mediana_h_14d']}h | 2026 {r['pct_corta_2026']}% de {r['piezas_2026']} | "
              f"tir+cor corta {lc[0]}/{lc[1]} vs resto {lr[0]}/{lr[1]} | rech corta {r['rech_corta'][0]}/{r['rech_corta'][1]} vs {r['rech_resto'][0]}/{r['rech_resto'][1]} | meses {r['meses']}")
    # misma parte contra sí misma, agregado de todas las partes (Mantel-Haenszel simple: O/E dentro de parte)
    o = e = 0.0
    for p, L in P.items():
        tasa = sum(x["lig"] for x in L) / len(L)
        for x in L:
            if x["h"] < 2:
                o += x["lig"]; e += tasa
    print(f"dentro de cada parte, piezas < 2 h: tirado+corazón O/E {o / e:.2f} ({int(o)} obs / {e:.1f} esp)")
    Path(__file__).with_name("espera_corta_partes_2026-11-23.json").write_text(
        json.dumps({"semanas": semanas, "partes": partes, "oe_dentro_parte": round(o / e, 2)}, default=str, ensure_ascii=False, indent=1), encoding="utf-8")
