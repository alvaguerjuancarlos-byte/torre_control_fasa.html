"""
Auditoría PC-4 y listado de tablas ia_fasa
"""
import pymysql, os, sys
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

conn = pymysql.connect(
    host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT')),
    user=os.getenv('DB_USER'), password=os.getenv('DB_PWD'),
    database='ia_fasa', connect_timeout=15, charset='utf8mb4'
)
cur = conn.cursor()

print("=== TABLAS ia_fasa (por tamaño) ===")
cur.execute("""SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES
               WHERE TABLE_SCHEMA='ia_fasa' ORDER BY TABLE_ROWS DESC""")
for r in cur.fetchall():
    print(r)

print("\n=== PC-4: columnas de cscmega_06modelosvaciado ===")
cur.execute("""SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA='ia_fasa' AND TABLE_NAME='cscmega_06modelosvaciado'
               ORDER BY ORDINAL_POSITION""")
for r in cur.fetchall():
    print(r)

print("\n=== PC-4: muestra de cscmega_06modelosvaciado ===")
cur.execute("SELECT * FROM cscmega_06modelosvaciado LIMIT 5")
cols = [d[0] for d in cur.description]
print("COLS:", cols)
for r in cur.fetchall():
    print(r)

print("\n=== PC-4: distribución de timestamps - grupos con mismo ts ===")
cur.execute("""
    SELECT vt_FechaHoraVaciado, COUNT(*) as n_coladas
    FROM cscmega_06modelosvaciado
    WHERE vt_FechaHoraVaciado IS NOT NULL
    GROUP BY vt_FechaHoraVaciado
    HAVING COUNT(*) > 1
    ORDER BY n_coladas DESC
    LIMIT 20
""")
rows = cur.fetchall()
if rows:
    print("Timestamps duplicados (ts, n_coladas):")
    for r in rows: print(r)
else:
    print("Sin duplicados en vt_FechaHoraVaciado - probando otras columnas de ts...")

print("\n=== PC-4: columnas con Fecha/timestamp en cscmega_06modelosvaciado ===")
cur.execute("""SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA='ia_fasa' AND TABLE_NAME='cscmega_06modelosvaciado'
               AND (DATA_TYPE IN ('datetime','timestamp','date')
                    OR LOWER(COLUMN_NAME) LIKE '%fecha%'
                    OR LOWER(COLUMN_NAME) LIKE '%hora%'
                    OR LOWER(COLUMN_NAME) LIKE '%ts%')""")
for r in cur.fetchall():
    print(r)

print("\n=== PC-4: total registros y % temperatura poblada ===")
cur.execute("""SELECT
    COUNT(*) as total,
    SUM(CASE WHEN vt_TemperaturaVaciado IS NOT NULL AND vt_TemperaturaVaciado != 0 THEN 1 ELSE 0 END) as con_temp,
    ROUND(100.0 * SUM(CASE WHEN vt_TemperaturaVaciado IS NOT NULL AND vt_TemperaturaVaciado != 0 THEN 1 ELSE 0 END) / COUNT(*), 1) as pct_temp,
    SUM(CASE WHEN vt_TemperaturaVaciado BETWEEN 1100 AND 1600 THEN 1 ELSE 0 END) as en_rango,
    MIN(vt_TemperaturaVaciado) as min_temp, MAX(vt_TemperaturaVaciado) as max_temp,
    ROUND(AVG(CASE WHEN vt_TemperaturaVaciado BETWEEN 1100 AND 1600 THEN vt_TemperaturaVaciado END), 1) as avg_temp_filtrada
    FROM cscmega_06modelosvaciado""")
cols = [d[0] for d in cur.description]
r = cur.fetchone()
for k, v in zip(cols, r): print(f"  {k}: {v}")

conn.close()
print("\nDONE")
sys.stdout.flush()
