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

eng = create_engine(
    f"mysql+pymysql://{os.environ['DB_USER']}:{os.environ['DB_PWD']}"
    f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/ia_fasa?charset=utf8mb4",
    pool_pre_ping=True
)

with eng.connect() as cx:

    # 1. Rango total y registros por año/mes
    print("=== RANGO Y VOLUMEN POR MES ===")
    df = pd.read_sql(text("""
        SELECT
            DATE_FORMAT(u_Fecha,'%Y-%m') AS mes,
            COUNT(*)                     AS tickets,
            SUM(bRechazo)                AS rechazos,
            COUNT(DISTINCT u_Colada)     AS coladas,
            COUNT(DISTINCT DATE(u_Fecha)) AS dias_con_datos
        FROM cscmega_01rechazosbyidticket
        GROUP BY mes
        ORDER BY mes
    """), cx)
    print(df.to_string(index=False))

    # 2. Distribución por hora del día (¿hay datos nocturnos? ¿turnos?)
    print("\n=== DISTRIBUCION POR HORA DEL DIA ===")
    df2 = pd.read_sql(text("""
        SELECT
            HOUR(u_Fecha)    AS hora,
            COUNT(*)         AS tickets,
            COUNT(DISTINCT DATE(u_Fecha)) AS dias
        FROM cscmega_01rechazosbyidticket
        GROUP BY hora
        ORDER BY hora
    """), cx)
    print(df2.to_string(index=False))

    # 3. ¿Cuántos registros hay en un día típico?
    print("\n=== TICKETS POR DIA (muestra 10 dias recientes) ===")
    df3 = pd.read_sql(text("""
        SELECT
            DATE(u_Fecha)            AS dia,
            COUNT(*)                 AS tickets,
            SUM(bRechazo)            AS rechazos,
            COUNT(DISTINCT u_Colada) AS coladas,
            COUNT(DISTINCT u_Horno)  AS hornos,
            MIN(TIME(u_Fecha))       AS primer_registro,
            MAX(TIME(u_Fecha))       AS ultimo_registro
        FROM cscmega_01rechazosbyidticket
        GROUP BY dia
        ORDER BY dia DESC
        LIMIT 10
    """), cx)
    print(df3.to_string(index=False))

    # 4. Intervalo mínimo entre registros consecutivos (muestra un día)
    print("\n=== INTERVALO ENTRE REGISTROS (un dia de muestra: 2025-12-29) ===")
    df4 = pd.read_sql(text("""
        SELECT
            u_Fecha,
            u_Colada,
            u_Horno,
            bRechazo,
            TIMESTAMPDIFF(SECOND,
                LAG(u_Fecha) OVER (ORDER BY u_Fecha),
                u_Fecha
            ) AS seg_desde_anterior
        FROM cscmega_01rechazosbyidticket
        WHERE DATE(u_Fecha) = '2025-12-29'
        ORDER BY u_Fecha
        LIMIT 20
    """), cx)
    print(df4.to_string(index=False))

    # 5. ¿Hay definición de turno en los datos?
    print("\n=== BUSCAR COLUMNA DE TURNO EN TODAS LAS TABLAS ===")
    df5 = pd.read_sql(text("""
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = 'ia_fasa'
          AND (COLUMN_NAME LIKE '%turno%'
            OR COLUMN_NAME LIKE '%Turno%'
            OR COLUMN_NAME LIKE '%shift%')
        ORDER BY TABLE_NAME
    """), cx)
    if df5.empty:
        print("  No hay columna de turno explícita en ia_fasa")
    else:
        print(df5.to_string(index=False))
