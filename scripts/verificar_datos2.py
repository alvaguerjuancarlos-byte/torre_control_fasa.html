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

HOST   = os.environ["DB_HOST"]
PORT   = int(os.environ.get("DB_PORT", 3306))
USER   = os.environ["DB_USER"]
PWD    = os.environ["DB_PWD"]
SCHEMA = os.environ.get("DB_SCHEMA", "ia_fasa")

eng = create_engine(
    f"mysql+pymysql://{USER}:{PWD}@{HOST}:{PORT}/{SCHEMA}?charset=utf8mb4",
    pool_pre_ping=True
)

with eng.connect() as cx:

    # 1. Columnas reales de cscmega_01rechazosbyidticket
    print("=== COLUMNAS rechazosbyidticket ===")
    df1 = pd.read_sql(text("""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA=:schema AND TABLE_NAME='cscmega_01rechazosbyidticket'
        ORDER BY ORDINAL_POSITION
    """), cx, params={"schema": SCHEMA})
    print(df1.to_string(index=False))

    # Muestra con bRechazo=1
    print("\n--- Muestra rechazos reales ---")
    df2 = pd.read_sql(text("""
        SELECT * FROM cscmega_01rechazosbyidticket WHERE bRechazo=1 LIMIT 5
    """), cx)
    print(df2.iloc[:, :15].to_string(index=False))

    # 2. Temperatura de vaciado — ¿tiene valores reales?
    print("\n=== TEMPERATURA VACIADO (cscmega_06) ===")
    df3 = pd.read_sql(text("""
        SELECT
          COUNT(*) total,
          SUM(vt_TemperaturaVaciado IS NOT NULL AND vt_TemperaturaVaciado > 0) con_temp,
          MIN(vt_TemperaturaVaciado) temp_min,
          MAX(vt_TemperaturaVaciado) temp_max,
          ROUND(AVG(vt_TemperaturaVaciado), 1) temp_avg
        FROM cscmega_06modelosvaciado
        WHERE vt_TemperaturaVaciado > 0
    """), cx)
    print(df3.to_string(index=False))

    # 3. cscmega_04clase — ¿tiene datos ITACA?
    print("\n=== COLUMNAS cscmega_04clase ===")
    df4 = pd.read_sql(text("""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA=:schema AND TABLE_NAME='cscmega_04clase'
        ORDER BY ORDINAL_POSITION
    """), cx, params={"schema": SCHEMA})
    print(df4.to_string(index=False))

    # Muestra
    print("\n--- Muestra cscmega_04clase (primeras 20 cols) ---")
    df5 = pd.read_sql(text("SELECT * FROM cscmega_04clase LIMIT 3"), cx)
    print(df5.iloc[:, :20].to_string(index=False))

    # 4. cscmega_02pksquimicos — puede tener CE% calculado
    print("\n=== COLUMNAS cscmega_02pksquimicos ===")
    df6 = pd.read_sql(text("""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA=:schema AND TABLE_NAME='cscmega_02pksquimicos'
        ORDER BY ORDINAL_POSITION
    """), cx, params={"schema": SCHEMA})
    print(df6.to_string(index=False))

    # Muestra
    print("\n--- Muestra pksquimicos (primeras 20 cols) ---")
    df7 = pd.read_sql(text("SELECT * FROM cscmega_02pksquimicos LIMIT 3"), cx)
    print(df7.iloc[:, :20].to_string(index=False))

    # 5. cscmega_08ruta — rutas de proceso
    print("\n=== COLUMNAS cscmega_08ruta ===")
    df8 = pd.read_sql(text("""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA=:schema AND TABLE_NAME='cscmega_08ruta'
        ORDER BY ORDINAL_POSITION
    """), cx, params={"schema": SCHEMA})
    print(df8.to_string(index=False))
