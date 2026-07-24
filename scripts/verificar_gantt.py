#!/usr/bin/env python3
"""
Verifica qué datos tenemos para el Gantt de coladas (48h)
y el tracking colada → piezas por SKU.
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

eng = create_engine(
    f"mysql+pymysql://{os.environ['DB_USER']}:{os.environ['DB_PWD']}"
    f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/ia_fasa?charset=utf8mb4",
    pool_pre_ping=True
)

MUESTRA = "2025-12-18"

with eng.connect() as cx:

    # 1. Coladas del día con fechas de ciclo de vida
    print("=== COLADAS: llenado de fechas de ciclo ===")
    df1 = pd.read_sql(text("""
        SELECT
            c_IdColada, u_Colada, u_Horno, c_Status,
            c_FechaInicial,
            c_FechaFinal,
            c_FechaInicioVaciado1,
            c_FechaFinalVaciado,
            c_Kilos,
            c_TiempoFundicion
        FROM cscmega_03coladacargametalica
        WHERE DATE(u_Fecha) = :dia
        ORDER BY c_FechaInicial
        LIMIT 10
    """), cx, params={"dia": MUESTRA})
    print(df1.to_string(index=False))

    # Llenado general
    print("\n--- % de fechas pobladas en cscmega_03 ---")
    df2 = pd.read_sql(text("""
        SELECT
            COUNT(*)                                        AS total,
            SUM(c_FechaInicial IS NOT NULL)                 AS con_inicio,
            SUM(c_FechaFinal IS NOT NULL)                   AS con_fin,
            SUM(c_FechaInicioVaciado1 IS NOT NULL)          AS con_ini_vaciado,
            SUM(c_FechaFinalVaciado IS NOT NULL)            AS con_fin_vaciado
        FROM cscmega_03coladacargametalica
        WHERE DATE(u_Fecha) BETWEEN '2025-11-01' AND '2025-12-31'
    """), cx)
    print(df2.to_string(index=False))

    # 2. Tracking colada -> piezas (via u_Colada)
    print("\n=== TRACKING colada → piezas por SKU (colada 335) ===")
    df3 = pd.read_sql(text("""
        SELECT
            r.u_Colada,
            t.No_Parte                  AS no_parte,
            COUNT(*)                    AS piezas_total,
            SUM(t.bRechazo)             AS rechazos,
            ROUND(AVG(t.bRechazo)*100,1) AS pct_rechazo
        FROM cscmega_01resultadoidticket t
        JOIN cscmega_01rechazosbyidticket r ON r.idTicket = t.idTicket
        WHERE r.u_Colada = 335
        GROUP BY r.u_Colada, t.No_Parte
        ORDER BY piezas_total DESC
        LIMIT 15
    """), cx)
    print(df3.to_string(index=False))

    # 3. Flujo de etapas para piezas de una colada
    print("\n=== FLUJO DE ETAPAS para colada 335 ===")
    df4 = pd.read_sql(text("""
        SELECT
            SUM(bMOLD)  AS en_moldeo,
            SUM(bVACI)  AS en_vaciado,
            SUM(bLIMP)  AS en_limpieza,
            SUM(bDESM)  AS en_desmoldeo,
            SUM(bCELD)  AS en_celdas,
            COUNT(*)    AS total_tickets
        FROM cscmega_08ruta r
        JOIN cscmega_01rechazosbyidticket t ON t.idTicket = r.u_idTicket
        WHERE t.u_Colada = 335
    """), cx)
    print(df4.to_string(index=False))

    # 4. ¿El JOIN u_Colada entre rechazos y ruta funciona?
    print("\n=== CALIDAD DEL JOIN: rechazos ↔ ruta (muestra dic 18) ===")
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

    # 5. ¿Coladas tienen quimica disponible?
    print("\n=== QUIMICA disponible por colada (muestra dic 18) ===")
    df6 = pd.read_sql(text("""
        SELECT
            c.u_Colada,
            c.u_Horno,
            c.c_Status,
            q.aB_C, q.aB_Si, q.aB_Mg,
            ROUND(q.aB_C + q.aB_Si/3.0 + q.aB_P/3.0, 3) AS ce_pct
        FROM cscmega_03coladacargametalica c
        LEFT JOIN cscmega_05cquimicosbase q
            ON q.u_Colada = c.u_Colada AND q.aB_C > 0
        WHERE DATE(c.u_Fecha) = :dia
        GROUP BY c.u_Colada, c.u_Horno, c.c_Status,
                 q.aB_C, q.aB_Si, q.aB_Mg, q.aB_P
        ORDER BY c.u_Colada
        LIMIT 12
    """), cx, params={"dia": MUESTRA})
    print(df6.to_string(index=False))
