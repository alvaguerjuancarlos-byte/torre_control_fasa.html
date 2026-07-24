"""
¿Las tablas alfamega_* tienen datos 2023-2024?
Verificamos si son la fuente histórica vs cscmega_* (solo 2025).
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

    # Nombres completos de tablas alfamega_01*
    run(conn, "Tablas alfamega_01* (rechazos históricos?)", "SHOW TABLES LIKE 'alfamega_01%'")

    # Nombres completos cscmega_01*
    run(conn, "Tablas cscmega_01* (rechazos actuales)", "SHOW TABLES LIKE 'cscmega_01%'")

    # Columnas alfamega_01_rechazosbyidticket
    run(conn, "Columnas alfamega_01_rechazosbyidticket", "SHOW COLUMNS FROM alfamega_01_rechazosbyidticket")

    # Rango de años en alfamega_01_rechazosbyidticket
    run(conn, "Años en alfamega_01_rechazosbyidticket", """
        SELECT YEAR(u_Fecha) AS anio, COUNT(*) AS registros
        FROM alfamega_01_rechazosbyidticket
        WHERE u_Fecha IS NOT NULL AND YEAR(u_Fecha) >= 2022
        GROUP BY YEAR(u_Fecha) ORDER BY anio
    """)

    # Rango de años en alfamega_01rechazosbyidticket (sin guion)
    run(conn, "Años en alfamega_01rechazosbyidticket (sin guion)", """
        SELECT YEAR(u_Fecha) AS anio, COUNT(*) AS registros
        FROM alfamega_01rechazosbyidticket
        WHERE u_Fecha IS NOT NULL AND YEAR(u_Fecha) >= 2022
        GROUP BY YEAR(u_Fecha) ORDER BY anio
    """)

    # cscmega_03coladacargametalica — ¿tiene histórico?
    run(conn, "Años en cscmega_03coladacargametalica", """
        SELECT YEAR(u_Fecha) AS anio, COUNT(*) AS registros
        FROM cscmega_03coladacargametalica
        WHERE u_Fecha IS NOT NULL AND YEAR(u_Fecha) >= 2022
        GROUP BY YEAR(u_Fecha) ORDER BY anio
    """)

    # cscmega_01resultadoidticket — ¿tiene histórico?
    run(conn, "Años en cscmega_01resultadoidticket", """
        SELECT YEAR(u_FechaHr) AS anio, COUNT(*) AS registros
        FROM cscmega_01resultadoidticket
        WHERE u_FechaHr IS NOT NULL AND YEAR(u_FechaHr) >= 2022
        GROUP BY YEAR(u_FechaHr) ORDER BY anio
    """)

print("\nListo.")
