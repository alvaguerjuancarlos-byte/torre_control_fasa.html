#!/usr/bin/env python3
import os
from pathlib import Path
import pandas as pd
from sqlalchemy import create_engine, text

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

HOST = os.environ["DB_HOST"]
PORT = int(os.environ.get("DB_PORT", 3306))
USER = os.environ["DB_USER"]
PWD  = os.environ["DB_PWD"]

eng = create_engine(
    f"mysql+pymysql://{USER}:{PWD}@{HOST}:{PORT}/piso_ia?charset=utf8mb4",
    pool_pre_ping=True
)

with eng.connect() as cx:

    # Tablas en piso_ia
    print("=== TABLAS en piso_ia ===")
    df1 = pd.read_sql(text("""
        SELECT TABLE_NAME, TABLE_ROWS,
               ROUND((DATA_LENGTH+INDEX_LENGTH)/1024/1024,1) AS MB
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = 'piso_ia'
        ORDER BY TABLE_ROWS DESC
    """), cx)
    print(df1.to_string(index=False))

    # Buscar Cementite/Nodular/Porosity/IGQ/arefBatch
    print("\n=== BUSCAR ITACA y arefBatch en piso_ia ===")
    df2 = pd.read_sql(text("""
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = 'piso_ia'
          AND (COLUMN_NAME LIKE '%Cement%'
            OR COLUMN_NAME LIKE '%Nodular%'
            OR COLUMN_NAME LIKE '%Porosity%'
            OR COLUMN_NAME LIKE '%IGQ%'
            OR COLUMN_NAME LIKE '%aref%'
            OR COLUMN_NAME LIKE '%Batch%'
            OR COLUMN_NAME LIKE '%itaca%'
            OR COLUMN_NAME LIKE '%Itaca%')
        ORDER BY TABLE_NAME, COLUMN_NAME
    """), cx)
    if df2.empty:
        print("  [NINGUNA]")
    else:
        print(df2.to_string(index=False))

    # Si hay tablas, muestra columnas de las principales
    if not df1.empty:
        for tabla in df1["TABLE_NAME"].head(5):
            print(f"\n--- Columnas de {tabla} ---")
            df3 = pd.read_sql(text("""
                SELECT COLUMN_NAME, DATA_TYPE
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA='piso_ia' AND TABLE_NAME=:t
                ORDER BY ORDINAL_POSITION
            """), cx, params={"t": tabla})
            print(df3.to_string(index=False))
            # muestra 2 filas
            try:
                df4 = pd.read_sql(text(f"SELECT * FROM `{tabla}` LIMIT 2"), cx)
                print(df4.iloc[:, :15].to_string(index=False))
            except Exception as e:
                print(f"  Error muestra: {e}")
