"""Audit PC-2b: rango de coladas en tabla química vs tabla principal"""
import pymysql, os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

conn = pymysql.connect(
    host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT')),
    user=os.getenv('DB_USER'), password=os.getenv('DB_PWD'),
    database='ia_fasa', connect_timeout=15, charset='utf8mb4',
    read_timeout=30, write_timeout=30
)
cur = conn.cursor()

def p(msg): print(msg)

# Rango de u_Colada en tabla química
p("=== Rango u_Colada en cscmega_05aquimicoscumplimiento ===")
cur.execute("SELECT MIN(u_Colada), MAX(u_Colada), COUNT(DISTINCT u_Colada) FROM cscmega_05aquimicoscumplimiento")
r = cur.fetchone()
p(f"  min={r[0]}  max={r[1]}  distintas={r[2]}")

# Rango de u_Colada en tabla principal de coladas
p("\n=== Rango u_Colada en cscmega_03coladacargametalica ===")
cur.execute("SELECT MIN(u_Colada), MAX(u_Colada), COUNT(DISTINCT u_Colada) FROM cscmega_03coladacargametalica")
r = cur.fetchone()
p(f"  min={r[0]}  max={r[1]}  distintas={r[2]}")

# ¿Hay solapamiento?
p("\n=== Solapamiento: coladas con química que también están en coladas principales ===")
cur.execute("""
    SELECT COUNT(DISTINCT q.u_Colada) AS en_ambas
    FROM cscmega_05aquimicoscumplimiento q
    INNER JOIN cscmega_03coladacargametalica c
      ON c.u_Colada = q.u_Colada AND c.u_Horno = q.u_Horno
""")
r = cur.fetchone()
p(f"  coladas con match: {r[0]}")

# ¿El u_Fecha de química corresponde a fechas recientes?
p("\n=== Rango de fechas en química (u_Fecha) ===")
cur.execute("SELECT MIN(u_Fecha), MAX(u_Fecha) FROM cscmega_05aquimicoscumplimiento WHERE u_Fecha > '2000-01-01'")
r = cur.fetchone()
p(f"  min={r[0]}  max={r[1]}")

# Últimas 5 filas con datos para ver qué tan reciente es
p("\n=== Últimas 5 filas por u_Fecha ===")
cur.execute("""
    SELECT u_Fecha, u_Colada, u_Horno, bC_F, bSi_F, bMg_F
    FROM cscmega_05aquimicoscumplimiento
    WHERE u_Fecha > '2000-01-01'
    ORDER BY u_Fecha DESC LIMIT 5
""")
for r in cur.fetchall():
    p(f"  {r[0]}  colada={r[1]} horno={r[2]}  C={r[3]} Si={r[4]} Mg={r[5]}")

# ¿Hay otra tabla de química en ia_fasa?
p("\n=== Tablas con 'quimico' o 'quimica' o 'analisis' en ia_fasa ===")
cur.execute("SHOW TABLES FROM ia_fasa")
tablas = [r[0] for r in cur.fetchall()]
for t in tablas:
    if any(k in t.lower() for k in ['quim', 'analisi', 'espec', 'chem']):
        p(f"  {t}")

conn.close()
p("\nDONE")
