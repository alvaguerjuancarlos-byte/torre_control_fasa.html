#!/usr/bin/env python3
"""
explorar_corex_test.py
Inventario de corex_test enfocado en:
  1. Tablas con resultados ITACA (Cementite, Nodularization, Porosity, IGQ)
  2. Columna arefBatch (llave del join ITACA)
  3. Tablas de mayor tamaño
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

HOST   = os.environ["DB_HOST"]
PORT   = int(os.environ.get("DB_PORT", 3306))
USER   = os.environ["DB_USER"]
PWD    = os.environ["DB_PWD"]
SCHEMA = "corex_test"   # foco de esta sesión

eng = create_engine(
    f"mysql+pymysql://{USER}:{PWD}@{HOST}:{PORT}/{SCHEMA}?charset=utf8mb4",
    pool_pre_ping=True
)

with eng.connect() as cx:

    # 1. Inventario de tablas
    print("=== TABLAS en corex_test (top 30 por filas) ===")
    df_t = pd.read_sql(text("""
        SELECT TABLE_NAME, TABLE_ROWS,
               ROUND((DATA_LENGTH+INDEX_LENGTH)/1024/1024,1) AS MB
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = 'corex_test'
        ORDER BY TABLE_ROWS DESC
        LIMIT 30
    """), cx)
    print(df_t.to_string(index=False))
    print(f"\nTotal tablas: ", end="")
    tot = cx.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='corex_test'"
    )).scalar()
    print(tot)

    # 2. Buscar arefBatch y llaves ITACA
    print("\n=== COLUMNAS: arefBatch / Batch / itaca / ITACA ===")
    df_b = pd.read_sql(text("""
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = 'corex_test'
          AND (COLUMN_NAME LIKE '%aref%'
            OR COLUMN_NAME LIKE '%Batch%'
            OR COLUMN_NAME LIKE '%batch%'
            OR COLUMN_NAME LIKE '%itaca%'
            OR COLUMN_NAME LIKE '%Itaca%'
            OR COLUMN_NAME LIKE '%ITACA%')
        ORDER BY TABLE_NAME, COLUMN_NAME
    """), cx)
    if df_b.empty:
        print("  [NINGUNA]")
    else:
        print(df_b.to_string(index=False))

    # 3. Buscar resultados ITACA (Cementite, Nodularization, Porosity, IGQ)
    print("\n=== COLUMNAS: Cementite / Nodular / Porosity / IGQ ===")
    df_i = pd.read_sql(text("""
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = 'corex_test'
          AND (COLUMN_NAME LIKE '%Cement%'
            OR COLUMN_NAME LIKE '%cement%'
            OR COLUMN_NAME LIKE '%Nodular%'
            OR COLUMN_NAME LIKE '%nodular%'
            OR COLUMN_NAME LIKE '%Porosity%'
            OR COLUMN_NAME LIKE '%porosity%'
            OR COLUMN_NAME LIKE '%IGQ%'
            OR COLUMN_NAME LIKE '%igq%')
        ORDER BY TABLE_NAME, COLUMN_NAME
    """), cx)
    if df_i.empty:
        print("  [SIN FUENTE directa]")
    else:
        print(df_i.to_string(index=False))

    # 4. Buscar tablas con nombre ITACA
    print("\n=== TABLAS con nombre ITACA / itaca ===")
    df_ti = pd.read_sql(text("""
        SELECT TABLE_NAME, TABLE_ROWS,
               ROUND((DATA_LENGTH+INDEX_LENGTH)/1024/1024,1) AS MB
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = 'corex_test'
          AND (TABLE_NAME LIKE '%itaca%'
            OR TABLE_NAME LIKE '%Itaca%'
            OR TABLE_NAME LIKE '%ITACA%')
        ORDER BY TABLE_ROWS DESC
    """), cx)
    if df_ti.empty:
        print("  [NINGUNA tabla con nombre ITACA]")
    else:
        print(df_ti.to_string(index=False))

    # 5. Tablas con IdColada (para confirmar el join)
    print("\n=== TABLAS con IdColada en corex_test ===")
    df_col = pd.read_sql(text("""
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = 'corex_test'
          AND COLUMN_NAME LIKE '%IdColada%'
        ORDER BY TABLE_NAME
    """), cx)
    if df_col.empty:
        print("  [NINGUNA]")
    else:
        print(df_col.to_string(index=False))
