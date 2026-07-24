import sqlalchemy as sa

engine = sa.create_engine('mysql+pymysql://sapiens:Pr0y3Ct0I4@200.76.23.164:3306/ia_fasa')
with engine.connect() as c:
    # Buscar columnas relacionadas con peso/tonelaje/producción
    rows = c.execute(sa.text("""
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = 'ia_fasa'
          AND (LOWER(COLUMN_NAME) LIKE '%peso%'
            OR LOWER(COLUMN_NAME) LIKE '%weight%'
            OR LOWER(COLUMN_NAME) LIKE '%kg%'
            OR LOWER(COLUMN_NAME) LIKE '%ton%'
            OR LOWER(COLUMN_NAME) LIKE '%carga%'
            OR LOWER(COLUMN_NAME) LIKE '%metal%'
            OR LOWER(COLUMN_NAME) LIKE '%piezas%'
            OR LOWER(COLUMN_NAME) LIKE '%produccion%')
        ORDER BY TABLE_NAME, COLUMN_NAME
    """)).fetchall()
    print("=== COLUMNAS PESO/TONELAJE ===")
    for r in rows:
        print(f"  {r[0]:45} | {r[1]:35} | {r[2]}")

    # Ver todas las tablas disponibles
    tables = c.execute(sa.text("""
        SELECT TABLE_NAME, TABLE_ROWS
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = 'ia_fasa'
        ORDER BY TABLE_NAME
    """)).fetchall()
    print("\n=== TABLAS EN ia_fasa ===")
    for t in tables:
        print(f"  {t[0]:50} rows~{t[1]}")
