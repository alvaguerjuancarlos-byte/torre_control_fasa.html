"""Auditoría v3 — queries pendientes: PC-4 ts-dup, PC-5 m_tenfriamiento, PC-7 arefBatch, PC-1 modelocastingrecetas"""
import pymysql, os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))
OUT = open(os.path.join(os.path.dirname(__file__), 'audit_v3_out.txt'), 'w', encoding='utf-8')

def p(*args):
    line = ' '.join(str(a) for a in args)
    print(line); print(line, file=OUT); OUT.flush()

conn = pymysql.connect(
    host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT')),
    user=os.getenv('DB_USER'), password=os.getenv('DB_PWD'),
    database='ia_fasa', connect_timeout=15, charset='utf8mb4',
    read_timeout=30, write_timeout=30
)
cur = conn.cursor()

# ── PC-4: audit timestamps correctos en alfamega_06_vaciado ──────────────────
p("=== PC-4: timestamps duplicados por colada (FHrVaciado) ===")
try:
    cur.execute("""
        SELECT c_IdColada, COUNT(DISTINCT FHrVaciado) as ts_distintos, COUNT(*) as tickets
        FROM alfamega_06_vaciado
        WHERE FHrVaciado IS NOT NULL AND c_IdColada IS NOT NULL
        GROUP BY c_IdColada
        HAVING COUNT(DISTINCT FHrVaciado) = 1 AND COUNT(*) > 1
        ORDER BY tickets DESC LIMIT 10
    """)
    rows = cur.fetchall()
    p("Coladas con 1 solo timestamp para multiples tickets (problema):", len(rows), "encontradas")
    for r in rows: p(" ", r)
except Exception as e:
    p("ERROR:", e)

p("\n=== PC-4: distribución — cuántos tickets comparten mismo FHrVaciado por colada ===")
try:
    cur.execute("""
        SELECT bucket, COUNT(*) as n_coladas FROM (
            SELECT c_IdColada, COUNT(DISTINCT FHrVaciado) as bucket
            FROM alfamega_06_vaciado
            WHERE FHrVaciado IS NOT NULL AND c_IdColada IS NOT NULL
            GROUP BY c_IdColada
        ) t GROUP BY bucket ORDER BY bucket
    """)
    p("(ts_distintos_por_colada, n_coladas_con_ese_patron):")
    for r in cur.fetchall(): p(" ", r)
except Exception as e:
    p("ERROR:", e)

# ── PC-3: % FechaInoculante1 poblado ─────────────────────────────────────────
p("\n=== PC-3: % c_FechaInoculante1 poblado ===")
try:
    cur.execute("""SELECT COUNT(*) as total,
        SUM(CASE WHEN c_FechaInoculante1 IS NOT NULL THEN 1 ELSE 0 END) as con_fecha
        FROM cscmega_03coladacargametalica""")
    r = cur.fetchone()
    pct = round(100.0 * r[1] / r[0], 1) if r[0] else 0
    p(f"  total={r[0]}  con_FechaInoculante1={r[1]}  %={pct}")
except Exception as e:
    p("ERROR:", e)

p("\n=== PC-3: % c_FechaTrasvase1 poblado ===")
try:
    cur.execute("""SELECT COUNT(*) as total,
        SUM(CASE WHEN c_FechaTrasvase1 IS NOT NULL THEN 1 ELSE 0 END) as con_fecha
        FROM cscmega_03coladacargametalica""")
    r = cur.fetchone()
    pct = round(100.0 * r[1] / r[0], 1) if r[0] else 0
    p(f"  total={r[0]}  con_FechaTrasvase1={r[1]}  %={pct}")
except Exception as e:
    p("ERROR:", e)

# ── PC-5: m_tenfriamiento poblado ─────────────────────────────────────────────
p("\n=== PC-5: % m_tenfriamiento poblado (cscmega_06modelosvaciado) ===")
try:
    cur.execute("""SELECT COUNT(*) as total,
        SUM(CASE WHEN m_tenfriamiento IS NOT NULL AND m_tenfriamiento != '00:00:00' THEN 1 ELSE 0 END) as con_valor
        FROM cscmega_06modelosvaciado""")
    r = cur.fetchone()
    pct = round(100.0 * r[1] / r[0], 1) if r[0] else 0
    p(f"  total={r[0]}  con_tenfriamiento={r[1]}  %={pct}")
    cur.execute("SELECT DISTINCT m_tenfriamiento FROM cscmega_06modelosvaciado WHERE m_tenfriamiento IS NOT NULL AND m_tenfriamiento != '00:00:00' LIMIT 10")
    p("  valores distintos:", [r[0] for r in cur.fetchall()])
except Exception as e:
    p("ERROR:", e)

p("\n=== PC-5: % FHrDESM y FHrMOLD poblados (cscmega_08ruta) ===")
try:
    cur.execute("""SELECT COUNT(*) as total,
        SUM(CASE WHEN FHrMOLD IS NOT NULL THEN 1 ELSE 0 END) as con_mold,
        SUM(CASE WHEN FHrDESM IS NOT NULL THEN 1 ELSE 0 END) as con_desm,
        SUM(CASE WHEN FHrMOLD IS NOT NULL AND FHrDESM IS NOT NULL THEN 1 ELSE 0 END) as con_ambos
        FROM cscmega_08ruta""")
    r = cur.fetchone()
    total = r[0]
    p(f"  total={total}  FHrMOLD={r[1]}({round(100.0*r[1]/total,1) if total else 0}%)  FHrDESM={r[2]}({round(100.0*r[2]/total,1) if total else 0}%)  ambos={r[3]}({round(100.0*r[3]/total,1) if total else 0}%)")
except Exception as e:
    p("ERROR:", e)

# ── PC-7: buscar arefBatch en ia_fasa ─────────────────────────────────────────
p("\n=== PC-7: columnas con 'aref' o 'batch' en TODAS las tablas ia_fasa ===")
try:
    cur.execute("SHOW TABLES FROM ia_fasa")
    tablas = [r[0] for r in cur.fetchall()]
    for t in tablas:
        cur.execute("SHOW COLUMNS FROM `ia_fasa`.`%s`" % t)
        for col in cur.fetchall():
            if any(k in col[0].lower() for k in ['aref', 'batch']):
                p(f"  {t}.{col[0]}")
except Exception as e:
    p("ERROR arefBatch ia_fasa:", e)

# ── PC-7: buscar arefBatch en corex_test ──────────────────────────────────────
p("\n=== PC-7: tablas con 'batch' en corex_test ===")
try:
    cur.execute("SHOW TABLES FROM corex_test")
    for r in cur.fetchall():
        if 'batch' in r[0].lower():
            p(" ", r[0])
except Exception as e:
    p("ERROR corex batch:", e)

# ── PC-1: columnas de modelocastingrecetas (TBL-HIC candidato) ───────────────
p("\n=== PC-1: SHOW COLUMNS corex_test.modelocastingrecetas ===")
try:
    cur.execute("SHOW COLUMNS FROM `corex_test`.`modelocastingrecetas`")
    for r in cur.fetchall(): p(r)
except Exception as e:
    p("ERROR modelocastingrecetas:", e)

p("\n=== PC-1: SHOW COLUMNS alfamega_07_cargametalicos ===")
try:
    cur.execute("SHOW COLUMNS FROM `ia_fasa`.`alfamega_07_cargametalicos`")
    for r in cur.fetchall(): p(r)
    cur.execute("SELECT * FROM `ia_fasa`.`alfamega_07_cargametalicos` LIMIT 3")
    p("MUESTRA:", [d[0] for d in cur.description])
    for r in cur.fetchall(): p(" ", r)
except Exception as e:
    p("ERROR alfamega_07_cargametalicos:", e)

# ── PC-2: tabla de cumplimiento químico ───────────────────────────────────────
p("\n=== PC-2: SHOW COLUMNS cscmega_05aquimicoscumplimiento ===")
try:
    cur.execute("SHOW COLUMNS FROM `ia_fasa`.`cscmega_05aquimicoscumplimiento`")
    for r in cur.fetchall(): p(r)
except Exception as e:
    p("ERROR quim cumpl:", e)

conn.close()
OUT.close()
