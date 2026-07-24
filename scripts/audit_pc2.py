"""Audit PC-2: flags bX_B / bX_F en cscmega_05aquimicoscumplimiento"""
import pymysql, os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))
OUT = open(os.path.join(os.path.dirname(__file__), 'audit_pc2_out.txt'), 'w', encoding='utf-8')

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

# 1. Valores distintos en flags clave
p("=== Valores distintos en flags _B ===")
for col in ['bC_B', 'bSi_B', 'bMg_B', 'bS_B', 'bP_B', 'bMn_B']:
    cur.execute(f"SELECT DISTINCT `{col}` FROM cscmega_05aquimicoscumplimiento LIMIT 10")
    vals = [str(r[0]) for r in cur.fetchall()]
    p(f"  {col}: {vals}")

p("\n=== Valores distintos en flags _F ===")
for col in ['bC_F', 'bSi_F', 'bMg_F', 'bS_F', 'bP_F', 'bMn_F']:
    cur.execute(f"SELECT DISTINCT `{col}` FROM cscmega_05aquimicoscumplimiento LIMIT 10")
    vals = [str(r[0]) for r in cur.fetchall()]
    p(f"  {col}: {vals}")

# 2. Cuántas filas por colada (¿análisis único o múltiple por colada?)
p("\n=== Filas por u_Colada+u_Horno (distribución) ===")
cur.execute("""
    SELECT n_filas, COUNT(*) AS n_coladas
    FROM (
        SELECT u_Colada, u_Horno, COUNT(*) AS n_filas
        FROM cscmega_05aquimicoscumplimiento
        GROUP BY u_Colada, u_Horno
    ) t
    GROUP BY n_filas ORDER BY n_filas
""")
for r in cur.fetchall():
    p(f"  {r[0]} filas → {r[1]} coladas")

# 3. Muestra: una fila completa para ver flags reales
p("\n=== Muestra de 3 filas con flags ===")
cur.execute("""
    SELECT u_Colada, u_Horno, bC_B, bSi_B, bMg_B, bS_B, bP_B,
           bC_F, bSi_F, bMg_F, bS_F, bP_F
    FROM cscmega_05aquimicoscumplimiento
    WHERE bC_B IS NOT NULL LIMIT 3
""")
for r in cur.fetchall():
    p(f"  colada={r[0]} horno={r[1]} | _B: C={r[2]} Si={r[3]} Mg={r[4]} S={r[5]} P={r[6]} | _F: C={r[7]} Si={r[8]} Mg={r[9]} S={r[10]} P={r[11]}")

# 4. Join test: ¿u_Colada+u_Horno matchea con cscmega_03coladacargametalica?
p("\n=== Join test: química vs coladas principales (últimas 100 coladas) ===")
cur.execute("""
    SELECT COUNT(DISTINCT q.u_Colada) AS con_quimica,
           COUNT(DISTINCT c.c_IdColada) AS total_coladas
    FROM (
        SELECT DISTINCT u_Colada, u_Horno FROM cscmega_05aquimicoscumplimiento
    ) q
    RIGHT JOIN (
        SELECT DISTINCT c_IdColada, MAX(u_Colada) AS u_Colada, MAX(u_Horno) AS u_Horno
        FROM cscmega_03coladacargametalica
        ORDER BY MIN(c_FechaInicial) DESC LIMIT 100
    ) c ON c.u_Colada = q.u_Colada AND c.u_Horno = q.u_Horno
""")
r = cur.fetchone()
p(f"  últimas 100 coladas: {r[1]} total, {r[0]} con química ({round(100*r[0]/r[1],1) if r[1] else 0}%)")

# 5. % flags True vs False vs NULL en bC_F (indicativo de cobertura real)
p("\n=== bC_F distribución completa ===")
cur.execute("""
    SELECT bC_F, COUNT(*) FROM cscmega_05aquimicoscumplimiento GROUP BY bC_F
""")
for r in cur.fetchall():
    p(f"  '{r[0]}' → {r[1]} filas")

conn.close()
OUT.close()
p("DONE")
