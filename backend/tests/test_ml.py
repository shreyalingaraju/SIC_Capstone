"""
ML integration tests (frozen repair-pressure model). Run from the repository root:

    python -m unittest backend.tests.test_ml -v

The tests never write to data/ or outputs/ (tampering tests work on temporary copies).
"""
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from backend import config
from backend.main import app
from backend.services.ml import common_ml, get_regime, ml_model
from backend.services.ml.model_store import MlModel

ROOT = Path(__file__).resolve().parents[2]
RESEARCH_MODEL = ROOT / "ml_model" / "models" / "frozen_model.joblib"
FEATURES_PKL = ROOT / "ml_model" / "data" / "processed" / "nyc_features.pkl"
TEST_PREDICTIONS = ROOT / "ml_model" / "outputs" / "nyc_test_predictions.csv"

# Responses that must be identical with ML on, off, or broken: the validated analytical pipeline.
ANALYTICAL_PATHS = [
    "/api/overview", "/api/priority/summary", "/api/optimization", "/api/comparison",
    "/api/causal/overview", "/api/outages?page_size=50", "/api/queue?method=FIFO&page_size=50",
]


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _analytical_responses(client):
    return {p: client.get(p).json() for p in ANALYTICAL_PATHS}


class ModelIntegrityTests(unittest.TestCase):
    def test_runtime_artifact_matches_manifest_and_research_source(self):
        manifest = json.loads((config.ML_ARTIFACT_DIR / "MANIFEST.json").read_text())
        for name, rec in manifest["artifacts"].items():
            self.assertEqual(_sha(config.ML_ARTIFACT_DIR / name), rec["sha256"], name)
            self.assertEqual(rec["sha256"], rec["source_sha256"], f"{name} differs from the ml_model source at freeze time")
        if RESEARCH_MODEL.exists():
            self.assertEqual(_sha(RESEARCH_MODEL), _sha(config.ML_ARTIFACT_DIR / "frozen_model.joblib"))

    def test_loads_and_reports_ready(self):
        m = MlModel()
        m.load()
        self.assertEqual(m.state, "ready", m.reason)
        self.assertTrue(m.status()["integrity_verified"])
        self.assertEqual(m.card["selected_model"], "xgboost")
        self.assertAlmostEqual(m.card["decision_threshold"], 0.6153, places=4)

    def test_tampered_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(config.ML_ARTIFACT_DIR, tmp, dirs_exist_ok=True)
            with open(Path(tmp) / "frozen_model.joblib", "ab") as f:
                f.write(b"x")
            with mock.patch.object(config, "ML_ARTIFACT_DIR", Path(tmp)):
                m = MlModel()
                m.load()
        self.assertEqual(m.state, "unavailable")
        self.assertIn("SHA-256", m.reason)
        self.assertIsNone(m.pipeline)

    def test_missing_artifact_is_unavailable_not_an_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(config, "ML_ARTIFACT_DIR", Path(tmp)):
                m = MlModel()
                m.load()
        self.assertEqual(m.state, "unavailable")

    def test_disabled_flag(self):
        with mock.patch.object(config, "ML_ENABLED", False):
            m = MlModel()
            m.load()
        self.assertEqual(m.state, "disabled")
        with self.assertRaises(RuntimeError):
            m.predict_proba(pd.DataFrame())

    @unittest.skipUnless(FEATURES_PKL.exists() and TEST_PREDICTIONS.exists(), "ml_model research data not present")
    def test_reproduces_research_test_predictions(self):
        m = MlModel()
        m.load()
        feats = pd.read_pickle(FEATURES_PKL)
        pred = pd.read_csv(TEST_PREDICTIONS)
        rows = feats.merge(pred[["unique_key", "p_slow_repair"]], on="unique_key").iloc[::40]
        got = m.predict_proba(rows)
        self.assertLess(float(np.abs(got - rows["p_slow_repair"].to_numpy()).max()), 1e-5)


class CategoryTests(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(common_ml.categorize(100), "High")
        self.assertEqual(common_ml.categorize(67), "High")
        self.assertEqual(common_ml.categorize(66.9), "Moderate")
        self.assertEqual(common_ml.categorize(33), "Moderate")
        self.assertEqual(common_ml.categorize(32.9), "Low")


class RegimeApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = TestClient(app)
        cls.client = cls.ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    def test_regime_shape_and_scope(self):
        d = self.client.get("/api/ml/regime").json()
        self.assertTrue(d["available"])
        self.assertEqual(d["role"], "context_only")
        self.assertEqual(d["model_status"]["state"], "ready")
        self.assertEqual(sorted(b["borough"] for b in d["boroughs"]),
                         ["Bronx", "Brooklyn", "Manhattan", "Queens", "Staten Island"])
        for b in d["boroughs"]:
            self.assertIn(b["category"], ("High", "Moderate", "Low"))
            self.assertTrue(0 <= b["relative_score"] <= 100)
            self.assertGreater(b["observation_count"], 0)
        # nothing per-outage or probability-like is exposed
        blob = json.dumps(d).lower()
        for banned in ("outage_id", "p_slow_repair", "probability\":", "crime_risk"):
            self.assertNotIn(banned, blob)
        self.assertIn("does not mean high crime", d["explanation"])

    def test_borough_filter(self):
        d = self.client.get("/api/ml/regime?borough=bronx").json()
        self.assertEqual([b["borough"] for b in d["boroughs"]], ["Bronx"])
        self.assertEqual(len(self.client.get("/api/ml/regime?borough=All").json()["boroughs"]), 5)

    def test_snapshot_from_other_model_is_not_served(self):
        snap = json.loads(config.ML_REGIME_SNAPSHOT_FILE.read_text())
        snap["model_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "s.json"
            p.write_text(json.dumps(snap))
            with mock.patch.object(config, "ML_REGIME_SNAPSHOT_FILE", p):
                d = get_regime()
        self.assertFalse(d["available"])
        self.assertEqual(d["boroughs"], [])

    def test_missing_snapshot(self):
        with mock.patch.object(config, "ML_REGIME_SNAPSHOT_FILE", Path("does/not/exist.json")):
            d = get_regime()
        self.assertFalse(d["available"])
        self.assertIn("not been generated", d["note"])


class AnalyticalOutputsUnaffectedTests(unittest.TestCase):
    """ML on, disabled and unavailable must serve byte-identical analytical responses."""

    def test_identical_with_ml_on_off_and_broken(self):
        with TestClient(app) as c:
            on = _analytical_responses(c)
            self.assertEqual(c.get("/api/ml/regime").json()["available"], True)

        with mock.patch.object(config, "ML_ENABLED", False), TestClient(app) as c:
            off = _analytical_responses(c)
            d = c.get("/api/ml/regime").json()
            self.assertFalse(d["available"])
            self.assertEqual(d["model_status"]["state"], "disabled")
            self.assertEqual(c.get("/api/health").json()["status"], "healthy")

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "ML_ARTIFACT_DIR", Path(tmp)), TestClient(app) as c:
            broken = _analytical_responses(c)
            d = c.get("/api/ml/regime").json()
            self.assertFalse(d["available"])
            self.assertEqual(d["model_status"]["state"], "unavailable")

        self.assertEqual(on, off)
        self.assertEqual(on, broken)
        ml_model.load()  # leave the shared singleton in its normal state for other tests


if __name__ == "__main__":
    unittest.main()
