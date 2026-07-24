"""
Auditoría 2026 — parte 3:
- Columnas reales de cscmega_08ruta
- Rango de u_Fecha en rechazos y quimicos
- Tabla maestra de coladas para 2026
- Resumen histórico completo
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
        header = " | ".join(str(k)[:28] for k in keys)
        print("  " + header)
        print("  " + "-" * min(len(header), 120))
        for r in rows:
            print("  " + " | ".join(str(v)[:28] for v in r))
    except Exception as e:
        print(f"  ERROR: {e}")

with engine.connect() as conn:

    # 1. Columnas de cscmega_08ruta (para fix del c_IdColada)
    run(conn, "Columnas de cscmega_08ruta", "SHOW COLUMNS FROM cscmega_08ruta")

    # 2. Fechas en rechazos (u_Fecha)
    run(conn, "Rechazos por año — u_Fecha", """
        SELECT
            YEAR(u_Fecha) AS anio,
            COUNT(*) AS rechazos,
            SUM(YEAR(u_Fecha) = 2026) AS rechazos_2026
        FROM cscmega_01rechazosbyidticket
        WHERE u_Fecha IS NOT NULL AND YEAR(u_Fecha) > 2020
        GROUP BY YEAR(u_Fecha)
        ORDER BY anio
    """)

    # 3. Fechas en quimicos (u_Fecha)
    run(conn, "Análisis químicos por año — u_Fecha", """
        SELECT
            YEAR(u_Fecha) AS anio,
            COUNT(*) AS registros
        FROM cscmega_05aquimicoscumplimiento
        WHERE u_Fecha IS NOT NULL AND YEAR(u_Fecha) > 2020
        GROUP BY YEAR(u_Fecha)
        ORDER BY anio
    """)

    # 4. alfamega_03_coladasproces — columnas y rango
    run(conn, "Columnas alfamega_03_coladasproces", "SHOW COLUMNS FROM alfamega_03_coladasproces")

    # 5. alfamega_00_coladaproceso — columnas
    run(conn, "Columnas alfamega_00_coladaproceso", "SHOW COLUMNS FROM alfamega_00_coladaproceso")

    # 6. cscmega_00_corridas — columnas y rango de fechas
    run(conn, "Columnas cscmega_00_corridas", "SHOW COLUMNS FROM cscmega_00_corridas")

print("\nListo.")
