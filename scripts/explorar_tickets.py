#!/usr/bin/env python3
import os, sys
from pathlib import Path
import pandas as pd
from sqlalchemy import create_engine, text

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

HOST   = os.environ["DB_HOST"]
PORT   = int(os.environ.get("DB_PORT", 3306))
USER   = os.environ["DB_USER"]
PWD    = os.environ["DB_PWD"]
SCHEMA = os.environ.get("DB_SCHEMA", "ia_fasa")

eng = create_engine(
    f"mysql+pymysql://{USER}:{PWD}@{HOST}:{PORT}/{SCHEMA}?charset=utf8mb4",
    pool_pre_ping=True
)

TABLAS = [
    "cscmega_00_tickets",
    "cscmega_03coladacargametalica",
    "cscmega_01resultadoidticket",
    "cscmega_05cquimicosbase",
    "cscmega_05bquimicosfinal",
]

with eng.connect() as cx:
    for tabla in TABLAS:
        print(f"\n{'='*60}")
        print(f"TABLA: {tabla}")
        print('='*60)

        # Columnas
        df_cols = pd.read_sql(text("""
            SELECT ORDINAL_POSITION pos, COLUMN_NAME col, DATA_TYPE tipo
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :tabla
            ORDER BY ORDINAL_POSITION
        """), cx, params={"schema": SCHEMA, "tabla": tabla})
        print(df_cols.to_string(index=False))

        # Muestra de 3 filas
        print(f"\n--- Muestra 3 filas ---")
        try:
            df_sample = pd.read_sql(
                text(f"SELECT * FROM `{tabla}` LIMIT 3"),
                cx
            )
            # Mostrar solo primeras 20 columnas si son muchas
            cols_show = df_sample.columns[:20]
            print(df_sample[cols_show].to_string(index=False))
            if len(df_sample.columns) > 20:
                print(f"  ... ({len(df_sample.columns) - 20} columnas más)")
        except Exception as e:
            print(f"  Error al muestrear: {e}")
