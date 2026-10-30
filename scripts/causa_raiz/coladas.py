"""Tabla por colada (desde ene-2025 hasta el mes en curso) para la vista Causa raíz.

Solo lectura sobre la BD. Por colada: piezas moldeadas (aleks), rechazos de producción por
defecto (ia_fasa.rechazo por idTicket), química FINAL del SpectroMAX, copa ITACA, temperatura de
vaciado (betamega_06), horno, tiempo en horno vs. límite, demora y carga metálica.

Uso: python scripts/causa_raiz/coladas.py <salida.json>   (normalmente lo llama generar.py)
"""
import sys, json, time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend import core
from backend.core import run


def _defecto_sql(alias="r"):
    return f"""CASE WHEN {alias}.defecto=57 THEN 'MICROSETRUCTURA' ELSE
               (SELECT MIN(c.nomDefecto) FROM gamamega_00_catalogdefectos c WHERE c.idDefecto={alias}.defecto) END"""


def norm_defecto(nd):
    return (nd or "?").upper().replace("Ó", "O")


def _opcional(nombre, fn, *args):
    try:
        return fn(*args)
    except Exception as e:  # una fuente caída no debe tumbar el mes completo
        print(f"  {nombre} ERR {str(e)[:120]}")
        return {}


def construir(salida, desde=(2025, 1)):
    hoy = date.today()
    meses = [(y, m) for y in range(desde[0], hoy.year + 1) for m in range(1, 13)
             if desde <= (y, m) <= (hoy.year, hoy.month)]
    filas = []
    for y, m in meses:
        t0 = time.time()
        d = f"{y}-{m:02d}-01"
        h = f"{y + (m == 12)}-{(m % 12) + 1:02d}-01"
        cols = core.coladas_en_ventana(d, h + " 00:00:00", limite=100000)
        cols = [c for c in cols if c["fin_fusion"] and c["fin_fusion"] < datetime.fromisoformat(h)]
        ids = [c["id_colada"] for c in cols]
        if not ids:
            print(y, m, "sin coladas")
            continue
        idl = ",".join(str(i) for i in ids)
        pz = {r["IdColada"]: r for r in run(f"""
            SELECT IdColada, COUNT(DISTINCT IdTicket) n, COUNT(DISTINCT TRIM(no_parte)) np, MAX(IdClase) clase
            FROM betamega_aleks_idtickethistorial WHERE IdColada IN ({idl}) GROUP BY IdColada""", {})}
        rech = {}
        for r in run(f"""
            SELECT a.IdColada, {_defecto_sql()} nd, COUNT(DISTINCT r.idticket) n
            FROM rechazo r JOIN (SELECT DISTINCT IdTicket, IdColada FROM betamega_aleks_idtickethistorial
                                 WHERE IdColada IN ({idl})) a ON a.IdTicket = r.idticket
            WHERE r.tipo_rech = 'Producción' GROUP BY a.IdColada, nd""", {}):
            rech.setdefault(r["IdColada"], {})[norm_defecto(r["nd"])] = int(r["n"])
        dem = {r["idColada"]: r for r in run(f"""
            SELECT idColada, MAX(Demora) demora, MAX(MinutosTiempoHorno) min_horno, MAX(TiempoLimite) t_lim,
                   MAX(Descripcion) desc_dem
            FROM betamega_00_coladaprocesodemoras WHERE idColada IN ({idl}) GROUP BY idColada""", {})}
        clase = {i: (pz.get(i) or {}).get("clase") for i in ids}
        quim = _opcional("quim", core.quimica_por_coladas, cols, clase)
        ita = _opcional("itaca", core.itaca_por_coladas, ids)
        tmp = _opcional("temp", core.temperatura_por_coladas, ids)
        car = _opcional("carga", core.cargas_por_colada, ids)
        for c in cols:
            i = c["id_colada"]
            p = pz.get(i)
            if not p:
                continue
            q = quim.get(i) or {}
            it = ita.get(i) or {}
            tp = tmp.get(i) or {}
            dm = dem.get(i) or {}
            rc = core.resumen_carga(car[i]) if car.get(i) else {}
            filas.append({
                "id": i, "mes": f"{y}-{m:02d}", "horno": c["horno"],
                "hora": c["fin_fusion"].hour,
                "n": int(p["n"]), "np": int(p["np"]), "clase": p["clase"],
                "rech": rech.get(i, {}),
                "q": {k: (float(q[k]) if q.get(k) is not None else None)
                      for k in ("C", "Si", "Mg", "S", "P", "Mn")} if q else None,
                "ita": it.get("alertas"), "ita_te": it.get("te"), "ita_n": it.get("n_copas"),
                "t_avg": float(tp["temp_avg"]) if tp.get("temp_avg") else None,
                "t_ok": (int(tp["n_ok"] or 0), int(tp["n_tickets"])) if tp else None,
                "tmin": float(tp["tmin"]) if tp.get("tmin") else None,
                "tmax": float(tp["tmax"]) if tp.get("tmax") else None,
                "demora": float(dm["demora"]) if dm.get("demora") is not None else None,
                "min_horno": float(dm["min_horno"]) if dm.get("min_horno") is not None else None,
                "t_lim": float(dm["t_lim"]) if dm.get("t_lim") is not None else None,
                "dem_desc": dm.get("desc_dem"),
                "carga_ok": rc.get("pct_ok"),
            })
        print(y, m, len(cols), "coladas", f"{time.time() - t0:.0f}s", "quim", len(quim), "itaca", len(ita),
              "temp", len(tmp), flush=True)
    json.dump(filas, open(salida, "w", encoding="utf-8"), default=str)
    print("TOTAL", len(filas))
    return filas


if __name__ == "__main__":
    construir(sys.argv[1])
