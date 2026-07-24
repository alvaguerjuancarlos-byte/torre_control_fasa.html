"""
Entrena rf_rechazo_v2 — RandomForest sobre tickets 2025.
Guarda: backend/models/rf_rechazo_v2.joblib
        backend/models/parte_lookup.json   (rate_prior, peso_kgs, log_peso, sin_hist)
        backend/models/feature_meta.json

Uso:
    cd torre-de-control-fasa
    python scripts/ml_train_v2.py
"""
import os, json, math
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_score, cross_val_predict
import joblib
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
HOST   = os.environ["DB_HOST"]
PORT   = os.environ.get("DB_PORT", "3306")
USER   = os.environ["DB_USER"]
PWD    = os.environ["DB_PWD"]
SCHEMA = os.environ.get("DB_SCHEMA", "ia_fasa")
DB_URL = f"mysql+pymysql://{USER}:{PWD}@{HOST}:{PORT}/{SCHEMA}?charset=utf8mb4"
engine = create_engine(DB_URL, pool_pre_ping=True)

MODELS_DIR = os.path.join(os.path.dirname(__file__), "..", "backend", "models")
os.makedirs(MODELS_DIR, exist_ok=True)

print("[1/6] Cargando tickets 2025 (cscmega)…")
with engine.connect() as conn:
    tickets = pd.read_sql(text("""
        SELECT
            rb.idTicket,
            rb.u_Fecha,
            rb.u_Colada,
            rb.u_Horno,
            rb.bRechazo,
            rt.No_Parte
        FROM cscmega_01rechazosbyidticket rb
        JOIN cscmega_01resultadoidticket  rt ON rt.idTicket = rb.idTicket
        WHERE rb.u_Fecha >= '2025-01-01'
          AND rt.No_Parte IS NOT NULL AND rt.No_Parte != ''
    """), conn)
print(f"   {len(tickets):,} tickets, rechazo {tickets.bRechazo.mean():.3f}")

print("[2/6] Cargando tasa 2024 por parte (alfamega)…")
with engine.connect() as conn:
    hist2024 = pd.read_sql(text("""
        SELECT No_Parte,
               COUNT(*)        AS n_2024,
               SUM(bRechazo)   AS rec_2024
        FROM alfamega_01_rechazosbyidticket
        WHERE FHrVaciado >= '2024-01-01' AND FHrVaciado < '2025-01-01'
          AND No_Parte IS NOT NULL AND No_Parte != ''
        GROUP BY No_Parte
    """), conn)
hist2024["rate_2024"] = hist2024["rec_2024"] / hist2024["n_2024"]
rate_map = dict(zip(hist2024.No_Parte, hist2024.rate_2024))
GLOBAL_MEAN = hist2024["rec_2024"].sum() / hist2024["n_2024"].sum()
print(f"   {len(rate_map):,} partes con historial 2024, global_mean={GLOBAL_MEAN:.4f}")

print("[3/6] Temperatura (PC-4) y tiempos (PC-5) — usando sentinel (igual que en producción)…")
# El modelo se usa sin features de proceso en el endpoint riesgo-partes,
# así que entrenamos con sentinel para que aprenda la relación real de uso.

print("[4/6] Cargando peso de partes desde parte_lookup.json existente…")
existing_lookup_path = os.path.join(MODELS_DIR, "parte_lookup.json")
with open(existing_lookup_path) as f:
    existing_lookup = json.load(f)
peso_map = {p: v["peso_kgs"] for p, v in existing_lookup.items() if v.get("peso_kgs")}
GLOBAL_MEDIAN_PESO = np.median(list(peso_map.values())) if peso_map else 158.0
print(f"   {len(peso_map):,} partes con peso")

print("[5/6] Construyendo features…")
tickets["mes"] = pd.to_datetime(tickets["u_Fecha"]).dt.month

# Rate parte 2024
tickets["rate_parte_2024"] = tickets["No_Parte"].map(rate_map)
tickets["rate_parte_2024_flag"] = tickets["rate_parte_2024"].isna().astype(float)
tickets["rate_parte_2024"] = tickets["rate_parte_2024"].fillna(GLOBAL_MEAN)

# Peso
tickets["peso_kgs"] = tickets["No_Parte"].map(peso_map).fillna(GLOBAL_MEDIAN_PESO)
tickets["log_peso"] = np.log1p(tickets["peso_kgs"])

SENTINEL = -1.0
tickets["temp_ok_n"]    = SENTINEL
tickets["pc5_cumple_n"] = SENTINEL
tickets["inoc_s_n"]     = SENTINEL

# Horno dummies (hornos conocidos: 0,1,2,4,8,9)
for h in [0,1,2,4,8,9]:
    tickets[f"horno_{h}"] = (tickets["u_Horno"] == h).astype(float)

# Química
tickets["pct_quimica_ok"]       = SENTINEL
tickets["n_quimica_disponible"] = SENTINEL

FEATURE_COLS = [
    "pct_quimica_ok","n_quimica_disponible",
    "temp_ok_n","pc5_cumple_n","inoc_s_n",
    "mes","rate_parte_2024","rate_parte_2024_flag","log_peso",
    "horno_0","horno_1","horno_2","horno_4","horno_8","horno_9"
]

X = tickets[FEATURE_COLS].values.astype(float)
y = tickets["bRechazo"].fillna(0).values.astype(int)
print(f"   X shape: {X.shape}, positivos: {y.sum():,} ({y.mean():.3f})")

clf = RandomForestClassifier(
    n_estimators=400, max_depth=8,
    n_jobs=-1, random_state=42   # sin class_weight: RF aprende la tasa real
)

print("[5b] Cross-validation 3-fold (AUC + calibración Platt OOF)…")
cv = StratifiedKFold(n_splits=3, shuffle=True, random_state=42)
aucs = cross_val_score(clf, X, y, cv=cv, scoring="roc_auc", n_jobs=-1)
print(f"   AUC: {aucs.mean():.3f} ± {aucs.std():.3f}")

# Platt scaling: LogReg sobre probabilidades OOF del RF
oof_proba = cross_val_predict(clf, X, y, cv=cv, method="predict_proba", n_jobs=-1)[:, 1]
calibrator = LogisticRegression(C=1e10, solver="lbfgs")
calibrator.fit(oof_proba.reshape(-1, 1), y)
cal_mean = calibrator.predict_proba(oof_proba.reshape(-1, 1))[:, 1].mean()
print(f"   OOF raw mean: {oof_proba.mean():.4f} | calibrado: {cal_mean:.4f} | real: {y.mean():.4f}")

print("[6/6] Entrenando modelo final y guardando…")
clf.fit(X, y)

model_path = os.path.join(MODELS_DIR, "rf_rechazo_v2.joblib")
joblib.dump(clf, model_path)
print(f"   Modelo guardado: {model_path} ({os.path.getsize(model_path)/1e6:.1f} MB)")

cal_path = os.path.join(MODELS_DIR, "rf_calibrator.joblib")
joblib.dump(calibrator, cal_path)
print(f"   Calibrador Platt guardado: {cal_path}")

# parte_lookup: tasa 2024, peso, log_peso, sin_hist
lookup = {}
all_partes = set(tickets["No_Parte"].unique()) | set(rate_map.keys())
for p in all_partes:
    lookup[p] = {
        "rate_prior": rate_map.get(p, GLOBAL_MEAN),
        "peso_kgs":   peso_map.get(p, GLOBAL_MEDIAN_PESO),
        "log_peso":   math.log1p(peso_map.get(p, GLOBAL_MEDIAN_PESO)),
        "sin_hist":   int(p not in rate_map),
    }
lookup_path = os.path.join(MODELS_DIR, "parte_lookup.json")
with open(lookup_path, "w") as f:
    json.dump(lookup, f)
print(f"   Lookup guardado: {len(lookup):,} partes")

# feature_meta
meta = {
    "feature_columns": FEATURE_COLS,
    "sentinel": SENTINEL,
    "global_mean": round(float(GLOBAL_MEAN), 4),
    "global_median_peso": float(GLOBAL_MEDIAN_PESO),
    "trained_on": "2025-01-01 / 2025-12-31",
    "auc_cv": round(float(aucs.mean()), 3),
    "n_samples": int(len(tickets)),
    "n_features": len(FEATURE_COLS),
    "calibration": "platt",
    "oof_raw_mean": round(float(oof_proba.mean()), 4),
    "oof_cal_mean": round(float(cal_mean), 4),
    "real_mean": round(float(y.mean()), 4),
}
meta_path = os.path.join(MODELS_DIR, "feature_meta.json")
with open(meta_path, "w") as f:
    json.dump(meta, f, indent=2)
print(f"   Meta guardado. AUC final: {meta['auc_cv']}")
print("\n✓ Listo. Reinicia uvicorn para cargar el modelo.")
