"""Diagnóstico: tiempo de ciclo disponible en cscmega_03coladacargametalica."""
import os
from pathlib import Path
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

engine = create_engine(
    f"mysql+pymysql://{os.environ['DB_USER']}:{os.environ['DB_PWD']}"
    f"@{os.environ['DB_HOST']}:{os.environ.get('DB_PORT',3306)}/ia_fasa?charset=utf8mb4"
)

def q(sql, label=""):
    with engine.connect() as cx:
        rows = cx.execute(text(sql)).mappings().fetchall()
    if label: print(f"\n-- {label} --")
    for r in rows: print(dict(r))
    return rows

# 1. c_TiempoFundicion — cobertura y valores
q("""
    SELECT
        COUNT(DISTINCT c_IdColada) AS total_coladas,
        SUM(c_TiempoFundicion IS NOT NULL AND c_TiempoFundicion > 0) AS con_tiempo,
        ROUND(AVG(CASE WHEN c_TiempoFundicion > 0 THEN c_TiempoFundicion END), 1) AS prom_min,
        MIN(CASE WHEN c_TiempoFundicion > 0 THEN c_TiempoFundicion END) AS min_min,
        MAX(CASE WHEN c_TiempoFundicion > 0 AND c_TiempoFundicion < 500 THEN c_TiempoFundicion END) AS max_min
    FROM cscmega_03coladacargametalica
    WHERE u_Fecha BETWEEN '2025-01-01' AND '2025-12-31'
""", "c_TiempoFundicion cobertura 2025")

# 2. Fechas de ciclo completo (carga → liberado)
q("""
    SELECT
        COUNT(DISTINCT c_IdColada) AS total_coladas,
        SUM(c_FechaCarga IS NOT NULL AND c_FechaLiberado IS NOT NULL) AS con_ciclo_completo,
        ROUND(AVG(CASE
            WHEN c_FechaCarga IS NOT NULL AND c_FechaLiberado IS NOT NULL
             AND c_FechaLiberado > c_FechaCarga
            THEN TIMESTAMPDIFF(MINUTE, c_FechaCarga, c_FechaLiberado)
        END), 1) AS ciclo_prom_min,
        ROUND(AVG(CASE
            WHEN c_FechaInicial IS NOT NULL AND c_FechaFinal IS NOT NULL
             AND c_FechaFinal > c_FechaInicial
            THEN TIMESTAMPDIFF(MINUTE, c_FechaInicial, c_FechaFinal)
        END), 1) AS fusion_prom_min
    FROM cscmega_03coladacargametalica
    WHERE u_Fecha BETWEEN '2025-01-01' AND '2025-12-31'
""", "Ciclo completo carga→liberado 2025")

# 3. Por mes
q("""
    SELECT
        DATE_FORMAT(u_Fecha,'%Y-%m') AS mes,
        COUNT(DISTINCT c_IdColada) AS coladas,
        ROUND(AVG(CASE
            WHEN c_TiempoFundicion > 0 AND c_TiempoFundicion < 300
            THEN c_TiempoFundicion END), 1) AS fusion_prom_min,
        ROUND(AVG(CASE
            WHEN c_FechaCarga IS NOT NULL AND c_FechaLiberado IS NOT NULL
             AND TIMESTAMPDIFF(MINUTE,c_FechaCarga,c_FechaLiberado) BETWEEN 30 AND 500
            THEN TIMESTAMPDIFF(MINUTE,c_FechaCarga,c_FechaLiberado)
        END), 1) AS ciclo_prom_min
    FROM cscmega_03coladacargametalica
    WHERE u_Fecha BETWEEN '2025-01-01' AND '2025-12-31'
    GROUP BY mes ORDER BY mes
""", "Por mes — coladas + tiempos")
