"""Borough repair-pressure context from the frozen slow-repair model.

What is computed: for each borough, the mean model score P(complaint still unresolved after 7 days) over the
complaints reported in a trailing window, ranked against the same statistic over the model's development period
(2024-01 to 2025-12: the train and validation splits). The result is a *relative operational-pressure* indicator,
not a crime measure and not a per-outage prediction. It is a descriptive snapshot built offline by
scripts/ml/build_regime_snapshot.py and served read-only; `get_regime` also re-checks that the snapshot was
produced by the model artifact that is currently loaded.
"""
import json
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ... import config
from .common_ml import BOROUGHS, CATEGORY_LABELS, EXPLANATION, LIMITATIONS, categorize
from .model_store import ml_model

WINDOW_DAYS = 28            # trailing window for a borough's score
MIN_WINDOW_ROWS = 30        # a window with fewer complaints is not scored
REFERENCE_START = "2024-01-01"   # model development period: train 2024-01..2025-06 + validation 2025-07..2025-12
REFERENCE_END = "2025-12-31"
REFERENCE_STEP_DAYS = 7


def build_snapshot(featured: pd.DataFrame) -> Dict[str, Any]:
    """Score every complaint from REFERENCE_START on with the loaded frozen model and summarise by borough.

    `featured` must come from ml_model.features.build_features.engineer_features() over the full complaint log.
    """
    if not ml_model.ready:
        raise RuntimeError(f"ML model is {ml_model.state}: {ml_model.reason}")
    df = featured[featured["created_date"] >= pd.Timestamp(REFERENCE_START)].copy()
    df = df[df["borough"].isin(BOROUGHS)]
    df["score"] = ml_model.predict_proba(df)

    as_of = df["created_date"].max()
    ref_ends = pd.date_range(pd.Timestamp(REFERENCE_START) + pd.Timedelta(days=WINDOW_DAYS), REFERENCE_END,
                             freq=f"{REFERENCE_STEP_DAYS}D")

    def window_mean(sub: pd.DataFrame, end: pd.Timestamp):
        w = sub[(sub["created_date"] > end - pd.Timedelta(days=WINDOW_DAYS)) & (sub["created_date"] <= end)]
        return (float(w["score"].mean()), int(len(w))) if len(w) >= MIN_WINDOW_ROWS else (None, int(len(w)))

    boroughs: List[Dict[str, Any]] = []
    for b in BOROUGHS:
        sub = df[df["borough"] == b]
        ref = np.array([m for m, _ in (window_mean(sub, e) for e in ref_ends) if m is not None])
        cur, n = window_mean(sub, as_of)
        if cur is None or len(ref) < 10:
            boroughs.append({"borough": b.title(), "available": False, "observation_count": n})
            continue
        pct = float((ref <= cur).mean() * 100)
        boroughs.append({
            "borough": b.title(),
            "available": True,
            "category": categorize(pct),
            "relative_score": round(pct, 1),
            "above_reference_range": bool(cur > ref.max()),
            "observation_count": n,
            "reference_windows": int(len(ref)),
        })

    return {
        "generated_by": "scripts/ml/build_regime_snapshot.py",
        "model_sha256": ml_model.model_sha256,
        "window": {"days": WINDOW_DAYS, "end": as_of.strftime("%Y-%m-%d"),
                   "start": (as_of - pd.Timedelta(days=WINDOW_DAYS - 1)).strftime("%Y-%m-%d")},
        "reference": {"start": REFERENCE_START, "end": REFERENCE_END, "step_days": REFERENCE_STEP_DAYS,
                      "description": "Model development period (train and validation splits)."},
        "boroughs": boroughs,
    }


def _empty(status: Dict[str, Any], note: str) -> Dict[str, Any]:
    return {"available": False, "model_status": status, "note": note, "boroughs": []}


def get_regime(borough: Optional[str] = None) -> Dict[str, Any]:
    status = ml_model.status()
    if ml_model.state == "disabled":
        return _empty(status, "Repair-pressure context is switched off for this deployment. All other LightSafe views are unaffected.")
    if not ml_model.ready:
        return _empty(status, "The repair-pressure model could not be verified, so no context is shown. All other LightSafe views are unaffected.")

    path = config.ML_REGIME_SNAPSHOT_FILE
    if not path.exists():
        return _empty(status, "The repair-pressure snapshot has not been generated yet.")
    snap = json.loads(path.read_text(encoding="utf-8"))
    if snap.get("model_sha256") != ml_model.model_sha256:
        return _empty(status, "The repair-pressure snapshot was not produced by the current model artifact, so it is not shown.")

    rows = snap["boroughs"]
    if borough and borough.strip().lower() != "all":
        rows = [r for r in rows if r["borough"].lower() == borough.strip().lower()]
    return {
        "available": True,
        "model_status": status,
        "window": snap["window"],
        "reference": snap["reference"],
        "category_labels": CATEGORY_LABELS,
        "boroughs": rows,
        "explanation": EXPLANATION,
        "limitations": LIMITATIONS,
        "role": "context_only",
    }
