"""Audit v4 — solo los % que fallaron por Decimal en v3"""
import pymysql, os
from decimal import Decimal
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))
OUT = open(os.path.join(os.path.dirname(__file__), 'audit_v4_out.txt'), 'w', encoding='utf-8')

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

def pct(cur, table, col_check):
    cur.execute(f"SELECT COUNT(*), SUM(CASE WHEN {col_check} THEN 1 ELSE 0 END) FROM `{table}`")
    r = cur.fetchone()
    total, nn = int(r[0]), int(r[1] or 0)
    return total, nn, round(100.0 * nn / total, 1) if total else 0

# PC-3
p("=== PC-3: FechaInoculante1 ===")
total, nn, pc = pct(cur, 'cscmega_03coladacargametalica', 'c_FechaInoculante1 IS NOT NULL')
p(f"  total={total}  con_valor={nn}  %={pc}")

p("\n=== PC-3: FechaTrasvase1 ===")
total, nn, pc = pct(cur, 'cscmega_03coladacargametalica', 'c_FechaTrasvase1 IS NOT NULL')
p(f"  total={total}  con_valor={nn}  %={pc}")

# PC-5
p("\n=== PC-5: m_tenfriamiento ===")
total, nn, pc = pct(cur, 'cscmega_06modelosvaciado', "m_tenfriamiento IS NOT NULL AND m_tenfriamiento != '00:00:00'")
p(f"  total={total}  con_valor={nn}  %={pc}")
cur.execute("SELECT DISTINCT m_tenfriamiento FROM cscmega_06modelosvaciado WHERE m_tenfriamiento IS NOT NULL AND m_tenfriamiento != '00:00:00' LIMIT 8")
p("  valores distintos:", [str(r[0]) for r in cur.fetchall()])

p("\n=== PC-5: FHrMOLD y FHrDESM ===")
total, nn_mold, _ = pct(cur, 'cscmega_08ruta', 'FHrMOLD IS NOT NULL')
_, nn_desm, _ = pct(cur, 'cscmega_08ruta', 'FHrDESM IS NOT NULL')
_, nn_ambos, _ = pct(cur, 'cscmega_08ruta', 'FHrMOLD IS NOT NULL AND FHrDESM IS NOT NULL')
p(f"  total={total}  FHrMOLD={nn_mold}({round(100*nn_mold/total,1)}%)  FHrDESM={nn_desm}({round(100*nn_desm/total,1)}%)  ambos={nn_ambos}({round(100*nn_ambos/total,1)}%)")

# PC-7: arefBatch scan (skip table con DDL lock)
p("\n=== PC-7: arefBatch scan ia_fasa (skip tablas con error) ===")
cur.execute("SHOW TABLES FROM ia_fasa")
tablas = [r[0] for r in cur.fetchall()]
for t in tablas:
    try:
        cur.execute("SHOW COLUMNS FROM `ia_fasa`.`%s`" % t)
        for col in cur.fetchall():
            if any(k in col[0].lower() for k in ['aref', 'batch', 'itaca']):
                p(f"  {t}.{col[0]}")
    except Exception:
        pass

# PC-2: % cumplimiento quimico poblado
p("\n=== PC-2: % CumplimientoAnalisisQuimico poblado ===")
total, nn, pc = pct(cur, 'cscmega_05aquimicoscumplimiento', "CumplimientoAnalisisQuimico IS NOT NULL AND CumplimientoAnalisisQuimico != ''")
p(f"  total={total}  con_valor={nn}  %={pc}")
cur.execute("SELECT DISTINCT CumplimientoAnalisisQuimico FROM cscmega_05aquimicoscumplimiento LIMIT 10")
p("  valores:", [r[0] for r in cur.fetchall()])

conn.close()
OUT.close()
