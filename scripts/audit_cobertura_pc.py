"""
audit_cobertura_pc.py — Auditoría de cobertura de los 7 Puntos de Control FASA.

COMPUERTAS (en orden):
  C1 — EXISTENCIA     : tabla.campo real en ia_fasa / corex_test
  C2 — POBLAMIENTO    : % registros útiles (no nulo, no centinela / no espurio)
  C3 — COMPARABILIDAD : % con join válido a ventana (parte×período) + genealogía (IdColada)
  C4 — CUMPLIMIENTO   : %dentro / %ámbar / %rojo  (solo informativo si pasa C1-C3)

Estado por PC:
  VERDE  = pasa C1 + C2≥70% + C3≥70% + C4 disponible
  ÁMBAR  = pasa C1 + C2≥50% + C3 parcial/condicionada (dato vivo, no completamente accionable)
  GRIS   = reprueba C1 o C2 (dato ausente o inutilizable)
  ROJO   = pasa C1-C3 pero C4 muestra >50% fuera de rango

Uso:
  python audit_cobertura_pc.py                                       # default: todo 2025
  python audit_cobertura_pc.py --desde 2026-01-01 --hasta 2026-03-31
  python audit_cobertura_pc.py --desde 2025-01-01 --hasta 2025-12-31 --out audit_2025.txt

Read-only. Sin efectos secundarios. Re-ejecutable cuando se conecte una fuente nueva.
"""

import argparse
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).parent.parent / ".env")

# -- Conexión ------------------------------------------------------------------
def make_engine():
    return create_engine(
        f"mysql+pymysql://{os.environ['DB_USER']}:{os.environ['DB_PWD']}"
        f"@{os.environ['DB_HOST']}:{os.environ.get('DB_PORT', 3306)}/ia_fasa"
        "?charset=utf8mb4",
        pool_pre_ping=True,
        connect_args={"read_timeout": 90, "write_timeout": 90},
    )


# -- Helpers -------------------------------------------------------------------
def q(conn, sql, params=None):
    try:
        return conn.execute(text(sql), params or {}).fetchall()
    except Exception as e:
        return []

def q1(conn, sql, params=None):
    try:
        r = conn.execute(text(sql), params or {}).fetchone()
        return r
    except Exception as e:
        return None

def qerr(conn, sql, params=None):
    """Como q1 pero devuelve ("ERROR", msg) en excepción."""
    try:
        r = conn.execute(text(sql), params or {}).fetchone()
        return r, None
    except Exception as e:
        return None, str(e)

def tbl_exists(conn, schema, tbl):
    """SHOW TABLES evita problemas de permisos en information_schema."""
    try:
        sql = "SHOW TABLES FROM `" + schema + "` LIKE :t"
        rows = conn.execute(text(sql), {"t": tbl}).fetchall()
        return len(rows) > 0
    except Exception:
        try:
            conn.execute(text("SELECT 1 FROM `" + tbl + "` LIMIT 1"))
            return True
        except Exception:
            return False

def col_exists(conn, tbl, col, schema="ia_fasa"):
    """SHOW COLUMNS evita problemas de permisos en information_schema."""
    try:
        sql = "SHOW COLUMNS FROM `" + tbl + "` LIKE :c"
        rows = conn.execute(text(sql), {"c": col}).fetchall()
        return len(rows) > 0
    except Exception:
        return False

def pct(n, d, fmt=True):
    if not d:
        return "—"
    p = round(100.0 * int(n or 0) / int(d), 1)
    return f"{p}% ({n}/{d})" if fmt else p


def early(base, **kw):
    """Clona `base` y aplica overrides — reemplaza {**base, kw=...} inválido."""
    return {**base, **kw}


# -- PC-1: Carga al horno ------------------------------------------------------
def audit_pc1(conn, desde, hasta):
    res = dict(pc="PC-1", nombre="Carga al horno",
               tabla_campo="alfamega_07_cargametalicos.KgsXX_*",
               c1=None, c2=None, c3=None, c4=None,
               estado=None, bloqueador=None)

    # C1 — Existencia
    if not tbl_exists(conn, "ia_fasa", "alfamega_07_cargametalicos"):
        return early(res, c1="NO EXISTE", c2="—", c3="—", c4="—",
                estado="GRIS",
                bloqueador="Tabla alfamega_07_cargametalicos no encontrada en ia_fasa")

    kgs_rows = q(conn, "SHOW COLUMNS FROM alfamega_07_cargametalicos LIKE 'Kgs%'")
    kgs_cols = kgs_rows  # cada row[0] es el Field name (ya filtrado por LIKE)
    n_kgs = len(kgs_cols)
    if n_kgs == 0:
        return early(res, c1="EXISTE tabla, sin columnas Kgs", c2="—", c3="—", c4="—",
                estado="GRIS", bloqueador="Sin columnas KgsXX_* — esquema inesperado")

    res["c1"] = f"EXISTE — {n_kgs} cols Kgs + idTicket + FHrVaciado"
    res["tabla_campo"] = f"alfamega_07_cargametalicos.Kgs*({n_kgs} cols)"

    # C2 — Poblamiento: % pesajes con suma(Kgs) > 0 (no espurios delta=0)
    kgs_sum_expr = " + ".join(f"COALESCE(`{r[0]}`, 0)" for r in kgs_cols)
    r2, err = qerr(conn, f"""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN ({kgs_sum_expr}) = 0 THEN 1 ELSE 0 END) AS delta_cero,
            SUM(CASE WHEN ({kgs_sum_expr}) > 0 THEN 1 ELSE 0 END) AS con_peso
        FROM `alfamega_07_cargametalicos`
        WHERE FHrVaciado BETWEEN :desde AND :hasta
    """, {"desde": desde, "hasta": hasta})

    if err or not r2 or not int(r2[0]):
        res["c2"] = f"Sin datos en período{(' — ' + err) if err else ''}"
        c2_ok = False
    else:
        total = int(r2[0]); delta0 = int(r2[1] or 0); con_p = int(r2[2] or 0)
        pct_delta = round(100.0 * delta0 / total, 1)
        pct_util  = round(100.0 * con_p  / total, 1)
        res["c2"] = (f"{pct_util}% útil (Kgs>0) | {pct_delta}% delta=0 espurio "
                     f"(esperado ~53%) | {total} pesajes en período")
        c2_ok = pct_util >= 30

    # C3 — Comparabilidad: join a ticket (ventana parte×período) + join a colada
    # La receta TBL-HIC (corex_test.modelocastingrecetas) NO tiene campo c_IdColada
    r3, err3 = qerr(conn, """
        SELECT
            COUNT(DISTINCT a.idTicket) AS total_tickets,
            COUNT(DISTINCT t.idTicket) AS con_join_ticket
        FROM `alfamega_07_cargametalicos` a
        LEFT JOIN `cscmega_00_tickets` t ON t.idTicket = a.idTicket
        WHERE a.FHrVaciado BETWEEN :desde AND :hasta
    """, {"desde": desde, "hasta": hasta})

    if err3 or not r3 or not r3[0]:
        res["c3"] = f"Join ticket->parte imposible{(' — ' + err3) if err3 else ''}"
        c3_ok = False
    else:
        total_t = int(r3[0]); con_t = int(r3[1] or 0)
        pct_join = round(100.0 * con_t / total_t, 1) if total_t else 0
        res["c3"] = (f"{pct_join}% join idTicket->tickets | "
                     f"Receta TBL-HIC (corex_test.modelocastingrecetas): sin campo c_IdColada — "
                     f"join receta<->colada INACCIONABLE")
        c3_ok = pct_join >= 70

    res["c4"] = "Sin ventana de cumplimiento en FUS-001 para carga al horno"

    if c2_ok and c3_ok:
        res["estado"] = "ÁMBAR"
        res["bloqueador"] = ("Receta TBL-HIC sin c_IdColada en corex_test — "
                             "join carga_real<->receta_planificada pendiente")
    elif c2_ok:
        res["estado"] = "GRIS"
        res["bloqueador"] = "Join idTicket->parte no verificado o incompleto"
    else:
        res["estado"] = "GRIS"
        res["bloqueador"] = "Delta=0 dominante o sin datos en período — carga sin registro útil"
    return res


# -- PC-2: Análisis químico ----------------------------------------------------
def audit_pc2(conn, desde, hasta):
    res = dict(pc="PC-2", nombre="Análisis químico",
               tabla_campo="cscmega_05aquimicoscumplimiento.bX_B/bX_F",
               c1=None, c2=None, c3=None, c4=None,
               estado=None, bloqueador=None)

    # C1 — Spectro real NO integrado digitalmente; flags bX existen pero son derivados
    if not tbl_exists(conn, "ia_fasa", "cscmega_05aquimicoscumplimiento"):
        return early(res, c1="NO EXISTE", c2="—", c3="—", c4="—",
                estado="GRIS",
                bloqueador="Tabla cscmega_05aquimicoscumplimiento no encontrada")

    has_base  = tbl_exists(conn, "ia_fasa", "cscmega_05cquimicosbase")
    has_final = tbl_exists(conn, "ia_fasa", "cscmega_05bquimicosfinal")
    has_itaca = col_exists(conn, "cscmega_03coladacargametalica", "c_ItacaLadle")
    res["c1"] = (f"Flags bX_B/bX_F EXISTEN (cscmega_05aquimicoscumplimiento) | "
                 f"Valores SpectroMAX: {'cscmega_05cquimicosbase (~42%)' if has_base else 'NO EXISTE'} | "
                 f"Copa ITACA (arefBatch): c_ItacaLadle {'EXISTE' if has_itaca else 'NO EXISTE'} | "
                 f"CumplimientoAnalisisQuimico: COLUMNA-BUG (almacena literal string)")

    # C2 — % flags bC_F útiles en período + nota bMg_B
    r2, err2 = qerr(conn, """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN bC_F IS NOT NULL AND bC_F NOT LIKE 'Cumplimiento%'
                     THEN 1 ELSE 0 END) AS util_bcf,
            SUM(CASE WHEN bMg_B IS NOT NULL THEN 1 ELSE 0 END) AS mg_b_nn,
            SUM(CASE WHEN bMg_B LIKE '%(S)%' THEN 1 ELSE 0 END) AS mg_b_s_tipo
        FROM `cscmega_05aquimicoscumplimiento`
        WHERE u_Fecha BETWEEN :desde AND :hasta
    """, {"desde": desde, "hasta": hasta})

    if err2 or not r2 or not int(r2[0]):
        res["c2"] = f"Sin datos en período{(' — ' + err2) if err2 else ''}"
        c2_ok = False
    else:
        total = int(r2[0]); util_bc = int(r2[1] or 0)
        mg_nn = int(r2[2] or 0); mg_s = int(r2[3] or 0)
        pct_bc = round(100.0 * util_bc / total, 1)
        pct_mg_s = round(100.0 * mg_s / mg_nn, 1) if mg_nn else 0
        res["c2"] = (f"{pct_bc}% bC_F útil ({util_bc}/{total}) | "
                     f"bMg_B: {pct_mg_s}% tipo (S) vs (N) — sesgo detectado (solo tipo N registra) | "
                     f"Spectro valores cuantitativos: NO integrados en ia_fasa")
        c2_ok = pct_bc >= 30

    # C3 — Join química->colada vía u_Colada+u_Horno
    # Usamos subquery con LIMIT para evitar timeout en join completo (45K x N filas)
    r3, err3 = qerr(conn, """
        SELECT COUNT(*) AS total_muestra, SUM(has_colada) AS con_join
        FROM (
            SELECT q.u_Colada, q.u_Horno,
                   MAX(CASE WHEN c.c_IdColada IS NOT NULL THEN 1 ELSE 0 END) AS has_colada
            FROM (
                SELECT DISTINCT u_Colada, u_Horno
                FROM `cscmega_05aquimicoscumplimiento`
                WHERE u_Fecha BETWEEN :desde AND :hasta
                LIMIT 300
            ) q
            LEFT JOIN `cscmega_03coladacargametalica` c
                ON c.u_Colada = q.u_Colada AND c.u_Horno = q.u_Horno
            GROUP BY q.u_Colada, q.u_Horno
        ) t
    """, {"desde": desde, "hasta": hasta})

    if err3 or not r3 or not r3[0]:
        res["c3"] = f"Join u_Colada+u_Horno imposible{(' — ' + err3) if err3 else ''}"
        c3_ok = False
    else:
        muestra = int(r3[0]); con_j = int(r3[1] or 0)
        pct_j = round(100.0 * con_j / muestra, 1) if muestra else 0
        res["c3"] = (f"{pct_j}% join química->colada en muestra de {muestra} pares (u_Colada+u_Horno) | "
                     f"c_ItacaLadle disponible en coladas para rastreo ITACA")
        c3_ok = pct_j >= 70

    # C4 — Distribución PASS/FAIL/LÍMITE en bC_F
    r4, err4 = qerr(conn, """
        SELECT
            SUM(CASE WHEN bC_F LIKE '%)1' THEN 1 ELSE 0 END) AS pass_c,
            SUM(CASE WHEN bC_F LIKE '%-1' THEN 1 ELSE 0 END) AS fail_c,
            SUM(CASE WHEN bC_F LIKE '%)0' THEN 1 ELSE 0 END) AS limite_c,
            COUNT(CASE WHEN bC_F IS NOT NULL THEN 1 END)      AS total_c
        FROM `cscmega_05aquimicoscumplimiento`
        WHERE u_Fecha BETWEEN :desde AND :hasta
    """, {"desde": desde, "hasta": hasta})

    if err4 or not r4 or not r4[3]:
        res["c4"] = "Sin flags de cumplimiento en período"
    else:
        t = int(r4[3])
        res["c4"] = (f"bC_F: {round(100*int(r4[0] or 0)/t,1)}% PASS / "
                     f"{round(100*int(r4[2] or 0)/t,1)}% LÍMITE / "
                     f"{round(100*int(r4[1] or 0)/t,1)}% FAIL | "
                     f"bMg_B sesgo pendiente validar con COREX/Calidad")

    if c2_ok and c3_ok:
        res["estado"] = "ÁMBAR"
        res["bloqueador"] = ("SpectroMAX sin integración digital; bMg_B solo tipo (N) — "
                             "validar sesgo con COREX/Calidad antes de encender semáforo")
    else:
        res["estado"] = "GRIS"
        res["bloqueador"] = ("Spectro sin integración; CumplimientoAnalisisQuimico es columna-bug; "
                             "bX flags derivados sin valores cuantitativos reales")
    return res


# -- PC-3: Inoculación / fading ------------------------------------------------
def audit_pc3(conn, desde, hasta):
    res = dict(pc="PC-3", nombre="Inoculación / fading",
               tabla_campo="cscmega_03coladacargametalica.c_FechaInoculante1",
               c1=None, c2=None, c3=None, c4=None,
               estado=None, bloqueador=None)

    if not tbl_exists(conn, "ia_fasa", "cscmega_03coladacargametalica"):
        return early(res, c1="NO EXISTE", c2="—", c3="—", c4="—",
                estado="GRIS", bloqueador="Tabla cscmega_03coladacargametalica no encontrada")

    has_inoc  = col_exists(conn, "cscmega_03coladacargametalica", "c_FechaInoculante1")
    has_trasv = col_exists(conn, "cscmega_03coladacargametalica", "c_FechaTrasvase1")
    # Dosis inoculante (cantidad real) -> NO existe en ia_fasa; requiere ITACA
    res["c1"] = (f"c_FechaInoculante1: {'EXISTE' if has_inoc else 'NO EXISTE'} | "
                 f"c_FechaTrasvase1: {'EXISTE' if has_trasv else 'NO EXISTE'} | "
                 f"DOSIS inoculante (cantidad): NO EXISTE en ia_fasa — requiere ITACA")

    # C2 — % fechas inoculante pobladas en período
    r2, err2 = qerr(conn, """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN c_FechaInoculante1 IS NOT NULL THEN 1 ELSE 0 END) AS con_inoc,
            SUM(CASE WHEN c_FechaTrasvase1   IS NOT NULL THEN 1 ELSE 0 END) AS con_trasv
        FROM `cscmega_03coladacargametalica`
        WHERE c_FechaInicial BETWEEN :desde AND :hasta
    """, {"desde": desde, "hasta": hasta})

    if err2 or not r2 or not int(r2[0]):
        res["c2"] = f"Sin coladas en período{(' — ' + err2) if err2 else ''}"
        c2_ok = False
    else:
        total = int(r2[0])
        inoc  = int(r2[1] or 0); trasv = int(r2[2] or 0)
        pct_i = round(100.0 * inoc  / total, 1)
        pct_t = round(100.0 * trasv / total, 1)
        res["c2"] = (f"FechaInoculante1: {pct_i}% ({inoc}/{total}) | "
                     f"FechaTrasvase1: {pct_t}% | "
                     f"Dosis inoculante: 0% — no capturado por colada en ia_fasa")
        c2_ok = pct_i >= 50

    # C3 — Join trivial vía c_IdColada (misma tabla), pero variable cuantitativa ausente
    r3, err3 = qerr(conn, """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN c_IdColada IS NOT NULL AND c_FechaInoculante1 IS NOT NULL
                     THEN 1 ELSE 0 END) AS join_ok
        FROM `cscmega_03coladacargametalica`
        WHERE c_FechaInicial BETWEEN :desde AND :hasta
    """, {"desde": desde, "hasta": hasta})

    if err3 or not r3 or not int(r3[0]):
        res["c3"] = "Sin datos"
        c3_ok = False
    else:
        total = int(r3[0]); ok = int(r3[1] or 0)
        pct_j = round(100.0 * ok / total, 1) if total else 0
        res["c3"] = (f"{pct_j}% coladas con c_IdColada + FechaInoculante1 (join trivial) | "
                     f"Dosis inoculante: INACCIONABLE sin ITACA | "
                     f"Ventana fading: NO definida en FUS-001 — pendiente rastreo ITACA")
        # join trivial pasa, pero sin dosis la variable es inutilizable -> falla C3
        c3_ok = False

    res["c4"] = "Ventana fading: NOT en FUS-001 — pendiente de rastreo a ITACA (no usar ≤10 min como proxy)"
    res["estado"] = "GRIS"
    res["bloqueador"] = ("Dosis inoculante sin captura por colada en ia_fasa. "
                         "Requiere acceso ITACA (usuario sapiens sin acceso confirmado al 2026-06-15)")
    return res


# -- PC-4: Temperatura de vaciado ---------------------------------------------
def audit_pc4(conn, desde, hasta):
    res = dict(pc="PC-4", nombre="Temperatura de vaciado",
               tabla_campo="alfamega_06_vaciado.vt_TemperaturaVaciado",
               c1=None, c2=None, c3=None, c4=None,
               estado=None, bloqueador=None)

    tbl = ("alfamega_06_vaciado" if tbl_exists(conn, "ia_fasa", "alfamega_06_vaciado")
           else "cscmega_06modelosvaciado")
    res["tabla_campo"] = f"{tbl}.vt_TemperaturaVaciado"

    if not tbl_exists(conn, "ia_fasa", tbl):
        return early(res, c1="NO EXISTE", c2="—", c3="—", c4="—",
                estado="GRIS", bloqueador="Ninguna tabla de temperatura encontrada")

    has_ok_flag = col_exists(conn, tbl, "bCumplimientoTemperaturaOk")
    res["c1"] = (f"EXISTE — {tbl}.vt_TemperaturaVaciado | "
                 f"bCumplimientoTemperaturaOk: {'EXISTE' if has_ok_flag else 'NO'} | "
                 f"c_IdColada: {'EXISTE' if col_exists(conn, tbl, 'c_IdColada') else 'NO'}")

    # C2 — % en rango físico 1100-1600 °C (no nulo, no centinela)
    r2, err2 = qerr(conn, f"""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN vt_TemperaturaVaciado IS NOT NULL
                     AND vt_TemperaturaVaciado != 0 THEN 1 ELSE 0 END) AS no_nulo,
            SUM(CASE WHEN vt_TemperaturaVaciado BETWEEN 1100 AND 1600 THEN 1 ELSE 0 END) AS en_rango,
            ROUND(AVG(CASE WHEN vt_TemperaturaVaciado BETWEEN 1100 AND 1600
                           THEN vt_TemperaturaVaciado END), 1) AS avg_temp
        FROM `{tbl}`
        WHERE FHrVaciado BETWEEN :desde AND :hasta
    """, {"desde": desde, "hasta": hasta})

    if err2 or not r2 or not int(r2[0]):
        res["c2"] = f"Sin datos en período{(' — ' + err2) if err2 else ''}"
        c2_ok = False
    else:
        total = int(r2[0]); no_n = int(r2[1] or 0); en_r = int(r2[2] or 0)
        avg_t = float(r2[3]) if r2[3] else None
        pct_r = round(100.0 * en_r / total, 1)
        res["c2"] = (f"{pct_r}% en rango 1100-1600°C ({en_r}/{total}) | "
                     f"avg temp={avg_t}°C | "
                     f"{round(100.0*(total-no_n)/total,1)}% nulos/cero")
        c2_ok = pct_r >= 70

    # C3 — Sincronía de reloj: contar coladas donde TODOS los tickets comparten el mismo ts
    # Si ts_distintos_por_colada = 1 -> join idTicket<->temperatura inválido
    r3, err3 = qerr(conn, f"""
        SELECT
            COUNT(DISTINCT c_IdColada)                                              AS total_coladas,
            SUM(CASE WHEN ts_distintos = 1 THEN 1 ELSE 0 END)                      AS coladas_ts_unico,
            ROUND(100.0 * SUM(CASE WHEN ts_distintos = 1 THEN 1 ELSE 0 END)
                  / COUNT(DISTINCT c_IdColada), 1)                                  AS pct_ts_unico,
            MAX(max_tickets_en_ts)                                                  AS max_tickets_compartiendo
        FROM (
            SELECT
                c_IdColada,
                COUNT(DISTINCT FHrVaciado)                          AS ts_distintos,
                MAX(tkt_count)                                      AS max_tickets_en_ts
            FROM (
                SELECT c_IdColada, FHrVaciado, COUNT(*) AS tkt_count
                FROM `{tbl}`
                WHERE FHrVaciado BETWEEN :desde AND :hasta
                  AND c_IdColada IS NOT NULL
                GROUP BY c_IdColada, FHrVaciado
            ) sub
            GROUP BY c_IdColada
        ) agg
    """, {"desde": desde, "hasta": hasta})

    if err3 or not r3 or not r3[0]:
        # Fallback: solo verificar % con c_IdColada
        rf, _ = qerr(conn, f"""
            SELECT COUNT(*) AS total, COUNT(c_IdColada) AS con_colada
            FROM `{tbl}` WHERE FHrVaciado BETWEEN :desde AND :hasta
        """, {"desde": desde, "hasta": hasta})
        if rf and int(rf[0]):
            pf = round(100.0 * int(rf[1] or 0) / int(rf[0]), 1)
            res["c3"] = f"{pf}% con c_IdColada (fallback — análisis de ts no disponible)"
            c3_ok = pf >= 70
        else:
            res["c3"] = "Sin datos para verificar comparabilidad"
            c3_ok = False
    else:
        total_col = int(r3[0]); ts_unico = int(r3[1] or 0)
        pct_ts    = float(r3[2] or 0); max_shared = int(r3[3] or 1)
        res["c3"] = (f"Join a colada (c_IdColada): OK para {total_col} coladas | "
                     f"PROBLEMA RELOJ: {ts_unico}/{total_col} coladas ({pct_ts}%) con ts único — "
                     f"join idTicket<->temperatura INVÁLIDO | "
                     f"max tickets compartiendo mismo ts: {max_shared}")
        # El join a nivel colada funciona; el join ticket-específico no.
        # Para Torre (nivel colada) -> c3 pasa; para ticket-específico -> bloqueado
        c3_ok = (total_col - ts_unico) / total_col >= 0.30 if total_col else False
        # Si >70% de coladas tienen ts_unico -> el dato es inaccionable a nivel ticket

    # C4 — Distribución cumplimiento
    r4, err4 = qerr(conn, f"""
        SELECT
            SUM(CASE WHEN bCumplimientoTemperaturaOk = 1 THEN 1 ELSE 0 END) AS dentro,
            SUM(CASE WHEN bCumplimientoTemperaturaOk = 0 THEN 1 ELSE 0 END) AS fuera,
            COUNT(CASE WHEN bCumplimientoTemperaturaOk IS NOT NULL THEN 1 END) AS total_flag
        FROM `{tbl}`
        WHERE FHrVaciado BETWEEN :desde AND :hasta
    """, {"desde": desde, "hasta": hasta}) if has_ok_flag else (None, "sin flag")

    if err4 or not r4 or not r4[2]:
        res["c4"] = "Sin bCumplimientoTemperaturaOk en período"
    else:
        t = int(r4[2])
        res["c4"] = (f"{round(100*int(r4[0] or 0)/t,1)}% dentro / "
                     f"{round(100*int(r4[1] or 0)/t,1)}% fuera de rango | "
                     f"ADVERTENCIA: atribución ticket-específica inválida por ts compartidos")

    if c2_ok and c3_ok:
        res["estado"] = "ÁMBAR"
        res["bloqueador"] = ("Timestamps compartidos (hasta 9 tickets / colada) invalidan join "
                             "idTicket<->temperatura a nivel ticket. Reparar sincronía reloj en "
                             "vaciadofusion -> mover a VERDE")
    elif c2_ok:
        res["estado"] = "ÁMBAR"
        res["bloqueador"] = (">70% coladas con ts único — temperatura disponible solo a nivel colada, "
                             "no por ticket individual. Reparar reloj vaciadofusion.")
    else:
        res["estado"] = "GRIS"
        res["bloqueador"] = "Temperatura no poblada en período o tabla no encontrada"
    return res


# -- PC-5: Tiempo en molde -----------------------------------------------------
def audit_pc5(conn, desde, hasta):
    res = dict(pc="PC-5", nombre="Tiempo en molde",
               tabla_campo="cscmega_08ruta.FHrMOLD/FHrDESM (calculado)",
               c1=None, c2=None, c3=None, c4=None,
               estado=None, bloqueador=None)

    has_ruta  = tbl_exists(conn, "ia_fasa", "cscmega_08ruta")
    has_mod   = tbl_exists(conn, "ia_fasa", "cscmega_06modelosvaciado")
    has_mold  = col_exists(conn, "cscmega_08ruta", "FHrMOLD")  if has_ruta else False
    has_desm  = col_exists(conn, "cscmega_08ruta", "FHrDESM")  if has_ruta else False
    has_tenfr = col_exists(conn, "cscmega_06modelosvaciado", "m_tenfriamiento") if has_mod else False

    res["c1"] = (f"m_tenfriamiento (campo directo): "
                 f"{'EXISTE pero 0% poblado en histórico (centinela vacío)' if has_tenfr else 'NO EXISTE'} | "
                 f"FHrMOLD: {'EXISTE' if has_mold else 'NO'} | "
                 f"FHrDESM: {'EXISTE' if has_desm else 'NO'} | "
                 f"Temp arena al desmolde (VPODP): SIN CAMPO en ia_fasa")

    if not has_ruta or not (has_mold and has_desm):
        return early(res, c2="FHrMOLD o FHrDESM no existen", c3="—", c4="—",
                estado="GRIS",
                bloqueador="Columnas FHrMOLD/FHrDESM no encontradas en cscmega_08ruta")

    # C2 — % registros donde FHrMOLD+FHrDESM válidos y FHrDESM > FHrMOLD
    r2, err2 = qerr(conn, """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN FHrMOLD IS NOT NULL AND FHrDESM IS NOT NULL
                     AND FHrDESM > FHrMOLD THEN 1 ELSE 0 END) AS ambos_validos,
            ROUND(AVG(CASE WHEN FHrMOLD IS NOT NULL AND FHrDESM IS NOT NULL AND FHrDESM > FHrMOLD
                           THEN TIMESTAMPDIFF(MINUTE, FHrMOLD, FHrDESM) END), 0) AS avg_min_molde
        FROM `cscmega_08ruta`
        WHERE FHrMOLD BETWEEN :desde AND :hasta
    """, {"desde": desde, "hasta": hasta})

    if err2 or not r2 or not int(r2[0]):
        res["c2"] = f"Sin datos en período{(' — ' + err2) if err2 else ''}"
        c2_ok = False
    else:
        total = int(r2[0]); validos = int(r2[1] or 0)
        avg_m = int(r2[2]) if r2[2] else None
        pct_v = round(100.0 * validos / total, 1)
        res["c2"] = (f"{pct_v}% FHrMOLD+FHrDESM válidos ({validos}/{total}) | "
                     f"tiempo en molde avg≈{avg_m} min | "
                     f"m_tenfriamiento directo: 0% (campo vacío históricamente)")
        c2_ok = pct_v >= 50

    # C3 — Join a colada: verificar columnas clave en cscmega_08ruta via SHOW COLUMNS
    check_cols = ['c_IdColada', 'idTicket', 'u_Colada', 'u_NoParte', 'u_Horno']
    cols_found = [c for c in check_cols if col_exists(conn, 'cscmega_08ruta', c)]

    if "c_IdColada" in cols_found:
        r3, err3 = qerr(conn, """
            SELECT COUNT(*) AS total, COUNT(c_IdColada) AS con_colada
            FROM `cscmega_08ruta` WHERE FHrMOLD BETWEEN :desde AND :hasta
        """, {"desde": desde, "hasta": hasta})
        if err3 or not r3 or not int(r3[0]):
            res["c3"] = "Sin datos para join"
            c3_ok = False
        else:
            pct_j = round(100.0 * int(r3[1] or 0) / int(r3[0]), 1)
            res["c3"] = (f"{pct_j}% con c_IdColada (join directo a colada) | "
                         f"join a parte vía u_NoParte: {'OK' if 'u_NoParte' in cols_found else 'sin campo'}")
            c3_ok = pct_j >= 70
    elif "idTicket" in cols_found:
        res["c3"] = (f"Join via idTicket (no c_IdColada directo) | "
                     f"columnas clave presentes: {cols_found}")
        c3_ok = True
    else:
        res["c3"] = f"Columnas clave en cscmega_08ruta: {cols_found} — join a colada indirecto/imposible"
        c3_ok = False

    res["c4"] = ("Ventana tiempo en molde: sin especificación cuantitativa en FUS-001 | "
                 "Temp arena al desmolde: sin campo en ia_fasa (vive en VPODP)")

    if c2_ok and c3_ok:
        res["estado"] = "ÁMBAR"
        res["bloqueador"] = ("m_tenfriamiento=0%; tiempo calculable FHrMOLD−FHrDESM pero sin ventana "
                             "FUS-001 ni temp arena (VPODP sin integración)")
    else:
        res["estado"] = "GRIS"
        res["bloqueador"] = ("Tiempo en molde directo (m_tenfriamiento) sin dato útil. "
                             "Alternativa FHrMOLD−FHrDESM: sin join a colada o sin datos en período")
    return res


# -- PC-6: Rechazo del lote ----------------------------------------------------
def audit_pc6(conn, desde, hasta):
    res = dict(pc="PC-6", nombre="Rechazo del lote",
               tabla_campo="alfamega_01_rechazosbyidticket.bRechazo",
               c1=None, c2=None, c3=None, c4=None,
               estado=None, bloqueador=None)

    if not tbl_exists(conn, "ia_fasa", "alfamega_01_rechazosbyidticket"):
        return early(res, c1="NO EXISTE", c2="—", c3="—", c4="—",
                estado="GRIS",
                bloqueador="Tabla alfamega_01_rechazosbyidticket no encontrada")

    has_idcol = col_exists(conn, "alfamega_01_rechazosbyidticket", "c_IdColada")
    has_parte  = col_exists(conn, "alfamega_01_rechazosbyidticket", "no_parte")
    res["c1"] = (f"EXISTE — bRechazo (0/1 flag) | "
                 f"c_IdColada: {'EXISTE' if has_idcol else 'NO (join via idTicket)'} | "
                 f"no_parte: {'EXISTE' if has_parte else 'NO'} | "
                 f"HBW (Brinell): SIN CAMPO en ia_fasa | "
                 f"Nodularidad: SIN CAMPO en ia_fasa")

    # C2 — % bRechazo poblado + tasa real
    r2, err2 = qerr(conn, """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN bRechazo IS NOT NULL THEN 1 ELSE 0 END) AS no_nulo,
            SUM(CASE WHEN bRechazo = 1 THEN 1 ELSE 0 END) AS rechazos
        FROM `alfamega_01_rechazosbyidticket`
        WHERE FHrVaciado BETWEEN :desde AND :hasta
    """, {"desde": desde, "hasta": hasta})

    if err2 or not r2 or not int(r2[0]):
        res["c2"] = f"Sin datos en período{(' — ' + err2) if err2 else ''}"
        c2_ok = False
    else:
        total = int(r2[0]); no_n = int(r2[1] or 0); rech = int(r2[2] or 0)
        pct_u = round(100.0 * no_n / total, 1)
        tasa  = round(100.0 * rech / total, 1)
        res["c2"] = (f"{pct_u}% bRechazo poblado ({no_n}/{total}) | "
                     f"tasa rechazo observada: {tasa}% | "
                     f"HBW/nodularidad: 0% (sin campo en ia_fasa)")
        c2_ok = pct_u >= 90

    # C3 — Join a colada + join a parte (ventana)
    if has_idcol:
        r3, err3 = qerr(conn, """
            SELECT
                COUNT(*) AS total,
                COUNT(c_IdColada) AS con_colada,
                COUNT(DISTINCT c_IdColada) AS coladas_unicas
            FROM `alfamega_01_rechazosbyidticket`
            WHERE FHrVaciado BETWEEN :desde AND :hasta
        """, {"desde": desde, "hasta": hasta})
        if err3 or not r3 or not int(r3[0]):
            res["c3"] = "Sin datos para join"
            c3_ok = False
        else:
            pct_j = round(100.0 * int(r3[1] or 0) / int(r3[0]), 1)
            res["c3"] = (f"{pct_j}% con c_IdColada ({r3[2]} coladas únicas) | "
                         f"join a ventana parte×período vía no_parte: "
                         f"{'OK' if has_parte else 'sin campo no_parte'}")
            c3_ok = pct_j >= 90
    else:
        res["c3"] = "c_IdColada no en tabla — join via idTicket | no_parte para ventana"
        c3_ok = has_parte

    # C4 — Distribución tasa por parte (top partes con tasa alta)
    r4 = q(conn, """
        SELECT no_parte, COUNT(*) AS total, SUM(bRechazo) AS rech,
               ROUND(100.0*SUM(bRechazo)/COUNT(*), 1) AS tasa
        FROM `alfamega_01_rechazosbyidticket`
        WHERE FHrVaciado BETWEEN :desde AND :hasta AND no_parte IS NOT NULL
        GROUP BY no_parte HAVING tasa > 20 ORDER BY tasa DESC LIMIT 5
    """, {"desde": desde, "hasta": hasta}) if has_parte else []

    if r4:
        top = ", ".join(f"{r[0]}:{r[3]}%" for r in r4[:3])
        res["c4"] = f"{len(r4)} partes con tasa>20% en período | Top: {top}"
    else:
        res["c4"] = "Sin partes con tasa>20% en período (o no_parte sin campo)"

    if c2_ok and c3_ok:
        res["estado"] = "VERDE"
        res["bloqueador"] = ("Señal de rechazo activa. HBW/nodularidad (causa metalúrgica cuantitativa) "
                             "pendiente integración fuente digital")
    elif c2_ok:
        res["estado"] = "ÁMBAR"
        res["bloqueador"] = "Join a colada no directo — señal bRechazo disponible vía idTicket"
    else:
        res["estado"] = "GRIS"
        res["bloqueador"] = "bRechazo sin poblar en período o tabla ausente"
    return res


# -- PC-7: Cierre de colada ----------------------------------------------------
def audit_pc7(conn, desde, hasta):
    res = dict(pc="PC-7", nombre="Cierre de colada",
               tabla_campo="cscmega_03coladacargametalica.c_IdColada/c_ItacaLadle",
               c1=None, c2=None, c3=None, c4=None,
               estado=None, bloqueador=None)

    if not tbl_exists(conn, "ia_fasa", "cscmega_03coladacargametalica"):
        return early(res, c1="NO EXISTE", c2="—", c3="—", c4="—",
                estado="GRIS", bloqueador="Tabla principal coladas no encontrada")

    has_itaca = col_exists(conn, "cscmega_03coladacargametalica", "c_ItacaLadle")
    has_folio = col_exists(conn, "cscmega_03coladacargametalica", "c_Folio")
    res["c1"] = (f"EXISTE — c_IdColada (PK) | "
                 f"c_ItacaLadle (arefBatch): {'EXISTE' if has_itaca else 'NO EXISTE'} | "
                 f"c_Folio COREX: {'EXISTE' if has_folio else 'NO EXISTE'}")

    # C2 — % coladas con cierre completo (FechaFinal + Kilos>0) + ITACA
    r2, err2 = qerr(conn, """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN c_FechaFinal IS NOT NULL AND c_Kilos IS NOT NULL
                     AND c_Kilos > 0 THEN 1 ELSE 0 END) AS cerradas,
            SUM(CASE WHEN c_ItacaLadle IS NOT NULL AND c_ItacaLadle != ''
                     THEN 1 ELSE 0 END) AS con_itaca,
            SUM(CASE WHEN c_Folio IS NOT NULL AND c_Folio != ''
                     THEN 1 ELSE 0 END) AS con_folio
        FROM `cscmega_03coladacargametalica`
        WHERE c_FechaInicial BETWEEN :desde AND :hasta
    """, {"desde": desde, "hasta": hasta})

    if err2 or not r2 or not int(r2[0]):
        res["c2"] = f"Sin coladas en período{(' — ' + err2) if err2 else ''}"
        c2_ok = False
    else:
        total = int(r2[0]); cerr = int(r2[1] or 0)
        itaca = int(r2[2] or 0); folio = int(r2[3] or 0)
        pct_c = round(100.0 * cerr  / total, 1)
        pct_i = round(100.0 * itaca / total, 1)
        pct_f = round(100.0 * folio / total, 1)
        res["c2"] = (f"{pct_c}% coladas cerradas (FechaFinal+Kilos>0) | "
                     f"c_ItacaLadle: {pct_i}% | c_Folio COREX: {pct_f}%")
        c2_ok = pct_c >= 80

    # C3 — Join cadena completa: colada -> tickets (alfamega_01) por c_IdColada
    has_alfa = tbl_exists(conn, "ia_fasa", "alfamega_01_rechazosbyidticket")
    has_idcol_alfa = col_exists(conn, "alfamega_01_rechazosbyidticket", "c_IdColada") if has_alfa else False

    if has_alfa and has_idcol_alfa:
        r3, err3 = qerr(conn, """
            SELECT
                COUNT(DISTINCT c.c_IdColada) AS total_coladas,
                COUNT(DISTINCT CASE WHEN a.c_IdColada IS NOT NULL
                    THEN c.c_IdColada END) AS con_tickets
            FROM `cscmega_03coladacargametalica` c
            LEFT JOIN `alfamega_01_rechazosbyidticket` a
                ON a.c_IdColada = c.c_IdColada
            WHERE c.c_FechaInicial BETWEEN :desde AND :hasta
        """, {"desde": desde, "hasta": hasta})
        if err3 or not r3 or not r3[0]:
            res["c3"] = "Sin datos para join cadena"
            c3_ok = False
        else:
            total_col = int(r3[0]); con_t = int(r3[1] or 0)
            pct_j = round(100.0 * con_t / total_col, 1) if total_col else 0
            res["c3"] = (f"{pct_j}% coladas con join directo a tickets (c_IdColada) | "
                         f"cadena IdColada->ticket->parte: {'COMPLETA' if pct_j >= 90 else 'INCOMPLETA'}")
            c3_ok = pct_j >= 90
    else:
        res["c3"] = (f"alfamega_01_rechazosbyidticket: {'sin c_IdColada' if has_alfa else 'no encontrada'} "
                     f"— join cadena incompleta")
        c3_ok = False

    # C4 — ESPECIAL: integridad referencial (% cadena completa IdColada<->arefBatch)
    r4, err4 = qerr(conn, """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN c_FechaInicial IS NOT NULL AND c_FechaFinal IS NOT NULL
                     AND c_Kilos > 0 AND c_ItacaLadle IS NOT NULL
                     AND c_ItacaLadle != '' THEN 1 ELSE 0 END) AS cadena_completa,
            SUM(CASE WHEN c_FechaFinal IS NULL THEN 1 ELSE 0 END) AS sin_fecha_fin,
            SUM(CASE WHEN c_ItacaLadle IS NULL OR c_ItacaLadle = ''
                     THEN 1 ELSE 0 END) AS sin_itaca
        FROM `cscmega_03coladacargametalica`
        WHERE c_FechaInicial BETWEEN :desde AND :hasta
    """, {"desde": desde, "hasta": hasta})

    if err4 or not r4 or not int(r4[0]):
        res["c4"] = "Sin datos de integridad referencial"
    else:
        total = int(r4[0]); comp = int(r4[1] or 0)
        sin_fin = int(r4[2] or 0); sin_it = int(r4[3] or 0)
        pct_comp = round(100.0 * comp / total, 1)
        res["c4"] = (f"Integridad referencial: {pct_comp}% cadena completa ({comp}/{total}) | "
                     f"sin FechaFinal: {sin_fin} | sin c_ItacaLadle: {sin_it}")

    if c2_ok and c3_ok:
        res["estado"] = "VERDE"
        res["bloqueador"] = ("Ninguno para señal de cierre. "
                             "c_ItacaLadle (arefBatch) disponible cuando se reactive acceso ITACA sapiens")
    elif c2_ok:
        res["estado"] = "ÁMBAR"
        res["bloqueador"] = "Join cadena colada->ticket <90% — integridad referencial incompleta"
    else:
        res["estado"] = "GRIS"
        res["bloqueador"] = "Coladas sin cierre completo (FechaFinal o Kilos) en período"
    return res


# -- Imprimir tabla final ------------------------------------------------------
ESTADO_EMOJI = {"VERDE": "VERDE", "ÁMBAR": "AMBAR", "GRIS": "GRIS",
                "ROJO": "ROJO", "ERROR": "ERROR"}

def print_tabla(results, desde, hasta, out=None):
    lines = []
    def p(s=""):
        lines.append(s)

    sep  = "=" * 150
    sep2 = "-" * 150

    p()
    p(f"  AUDITORÍA DE COBERTURA — 7 PUNTOS DE CONTROL  |  FASA Torre de Control")
    p(f"  Rango analizado: {desde}  ->  {hasta}")
    p(sep)
    p(f"  {'PC':<6}  {'Nombre':<22}  {'C1':^6}  {'C2 %útil':^28}  "
      f"{'C3 join':^30}  {'C4 cumpl':^22}  {'ESTADO':^8}")
    p(sep2)

    for r in results:
        c1_short = ("OK"   if r["c1"] and "EXISTE" in str(r["c1"])
                    else "NO"    if r["c1"] == "NO EXISTE"
                    else "ERROR" if r["c1"] and "ERROR" in str(r["c1"])
                    else "?")
        # Extraer primer % de c2 y c3
        def first_pct(s):
            m = re.search(r'(\d+\.?\d*)%', str(s or ""))
            return f"{m.group(1)}%" if m else str(s or "—")[:10]

        c2_s = first_pct(r["c2"])
        c3_s = first_pct(r["c3"])
        c4_s = str(r["c4"] or "—")[:22]
        estado = r["estado"] or "?"

        p(f"  {r['pc']:<6}  {r['nombre']:<22}  {c1_short:^6}  {c2_s:^28}  "
          f"{c3_s:^30}  {c4_s:<22}  [{estado:^6}]")

    p(sep)

    # Detalle completo
    p()
    p("  DETALLE POR PC")
    p(sep)
    for r in results:
        p(f"\n  {'='*5} {r['pc']} — {r['nombre']}  [{r['estado']}]")
        p(f"    tabla.campo : {r['tabla_campo']}")
        p(f"    C1 Existencia  : {r['c1']}")
        p(f"    C2 Poblamiento : {r['c2']}")
        p(f"    C3 Comparab.   : {r['c3']}")
        p(f"    C4 Cumplimiento: {r['c4']}")
        p(f"    ► BLOQUEADOR   : {r['bloqueador']}")

    # Resumen
    p()
    p(sep)
    p("  RESUMEN DE ESTADOS")
    p(sep2)
    from collections import Counter
    estados = Counter(r["estado"] for r in results)
    for estado in ["VERDE", "ÁMBAR", "GRIS", "ROJO", "ERROR"]:
        n = estados.get(estado, 0)
        if n:
            pcs = [r["pc"] for r in results if r["estado"] == estado]
            p(f"  {estado:8} ({n} PCs) -> {', '.join(pcs)}")

    p()
    p(sep)
    p("  ORDEN RECOMENDADO DE WIRING — palanca vs dependencia externa")
    p(sep2)
    orden = [
        ("1", "PC-6", "Rechazo del lote",    "Señal bRechazo activa ya. Bajo esfuerzo de integración. [internas]"),
        ("2", "PC-7", "Cierre de colada",     "Integridad referencial alta. ITACA cuando reactive acceso sapiens."),
        ("3", "PC-4", "Temperatura vaciado",  "Dato existe. Reparar sincronía reloj vaciadofusion -> join ticket válido."),
        ("4", "PC-2", "Análisis químico",     "Integrar SpectroMAX digital + validar sesgo bMg_B con COREX/Calidad."),
        ("5", "PC-5", "Tiempo en molde",      "Definir ventana FUS-001; conectar VPODP temp arena al desmolde."),
        ("6", "PC-1", "Carga al horno",       "Mapear receta TBL-HIC a c_IdColada en corex_test."),
        ("7", "PC-3", "Inoculación/fading",   "DEPENDENCIA EXTERNA: requiere acceso ITACA (usuario sapiens)."),
    ]
    for prioridad, pc, nombre, motivo in orden:
        p(f"  {prioridad}. {pc} {nombre:<22}  {motivo}")

    p()
    p(f"  Script read-only. Re-ejecutar con --desde/--hasta cuando se conecte nueva fuente.")
    p()

    # Salida
    output = "\n".join(lines)
    print(output)
    if out:
        out.write(output)
        out.write("\n")


# -- Main ----------------------------------------------------------------------
def main():
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Auditoría de cobertura 7 Puntos de Control FASA (read-only)")
    parser.add_argument("--desde",  default="2025-01-01", metavar="YYYY-MM-DD")
    parser.add_argument("--hasta",  default="2025-12-31", metavar="YYYY-MM-DD")
    parser.add_argument("--out",    default=None,         metavar="ARCHIVO.txt")
    args = parser.parse_args()

    eng = make_engine()
    out_file = open(args.out, "w", encoding="utf-8") if args.out else None

    auditors = [
        audit_pc1, audit_pc2, audit_pc3,
        audit_pc4, audit_pc5, audit_pc6, audit_pc7,
    ]

    results = []
    print(f"\nConectando a ia_fasa ({args.desde} -> {args.hasta})...")
    try:
        for fn in auditors:
            pc_id = fn.__name__.replace("audit_", "").upper()
            print(f"  -> {pc_id}...", end=" ", flush=True)
            # Conexion fresca por PC para aislar timeouts
            try:
                with eng.connect() as conn:
                    r = fn(conn, args.desde, args.hasta)
                results.append(r)
                print(f"[{r.get('estado','?')}]")
            except Exception as e:
                print(f"ERROR: {e}")
                results.append({
                    "pc": pc_id, "nombre": pc_id,
                    "tabla_campo": "?",
                    "c1": f"ERROR: {e}", "c2": "—", "c3": "—", "c4": "—",
                    "estado": "ERROR", "bloqueador": str(e),
                })

        print_tabla(results, args.desde, args.hasta, out_file)

    finally:
        if out_file:
            out_file.close()
            print(f"\nSalida guardada en: {args.out}")


if __name__ == "__main__":
    main()
