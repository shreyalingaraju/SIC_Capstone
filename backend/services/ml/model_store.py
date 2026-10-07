"""Load the frozen model once, verify its integrity, and expose a ready/disabled/unavailable state.

Checks at load time (any failure leaves the model unavailable; nothing is downloaded or substituted):
  1. LIGHTSAFE_ML_ENABLED is not 0.
  2. Every artifact listed in MANIFEST.json exists and matches its recorded SHA-256.
  3. The scikit-learn and xgboost versions match the ones recorded in the model card.
  4. The loaded pipeline reproduces the stored golden predictions (golden_check.json).
"""
import hashlib
import json
import logging
import threading
from typing import Any, Dict, List, Optional

from ... import config

logger = logging.getLogger("lightsafe.ml")

READY, DISABLED, UNAVAILABLE = "ready", "disabled", "unavailable"


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class MlModel:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state = UNAVAILABLE
        self.reason: Optional[str] = "not loaded"
        self.pipeline = None
        self.card: Dict[str, Any] = {}
        self.schema: Dict[str, Any] = {}
        self.model_sha256: Optional[str] = None

    @property
    def ready(self) -> bool:
        return self.state == READY

    def _fail(self, reason: str) -> None:
        self.state, self.reason, self.pipeline, self.model_sha256 = UNAVAILABLE, reason, None, None
        logger.error("ML model unavailable: %s", reason)

    def load(self) -> None:
        with self._lock:
            if not config.ML_ENABLED:
                self.state, self.reason, self.pipeline, self.model_sha256 = DISABLED, "disabled by LIGHTSAFE_ML_ENABLED=0", None, None
                logger.info("ML disabled (LIGHTSAFE_ML_ENABLED=0)")
                return
            try:
                self._load_verified()
            except Exception as exc:  # fail safe: the rest of the application must keep working
                self._fail(f"{type(exc).__name__}: {exc}")

    def _load_verified(self) -> None:
        art = config.ML_ARTIFACT_DIR
        manifest_path = art / "MANIFEST.json"
        if not manifest_path.exists():
            return self._fail("MANIFEST.json is missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for name, rec in manifest["artifacts"].items():
            p = art / name
            if not p.exists():
                return self._fail(f"artifact {name} is missing")
            if sha256_file(p) != rec["sha256"]:
                return self._fail(f"artifact {name} does not match its recorded SHA-256")

        self.card = json.loads((art / "model_card.json").read_text(encoding="utf-8"))
        self.schema = json.loads((art / "feature_schema.json").read_text(encoding="utf-8"))

        import joblib
        import sklearn
        import xgboost
        recorded = self.card.get("library_versions", {})
        for lib, installed in (("sklearn", sklearn.__version__), ("xgboost", xgboost.__version__)):
            if recorded.get(lib) and recorded[lib] != installed:
                return self._fail(f"{lib} {installed} installed, frozen model was saved with {recorded[lib]}")

        pipeline = joblib.load(art / "frozen_model.joblib")
        self._golden_check(pipeline, art / "golden_check.json")
        self.pipeline = pipeline
        self.model_sha256 = manifest["artifacts"]["frozen_model.joblib"]["sha256"]
        self.state, self.reason = READY, None
        logger.info("ML model ready (sha256 %s)", self.model_sha256[:12])

    def _golden_check(self, pipeline, path) -> None:
        import numpy as np
        import pandas as pd
        golden = json.loads(path.read_text(encoding="utf-8"))
        X = pd.DataFrame(golden["rows"])[self.schema["features"]]
        for c in self.schema["categorical"]:  # JSON null -> missing, as in training
            X[c] = X[c].astype(object).where(X[c].notna(), np.nan)
        got = pipeline.predict_proba(X)[:, 1]
        err = float(np.abs(got - np.asarray(golden["expected"])).max())
        if err > golden["tolerance"]:
            raise RuntimeError(f"golden predictions not reproduced (max error {err:.2e})")

    def predict_proba(self, features):
        """P(slow repair) for a feature frame built by ml_model.features.build_features."""
        if not self.ready:
            raise RuntimeError(f"ML model is {self.state}: {self.reason}")
        return self.pipeline.predict_proba(features[self.schema["features"]])[:, 1]

    def status(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "reason": self.reason,
            "model": "XGBoost" if self.card.get("selected_model") == "xgboost" else self.card.get("selected_model"),
            "artifact_sha256": self.model_sha256,
            "integrity_verified": self.ready,
        }


ml_model = MlModel()
