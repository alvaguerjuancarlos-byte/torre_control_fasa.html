"""
¿Tenemos kilos/toneladas para 2023 y 2024?
Buscamos en tablas alfamega que podrían tener peso histórico.
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
    run(conn, "Tablas alfamega_07*", "SHOW TABLES LIKE 'alfamega_07%'")
    run(conn, "Columnas alfamega_07_cargametalico", "SHOW COLUMNS FROM alfamega_07_cargametalico")
    run(conn, "Columnas alfamega_03_coladasproceso", "SHOW COLUMNS FROM alfamega_03_coladasproceso")
    run(conn, "alfamega_03_coladasproceso — kilos por año", """
        SELECT YEAR(FHrColada) AS anio, COUNT(*) AS coladas,
               ROUND(SUM(c_Kilos)/1000, 1) AS toneladas
        FROM alfamega_03_coladasproceso
        WHERE FHrColada IS NOT NULL AND YEAR(FHrColada) >= 2022
        GROUP BY YEAR(FHrColada) ORDER BY anio
    """)
    run(conn, "alfamega_07_cargametalico — kilos por año", """
        SELECT YEAR(FHrColada) AS anio, COUNT(*) AS registros,
               ROUND(SUM(c_Kilos)/1000, 1) AS toneladas
        FROM alfamega_07_cargametalico
        WHERE FHrColada IS NOT NULL AND YEAR(FHrColada) >= 2022
        GROUP BY YEAR(FHrColada) ORDER BY anio
    """)
