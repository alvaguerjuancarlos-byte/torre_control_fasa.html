"""
Auditoría rápida: ¿hay datos de 2026 en ia_fasa?
Revisa las tablas con columnas de fecha conocidas.
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

CONSULTAS = [
    ("cscmega_08ruta — FHrMOLD", """
        SELECT
            MIN(FHrMOLD) AS primer_registro,
            MAX(FHrMOLD) AS ultimo_registro,
            COUNT(*) AS total_filas,
            SUM(YEAR(FHrMOLD) = 2026) AS filas_2026
        FROM cscmega_08ruta
        WHERE FHrMOLD IS NOT NULL
    """),
    ("cscmega_08ruta — FHrDESM", """
        SELECT
            MIN(FHrDESM) AS primer_registro,
            MAX(FHrDESM) AS ultimo_registro,
            COUNT(*) AS total_filas,
            SUM(YEAR(FHrDESM) = 2026) AS filas_2026
        FROM cscmega_08ruta
        WHERE FHrDESM IS NOT NULL
    """),
    ("alfamega_06_vaciado — FHrVaciado", """
        SELECT
            MIN(FHrVaciado) AS primer_registro,
            MAX(FHrVaciado) AS ultimo_registro,
            COUNT(*) AS total_filas,
            SUM(YEAR(FHrVaciado) = 2026) AS filas_2026
        FROM alfamega_06_vaciado
        WHERE FHrVaciado IS NOT NULL
    """),
    ("cscmega_01rechazosbyidticket — FHrRechazo", """
        SELECT
            MIN(FHrRechazo) AS primer_registro,
            MAX(FHrRechazo) AS ultimo_registro,
            COUNT(*) AS total_filas,
            SUM(YEAR(FHrRechazo) = 2026) AS filas_2026
        FROM cscmega_01rechazosbyidticket
        WHERE FHrRechazo IS NOT NULL
    """),
    ("cscmega_05aquimicoscumplimiento — FHrQuimicos", """
        SELECT
            MIN(FHrQuimicos) AS primer_registro,
            MAX(FHrQuimicos) AS ultimo_registro,
            COUNT(*) AS total_filas,
            SUM(YEAR(FHrQuimicos) = 2026) AS filas_2026
        FROM cscmega_05aquimicoscumplimiento
        WHERE FHrQuimicos IS NOT NULL
    """),
    ("alfamega_coladas — FHrColada (coladas maestras)", """
        SELECT
            MIN(FHrColada) AS primer_registro,
            MAX(FHrColada) AS ultimo_registro,
            COUNT(*) AS total_coladas,
            SUM(YEAR(FHrColada) = 2026) AS coladas_2026
        FROM alfamega_coladas
        WHERE FHrColada IS NOT NULL
    """),
]

print("=" * 70)
print("AUDITORÍA 2026 — ia_fasa")
print("=" * 70)

with engine.connect() as conn:
    for nombre, sql in CONSULTAS:
        print(f"\n[{nombre}]")
        try:
            row = conn.execute(text(sql)).fetchone()
            if row:
                keys = row._fields if hasattr(row, '_fields') else row.keys()
                for k, v in zip(keys, row):
                    print(f"  {k}: {v}")
        except Exception as e:
            print(f"  ERROR: {e}")

print("\n" + "=" * 70)
print("Listo.")
