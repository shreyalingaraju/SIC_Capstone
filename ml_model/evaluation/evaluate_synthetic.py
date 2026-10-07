"""External validation of the FROZEN NYC model on a synthetic dataset.

    python -m evaluation.evaluate_synthetic --data path/to/synthetic.csv [--snapshot 2026-01-01] [--out outputs/synthetic]

The synthetic data is used for inference and reporting only. Nothing is refit, retuned, or
re-thresholded here: the decision threshold comes from models/model_card.json (chosen on
NYC validation). Required columns: created_date, closed_date, borough (status optional).
Optional columns (see features/build_features.OPTIONAL_COLS) are used when present; absent ones
become missing values and are reported in the domain-shift table.
"""
import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from evaluation.metrics import (domain_shift_table, evaluate, plot_confusion, plot_curves)
from features.build_features import (CAT_FEATURES, FEATURES, NUM_FEATURES, add_labels,
                                     engineer_features, standardize)

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--snapshot", default=None, help="data-extraction time; default = max created_date")
    ap.add_argument("--out", default=str(ROOT / "outputs" / "synthetic"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    card = json.loads((ROOT / "models/model_card.json").read_text())
    model = joblib.load(ROOT / "models/frozen_model.joblib")
    thr = card["decision_threshold"]

    raw = pd.read_csv(a.data, low_memory=False)
    df = engineer_features(add_labels(standardize(raw), pd.Timestamp(a.snapshot) if a.snapshot else None))
    known = df[df["label_known"]].copy()
    known["y"] = known["slow_repair"].astype(int)
    print(f"synthetic rows {len(df)}, with known label {len(known)}, slow-repair rate {known.y.mean():.3f}")

    p = model.predict_proba(known[FEATURES])[:, 1]
    m = evaluate(known.y, p, thr)
    nyc_test = card["nyc_test_metrics"]
    keys = ["n", "prevalence", "roc_auc", "pr_auc", "balanced_accuracy", "accuracy", "precision", "recall", "f1", "brier"]
    cmp_ = pd.DataFrame({"nyc_test": {k: nyc_test[k] for k in keys}, "synthetic": {k: m[k] for k in keys}})
    cmp_["delta"] = cmp_["synthetic"] - cmp_["nyc_test"]
    cmp_.to_csv(out / "nyc_vs_synthetic_metrics.csv")
    (out / "synthetic_metrics.json").write_text(json.dumps(m, indent=2, default=float))

    pd.DataFrame({"created_date": known["created_date"].values, "borough": known["borough"].values,
                  "y_true": known.y.values, "p_slow_repair": p,
                  "y_pred": (p >= thr).astype(int)}).to_csv(out / "synthetic_predictions.csv", index=False)
    plot_confusion(m, out / "synthetic_confusion_matrix.png", "Synthetic (frozen NYC model)")
    nyc = pd.read_csv(ROOT / "outputs/nyc_test_predictions.csv")
    if known.y.nunique() == 2:
        plot_curves({"NYC test": (nyc.y_true, nyc.p_slow_repair), "Synthetic": (known.y, p)},
                    out / "roc_nyc_vs_synthetic.png", out / "pr_nyc_vs_synthetic.png")

    # domain shift vs the NYC TRAIN distribution the model actually saw
    nyc_all = pd.read_pickle(ROOT / "data/processed/nyc_features.pkl")
    tr = card["split"]["train"]
    ref = nyc_all[nyc_all.label_known & (nyc_all.created_date >= tr["from"]) & (nyc_all.created_date <= tr["to"])]
    shift = domain_shift_table(ref, known, NUM_FEATURES, CAT_FEATURES)
    shift["flag"] = np.where(shift.psi > 0.25, "major", np.where(shift.psi > 0.1, "moderate", "ok"))
    shift.to_csv(out / "domain_shift_psi.csv", index=False)
    pd.DataFrame({"nyc_train_mean_p": [float(model.predict_proba(ref[FEATURES])[:, 1].mean())],
                  "synthetic_mean_p": [float(p.mean())], "synthetic_prevalence": [m["prevalence"]],
                  "nyc_train_prevalence": [float(ref.slow_repair.mean())]}).to_csv(out / "score_shift.csv", index=False)
    print(cmp_.round(4).to_string())
    print(shift.head(12).round(3).to_string())


if __name__ == "__main__":
    main()
