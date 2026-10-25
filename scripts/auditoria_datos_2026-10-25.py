import sys, time, json, os
sys.path.insert(0, r"C:\Users\Administrator\Documents\FASA\torre-de-control-fasa")
from backend.core import engine
from sqlalchemy import text

OUT = {}


def run(c, label, q):
    t = time.time()
    try:
        rows = [tuple(x) for x in c.execute(text(q)).fetchall()]
        OUT[label] = rows
        print(f"[{label}] ({time.time()-t:.0f}s)")
        for x in rows[:40]:
            print("    ", x)
    except Exception as e:
        OUT[label] = f"ERR {e}"
        print(f"[{label}] ERR {str(e)[:200]}")
    sys.stdout.flush()


FRESCURA = [
    ("max origen", "SELECT MAX(FHrVaciado), MAX(AAMM) FROM ia_fasa.betamega_02_pks_ticketsorigen"),
    ("max b01", "SELECT MAX(FHrVaciado), MAX(FechaRechazo) FROM ia_fasa.betamega_01_rechazosbyidticket"),
    ("max b03", "SELECT MAX(FHrVaciado) FROM ia_fasa.betamega_03_coladasproceso"),
    ("max b06", "SELECT MAX(FHrVaciado) FROM ia_fasa.betamega_06_vaciado"),
    ("max b08", "SELECT MAX(FHrMOLD), MAX(FHrVACI), MAX(FHrDESM), MAX(FHrLIMP) FROM ia_fasa.betamega_08_ticketrutacritica"),
    ("max b07", "SELECT MAX(FHrVaciado) FROM ia_fasa.betamega_07_cargametalicos"),
    ("max tcorr", "SELECT MAX(FHrVaciado) FROM ia_fasa.betamega_02_pks_ticketcorrida"),
    ("max vdir", "SELECT MAX(FHrVaciado) FROM ia_fasa.betamega_00_ticketsconvaciadodirecto"),
    ("max aleks", "SELECT MAX(FHrMoldeo) FROM ia_fasa.betamega_aleks_idtickethistorial"),
    ("max demoras", "SELECT MAX(FechaInicial) FROM ia_fasa.betamega_00_coladaprocesodemoras"),
    ("max cargas", "SELECT MAX(IdColada) FROM ia_fasa.betamega_00_cargasmetalicas"),
    ("max corridas", "SELECT MAX(DateCorridaIniVac) FROM ia_fasa.betamega_00_corridasrechazo"),
    ("max b09", "SELECT MAX(Fecha) FROM ia_fasa.betamega_09_variablesdecontrol"),
    ("max itaca", "SELECT MAX(aResDate), MAX(DataIns) FROM ia_fasa.itaca_acqresults"),
    ("periodos programaperiodo", "SELECT GROUP_CONCAT(DISTINCT Periodo ORDER BY Periodo) FROM ia_fasa.gamamega_01_programaperiodo"),
    ("periodos paretopeso", "SELECT GROUP_CONCAT(DISTINCT Periodo ORDER BY Periodo) FROM ia_fasa.gamamega_01_paretopesoperiodo"),
    ("max programa3dias", "SELECT MAX(fecha), MAX(fechavac) FROM ia_fasa.gamamega_01_programa3dias"),
    ("max recomendaciones", "SELECT MAX(Periodo), COUNT(DISTINCT Periodo) FROM ia_fasa.gamamega_02_recomendacionescontrol"),
    ("logerrores ultimos", "SELECT * FROM ia_fasa.betamega_00_logerrores ORDER BY 1 DESC LIMIT 5"),
    ("tablas ia_fasa recientes", "SELECT table_name, create_time, table_rows FROM information_schema.tables WHERE table_schema='ia_fasa' ORDER BY create_time DESC LIMIT 15"),
    ("n tablas por esquema", "SELECT table_schema, COUNT(*) FROM information_schema.tables WHERE table_schema IN ('ia_fasa','corex_test','piso_ia','test') GROUP BY 1"),
    ("corex/piso max", "SELECT (SELECT MAX(FechaInicial) FROM corex_test.coladas), (SELECT MAX(FechaModificacion) FROM corex_test.modelos), (SELECT MAX(fecha) FROM piso_ia.rechazo), (SELECT MAX(fecha) FROM piso_ia.fdbase)"),
]

COBERTURA = {
    "origen_tot": "SELECT AAMM, COUNT(*) FROM ia_fasa.betamega_02_pks_ticketsorigen WHERE AAMM BETWEEN 2605 AND 2610 GROUP BY AAMM",
    "origen_sinvac": "SELECT AAMM, SUM(FHrVaciado IS NULL) FROM ia_fasa.betamega_02_pks_ticketsorigen WHERE AAMM BETWEEN 2605 AND 2610 GROUP BY AAMM",
    "b01": "SELECT DATE_FORMAT(FHrVaciado,'%y%m') m, COUNT(DISTINCT idTicket) FROM ia_fasa.betamega_01_rechazosbyidticket WHERE FHrVaciado>='2026-05-01' GROUP BY m",
    "b01rch": "SELECT DATE_FORMAT(FHrVaciado,'%y%m') m, SUM(bRechazo) FROM ia_fasa.betamega_01_rechazosbyidticket WHERE FHrVaciado>='2026-05-01' GROUP BY m",
    "b03": "SELECT DATE_FORMAT(FHrVaciado,'%y%m') m, COUNT(DISTINCT idTicket) FROM ia_fasa.betamega_03_coladasproceso WHERE FHrVaciado>='2026-05-01' GROUP BY m",
    "b06": "SELECT DATE_FORMAT(FHrVaciado,'%y%m') m, COUNT(DISTINCT idTicket) FROM ia_fasa.betamega_06_vaciado WHERE FHrVaciado>='2026-05-01' GROUP BY m",
    "b08vaci": "SELECT DATE_FORMAT(FHrVACI,'%y%m') m, COUNT(*) FROM ia_fasa.betamega_08_ticketrutacritica WHERE FHrVACI>='2026-05-01' GROUP BY m",
    "b08desm": "SELECT DATE_FORMAT(FHrDESM,'%y%m') m, COUNT(*) FROM ia_fasa.betamega_08_ticketrutacritica WHERE FHrDESM>='2026-05-01' GROUP BY m",
    "b08limp": "SELECT DATE_FORMAT(FHrLIMP,'%y%m') m, COUNT(*) FROM ia_fasa.betamega_08_ticketrutacritica WHERE FHrLIMP>='2026-05-01' GROUP BY m",
    "b07": "SELECT DATE_FORMAT(FHrVaciado,'%y%m') m, COUNT(DISTINCT idTicket) FROM ia_fasa.betamega_07_cargametalicos WHERE FHrVaciado>='2026-05-01' GROUP BY m",
    "vdir": "SELECT DATE_FORMAT(FHrVaciado,'%y%m') m, COUNT(DISTINCT idTicket) FROM ia_fasa.betamega_00_ticketsconvaciadodirecto WHERE FHrVaciado>='2026-05-01' GROUP BY m",
    "aleks": "SELECT DATE_FORMAT(FHrMoldeo,'%y%m') m, COUNT(DISTINCT IdTicket) FROM ia_fasa.betamega_aleks_idtickethistorial WHERE FHrMoldeo>='2026-05-01' GROUP BY m",
    "demoras": "SELECT DATE_FORMAT(FechaInicial,'%y%m') m, COUNT(*) FROM ia_fasa.betamega_00_coladaprocesodemoras WHERE FechaInicial>='2026-05-01' GROUP BY m",
    "b09": "SELECT DATE_FORMAT(Fecha,'%y%m') m, COUNT(*) FROM ia_fasa.betamega_09_variablesdecontrol WHERE Fecha>='2026-05-01' GROUP BY m",
    "itaca_muestras": "SELECT DATE_FORMAT(aResDate,'%y%m') m, COUNT(DISTINCT aResDate, aResNum) FROM ia_fasa.itaca_acqresults WHERE aResDate>='2026-05-01' GROUP BY m",
}

DUPS_Y_RANGOS = [
    ("idticketsidmuestras 2026", "SELECT LEFT(o.AAMM,4), COUNT(*), SUM(m.IdAnalisisQuimico>0), SUM(m.idItaca>0), SUM(m.idTension>0), SUM(m.idArenas>0) FROM ia_fasa.betamega_02_pks_idticketsidmuestras m JOIN ia_fasa.betamega_02_pks_ticketsorigen o ON o.idTicket=m.IdTicket WHERE o.AAMM BETWEEN 2601 AND 2610 GROUP BY 1"),
    ("dup programaperiodo", "SELECT Periodo, COUNT(*) FROM (SELECT Periodo, TRIM(No_Parte) p FROM ia_fasa.gamamega_01_programaperiodo GROUP BY 1,2 HAVING COUNT(*)>1) z GROUP BY Periodo"),
    ("paretopeso suma != 1", "SELECT Periodo, ROUND(SUM(PrcContribucion),3) FROM ia_fasa.gamamega_01_paretopesoperiodo GROUP BY Periodo HAVING ABS(SUM(PrcContribucion)-1)>0.01"),
    ("dup cargasmetalicas", "SELECT COUNT(*), SUM(n), MAX(n) FROM (SELECT IdColada,Tipo,Clave,COUNT(*) n FROM ia_fasa.betamega_00_cargasmetalicas GROUP BY 1,2,3 HAVING COUNT(*)>1) z"),
    ("dup corridasrechazo 2026", "SELECT COUNT(*) FROM (SELECT No_Parte,CveCorrida FROM ia_fasa.betamega_00_corridasrechazo WHERE DateCorridaIniVac>='2026-01-01' GROUP BY 1,2 HAVING COUNT(*)>1) z"),
    ("dup ticketcorrida", "SELECT COUNT(*)-COUNT(DISTINCT idTicket) FROM ia_fasa.betamega_02_pks_ticketcorrida"),
    ("dup aleks", "SELECT COUNT(*), SUM(nc>1) FROM (SELECT IdTicket, COUNT(DISTINCT IdColada) nc FROM ia_fasa.betamega_aleks_idtickethistorial GROUP BY IdTicket HAVING COUNT(*)>1) z"),
    ("dup idticketsidmuestras", "SELECT COUNT(*) FROM (SELECT IdTicket FROM ia_fasa.betamega_02_pks_idticketsidmuestras GROUP BY IdTicket HAVING COUNT(DISTINCT IdAnalisisQuimico)>1) z"),
    ("dup b01", "SELECT COUNT(*)-COUNT(DISTINCT idTicket) FROM ia_fasa.betamega_01_rechazosbyidticket"),
    ("b03 tickets 2 coladas", "SELECT COUNT(*) FROM (SELECT idTicket FROM ia_fasa.betamega_03_coladasproceso GROUP BY idTicket HAVING COUNT(DISTINCT c_IdColada)>1) z"),
    ("b06 tickets varios vacticket", "SELECT COUNT(*) FROM (SELECT idTicket FROM ia_fasa.betamega_06_vaciado GROUP BY idTicket HAVING COUNT(DISTINCT vt_IdVacTicket)>1) z"),
    ("b06 temperaturas", "SELECT YEAR(FHrVaciado), COUNT(*), SUM(vt_TemperaturaVaciado=0), SUM(vt_TemperaturaVaciado IS NULL), SUM(vt_TemperaturaVaciado>1550), MAX(vt_TemperaturaVaciado), SUM(m_tmin>=m_tmax) FROM ia_fasa.betamega_06_vaciado WHERE FHrVaciado>='2025-01-01' GROUP BY 1"),
    ("b08 secuencia", "SELECT COUNT(*), SUM(FHrLIMP<FHrDESM), SUM(FHrDESM<FHrVACI), SUM(FHrVACI<FHrMOLD), SUM(bLIMP=1 AND bVACI=0) FROM ia_fasa.betamega_08_ticketrutacritica WHERE anio>=2025"),
    ("cargas tolerancia", "SELECT Tipo, COUNT(*), SUM(PrcTolerancia=0), SUM(bFueraTolerancia) FROM ia_fasa.betamega_00_cargasmetalicas WHERE IdColada>=78000 GROUP BY Tipo"),
    ("b09 AFS 48-54 y mallas desde 13-oct", "SELECT CASE WHEN Variable LIKE 'AFS%' AND LimiteInferior+0=48 AND LimiteSuperior+0=54 THEN 'AFS 48-54' WHEN Variable LIKE '%RETENIDO%' THEN 'mallas' ELSE 'otro' END g, COUNT(*), SUM(Respuesta REGEXP '^-?[0-9.]+$' AND (Respuesta+0 < LimiteInferior+0 OR Respuesta+0 > LimiteSuperior+0)) FROM ia_fasa.betamega_09_variablesdecontrol WHERE Departamento='Laboratorio' AND Fecha>='2026-10-13' GROUP BY g"),
    ("programa3dias fechas raras", "SELECT SUM(fecha<'2015-01-01'), COUNT(*) FROM ia_fasa.gamamega_01_programa3dias"),
    ("catalogo defectos dup", "SELECT idDefecto, GROUP_CONCAT(nomDefecto) FROM ia_fasa.gamamega_00_catalogdefectos GROUP BY 1 HAVING COUNT(*)>1"),
    ("modelos partes 2025-26", "SELECT COUNT(*), SUM(m.NoParte IS NULL), SUM(IFNULL(m.PesoKgs,0)=0), SUM(IFNULL(m.TemperaturaMin,0)=0 OR IFNULL(m.TemperaturaMax,0)=0) FROM (SELECT DISTINCT No_Parte FROM ia_fasa.betamega_02_pks_ticketsorigen WHERE anio>=2025) o LEFT JOIN (SELECT NoParte, MAX(PesoKgs) PesoKgs, MAX(TemperaturaMin) TemperaturaMin, MAX(TemperaturaMax) TemperaturaMax FROM corex_test.modelos GROUP BY NoParte) m ON m.NoParte=o.No_Parte"),
    ("demoras hueco ago-sep", "SELECT DATE_FORMAT(FechaInicial,'%Y-%m-%d') d, COUNT(*) FROM ia_fasa.betamega_00_coladaprocesodemoras WHERE FechaInicial BETWEEN '2026-08-15' AND '2026-10-01' GROUP BY d ORDER BY d"),
    ("vdir en b08 desde 12-oct", "SELECT COUNT(*), SUM(b.idTicket IS NOT NULL) FROM ia_fasa.betamega_00_ticketsconvaciadodirecto v LEFT JOIN ia_fasa.betamega_08_ticketrutacritica b ON b.idTicket=v.idTicket WHERE v.FHrVaciado>='2026-10-12'"),
]

with engine.connect() as c:
    for lbl, q in FRESCURA:
        run(c, lbl, q)
    res = {}
    for k, q in COBERTURA.items():
        t = time.time()
        try:
            res[k] = {str(a): b for a, b in c.execute(text(q)).fetchall()}
        except Exception as e:
            res[k] = {}
            print(k, "ERR", str(e)[:150])
        print("cov", k, f"{time.time()-t:.0f}s")
        sys.stdout.flush()
    OUT["cobertura"] = res
    for m in ("2605", "2606", "2607", "2608", "2609", "2610"):
        print(m, {k: res[k].get(m, "-") for k in COBERTURA})
    for lbl, q in DUPS_Y_RANGOS:
        run(c, lbl, q)

json.dump(OUT, open(os.path.join(os.environ["TEMP"], "claude", "fasa_audit", "audit2.json"), "w"), default=str, indent=1)
print("FIN")
