-- Plan de Producción Mensual — DDL propuesto (ia_fasa)
-- NO EJECUTAR sin confirmación explícita de JC.
-- Paso 0 (solo lectura) confirmó: sin colisión de nombres, No_Parte en el resto
-- del esquema es varchar(35)/varchar(50) — parte VARCHAR(50) es superset seguro.
--
-- No modifica ninguna tabla existente de ia_fasa (incluyendo gammaMega_02).

-- ── Tabla 1: encabezado, un registro por mes/revisión ───────────────────────
CREATE TABLE programa_produccion_mensual (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    anio_mes        DATE NOT NULL,
    rev_no          INT NOT NULL,
    fecha_rev       DATE NULL,
    dias_habiles    INT NULL,
    ventas_ton      DECIMAL(12,2) NULL,
    presup_ton      DECIMAL(12,2) NULL,
    buenas_ton      DECIMAL(12,2) NULL,
    vaciadas_ton    DECIMAL(12,2) NULL,
    rech_int_pct    DECIMAL(6,3) NULL,
    linea_ton       DECIMAL(12,2) NULL,   -- dato principal (confirmado)
    desarrollo_ton  DECIMAL(12,2) NULL,   -- dato principal (confirmado)
    linea_pct       DECIMAL(6,3) NULL,    -- informativo, secundario
    desarrollo_pct  DECIMAL(6,3) NULL,    -- informativo, secundario
    inv_total       DECIMAL(12,2) NULL,   -- tonelaje no facturado
    archivo_origen  VARCHAR(255) NOT NULL,
    fecha_carga     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_programa_mes_rev (anio_mes, rev_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── Tabla 2: detalle por parte, hoja `resumen` — NO AUTORITATIVA ────────────
-- Nombre deliberadamente distinto (_referencial): la hoja `resumen` es zona de
-- trabajo del responsable del plan, sus totales internos no reconcilian entre
-- sí por diseño. Solo contexto de referencia — ningún agente (Alfa/Beta) ni el
-- dashboard deben usarla para tomar o justificar una decisión.
CREATE TABLE programa_produccion_detalle_referencial (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    programa_id     INT NOT NULL,
    parte           VARCHAR(50) NOT NULL,
    sdo_final       INT NULL,
    peso_kg         DECIMAL(10,3) NULL,
    kg_buenos       DECIMAL(12,2) NULL,
    proceso         VARCHAR(10) NULL,   -- AF1/AF2/AF3
    status          VARCHAR(10) NULL,   -- BR/PL/PLM/LP1-3/PPAP/P0/BAJA, sin traducir
    es_autoritativo BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT fk_ppdr_programa FOREIGN KEY (programa_id)
        REFERENCES programa_produccion_mensual (id),
    KEY ix_ppdr_parte (parte)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── Tabla 3: ritmos de vaciado por proceso — indicador operativo principal ──
CREATE TABLE programa_ritmos_vaciado (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    programa_id     INT NOT NULL,
    tipo_dia        ENUM('LV','SAB') NOT NULL,
    proceso         VARCHAR(10) NOT NULL,  -- AF1/AF2/AF3
    moldes_dia      INT NULL,
    peso_prom_kg    DECIMAL(10,3) NULL,
    kg_diarios      DECIMAL(12,2) NULL,
    CONSTRAINT fk_prv_programa FOREIGN KEY (programa_id)
        REFERENCES programa_produccion_mensual (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── Tabla 4: ritmos agregados por tipo de día — indicador clave operativo ───
CREATE TABLE programa_ritmos_agregados (
    id                      INT AUTO_INCREMENT PRIMARY KEY,
    programa_id             INT NOT NULL,
    tipo_dia                ENUM('LV','SAB') NOT NULL,
    kg_vaciado_dia_con_ri   DECIMAL(12,2) NULL,
    kg_diarios_buenos       DECIMAL(12,2) NULL,
    coladas_diarias         DECIMAL(8,2) NULL,
    CONSTRAINT fk_pra_programa FOREIGN KEY (programa_id)
        REFERENCES programa_produccion_mensual (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
