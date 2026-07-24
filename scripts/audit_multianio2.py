"""
Confirmar rango multi-año en tablas candidatas.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).parent.parent / ".env")

engine = create_engine(
    f"mysql+pymysql://{os.environ['DB_USER']}:{os.environ['DB_PWD']}"
    f"@{os.environ['DB_HOST']}:{os.environ.get('DB_PORT', 3306)}/{os.environ.get('DB_SCHEMA', 'ia_fasa')}"
    "?charset=utf8mb4",
    pool_pre_ping=True,
)

def run(conn, titulo, sql):
    print(f"\n{'='*65}")
    print(f"  {titulo}")
    print(f"{'='*65}")
    try:
        rows = conn.execute(text(sql)).fetchall()
        if not rows:
            print("  (sin resultados)")
            return
        keys = rows[0]._fields if hasattr(rows[0], '_fields') else rows[0].keys()
        header = " | ".join(str(k)[:30] for k in keys)
        print("  " + header)
        print("  " + "-" * min(len(header), 130))
        for r in rows:
            print("  " + " | ".join(str(v)[:30] for v in r))
    except Exception as e:
        print(f"  ERROR: {e}")

with engine.connect() as conn:

    # 1. alfamega_01_rechazosbyidticket — por anio (columna nativa)
    run(conn, "alfamega_01_rechazosbyidticket — por campo anio", """
        SELECT anio, COUNT(*) AS registros, SUM(bRechazo) AS rechazos
        FROM alfamega_01_rechazosbyidticket
        WHERE anio >= 2022
        GROUP BY anio ORDER BY anio
    """)

    # 2. alfamega_01_rechazosbyidticket — por FechaRechazo
    run(conn, "alfamega_01_rechazosbyidticket — por FechaRechazo", """
        SELECT YEAR(FechaRechazo) AS anio, COUNT(*) AS registros
        FROM alfamega_01_rechazosbyidticket
        WHERE FechaRechazo IS NOT NULL AND YEAR(FechaRechazo) >= 2022
        GROUP BY YEAR(FechaRechazo) ORDER BY anio
    """)

    # 3. alfamega_01_rechazosbyidticket — por FHrVaciado
    run(conn, "alfamega_01_rechazosbyidticket — por FHrVaciado", """
        SELECT YEAR(FHrVaciado) AS anio, COUNT(*) AS registros, SUM(bRechazo) AS rechazos
        FROM alfamega_01_rechazosbyidticket
        WHERE FHrVaciado IS NOT NULL AND YEAR(FHrVaciado) >= 2022
        GROUP BY YEAR(FHrVaciado) ORDER BY anio
    """)

    # 4. cscmega_01resultadoidticket — columnas completas
    run(conn, "Columnas cscmega_01resultadoidticket", "SHOW COLUMNS FROM cscmega_01resultadoidticket")

    # 5. alfamega_06_vaciado — temperatura promedio por año (KPI de proceso)
    run(conn, "alfamega_06_vaciado — KPI temperatura por año", """
        SELECT
            YEAR(FHrVaciado) AS anio,
            COUNT(*) AS tickets,
            COUNT(DISTINCT c_IdColada) AS coladas,
            ROUND(AVG(CASE WHEN vt_TemperaturaVaciado BETWEEN 1100 AND 1600
                           THEN vt_TemperaturaVaciado END), 1) AS temp_avg,
            ROUND(SUM(bCumplimientoTemperaturaOk) / COUNT(*) * 100, 1) AS pct_en_rango
        FROM alfamega_06_vaciado
        WHERE FHrVaciado IS NOT NULL AND YEAR(FHrVaciado) >= 2022
        GROUP BY YEAR(FHrVaciado) ORDER BY anio
    """)

print("\nListo.")
