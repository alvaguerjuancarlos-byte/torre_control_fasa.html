"""Paso 1: listar tablas ia_fasa y columnas de tablas clave para PC-4"""
import pymysql, os, sys
from dotenv import load_dotenv

OUT = open(os.path.join(os.path.dirname(__file__), 'out_step2.txt'), 'w', encoding='utf-8')
def p(*args):
    print(*args)
    print(*args, file=OUT)
    OUT.flush()

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

def q(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchall()

conn = pymysql.connect(
    host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT')),
    user=os.getenv('DB_USER'), password=os.getenv('DB_PWD'),
    database='ia_fasa', connect_timeout=15, charset='utf8mb4'
)
cur = conn.cursor()

p("=== ia_fasa TABLAS ===")
for r in q(cur, "SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA='ia_fasa' ORDER BY TABLE_ROWS DESC"):
    p(r)

p("\n=== cscmega_06modelosvaciado COLUMNAS ===")
for r in q(cur, "SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='ia_fasa' AND TABLE_NAME='cscmega_06modelosvaciado' ORDER BY ORDINAL_POSITION"):
    p(r)

p("\n=== cscmega_03coladacargametalica COLUMNAS ===")
for r in q(cur, "SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='ia_fasa' AND TABLE_NAME='cscmega_03coladacargametalica' ORDER BY ORDINAL_POSITION"):
    p(r)

p("\n=== cscmega_06modelosvaciado MUESTRA (5 filas) ===")
cur.execute("SELECT * FROM cscmega_06modelosvaciado LIMIT 5")
p("COLS:", [d[0] for d in cur.description])
for r in cur.fetchall(): p(r)

conn.close()
OUT.close()
p("DONE")
