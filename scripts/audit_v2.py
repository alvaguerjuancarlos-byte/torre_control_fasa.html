"""Auditoría PC-1 a PC-7 — versión sin information_schema.TABLE_ROWS"""
import pymysql, os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

OUT = open(os.path.join(os.path.dirname(__file__), 'audit_v2_out.txt'), 'w', encoding='utf-8')

def p(*args):
    line = ' '.join(str(a) for a in args)
    print(line)
    print(line, file=OUT)
    OUT.flush()

def cols(cur, schema, table):
    cur.execute("SHOW COLUMNS FROM `%s`.`%s`" % (schema, table))
    return [(r[0], r[1]) for r in cur.fetchall()]

def cnt(cur, table, where=''):
    sql = "SELECT COUNT(*) FROM `%s` %s" % (table, where)
    cur.execute(sql)
    return cur.fetchone()[0]

def pct_nonzero(cur, table, col, extra_where=''):
    w = "WHERE (%s IS NOT NULL AND %s != 0)" % (col, col)
    if extra_where:
        w += ' AND ' + extra_where
    total = cnt(cur, table)
    nonnull = cnt(cur, table, w)
    return total, nonnull, round(100.0 * nonnull / total, 1) if total else 0

conn = pymysql.connect(
    host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT')),
    user=os.getenv('DB_USER'), password=os.getenv('DB_PWD'),
    database='ia_fasa', connect_timeout=15, charset='utf8mb4',
    read_timeout=30, write_timeout=30
)
cur = conn.cursor()

# ── TABLAS ia_fasa ────────────────────────────────────────────────────────────
p("=== SHOW TABLES ia_fasa ===")
cur.execute("SHOW TABLES FROM ia_fasa")
for r in cur.fetchall():
    p(r[0])

# ── PC-4: alfamega_06_vaciado ─────────────────────────────────────────────────
p("\n=== PC-4: SHOW COLUMNS alfamega_06_vaciado ===")
for c in cols(cur, 'ia_fasa', 'alfamega_06_vaciado'):
    p(c)

p("\n=== PC-4: muestra alfamega_06_vaciado (3 filas) ===")
cur.execute("SELECT * FROM alfamega_06_vaciado LIMIT 3")
p("COLS:", [d[0] for d in cur.description])
for r in cur.fetchall():
    p(r)

p("\n=== PC-4: SHOW COLUMNS cscmega_06modelosvaciado ===")
for c in cols(cur, 'ia_fasa', 'cscmega_06modelosvaciado'):
    p(c)

p("\n=== PC-4: muestra cscmega_06modelosvaciado (3 filas) ===")
cur.execute("SELECT * FROM cscmega_06modelosvaciado LIMIT 3")
p("COLS:", [d[0] for d in cur.description])
for r in cur.fetchall():
    p(r)

# ── PC-4: audit de timestamps duplicados ─────────────────────────────────────
p("\n=== PC-4: timestamps duplicados en alfamega_06_vaciado ===")
try:
    cur.execute("""
        SELECT vf_FechaHora, COUNT(*) as n
        FROM alfamega_06_vaciado
        WHERE vf_FechaHora IS NOT NULL
        GROUP BY vf_FechaHora HAVING COUNT(*) > 1
        ORDER BY n DESC LIMIT 15
    """)
    rows = cur.fetchall()
    p("Duplicados encontrados:", len(rows))
    for r in rows: p(r)
except Exception as e:
    p("ERROR ts-dup alfamega:", e)

p("\n=== PC-4: % temperatura poblada (cscmega_06modelosvaciado) ===")
try:
    total, nn, pct = pct_nonzero(cur, 'cscmega_06modelosvaciado', 'vt_TemperaturaVaciado')
    p(f"  total={total}  con_temp={nn}  %={pct}")
    cur.execute("SELECT MIN(vt_TemperaturaVaciado), MAX(vt_TemperaturaVaciado), AVG(CASE WHEN vt_TemperaturaVaciado BETWEEN 1100 AND 1600 THEN vt_TemperaturaVaciado END) FROM cscmega_06modelosvaciado")
    r = cur.fetchone()
    p(f"  min={r[0]}  max={r[1]}  avg_filtrada={round(r[2],1) if r[2] else None}")
except Exception as e:
    p("ERROR temp:", e)

# ── PC-3: inoculante / nodularización ────────────────────────────────────────
p("\n=== PC-3: SHOW COLUMNS cscmega_03coladacargametalica ===")
for c in cols(cur, 'ia_fasa', 'cscmega_03coladacargametalica'):
    p(c)

p("\n=== PC-3: % c_InoculanteEnHornos poblado ===")
try:
    total, nn, pct = pct_nonzero(cur, 'cscmega_03coladacargametalica', 'c_InoculanteEnHornos')
    p(f"  total={total}  con_valor={nn}  %={pct}")
except Exception as e:
    p("ERROR inoculante:", e)

# ── PC-2: química base ────────────────────────────────────────────────────────
p("\n=== PC-2: % química base poblada (cscmega_05cquimicosbase) ===")
for elemento in ['aB_C', 'aB_Si', 'aB_Mn', 'aB_S', 'aB_P', 'aB_Mg']:
    try:
        total, nn, pct = pct_nonzero(cur, 'cscmega_05cquimicosbase', elemento)
        p(f"  {elemento}: {nn}/{total} = {pct}%")
    except Exception as e:
        p(f"  {elemento}: ERROR {e}")

# ── PC-1: receta de carga ─────────────────────────────────────────────────────
p("\n=== PC-1: tablas con 'carga' o 'receta' en ia_fasa ===")
cur.execute("SHOW TABLES FROM ia_fasa")
todas = [r[0] for r in cur.fetchall()]
for t in todas:
    if any(k in t.lower() for k in ['carg', 'recet', 'horno', 'metal', 'inocu']):
        p(" ", t)

p("\n=== PC-1: SHOW COLUMNS cscmega_03coladacargametalica (carga metálica) ===")
for c in cols(cur, 'ia_fasa', 'cscmega_03coladacargametalica'):
    p(c)

# ── PC-1: buscar TBL-HIC equivalente en corex_test ───────────────────────────
p("\n=== PC-1: tablas con 'hic' o 'receta' en corex_test ===")
try:
    cur.execute("SHOW TABLES FROM corex_test")
    for r in cur.fetchall():
        t = r[0]
        if any(k in t.lower() for k in ['hic', 'recet', 'carga', 'metalic']):
            p(" ", t)
except Exception as e:
    p("ERROR corex_test:", e)

# ── PC-7: ColadaAsHistorial ───────────────────────────────────────────────────
p("\n=== PC-7: tablas con 'historial' o 'colada' en ia_fasa ===")
for t in todas:
    if any(k in t.lower() for k in ['historial', 'colada']):
        p(" ", t)

p("\n=== PC-7: SHOW COLUMNS tabla coladas ===")
try:
    for c in cols(cur, 'ia_fasa', 'cscmega_03coladacargametalica'):
        if any(k in c[0].lower() for k in ['colada', 'batch', 'aref', 'historial']):
            p(" ", c)
except Exception as e:
    p("ERROR PC7 cols:", e)

# ── PC-5: tiempo en molde ─────────────────────────────────────────────────────
p("\n=== PC-5: SHOW COLUMNS cscmega_08ruta ===")
try:
    for c in cols(cur, 'ia_fasa', 'cscmega_08ruta'):
        p(c)
except Exception as e:
    p("ERROR ruta:", e)

# ── PC-6: HBW / nodularidad ───────────────────────────────────────────────────
p("\n=== PC-6: tablas con 'evc', 'inspec', 'hbw', 'nodular', 'liber' en ia_fasa ===")
for t in todas:
    if any(k in t.lower() for k in ['evc', 'inspec', 'hbw', 'nodular', 'liber', 'calid']):
        p(" ", t)

p("\n=== PC-6: tablas con 'evc', 'inspec', 'hbw', 'nodular' en corex_test ===")
try:
    cur.execute("SHOW TABLES FROM corex_test")
    for r in cur.fetchall():
        t = r[0]
        if any(k in t.lower() for k in ['evc', 'inspec', 'hbw', 'nodular', 'liber']):
            p(" ", t)
except Exception as e:
    p("ERROR corex_test PC6:", e)

conn.close()
OUT.close()
p("=== DONE ===")
