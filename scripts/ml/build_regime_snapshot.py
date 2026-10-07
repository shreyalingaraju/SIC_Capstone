"""Build outputs/ml/regime_snapshot.json: borough repair-pressure context from the frozen model.

Reads the raw 311 complaint log (data/raw/streetlight_complaints.csv), builds features with the research
feature code (ml_model/features/build_features.py), scores with the verified frozen model in backend/services/ml
and writes a small descriptive snapshot. Nothing is retrained. Run from the repository root:

    python scripts/ml/build_regime_snapshot.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402
from backend.services.ml import ml_model  # noqa: E402
from backend.services.ml.regime import build_snapshot  # noqa: E402
from ml_model.features.build_features import engineer_features, load_raw  # noqa: E402

RAW = ROOT / "data" / "raw" / "streetlight_complaints.csv"


def main() -> int:
    ml_model.load()
    if not ml_model.ready:
        print(f"ML model not ready: {ml_model.state} ({ml_model.reason})", file=sys.stderr)
        return 1
    raw = load_raw(RAW)
    snap = build_snapshot(engineer_features(raw))
    out = config.ML_REGIME_SNAPSHOT_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, indent=1), encoding="utf-8")
    print(json.dumps(snap, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
