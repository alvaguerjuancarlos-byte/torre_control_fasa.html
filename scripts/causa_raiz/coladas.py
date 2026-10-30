"""Tabla por colada (ene-2025 .. oct-2026) para hipótesis de causa raíz de los 5 defectos principales.
Solo lectura. Salida: coladas.json (lista de dicts)."""
import sys, json, time
from datetime import date
sys.path.insert(0, r"C:\Users\Administrator\Documents\FASA\torre-de-control-fasa")
from backend import core
from backend.core import run

OUT = sys.argv[1]
meses = [(y, m) for y in (2025, 2026) for m in range(1, 13) if (y, m) <= (2026, 10)]
filas = []
for y, m in meses:
    t0 = time.time()
    d = f"{y}-{m:02d}-01"
    h = f"{y + (m == 12)}-{(m % 12) + 1:02d}-01"
    cols = core.coladas_en_ventana(d, h + " 00:00:00", limite=100000)
    cols = [c for c in cols if c["fin_fusion"] and c["fin_fusion"] < __import__("datetime").datetime.fromisoformat(h)]
    ids = [c["id_colada"] for c in cols]
    if not ids:
        print(y, m, "sin coladas"); continue
    idl = ",".join(str(i) for i in ids)
    # piezas moldeadas por colada (aleks) y parte dominante
    pz = run(f"""SELECT IdColada, COUNT(DISTINCT IdTicket) n, COUNT(DISTINCT TRIM(no_parte)) np,
                 SUBSTRING_INDEX(GROUP_CONCAT(TRIM(no_parte) ORDER BY no_parte), ',', 1) parte,
                 MAX(IdClase) clase
                 FROM betamega_aleks_idtickethistorial WHERE IdColada IN ({idl}) GROUP BY IdColada""", {})
    pz = {r["IdColada"]: r for r in pz}
    # rechazos de producción por colada y defecto (registro de piso, idticket)
    rz = run(f"""SELECT a.IdColada, CASE WHEN r.defecto=57 THEN 'MICROSETRUCTURA' ELSE
                   (SELECT MIN(c.nomDefecto) FROM gamamega_00_catalogdefectos c WHERE c.idDefecto=r.defecto) END nd,
                   COUNT(DISTINCT r.idticket) n
                 FROM rechazo r JOIN (SELECT DISTINCT IdTicket, IdColada FROM betamega_aleks_idtickethistorial
                                      WHERE IdColada IN ({idl})) a ON a.IdTicket = r.idticket
                 WHERE r.tipo_rech = 'Producción' GROUP BY a.IdColada, nd""", {})
    rzi = {}
    for r in rz:
        rzi.setdefault(r["IdColada"], {})[(r["nd"] or "?").upper().replace("Ó", "O")] = int(r["n"])
    # demoras y tiempo de horno
    dem = {r["idColada"]: r for r in run(f"""SELECT idColada, MAX(Demora) demora, MAX(MinutosTiempoHorno) min_horno,
              MAX(TiempoLimite) t_lim, MAX(Clave) clave_dem, MAX(Descripcion) desc_dem
              FROM betamega_00_coladaprocesodemoras WHERE idColada IN ({idl}) GROUP BY idColada""", {})}
    clase = {i: (pz.get(i) or {}).get("clase") for i in ids}
    try:
        quim = core.quimica_por_coladas(cols, clase)
    except Exception as e:
        print("  quim ERR", str(e)[:120]); quim = {}
    try:
        ita = core.itaca_por_coladas(ids)
    except Exception as e:
        print("  itaca ERR", str(e)[:120]); ita = {}
    try:
        tmp = core.temperatura_por_coladas(ids)
    except Exception as e:
        print("  temp ERR", str(e)[:120]); tmp = {}
    try:
        car = core.cargas_por_colada(ids)
    except Exception as e:
        print("  carga ERR", str(e)[:120]); car = {}
    for c in cols:
        i = c["id_colada"]; p = pz.get(i)
        if not p:
            continue
        q = quim.get(i) or {}
        ev = core.evaluar_quimica(q) if q else {}
        it = ita.get(i) or {}
        tp = tmp.get(i) or {}
        dm = dem.get(i) or {}
        rc = core.resumen_carga(car.get(i, [])) if car.get(i) else {}
        filas.append({
            "id": i, "mes": f"{y}-{m:02d}", "horno": c["horno"], "kilos": float(c["kilos"] or 0) or None,
            "ini": str(c["inicio_fusion"]), "fin": str(c["fin_fusion"]),
            "hora": c["fin_fusion"].hour if c["fin_fusion"] else None,
            "n": int(p["n"]), "np": int(p["np"]), "parte": p["parte"], "clase": p["clase"],
            "rech": rzi.get(i, {}),
            "q": {k: (float(q[k]) if q.get(k) is not None else None) for k in ("C", "Si", "Mg", "S", "P", "Mn")} if q else None,
            "q_ev": ev or None,
            "ita": it.get("alertas"), "ita_te": it.get("te"), "ita_score": it.get("score"), "ita_n": it.get("n_copas"),
            "t_avg": float(tp["temp_avg"]) if tp.get("temp_avg") else None,
            "t_ok": (int(tp["n_ok"] or 0), int(tp["n_tickets"])) if tp else None,
            "tmin": float(tp["tmin"]) if tp.get("tmin") else None, "tmax": float(tp["tmax"]) if tp.get("tmax") else None,
            "demora": float(dm["demora"]) if dm.get("demora") is not None else None,
            "min_horno": float(dm["min_horno"]) if dm.get("min_horno") is not None else None,
            "t_lim": float(dm["t_lim"]) if dm.get("t_lim") is not None else None,
            "dem_desc": dm.get("desc_dem"),
            "carga_ok": rc.get("pct_ok"),
        })
    print(y, m, len(cols), "coladas", f"{time.time()-t0:.0f}s", "quim", len(quim), "itaca", len(ita), "temp", len(tmp), flush=True)
    json.dump(filas, open(OUT, "w", encoding="utf-8"), default=str)
print("TOTAL", len(filas))
