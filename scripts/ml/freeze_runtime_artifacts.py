"""Copy the frozen model from ml_model/models into backend/services/ml/artifacts and record its provenance.

The research folder (ml_model/) is the source of the frozen artifact; the backend serves a byte-identical copy
and verifies its SHA-256 at startup. This script never retrains or modifies the model. It writes:

  MANIFEST.json      SHA-256 of each runtime artifact and of its ml_model source
  golden_check.json  60 NYC test rows (features + the probabilities the frozen model produced at training time),
                     used at startup to confirm the artifact still reproduces its own predictions

Run from the repository root:  python scripts/ml/freeze_runtime_artifacts.py
"""
import hashlib
import json
import shutil
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "ml_model" / "models"
DST = ROOT / "backend" / "services" / "ml" / "artifacts"
FILES = ["frozen_model.joblib", "model_card.json", "feature_schema.json"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    DST.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copyfile(SRC / name, DST / name)

    schema = json.loads((DST / "feature_schema.json").read_text())
    model = joblib.load(DST / "frozen_model.joblib")
    feats = pd.read_pickle(ROOT / "ml_model" / "data" / "processed" / "nyc_features.pkl")
    pred = pd.read_csv(ROOT / "ml_model" / "outputs" / "nyc_test_predictions.csv")[["unique_key", "p_slow_repair"]]
    rows = feats.merge(pred, on="unique_key").sort_values("unique_key").iloc[::330].head(60)
    X = rows[schema["features"]]
    recomputed = model.predict_proba(X)[:, 1]
    assert np.abs(recomputed - rows["p_slow_repair"].to_numpy()).max() < 1e-5, "artifact does not reproduce saved predictions"

    def clean(v):
        if pd.isna(v):
            return None
        return v.item() if hasattr(v, "item") else v

    golden = {
        "source": "ml_model/outputs/nyc_test_predictions.csv (NYC test split, 2026)",
        "tolerance": 1e-5,
        "rows": [{c: clean(r[c]) for c in schema["features"]} for _, r in X.iterrows()],
        "expected": [float(v) for v in rows["p_slow_repair"]],
    }
    (DST / "golden_check.json").write_text(json.dumps(golden, indent=1))

    manifest = {
        "model": "xgboost slow-repair pipeline (preprocessing + XGBClassifier)",
        "note": "Byte-identical copy of ml_model/models; do not edit. Provenance and metrics: model_card.json.",
        "artifacts": {n: {"sha256": sha256(DST / n), "source_sha256": sha256(SRC / n)} for n in FILES},
    }
    (DST / "MANIFEST.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps(manifest, indent=1))


if __name__ == "__main__":
    main()
