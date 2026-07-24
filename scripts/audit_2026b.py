"""
Auditoría 2026 — parte 2:
- Columnas de fecha en tablas que fallaron
- Detalle de las 37 piezas desmoldadas en 2026
- Confirmar si hay coladas vaciadas en 2026
- Histórico completo de alfamega_06_vaciado
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
    print(f"\n{'='*60}")
    print(f"  {titulo}")
    print(f"{'='*60}")
    try:
        rows = conn.execute(text(sql)).fetchall()
        if not rows:
            print("  (sin resultados)")
            return
        keys = rows[0]._fields if hasattr(rows[0], '_fields') else rows[0].keys()
        header = " | ".join(str(k)[:25] for k in keys)
        print("  " + header)
        print("  " + "-" * len(header))
        for r in rows:
            print("  " + " | ".join(str(v)[:25] for v in r))
    except Exception as e:
        print(f"  ERROR: {e}")

with engine.connect() as conn:

    # 1. Columnas con fecha en cscmega_01rechazosbyidticket
    run(conn, "Columnas tipo fecha en cscmega_01rechazosbyidticket", """
        SHOW COLUMNS FROM cscmega_01rechazosbyidticket
        WHERE Type LIKE '%date%' OR Type LIKE '%time%'
    """)

    # 2. Columnas con fecha en cscmega_05aquimicoscumplimiento
    run(conn, "Columnas tipo fecha en cscmega_05aquimicoscumplimiento", """
        SHOW COLUMNS FROM cscmega_05aquimicoscumplimiento
        WHERE Type LIKE '%date%' OR Type LIKE '%time%'
    """)

    # 3. Detalle de las 37 piezas desmoldadas en 2026
    run(conn, "Piezas desmoldadas en 2026 — detalle por colada", """
        SELECT
            c_IdColada,
            COUNT(*) AS piezas,
            MIN(FHrMOLD) AS primer_molde,
            MIN(FHrDESM) AS primer_desmolde,
            MAX(FHrDESM) AS ultimo_desmolde
        FROM cscmega_08ruta
        WHERE YEAR(FHrDESM) = 2026
        GROUP BY c_IdColada
        ORDER BY ultimo_desmolde DESC
    """)

    # 4. Histórico de vaciados por año
    run(conn, "Vaciados por año — alfamega_06_vaciado", """
        SELECT
            YEAR(FHrVaciado) AS anio,
            COUNT(*) AS tickets,
            COUNT(DISTINCT c_IdColada) AS coladas
        FROM alfamega_06_vaciado
        WHERE FHrVaciado IS NOT NULL
        GROUP BY YEAR(FHrVaciado)
        ORDER BY anio
    """)

    # 5. Tablas existentes en ia_fasa (para encontrar la tabla maestra de coladas)
    run(conn, "Tablas de ia_fasa que contengan 'colada' en el nombre", """
        SHOW TABLES
    """)

print("\nListo.")
