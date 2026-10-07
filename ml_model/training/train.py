"""Train, select and freeze the slow-repair model using NYC data only.

Run from the project root:  python -m training.train
"""
import itertools
import json
import platform
import time
from pathlib import Path

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import sklearn
import xgboost
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler
from xgboost import XGBClassifier

from evaluation.metrics import evaluate, plot_confusion, plot_curves, youden_threshold
from features.build_features import (CAT_FEATURES, FEATURES, LABEL_HOURS, LEAKY_COLS, NUM_FEATURES,
                                     add_labels, engineer_features, load_raw)

ROOT = Path(__file__).resolve().parents[1]
SEED = 42

# Chronological split. Modelling window starts 2024-01-01 because 2020-2022 closures contain a
# large share of zero-duration records (~50%) that is a recording artefact, not repair behaviour.
WINDOW_START = "2024-01-01"
TRAIN_END = "2025-07-01"      # train: [2024-01-01, 2025-07-01)
VAL_END = "2026-01-01"        # val:   [2025-07-01, 2026-01-01); test: [2026-01-01, end]

LOG_COLS = ["loc_prior_90d", "loc_prior_365d", "loc_days_since_prev", "boro_backlog_60d",
            "boro_created_7d", "boro_closed_14d", "boro_median_dur_14d", "city_median_dur_14d"]
PLAIN_NUM = [c for c in NUM_FEATURES if c not in LOG_COLS]


def make_preprocessor(scale, features=FEATURES):
    cats = [c for c in CAT_FEATURES if c in features]
    logc = [c for c in LOG_COLS if c in features]
    plain = [c for c in PLAIN_NUM if c in features]
    steps_plain = [("imp", SimpleImputer(strategy="median", add_indicator=True))]
    steps_log = [("imp", SimpleImputer(strategy="median", add_indicator=True)),
                 ("log", FunctionTransformer(np.log1p, feature_names_out="one-to-one"))]
    if scale:
        steps_plain.append(("sc", StandardScaler()))
        steps_log.append(("sc", StandardScaler()))
    parts = [("cat", Pipeline([("imp", SimpleImputer(strategy="constant", fill_value="missing")),
                               ("oh", OneHotEncoder(handle_unknown="infrequent_if_exist",
                                                    min_frequency=50))]), cats)]
    if logc:
        parts.append(("log", Pipeline(steps_log), logc))
    if plain:
        parts.append(("num", Pipeline(steps_plain), plain))
    return ColumnTransformer(parts, remainder="drop")


def candidates(pos_weight):
    grid = []
    for C in [0.01, 0.1, 1.0]:
        grid.append(("logreg", {"C": C}, lambda p, C=C: (True, LogisticRegression(
            C=C, class_weight="balanced", max_iter=2000, random_state=SEED))))
    for depth, leaf in itertools.product([8, 16], [5, 30]):
        grid.append(("random_forest", {"max_depth": depth, "min_samples_leaf": leaf},
                     lambda p, d=depth, l=leaf: (False, RandomForestClassifier(
                         n_estimators=300, max_depth=d, min_samples_leaf=l, n_jobs=-1,
                         class_weight="balanced_subsample", random_state=SEED))))
    for depth, n in itertools.product([3, 5], [200, 500]):
        grid.append(("xgboost", {"max_depth": depth, "n_estimators": n, "learning_rate": 0.05},
                     lambda p, d=depth, n=n: (False, XGBClassifier(
                         max_depth=d, n_estimators=n, learning_rate=0.05, subsample=0.8,
                         colsample_bytree=0.8, min_child_weight=5, scale_pos_weight=p,
                         tree_method="hist", n_jobs=-1, random_state=SEED, eval_metric="logloss"))))
    return grid


def build(scale, model, features=FEATURES):
    return Pipeline([("prep", make_preprocessor(scale, features)), ("model", model)])


def main():
    t0 = time.time()
    out = ROOT / "outputs"
    (ROOT / "models").mkdir(exist_ok=True)
    out.mkdir(exist_ok=True)
    (ROOT / "data" / "processed").mkdir(parents=True, exist_ok=True)

    # ---- data, labels, features ------------------------------------------------------------
    df = engineer_features(add_labels(load_raw(ROOT / "data/raw/streetlight_complaints.csv")))
    df.to_pickle(ROOT / "data/processed/nyc_features.pkl")
    d = df[df["label_known"] & (df["created_date"] >= WINDOW_START)].copy()
    d["y"] = d["slow_repair"].astype(int)
    train = d[d["created_date"] < TRAIN_END]
    val = d[(d["created_date"] >= TRAIN_END) & (d["created_date"] < VAL_END)]
    test = d[d["created_date"] >= VAL_END]
    split_info = {n: {"rows": len(s), "from": str(s.created_date.min()), "to": str(s.created_date.max()),
                      "slow_repair_rate": round(float(s.y.mean()), 4)}
                  for n, s in [("train", train), ("val", val), ("test", test)]}
    print(json.dumps(split_info, indent=1))
    Xtr, ytr, Xva, yva, Xte, yte = (train[FEATURES], train.y, val[FEATURES], val.y,
                                    test[FEATURES], test.y)
    pos_weight = float((ytr == 0).sum() / (ytr == 1).sum())

    # ---- fit candidates; select on validation ROC-AUC only ---------------------------------
    rows, fitted = [], {}
    for name, params, factory in candidates(pos_weight):
        scale, est = factory(pos_weight)
        pipe = build(scale, est).fit(Xtr, ytr)
        p_tr, p_va = pipe.predict_proba(Xtr)[:, 1], pipe.predict_proba(Xva)[:, 1]
        thr = youden_threshold(yva, p_va)
        mv = evaluate(yva, p_va, thr)
        key = f"{name}|{json.dumps(params, sort_keys=True)}"
        fitted[key] = (name, params, pipe, thr)
        rows.append({"model": name, "params": json.dumps(params, sort_keys=True), "key": key,
                     "train_roc_auc": evaluate(ytr, p_tr, 0.5)["roc_auc"],
                     **{f"val_{k}": mv[k] for k in ["roc_auc", "pr_auc", "balanced_accuracy", "f1",
                                                    "precision", "recall", "brier"]},
                     "val_threshold": thr})
        print(f"{name:14s} {params}  val AUC {mv['roc_auc']:.4f}  PR-AUC {mv['pr_auc']:.4f}  "
              f"BalAcc {mv['balanced_accuracy']:.4f}", flush=True)
    comp = pd.DataFrame(rows)
    best_per_model = comp.loc[comp.groupby("model")["val_roc_auc"].idxmax()].copy()
    best_per_model = best_per_model.sort_values("val_roc_auc", ascending=False)
    winner = best_per_model.iloc[0]
    name, params, pipe, thr = fitted[winner["key"]]
    print("\nSELECTED:", name, params, "threshold", round(thr, 4))

    # ---- reference baseline + diagnostic ablation (not used for selection) -----------------
    from sklearn.dummy import DummyClassifier
    dummy = Pipeline([("prep", make_preprocessor(False)), ("model", DummyClassifier(strategy="prior"))]).fit(Xtr, ytr)
    static = [c for c in FEATURES if not (c.startswith("loc_") or c.startswith("boro_") or c.startswith("city_"))]
    _, est = dict(((n, tuple(sorted(pp.items()))), f) for n, pp, f in candidates(pos_weight))[
        (name, tuple(sorted(params.items())))](pos_weight)
    scale_flag = name == "logreg"
    abl = build(scale_flag, est, static).fit(Xtr[static], ytr)

    # ---- final NYC test (touched once, after the model is chosen) ---------------------------
    p_va, p_te = pipe.predict_proba(Xva)[:, 1], pipe.predict_proba(Xte)[:, 1]
    m_val, m_test = evaluate(yva, p_va, thr), evaluate(yte, p_te, thr)
    diag = {}
    for label, mdl, cols in [("baseline_prior", dummy, FEATURES), ("selected_static_features_only", abl, static)]:
        pv, pt = mdl.predict_proba(Xva[cols])[:, 1], mdl.predict_proba(Xte[cols])[:, 1]
        diag[label] = {"val_roc_auc": evaluate(yva, pv, 0.5)["roc_auc"], "val_pr_auc": evaluate(yva, pv, 0.5)["pr_auc"],
                       "test_roc_auc": evaluate(yte, pt, 0.5)["roc_auc"], "test_pr_auc": evaluate(yte, pt, 0.5)["pr_auc"]}
    # test scores of every per-model winner, for the comparison table (informational only)
    for i, r in best_per_model.iterrows():
        _, _, pp, th = fitted[r["key"]]
        pt = pp.predict_proba(Xte)[:, 1]
        best_per_model.loc[i, "test_roc_auc"] = evaluate(yte, pt, th)["roc_auc"]
        best_per_model.loc[i, "test_pr_auc"] = evaluate(yte, pt, th)["pr_auc"]
        best_per_model.loc[i, "test_balanced_accuracy"] = evaluate(yte, pt, th)["balanced_accuracy"]
    comp.drop(columns="key").to_csv(out / "model_comparison_all_configs.csv", index=False)
    best_per_model.drop(columns="key").to_csv(out / "model_comparison.csv", index=False)

    pd.DataFrame({"unique_key": test["unique_key"].values, "created_date": test["created_date"].values,
                  "borough": test["borough"].values, "y_true": yte.values, "p_slow_repair": p_te,
                  "y_pred": (p_te >= thr).astype(int)}).to_csv(out / "nyc_test_predictions.csv", index=False)
    plot_confusion(m_test, out / "nyc_test_confusion_matrix.png", "NYC test")
    plot_curves({"NYC val": (yva, p_va), "NYC test": (yte, p_te)}, out / "nyc_roc_curve.png", out / "nyc_pr_curve.png")

    # per-borough test breakdown
    bt = pd.DataFrame({"borough": test["borough"].values, "y": yte.values, "p": p_te})
    brow = []
    for b, g in bt.groupby("borough"):
        if g.y.nunique() == 2 and len(g) >= 100:
            m = evaluate(g.y, g.p, thr)
            brow.append({"borough": b, "n": m["n"], "prevalence": m["prevalence"], "roc_auc": m["roc_auc"],
                         "pr_auc": m["pr_auc"], "balanced_accuracy": m["balanced_accuracy"]})
    pd.DataFrame(brow).to_csv(out / "nyc_test_by_borough.csv", index=False)

    # ---- feature importance (permutation, on validation, ROC-AUC drop) ---------------------
    pi = permutation_importance(pipe, Xva, yva, scoring="roc_auc", n_repeats=5, random_state=SEED, n_jobs=1)
    imp = pd.DataFrame({"feature": FEATURES, "auc_drop": pi.importances_mean, "std": pi.importances_std})
    imp = imp.sort_values("auc_drop", ascending=False)
    imp.to_csv(out / "feature_importance.csv", index=False)
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.barh(imp.feature[::-1], imp.auc_drop[::-1], xerr=imp["std"][::-1])
    ax.set_xlabel("drop in validation ROC-AUC when permuted"); ax.set_title(f"Permutation importance ({name})")
    fig.tight_layout(); fig.savefig(out / "feature_importance.png", dpi=140); plt.close(fig)

    # ---- freeze ----------------------------------------------------------------------------
    joblib.dump(pipe, ROOT / "models/frozen_model.joblib")
    schema = {
        "features": FEATURES,
        "categorical": CAT_FEATURES,
        "numeric": NUM_FEATURES,
        "target": "slow_repair",
        "target_definition": f"1 if complaint is not resolved within {LABEL_HOURS:.0f} hours of created_date",
        "leaky_columns_never_used": LEAKY_COLS,
        "required_raw_columns": ["created_date", "closed_date", "status", "borough"],
        "note": "Feature columns must be produced by features.build_features.engineer_features(); "
                "history features need the full complaint log (incl. earlier complaints) of the target system.",
    }
    (ROOT / "models/feature_schema.json").write_text(json.dumps(schema, indent=2))
    card = {
        "selected_model": name, "hyperparameters": params, "decision_threshold": thr,
        "threshold_rule": "Youden J (max TPR-FPR) on NYC validation set",
        "selection_rule": "highest validation ROC-AUC among best config per model family; NYC data only",
        "split": split_info, "window_start": WINDOW_START, "seed": SEED,
        "nyc_validation_metrics": m_val, "nyc_test_metrics": m_test, "diagnostics": diag,
        "library_versions": {"python": platform.python_version(), "sklearn": sklearn.__version__,
                             "xgboost": xgboost.__version__, "pandas": pd.__version__, "numpy": np.__version__},
        "frozen_pipeline": "models/frozen_model.joblib (preprocessing + model, call predict_proba on FEATURES)",
        "trained_on": "NYC only; the model is trained on the train split only (validation not merged back)",
    }
    (ROOT / "models/model_card.json").write_text(json.dumps(card, indent=2, default=float))
    (out / "nyc_test_metrics.json").write_text(json.dumps({"val": m_val, "test": m_test, "diagnostics": diag}, indent=2, default=float))
    print("\nVAL ", {k: round(v, 4) for k, v in m_val.items() if isinstance(v, float)})
    print("TEST", {k: round(v, 4) for k, v in m_test.items() if isinstance(v, float)})
    print("diag", json.dumps(diag, indent=1))
    print(best_per_model.drop(columns="key").round(4).to_string())
    print(imp.head(12).to_string())
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
