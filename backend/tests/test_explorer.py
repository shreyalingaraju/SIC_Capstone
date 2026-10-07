"""
Evidence / operations explorer tests. Run from the repository root:

    .venv\\Scripts\\python.exe -m unittest backend.tests.test_explorer -v

Checks that the explorer endpoints return the stored Stage 11 values unchanged, that the capacity
explorer is the real frozen simulation (re-runs reproduce outputs/stage14_capacity), and that
changing K changes the outputs. Nothing is written to data/ or outputs/.
"""
import unittest

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from backend import config
from backend.main import app


class ExplorerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = TestClient(app)
        cls.client = cls.ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    # ---- evidence
    def test_stage11_values_match_stored_files(self):
        d = self.client.get("/api/evidence/stage11").json()
        self.assertTrue(d["available"])
        est = pd.read_csv(config.STAGE11_ESTIMATES_FILE)
        sens = pd.read_csv(config.STAGE11_SENSITIVITY_FILE)
        for r in d["rows"]:
            if r["variant"] in ("primary", "cutoff_750", "fortnight"):
                src = est[(est["spec"] == r["variant"]) & (est["band"] == r["band"])].iloc[0]
            else:
                scen, _, part = r["variant"].partition(":")
                band = f"{r['band']}_{part}" if part else r["band"]
                src = sens[(sens["spec"] == scen) & (sens["band"] == band)].iloc[0]
            for a, b in (("estimate", "estimate"), ("ci_lower", "ci_lower"), ("ci_upper", "ci_upper"), ("p_value", "p_value")):
                self.assertAlmostEqual(r[a], float(src[b]), places=12)
        # every stored row is shown except the E0 rebuild (which equals primary)
        self.assertEqual(len(d["rows"]), len(est) + len(sens[sens["spec"] != "E0"]))
        self.assertTrue(d["e0_matches_primary"])

    def test_stage11_doc_statements_hold(self):
        s = self.client.get("/api/evidence/stage11").json()["stats"]
        self.assertEqual(s["n_ci_excluding_zero"], 0)              # all null
        self.assertGreaterEqual(s["min_p_value"], 0.23)             # "every p >= 0.23"
        self.assertEqual(s["timing_sign_flips_0_100"], 3)           # "sign differs in 3 ... scenarios"

    def test_legacy_proportion_has_no_interval(self):
        d = self.client.get("/api/evidence/legacy").json()
        props = [r for r in d["rows"] if r["effect"] == "displacement_proportion"]
        self.assertTrue(props)
        for r in props:
            self.assertIsNone(r["ci_lower"])
            self.assertFalse(r["interval_available"])

    # ---- operations
    def test_capacity_rerun_reproduces_frozen_stage14(self):
        frozen = pd.read_csv(config.STAGE14_CAPACITY_METRICS_FILE).set_index("capacity_k")
        for k in frozen.index:
            d = self.client.get(f"/api/operations/capacity?k={k}").json()
            self.assertTrue(d["matches_frozen_stage14_output"], k)
            for c in ("wait_mean_days", "wait_p90_days", "served_within_7d_pct", "backlog_mean"):
                self.assertAlmostEqual(d["metrics"][c], float(frozen.loc[k, c]), places=3)

    def test_changing_capacity_changes_outputs(self):
        a = self.client.get("/api/operations/capacity?k=58").json()
        b = self.client.get("/api/operations/capacity?k=72").json()
        self.assertIsNone(a["matches_frozen_stage14_output"])  # off-grid: exploratory
        self.assertGreater(a["metrics"]["wait_mean_days"], b["metrics"]["wait_mean_days"])
        self.assertGreater(a["metrics"]["backlog_max"], b["metrics"]["backlog_max"])
        self.assertNotEqual(a["series"]["backlog"], b["series"]["backlog"])

    def test_series_queue_identity(self):
        s = self.client.get("/api/operations/capacity?k=65").json()["series"]
        b, n, x = (np.array(s[c]) for c in ("backlog", "new_jobs", "dispatched"))
        prev = np.concatenate([[0], b[:-1]])
        self.assertTrue((b == prev + n - x).all())
        self.assertEqual(int(n.sum()), 57444)
        self.assertEqual(int(x.sum()), 57444)
        self.assertTrue((x <= 65).all())

    def test_replay_consistent_with_series(self):
        d = self.client.get("/api/operations/replay?k=65").json()
        j = d["jobs"]
        known, disp = np.array(j["known_day"]), np.array(j["dispatch_day"])
        self.assertTrue((disp >= known).all())
        backlog = np.array(d["series"]["backlog"])
        for day in (0, 200, 600, len(backlog) - 1):
            pending = int(((known <= day) & (disp > day)).sum())
            self.assertEqual(pending, int(backlog[day]))

    def test_capacity_out_of_range_rejected(self):
        self.assertEqual(self.client.get("/api/operations/capacity?k=10").status_code, 422)
        self.assertEqual(self.client.get("/api/operations/replay?k=500").status_code, 422)


if __name__ == "__main__":
    unittest.main()
