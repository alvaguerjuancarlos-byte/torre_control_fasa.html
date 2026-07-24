#!/usr/bin/env python3
"""
Exploración focalizada en corex_test:
- Vista vistaanalisisquimicoparaitaca
- Tabla analisisquimico
- Tabla coladas (estructura y join a IdColada)
- Buscar resultados ITACA en todos los esquemas accesibles
"""
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
    f"mysql+pymysql://{USER}:{PWD}@{HOST}:{PORT}/corex_test?charset=utf8mb4",
    pool_pre_ping=True
)

with eng.connect() as cx:

    # 1. Vista para ITACA — columnas
    print("=== VISTA vistaanalisisquimicoparaitaca — columnas ===")
    df1 = pd.read_sql(text("""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = 'corex_test'
          AND TABLE_NAME = 'vistaanalisisquimicoparaitaca'
        ORDER BY ORDINAL_POSITION
    """), cx)
    print(df1.to_string(index=False))

    # Muestra de la vista
    print("\n--- Muestra vista (3 filas, primeras 20 cols) ---")
    try:
        df1s = pd.read_sql(text(
            "SELECT * FROM vistaanalisisquimicoparaitaca LIMIT 3"
        ), cx)
        print(df1s.iloc[:, :20].to_string(index=False))
    except Exception as e:
        print(f"  Error: {e}")

    # 2. Tabla analisisquimico — columnas
    print("\n=== TABLA analisisquimico — columnas ===")
    df2 = pd.read_sql(text("""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = 'corex_test'
          AND TABLE_NAME = 'analisisquimico'
        ORDER BY ORDINAL_POSITION
    """), cx)
    print(df2.to_string(index=False))

    # Muestra
    print("\n--- Muestra analisisquimico (3 filas, 20 cols) ---")
    df2s = pd.read_sql(text("SELECT * FROM analisisquimico LIMIT 3"), cx)
    print(df2s.iloc[:, :20].to_string(index=False))

    # 3. Tabla coladas — columnas
    print("\n=== TABLA coladas — columnas ===")
    df3 = pd.read_sql(text("""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = 'corex_test'
          AND TABLE_NAME = 'coladas'
        ORDER BY ORDINAL_POSITION
    """), cx)
    print(df3.to_string(index=False))

    # Muestra
    print("\n--- Muestra coladas (3 filas, 20 cols) ---")
    df3s = pd.read_sql(text("SELECT * FROM coladas LIMIT 3"), cx)
    print(df3s.iloc[:, :20].to_string(index=False))

    # 4. Buscar Cementite/Nodular/Porosity en TODOS los esquemas accesibles
    print("\n=== BUSCAR Cementite/Nodular/Porosity/IGQ en TODOS los esquemas ===")
    df4 = pd.read_sql(text("""
        SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE
        FROM information_schema.COLUMNS
        WHERE COLUMN_NAME LIKE '%Cement%'
           OR COLUMN_NAME LIKE '%cement%'
           OR COLUMN_NAME LIKE '%Nodular%'
           OR COLUMN_NAME LIKE '%nodular%'
           OR COLUMN_NAME LIKE '%Porosity%'
           OR COLUMN_NAME LIKE '%porosity%'
           OR COLUMN_NAME LIKE '%IGQ%'
           OR COLUMN_NAME LIKE '%igq%'
        ORDER BY TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME
    """), cx)
    if df4.empty:
        print("  [SIN FUENTE en ningún esquema accesible]")
    else:
        print(df4.to_string(index=False))

    # 5. Buscar arefBatch en TODOS los esquemas
    print("\n=== BUSCAR arefBatch en TODOS los esquemas ===")
    df5 = pd.read_sql(text("""
        SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE
        FROM information_schema.COLUMNS
        WHERE COLUMN_NAME LIKE '%aref%'
           OR COLUMN_NAME LIKE '%Aref%'
        ORDER BY TABLE_SCHEMA, TABLE_NAME
    """), cx)
    if df5.empty:
        print("  [NO encontrado en ningún esquema]")
    else:
        print(df5.to_string(index=False))

    # 6. ¿Existe algún esquema ITACA independiente?
    print("\n=== TODOS los esquemas visibles para el usuario ===")
    df6 = pd.read_sql(text("""
        SELECT SCHEMA_NAME
        FROM information_schema.SCHEMATA
        ORDER BY SCHEMA_NAME
    """), cx)
    print(df6.to_string(index=False))
