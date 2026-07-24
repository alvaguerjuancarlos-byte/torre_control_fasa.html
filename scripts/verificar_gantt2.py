#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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

MUESTRA = "2025-12-18"

with eng.connect() as cx:

    # Tracking colada -> piezas por SKU
    print("=== TRACKING colada -> piezas por SKU (colada 335) ===")
    df3 = pd.read_sql(text("""
        SELECT
            r.u_Colada,
            t.No_Parte              AS no_parte,
            COUNT(*)                AS piezas_total,
            SUM(t.bRechazo)         AS rechazos,
            ROUND(AVG(t.bRechazo)*100,1) AS pct_rechazo
        FROM cscmega_01resultadoidticket t
        JOIN cscmega_01rechazosbyidticket r ON r.idTicket = t.idTicket
        WHERE r.u_Colada = 335
        GROUP BY r.u_Colada, t.No_Parte
        ORDER BY piezas_total DESC
        LIMIT 15
    """), cx)
    print(df3.to_string(index=False))

    # Flujo de etapas por colada usando cscmega_08ruta
    print("\n=== FLUJO DE ETAPAS colada 335 (via cscmega_08ruta) ===")
    df4 = pd.read_sql(text("""
        SELECT
            COUNT(*)                AS total_tickets,
            SUM(bMOLD)              AS paso_moldeo,
            SUM(bVACI)              AS paso_vaciado,
            SUM(bLIMP)              AS paso_limpieza,
            SUM(bDESM)              AS paso_desmoldeo,
            SUM(bCELD)              AS paso_celdas,
            MIN(FHrMOLD)            AS primer_moldeo,
            MAX(FHrMOLD)            AS ultimo_moldeo,
            MIN(FhrVACI)            AS primer_vaciado,
            MAX(FhrVACI)            AS ultimo_vaciado
        FROM cscmega_08ruta r
        JOIN cscmega_01rechazosbyidticket t ON t.idTicket = r.u_idTicket
        WHERE t.u_Colada = 335
    """), cx)
    print(df4.to_string(index=False))

    # Calidad del JOIN rechazos <-> ruta
    print("\n=== CALIDAD DEL JOIN: rechazos <-> ruta (dic 18) ===")
    df5 = pd.read_sql(text("""
        SELECT
            COUNT(DISTINCT r.idTicket)       AS tickets_rechazo,
            COUNT(DISTINCT rt.u_idTicket)    AS tickets_con_ruta,
            COUNT(DISTINCT r.u_Colada)       AS coladas
        FROM cscmega_01rechazosbyidticket r
        LEFT JOIN cscmega_08ruta rt ON rt.u_idTicket = r.idTicket
        WHERE DATE(r.u_Fecha) = :dia
    """), cx, params={"dia": MUESTRA})
    print(df5.to_string(index=False))

    # Quimica disponible por colada
    print("\n=== QUIMICA disponible por colada (dic 18) ===")
    df6 = pd.read_sql(text("""
        SELECT
            c.u_Colada, c.u_Horno, c.c_Status,
            c.c_FechaInicial, c.c_FechaFinal,
            q.aB_C, q.aB_Si, q.aB_Mg,
            ROUND(q.aB_C + q.aB_Si/3.0 + q.aB_P/3.0, 3) AS ce_pct
        FROM cscmega_03coladacargametalica c
        LEFT JOIN cscmega_05cquimicosbase q
            ON q.u_Colada = c.u_Colada AND q.aB_C > 0
        WHERE DATE(c.u_Fecha) = :dia AND c.c_IdColada > 0
        GROUP BY c.u_Colada, c.u_Horno, c.c_Status,
                 c.c_FechaInicial, c.c_FechaFinal,
                 q.aB_C, q.aB_Si, q.aB_Mg, q.aB_P
        ORDER BY c.c_FechaInicial
        LIMIT 12
    """), cx, params={"dia": MUESTRA})
    print(df6.to_string(index=False))

    # Usando vaciado timestamps de ruta para reconstruir el rango por colada
    print("\n=== RANGO DE VACIADO por colada (reconstituido de ruta) ===")
    df7 = pd.read_sql(text("""
        SELECT
            t.u_Colada,
            t.u_Horno,
            COUNT(DISTINCT r.u_idTicket)   AS tickets_con_ruta,
            MIN(r.FhrVACI)                 AS inicio_vaciado,
            MAX(r.FhrVACI)                 AS fin_vaciado,
            MIN(r.FHrMOLD)                 AS inicio_moldeo,
            MAX(r.FHrLIMP)                 AS fin_limpieza
        FROM cscmega_01rechazosbyidticket t
        JOIN cscmega_08ruta r ON r.u_idTicket = t.idTicket
        WHERE DATE(t.u_Fecha) = :dia
        GROUP BY t.u_Colada, t.u_Horno
        ORDER BY inicio_vaciado
        LIMIT 12
    """), cx, params={"dia": MUESTRA})
    print(df7.to_string(index=False))

    # Resumen de brechas
    print("\n=== RESUMEN DE BRECHAS para el Gantt ===")
    df8 = pd.read_sql(text("""
        SELECT
            'c_FechaInicial (inicio fusion)'    AS campo,
            ROUND(SUM(c_FechaInicial IS NOT NULL)/COUNT(*)*100,1) AS pct_lleno
        FROM cscmega_03coladacargametalica WHERE c_IdColada > 0
        UNION ALL
        SELECT 'c_FechaFinal (fin fusion)',
            ROUND(SUM(c_FechaFinal IS NOT NULL)/COUNT(*)*100,1)
        FROM cscmega_03coladacargametalica WHERE c_IdColada > 0
        UNION ALL
        SELECT 'c_FechaInicioVaciado1 (inicio vaciado directo)',
            ROUND(SUM(c_FechaInicioVaciado1 IS NOT NULL)/COUNT(*)*100,1)
        FROM cscmega_03coladacargametalica WHERE c_IdColada > 0
        UNION ALL
        SELECT 'FhrVACI en ruta (vaciado reconstituido)',
            ROUND(COUNT(DISTINCT t.u_Colada) /
                  (SELECT COUNT(DISTINCT u_Colada) FROM cscmega_01rechazosbyidticket
                   WHERE u_Fecha >= '2025-01-01') * 100, 1)
        FROM cscmega_08ruta r
        JOIN cscmega_01rechazosbyidticket t ON t.idTicket = r.u_idTicket
        WHERE r.FhrVACI IS NOT NULL
    """), cx)
    print(df8.to_string(index=False))
