#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inventariar_ia_fasa.py
Inventario del esquema ia_fasa: tablas, columnas, llaves clave.
Lee credenciales de .env — nunca hardcodeadas.

Instalacion:  pip install pymysql sqlalchemy pandas openpyxl python-dotenv
Ejecucion:    python scripts/inventariar_ia_fasa.py
Salida:       mapeo_ia_fasa.xlsx
"""

import sys
import os
from pathlib import Path
import pandas as pd
from sqlalchemy import create_engine, text

# Cargar .env desde la raiz del proyecto
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass  # dotenv opcional — usa las vars de entorno del sistema si no está

HOST   = os.environ["DB_HOST"]
PORT   = int(os.environ.get("DB_PORT", 3306))
USER   = os.environ["DB_USER"]
PWD    = os.environ["DB_PWD"]
SCHEMA = os.environ.get("DB_SCHEMA", "ia_fasa")
OUTFILE = "mapeo_ia_fasa.xlsx"

LLAVES_CLAVE = ["%Colada%", "%Batch%", "%Ticket%", "%aref%"]


def make_engine():
    url = f"mysql+pymysql://{USER}:{PWD}@{HOST}:{PORT}/{SCHEMA}?charset=utf8mb4"
    return create_engine(url, pool_pre_ping=True)


def main():
    print(f"Conectando a {HOST}:{PORT} / {SCHEMA} ...")
    try:
        eng = make_engine()
        cx = eng.connect()
    except Exception as e:
        print("ERROR al conectar:", e)
        sys.exit(1)

    with cx:
        # 1. Tablas y tamaño
        df_tablas = pd.read_sql(text("""
            SELECT TABLE_NAME,
                   TABLE_ROWS,
                   ROUND((DATA_LENGTH+INDEX_LENGTH)/1024/1024,1) AS MB
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = :schema
            ORDER BY TABLE_ROWS DESC
        """), cx, params={"schema": SCHEMA})
        print(f"\n--- Tablas en {SCHEMA} ({len(df_tablas)}) ---")
        print(df_tablas.to_string(index=False))

        # 2. Columnas
        df_cols = pd.read_sql(text("""
            SELECT TABLE_NAME, ORDINAL_POSITION, COLUMN_NAME,
                   DATA_TYPE, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = :schema
            ORDER BY TABLE_NAME, ORDINAL_POSITION
        """), cx, params={"schema": SCHEMA})

        # 3. Buscar llaves clave del join
        like_clauses = " OR ".join(
            f"COLUMN_NAME LIKE :kw{i}" for i in range(len(LLAVES_CLAVE))
        )
        params = {"schema": SCHEMA}
        for i, kw in enumerate(LLAVES_CLAVE):
            params[f"kw{i}"] = kw

        df_llaves = pd.read_sql(text(f"""
            SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = :schema AND ({like_clauses})
            ORDER BY TABLE_NAME, COLUMN_NAME
        """), cx, params=params)

        print(f"\n--- Llaves clave (Colada/Batch/Ticket/aref) ---")
        if df_llaves.empty:
            print("  [NINGUNA ENCONTRADA] — ia_fasa no trae el join hecho.")
        else:
            print(df_llaves.to_string(index=False))

    # Exportar
    with pd.ExcelWriter(OUTFILE, engine="openpyxl") as xw:
        df_tablas.to_excel(xw, sheet_name="tablas", index=False)
        df_cols.to_excel(xw, sheet_name="columnas", index=False)
        df_llaves.to_excel(xw, sheet_name="llaves_clave", index=False)

    print(f"\nListo. Exportado a: {OUTFILE}")
    print(f"  Tablas: {len(df_tablas)} | Columnas: {len(df_cols)} | Llaves clave: {len(df_llaves)}")


if __name__ == "__main__":
    main()
