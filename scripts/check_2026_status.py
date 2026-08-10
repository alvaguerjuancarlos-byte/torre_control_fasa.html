"""
Chequeo de estado de datos 2026 (2026-08-06) — solo lectura.
Revisa MAX(fecha) real en las fuentes documentadas como "ETL detenido"
en memoria (ia_fasa frozen 2025-12-29, corex_test/piso_ia frozen medio marzo 2026).
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).parent.parent / ".env")

def engine_for(schema):
    return create_engine(
        f"mysql+pymysql://{os.environ['DB_USER']}:{os.environ['DB_PWD']}"
        f"@{os.environ['DB_HOST']}:{os.environ.get('DB_PORT', 3306)}/{schema}"
        "?charset=utf8mb4",
        pool_pre_ping=True,
    )

CHECKS = [
    ("ia_fasa", "alfamega_01_rechazosbyidticket", "FechaRechazo"),
    ("ia_fasa", "cscmega_03coladacargametalica", "c_FechaInicial"),
    ("ia_fasa", "cscmega_08ruta", "FHrLIMP"),
    ("corex_test", "analisisquimico", "Fecha"),
    ("piso_ia", "fdbase", "Fecha"),
    ("corex_test", "programacorazones", "FechaDocumento"),
    ("corex_test", "programamoldeo", "FechaDocumento"),
    ("piso_ia", "programas", "Fecha"),
]

print(f"{'esquema':<12} {'tabla':<32} {'MAX(fecha)':<22} filas_2026")
print("-" * 90)

for schema, tabla, col in CHECKS:
    eng = engine_for(schema)
    try:
        with eng.connect() as conn:
            r = conn.execute(text(f"SELECT MAX({col}) AS mx FROM {tabla}")).fetchone()
            mx = r[0]
            r2 = conn.execute(text(
                f"SELECT COUNT(*) FROM {tabla} WHERE {col} >= '2026-01-01'"
            )).fetchone()
            n2026 = r2[0]
            print(f"{schema:<12} {tabla:<32} {str(mx):<22} {n2026}")
    except Exception as e:
        print(f"{schema:<12} {tabla:<32} ERROR: {e}")
