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

    # 1. ¿La química base tiene valores reales o NULLs?
    print("=== QUIMICA BASE: llenado de C, Si, Mn, S, P, Mg ===")
    df = pd.read_sql(text("""
        SELECT
          COUNT(*) total,
          SUM(aB_C  IS NOT NULL AND aB_C  > 0) C_ok,
          SUM(aB_Si IS NOT NULL AND aB_Si > 0) Si_ok,
          SUM(aB_Mn IS NOT NULL AND aB_Mn > 0) Mn_ok,
          SUM(aB_S  IS NOT NULL AND aB_S  > 0) S_ok,
          SUM(aB_P  IS NOT NULL AND aB_P  > 0) P_ok,
          SUM(aB_Mg IS NOT NULL AND aB_Mg > 0) Mg_ok
        FROM cscmega_05cquimicosbase
    """), cx)
    print(df.to_string(index=False))

    # Muestra con valores
    print("\n--- Muestra quimica (con valores) ---")
    df2 = pd.read_sql(text("""
        SELECT idTicket, u_Colada, u_Horno, u_Fecha,
               aB_C, aB_Si, aB_Mn, aB_S, aB_P, aB_Mg, aB_Origen
        FROM cscmega_05cquimicosbase
        WHERE aB_C > 0
        LIMIT 5
    """), cx)
    print(df2.to_string(index=False))

    # 2. ¿Dónde están los resultados ITACA? (Cementite, Nodularization, Porosity, IGQ)
    print("\n=== BUSCAR ITACA: columnas con Cementite/Nodular/Porosity/IGQ ===")
    df3 = pd.read_sql(text("""
        SELECT TABLE_NAME, COLUMN_NAME
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = :schema
          AND (COLUMN_NAME LIKE '%Cement%'
            OR COLUMN_NAME LIKE '%Nodular%'
            OR COLUMN_NAME LIKE '%Porosity%'
            OR COLUMN_NAME LIKE '%IGQ%'
            OR COLUMN_NAME LIKE '%itaca%'
            OR COLUMN_NAME LIKE '%Itaca%'
            OR COLUMN_NAME LIKE '%ITACA%')
        ORDER BY TABLE_NAME, COLUMN_NAME
    """), cx, params={"schema": SCHEMA})
    if df3.empty:
        print("  [SIN FUENTE] — resultados ITACA no encontrados en ia_fasa")
    else:
        print(df3.to_string(index=False))

    # 3. Temperatura de vaciado en cscmega_03
    print("\n=== TEMPERATURA DE VACIADO en cscmega_03 ===")
    df4 = pd.read_sql(text("""
        SELECT TABLE_NAME, COLUMN_NAME
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = :schema
          AND TABLE_NAME = 'cscmega_03coladacargametalica'
          AND (COLUMN_NAME LIKE '%Temp%'
            OR COLUMN_NAME LIKE '%Grad%'
            OR COLUMN_NAME LIKE '%Celsius%'
            OR COLUMN_NAME LIKE '%Piro%'
            OR COLUMN_NAME LIKE '%temp%')
        ORDER BY COLUMN_NAME
    """), cx, params={"schema": SCHEMA})
    if df4.empty:
        print("  [SIN FUENTE] — columnas de temperatura no encontradas en cscmega_03")
    else:
        print(df4.to_string(index=False))

    # 4. ¿Qué tiene cscmega_06modelosvaciado? (puede tener temperatura)
    print("\n=== COLUMNAS cscmega_06modelosvaciado ===")
    df5 = pd.read_sql(text("""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = 'cscmega_06modelosvaciado'
        ORDER BY ORDINAL_POSITION
    """), cx, params={"schema": SCHEMA})
    print(df5.to_string(index=False))

    # Muestra
    df6 = pd.read_sql(text("SELECT * FROM cscmega_06modelosvaciado LIMIT 3"), cx)
    print("\n--- Muestra (primeras 15 cols) ---")
    print(df6.iloc[:, :15].to_string(index=False))

    # 5. Datos de rechazo y defectos
    print("\n=== RECHAZOS: muestra con defecto real ===")
    df7 = pd.read_sql(text("""
        SELECT idTicket, u_Colada, u_Horno, bRechazo, cveDefecto, nomDefecto
        FROM cscmega_01rechazosbyidticket
        WHERE bRechazo = 1 AND nomDefecto IS NOT NULL
        LIMIT 10
    """), cx)
    print(df7.to_string(index=False))
