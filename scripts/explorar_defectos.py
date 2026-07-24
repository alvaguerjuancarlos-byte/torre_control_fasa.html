#!/usr/bin/env python3
import os, sys
from pathlib import Path
import pandas as pd
from sqlalchemy import create_engine, text
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass
eng = create_engine(
    f"mysql+pymysql://{os.environ['DB_USER']}:{os.environ['DB_PWD']}"
    f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/ia_fasa?charset=utf8mb4",
    pool_pre_ping=True
)
with eng.connect() as cx:
    print("=== TODOS LOS DEFECTOS con su frecuencia ===")
    df = pd.read_sql(text("""
        SELECT nomDefecto, COUNT(*) AS n
        FROM cscmega_01resultadoidticket
        WHERE bRechazo=1 AND nomDefecto IS NOT NULL
        GROUP BY nomDefecto ORDER BY n DESC
    """), cx)
    print(df.to_string(index=False))
