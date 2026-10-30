"""Reporte de texto exploratorio: cruza muchos factores de proceso por colada contra los defectos principales
(O/E ajustado por mezcla de partes). La vista Causa raíz usa generar.py; este script sirve para buscar señales nuevas.
Uso: python scripts/causa_raiz/analisis.py backend/data/cache   (carpeta con causa_raiz_coladas.json)"""
import sys, json
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.core import run

S = sys.argv[1]
C = json.load(open(S + "/causa_raiz_coladas.json", encoding="utf-8"))
ids = [c["id"] for c in C]
GR = {
    "SOPLADURA": ["SOPLADURA"], "POROSIDAD": ["POROSIDAD"],
    "RECHUPE": ["RECHUPE"], "DEPRESION": ["DEPRESION"], "CONTRACCION": ["RECHUPE", "DEPRESION", "MICRORECHUPE"],
    "CAIDO": ["CAIDO"], "TIRADO": ["TIRADO"],
    "FIERRO FRIO": ["FIERRO FRIO"], "NO LLENO": ["NO LLENO"],
}

# piezas por colada y parte; tasa histórica por parte y defecto
pp = defaultdict(dict)
for i in range(0, len(ids), 2000):
    for r in run(f"""SELECT IdColada, TRIM(no_parte) p, COUNT(DISTINCT IdTicket) n FROM betamega_aleks_idtickethistorial
                    WHERE IdColada IN ({",".join(map(str, ids[i:i+2000]))}) GROUP BY 1,2""", {}):
        pp[r["IdColada"]][r["p"]] = int(r["n"])
prod_p = defaultdict(int)
for cid, d in pp.items():
    for p, n in d.items():
        prod_p[p] += n
rp = defaultdict(lambda: defaultdict(int))
for i in range(0, len(ids), 2000):
    for r in run(f"""SELECT TRIM(a.no_parte) p, CASE WHEN r.defecto=57 THEN 'MICROSETRUCTURA' ELSE
                       (SELECT MIN(c.nomDefecto) FROM gamamega_00_catalogdefectos c WHERE c.idDefecto=r.defecto) END nd,
                       COUNT(DISTINCT r.idticket) n
                    FROM rechazo r JOIN (SELECT DISTINCT IdTicket, IdColada, no_parte FROM betamega_aleks_idtickethistorial
                        WHERE IdColada IN ({",".join(map(str, ids[i:i+2000]))})) a ON a.IdTicket = r.idticket
                    WHERE r.tipo_rech='Producción' GROUP BY 1,2""", {}):
        rp[r["p"]][(r["nd"] or "?").upper().replace("Ó", "O")] += int(r["n"])

def obs(c, g):
    return sum(c["rech"].get(d, 0) for d in GR[g])

def esp(c, g):
    return sum(n * sum(rp[p].get(d, 0) for d in GR[g]) / prod_p[p] for p, n in pp.get(c["id"], {}).items() if prod_p[p])

for c in C:
    c["_obs"] = {g: obs(c, g) for g in GR}
    c["_esp"] = {g: esp(c, g) for g in GR}

def tabla(titulo, clave, filtro=None):
    """clave(c) -> etiqueta del grupo o None."""
    grupos = defaultdict(list)
    for c in C:
        if filtro and not filtro(c):
            continue
        k = clave(c)
        if k is not None:
            grupos[k].append(c)
    print(f"\n=== {titulo}")
    hdr = "  grupo".ljust(30) + "colad  piezas  " + "  ".join(g[:9].rjust(9) for g in GR)
    print(hdr)
    for k in sorted(grupos, key=str):
        L = grupos[k]
        n = sum(c["n"] for c in L)
        celdas = []
        for g in GR:
            o = sum(c["_obs"][g] for c in L); e = sum(c["_esp"][g] for c in L)
            celdas.append((f"{o/e:.2f}" if e >= 3 else "  -") + f"/{o}")
        print(f"  {str(k)[:26]:<28}{len(L):>5}{n:>8}  " + "  ".join(x.rjust(9) for x in celdas))

def cuant(vals, qs=(0.2, 0.8)):
    v = sorted(x for x in vals if x is not None)
    return [v[int(q * (len(v) - 1))] for q in qs] if v else [None, None]

def bucket(x, lo, hi, fmt="{:.2f}"):
    if x is None: return None
    return ("1 bajo <" + fmt.format(lo)) if x < lo else ("3 alto >" + fmt.format(hi)) if x > hi else "2 medio"

print("O/E = observado/esperado según mezcla de partes (1.00 = lo normal para esas partes); /n = rechazos observados")
tabla("Año", lambda c: c["mes"][:4])
tabla("Horno", lambda c: c["horno"])
tabla("Turno (hora de liberado)", lambda c: None if c["hora"] is None else ("1 07-15" if 7 <= c["hora"] < 15 else "2 15-23" if 15 <= c["hora"] < 23 else "3 23-07"))
for a in ["Porosity", "MacroShrinkage", "Cementite", "Nodularization", "InverseChill"]:
    tabla(f"ITACA {a} (100 = sin riesgo)", lambda c, a=a: None if not c["ita"] or c["ita"].get(a) is None else
          ("1 <50" if c["ita"][a] < 50 else "2 50-70" if c["ita"][a] < 70 else "3 >=70"))
tabla("ITACA # copas (re-pruebas)", lambda c: None if not c["ita_n"] else ("1 una" if c["ita_n"] == 1 else "2 dos+"))
for k in ("calcC", "calcSi", "calcCeq", "CE", "Ceq"):
    vals = [c["ita_te"].get(k) for c in C if c.get("ita_te")]
    if any(v is not None for v in vals):
        lo, hi = cuant(vals)
        tabla(f"ITACA telurio {k} (p20={lo}, p80={hi})", lambda c, k=k, lo=lo, hi=hi: bucket((c.get("ita_te") or {}).get(k), lo, hi))
for el in ("C", "Si", "Mg", "S", "P", "Mn"):
    vals = [c["q"].get(el) for c in C if c["q"]]
    lo, hi = cuant(vals)
    tabla(f"Química {el} valor (p20={lo}, p80={hi})", lambda c, el=el, lo=lo, hi=hi: bucket((c["q"] or {}).get(el), lo, hi, "{:.3f}"))
tabla("Temperatura vaciado: % tickets en rango", lambda c: None if not c["t_ok"] or not c["t_ok"][1] else
      ("1 <50%" if c["t_ok"][0] / c["t_ok"][1] < .5 else "2 50-99%" if c["t_ok"][0] < c["t_ok"][1] else "3 100%"))
tabla("Temperatura promedio vs mín. de la parte", lambda c: None if not c["t_avg"] or not c["tmin"] else
      ("1 bajo mín" if c["t_avg"] < c["tmin"] else "2 0-20°C sobre mín" if c["t_avg"] < c["tmin"] + 20 else "3 >20°C sobre mín"))
tabla("Temperatura promedio vs máx. de la parte", lambda c: None if not c["t_avg"] or not c["tmax"] else
      ("1 <=máx" if c["t_avg"] <= c["tmax"] else "2 sobre máx"))
tabla("Demora registrada", lambda c: None if c["demora"] is None else ("con demora" if c["demora"] else "sin demora"))
tabla("Tiempo en horno vs límite", lambda c: None if not c["min_horno"] or not c["t_lim"] else
      ("1 dentro" if c["min_horno"] <= c["t_lim"] else "2 excede" if c["min_horno"] <= c["t_lim"] * 1.5 else "3 excede >50%"))
mh = cuant([c["min_horno"] for c in C if c["min_horno"] and 0 < c["min_horno"] < 600])
tabla(f"Minutos en horno (p20={mh[0]}, p80={mh[1]})", lambda c: bucket(c["min_horno"] if c["min_horno"] and 0 < c["min_horno"] < 600 else None, mh[0], mh[1], "{:.0f}"))
tabla("Carga metálica % materiales en tolerancia", lambda c: None if c["carga_ok"] is None else ("100%" if c["carga_ok"] >= 100 else "<100%"))
tabla("Partes distintas en la colada", lambda c: "1 una" if c["np"] == 1 else "2 dos-tres" if c["np"] <= 3 else "3 cuatro+")
dd = defaultdict(int)
for c in C:
    if c["dem_desc"]: dd[c["dem_desc"]] += 1
top = [k for k, _ in sorted(dd.items(), key=lambda x: -x[1])[:8]]
tabla("Causa de demora (top 8)", lambda c: (c["dem_desc"] or "")[:26] if c["dem_desc"] in top else None)
tabla("Mes", lambda c: c["mes"])
