"""
Toneladas por año usando alfamega_03_coladasproceso.Kilos + FechaInicial
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).parent.parent / ".env")
engine = create_engine(
    f"mysql+pymysql://{os.environ['DB_USER']}:{os.environ['DB_PWD']}"
    f"@{os.environ['DB_HOST']}:3306/ia_fasa?charset=utf8mb4", pool_pre_ping=True)

def run(conn, titulo, sql):
    print(f"\n{'='*60}\n  {titulo}\n{'='*60}")
    try:
        rows = conn.execute(text(sql)).fetchall()
        if not rows: print("  (sin resultados)"); return
        keys = rows[0]._fields if hasattr(rows[0], '_fields') else rows[0].keys()
        print("  " + " | ".join(str(k)[:28] for k in keys))
        print("  " + "-"*60)
        for r in rows: print("  " + " | ".join(str(v)[:28] for v in r))
    except Exception as e: print(f"  ERROR: {e}")

with engine.connect() as conn:
    # Toneladas por año
    run(conn, "Toneladas por año — alfamega_03_coladasproceso", """
        SELECT YEAR(FechaInicial) AS anio,
               COUNT(DISTINCT c_IdColada) AS coladas,
               ROUND(SUM(Kilos)/1000, 1) AS toneladas
        FROM alfamega_03_coladasproceso
        WHERE FechaInicial IS NOT NULL AND Kilos > 0
          AND YEAR(FechaInicial) >= 2022
        GROUP BY YEAR(FechaInicial) ORDER BY anio
    """)

    # Toneladas por mes (para ver estacionalidad)
    run(conn, "Toneladas por mes 2023-2025", """
        SELECT YEAR(FechaInicial) AS anio,
               MONTH(FechaInicial) AS mes,
               ROUND(SUM(Kilos)/1000, 1) AS toneladas,
               COUNT(DISTINCT c_IdColada) AS coladas
        FROM alfamega_03_coladasproceso
        WHERE FechaInicial IS NOT NULL AND Kilos > 0
          AND YEAR(FechaInicial) BETWEEN 2023 AND 2025
        GROUP BY YEAR(FechaInicial), MONTH(FechaInicial)
        ORDER BY anio, mes
    """)

    # alfamega_07_cargametalicos — columnas y rango
    run(conn, "Columnas alfamega_07_cargametalicos", "SHOW COLUMNS FROM alfamega_07_cargametalicos")
