"""
Auditoría 2026 — parte 4:
- Corridas de 2026 en cscmega_00_corridas
- Nombres completos de tablas alfamega_03 y alfamega_02_pks
- 37 desmoldes 2026: agrupar por u_NoParte
- Rango de FHrDESM 2026 más detallado
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

    # 1. Corridas por año (tabla con AnioCorrida)
    run(conn, "Corridas por año — cscmega_00_corridas", """
        SELECT
            AnioCorrida,
            MesCorrida,
            COUNT(*) AS corridas,
            MIN(FechaHrIni) AS inicio,
            MAX(FechaHrFin) AS fin
        FROM cscmega_00_corridas
        WHERE AnioCorrida >= 2025
        GROUP BY AnioCorrida, MesCorrida
        ORDER BY AnioCorrida, MesCorrida
    """)

    # 2. Nombres completos de tablas alfamega_03*
    run(conn, "Tablas alfamega_03* (nombres completos)", """
        SHOW TABLES LIKE 'alfamega_03%'
    """)

    # 3. Nombres completos alfamega_02_pks*
    run(conn, "Tablas alfamega_02_pks* (nombres completos)", """
        SHOW TABLES LIKE 'alfamega_02_pks%'
    """)

    # 4. Las 37 piezas desmoldadas en 2026 — por parte
    run(conn, "Desmoldes 2026 — por número de parte", """
        SELECT
            u_NoParte,
            COUNT(*) AS piezas,
            MIN(FHrMOLD) AS primer_molde,
            MIN(FHrDESM) AS primer_desmolde,
            MAX(FHrDESM) AS ultimo_desmolde
        FROM cscmega_08ruta
        WHERE YEAR(FHrDESM) = 2026
        GROUP BY u_NoParte
        ORDER BY ultimo_desmolde DESC
    """)

    # 5. FHrDESM 2026 — rango completo de meses
    run(conn, "Desmoldes 2026 — distribución por mes", """
        SELECT
            YEAR(FHrDESM) AS anio,
            MONTH(FHrDESM) AS mes,
            COUNT(*) AS piezas
        FROM cscmega_08ruta
        WHERE YEAR(FHrDESM) = 2026
        GROUP BY YEAR(FHrDESM), MONTH(FHrDESM)
        ORDER BY anio, mes
    """)

    # 6. alfamega_06_vaciado — ¿hay vaciados de 2026?
    run(conn, "Vaciados más recientes (últimos 10 registros)", """
        SELECT c_IdColada, FHrVaciado, vt_TemperaturaVaciado
        FROM alfamega_06_vaciado
        WHERE FHrVaciado IS NOT NULL
        ORDER BY FHrVaciado DESC
        LIMIT 10
    """)

print("\nListo.")
