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

DIA = "2025-12-18"

with eng.connect() as cx:

    # 1. Cobertura de etapas en cscmega_08ruta para ese dia
    print("=== COBERTURA DE ETAPAS (dic 18) ===")
    df1 = pd.read_sql(text("""
        SELECT
            COUNT(*)            AS total_piezas,
            SUM(bCELD)          AS con_celd,
            SUM(bMOLD)          AS con_mold,
            SUM(bVACI)          AS con_vaci,
            SUM(bDESM)          AS con_desm,
            SUM(bLIMP)          AS con_limp,
            SUM(bCELD AND bMOLD AND bVACI AND bDESM AND bLIMP) AS terminadas
        FROM cscmega_08ruta r
        JOIN cscmega_01rechazosbyidticket t ON t.idTicket = r.u_idTicket
        WHERE DATE(t.u_Fecha) = :dia
    """), cx, params={"dia": DIA})
    print(df1.to_string(index=False))

    # 2. Distribucion de piezas por etapa actual
    print("\n=== ETAPA ACTUAL de cada pieza (dic 18) ===")
    df2 = pd.read_sql(text("""
        SELECT
            CASE
              WHEN r.bLIMP = 1  THEN 'Terminada'
              WHEN r.bDESM = 1  THEN 'En limpieza'
              WHEN r.bVACI = 1  THEN 'En desmoldeo'
              WHEN r.bMOLD = 1  THEN 'En vaciado'
              WHEN r.bCELD = 1  THEN 'En moldeo'
              ELSE 'Sin inicio'
            END AS etapa_actual,
            COUNT(*) AS piezas
        FROM cscmega_08ruta r
        JOIN cscmega_01rechazosbyidticket t ON t.idTicket = r.u_idTicket
        WHERE DATE(t.u_Fecha) = :dia
        GROUP BY etapa_actual
        ORDER BY piezas DESC
    """), cx, params={"dia": DIA})
    print(df2.to_string(index=False))

    # 3. Por colada: piezas en cada etapa + tiempos de etapas
    print("\n=== FLUJO POR COLADA (dic 18, primeras 6) ===")
    df3 = pd.read_sql(text("""
        SELECT
            t.u_Colada, t.u_Horno,
            COUNT(DISTINCT t.idTicket)  AS piezas,
            SUM(t.bRechazo)             AS rechazos,
            SUM(r.bMOLD)                AS n_mold,
            SUM(r.bVACI)                AS n_vaci,
            SUM(r.bDESM)                AS n_desm,
            SUM(r.bLIMP)                AS n_limp,
            MIN(r.FhrVACI)              AS vaci_ini,
            MAX(r.FhrVACI)              AS vaci_fin,
            MIN(r.FHrLIMP)              AS limp_ini,
            MAX(r.FHrLIMP)              AS limp_fin
        FROM cscmega_01rechazosbyidticket t
        JOIN cscmega_08ruta r ON r.u_idTicket = t.idTicket
        WHERE DATE(t.u_Fecha) = :dia
        GROUP BY t.u_Colada, t.u_Horno
        ORDER BY vaci_ini
        LIMIT 6
    """), cx, params={"dia": DIA})
    print(df3.to_string(index=False))

    # 4. Cuánto tiempo transcurre entre vaciado y limpieza (ciclo de pieza)
    print("\n=== CICLO DE PIEZA: tiempo entre vaciado y limpieza ===")
    df4 = pd.read_sql(text("""
        SELECT
            ROUND(AVG(TIMESTAMPDIFF(HOUR, r.FhrVACI, r.FHrLIMP)), 1) AS hrs_vaci_a_limp_avg,
            MIN(TIMESTAMPDIFF(HOUR, r.FhrVACI, r.FHrLIMP))           AS hrs_min,
            MAX(TIMESTAMPDIFF(HOUR, r.FhrVACI, r.FHrLIMP))           AS hrs_max
        FROM cscmega_08ruta r
        JOIN cscmega_01rechazosbyidticket t ON t.idTicket = r.u_idTicket
        WHERE DATE(t.u_Fecha) = :dia
          AND r.FhrVACI IS NOT NULL AND r.FHrLIMP IS NOT NULL
    """), cx, params={"dia": DIA})
    print(df4.to_string(index=False))
