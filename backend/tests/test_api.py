"""
API tests. Run from the repository root:

    .venv\\Scripts\\python.exe -m unittest backend.tests.test_api -v

Displayed values are checked against the analytical artifacts themselves, and the
tests never write to data/ or outputs/.
"""
import json
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd
from fastapi.testclient import TestClient

from backend import config
from backend.main import app
from backend.services.data_store import store


class RealDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = TestClient(app)
        cls.client = cls.ctx.__enter__()  # runs lifespan -> loads artifacts
        cls.summary = json.loads(config.OPTIMAL_DISPATCH_SUMMARY_FILE.read_text())
        cls.plan = pd.read_csv(config.OPTIMAL_DISPATCH_FILE)
        cls.fifo = pd.read_csv(config.FIFO_COMPARISON_FILE)

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    # ---- health / summary
    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "healthy")
        self.assertTrue(r.json()["artifacts_loaded"])

    def test_summary_lists_artifacts_and_notice(self):
        j = self.client.get("/api/summary").json()
        self.assertTrue(j["all_artifacts_available"])
        self.assertIn("not a causal or crime-prevention effect", j["notice"])

    # ---- Stage 14 values match the artifacts
    def test_optimization_matches_summary_json(self):
        j = self.client.get("/api/optimization").json()
        self.assertEqual(j["n_selected"], self.summary["n_selected"])
        self.assertEqual(j["n_deferred"], self.summary["n_deferred"])
        self.assertAlmostEqual(j["objective_value"], round(self.summary["objective_value"], 2))
        self.assertEqual(j["budget_used"], self.summary["budget_used"])
        self.assertEqual(j["budget_remaining"], self.summary["budget_remaining"])
        self.assertTrue(j["all_quotas_satisfied"])
        selected = {r["borough"].upper(): r["selected"] for r in j["borough_table"]}
        for b, n in self.summary["borough_allocation"].items():
            self.assertEqual(selected[b], n)

    def test_recommended_plan_equals_csv(self):
        j = self.client.get("/api/optimization/plan?decision=recommended&page_size=100").json()
        want = self.plan[self.plan.selected_for_repair].sort_values("optimization_rank")
        self.assertEqual(j["total_count"], len(want))
        self.assertEqual([i["outage_id"] for i in j["items"]], [str(x) for x in want.outage_id])
        self.assertEqual([i["optimization_rank"] for i in j["items"]], list(want.optimization_rank))
        self.assertTrue(all(i["dispatch_status"] == "recommended" for i in j["items"]))

    def test_deferred_plan_count_and_pagination(self):
        j = self.client.get("/api/optimization/plan?decision=deferred&page=2&page_size=10").json()
        self.assertEqual(j["total_count"], self.summary["n_deferred"])
        self.assertEqual(len(j["items"]), 10)
        self.assertEqual(j["page"], 2)
        self.assertTrue(all(i["dispatch_status"] == "deferred" for i in j["items"]))

    # ---- Stage 13
    def test_comparison_matches_csv(self):
        j = self.client.get("/api/comparison").json()
        d1 = self.fifo.iloc[0]
        self.assertAlmostEqual(j["day_one"]["lightsafe"], round(d1.lightsafe_cumulative_impact, 2))
        self.assertAlmostEqual(j["day_one"]["fifo"], round(d1.fifo_cumulative_impact, 2))
        self.assertEqual(j["total_days"], int(self.fifo.day.max()))
        self.assertLessEqual(len(j["curve"]), 150)
        self.assertEqual(j["curve"][-1]["day"], int(self.fifo.day.max()))
        m = j["milestones"]
        self.assertLess(m["time_to_50pct_impact_days"]["lightsafe"], m["time_to_50pct_impact_days"]["fifo"])
        self.assertEqual(j["impact_unit"], "priority index points")

    def test_queue_methods(self):
        for method in ("FIFO", "LightSafe"):
            j = self.client.get(f"/api/queue?method={method}&page_size=5").json()
            self.assertEqual(j["method"], method)
            self.assertEqual([i["queue_rank"] for i in j["items"]], [1, 2, 3, 4, 5])
        ls = self.client.get("/api/queue?method=LightSafe&page_size=3").json()["items"]
        self.assertEqual(ls[0]["outage_id"], str(self.plan[self.plan.optimization_rank == 1].outage_id.iloc[0]))

    def test_invalid_queue_method(self):
        self.assertEqual(self.client.get("/api/queue?method=bogus").status_code, 422)

    # ---- Stage 12
    def test_priority_summary_counts(self):
        j = self.client.get("/api/priority/summary").json()
        scored = pd.read_parquet(config.OUTAGES_SCORED_FILE, columns=["scored", "priority_tier"])
        self.assertEqual(j["total_scored"], int(scored.scored.sum()))
        self.assertEqual(j["total_excluded"], int((~scored.scored).sum()))
        self.assertEqual(j["tier_counts"]["High"], int((scored.priority_tier == "High").sum()))

    def test_outage_detail_scored_and_recommended(self):
        top = str(self.plan[self.plan.optimization_rank == 1].outage_id.iloc[0])
        j = self.client.get(f"/api/outages/{top}").json()
        self.assertEqual(j["dispatch_status"], "recommended")
        self.assertEqual(j["priority_score"], 100.0)
        self.assertIsNotNone(j["decomposition"])
        self.assertEqual(j["lightsafe_queue_rank"], 1)
        self.assertIsNotNone(j["fifo_queue_rank"])

    def test_outage_detail_not_scored(self):
        j = self.client.get("/api/outages?scope=excluded&page_size=1").json()
        self.assertGreater(j["total_count"], 0)
        item = j["items"][0]
        self.assertFalse(item["scored"])
        self.assertEqual(item["dispatch_status"], "not_scored")
        self.assertIsNotNone(item["exclusion_reason"])
        d = self.client.get(f"/api/outages/{item['outage_id']}").json()
        self.assertIsNone(d["decomposition"])
        self.assertIsNone(d["priority_score"])

    def test_invalid_outage_id(self):
        self.assertEqual(self.client.get("/api/outages/does-not-exist").status_code, 404)
        self.assertEqual(self.client.post("/api/outages/does-not-exist/approve").status_code, 404)
        self.assertEqual(self.client.get("/api/map/buffers/does-not-exist").status_code, 404)

    # ---- filtering / pagination
    def test_filters(self):
        j = self.client.get("/api/outages?borough=Brooklyn&priority_tier=Medium&page_size=100").json()
        self.assertGreater(j["total_count"], 0)
        self.assertTrue(all(i["borough"] == "Brooklyn" and i["priority_tier"] == "Medium" for i in j["items"]))
        scores = [i["priority_score"] for i in j["items"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_search_by_id(self):
        top = str(self.plan[self.plan.optimization_rank == 1].outage_id.iloc[0])
        j = self.client.get(f"/api/outages?search={top}").json()
        self.assertIn(top, [i["outage_id"] for i in j["items"]])

    def test_pagination_bounds(self):
        self.assertEqual(self.client.get("/api/outages?page=0").status_code, 422)
        self.assertEqual(self.client.get("/api/outages?page_size=501").status_code, 422)
        j = self.client.get("/api/outages?page=999999").json()
        self.assertEqual(j["items"], [])
        self.assertEqual(self.client.get("/api/outages?priority_tier=Bogus").status_code, 422)

    def test_empty_filter_result(self):
        j = self.client.get("/api/outages?search=zzzz-no-such-street").json()
        self.assertEqual(j["total_count"], 0)
        self.assertEqual(j["items"], [])

    # ---- map
    def test_map_includes_all_recommended_and_valid_coordinates(self):
        j = self.client.get("/api/map/outages?limit=50").json()
        statuses = [f["properties"]["dispatch_status"] for f in j["features"]]
        self.assertEqual(statuses.count("recommended"), self.summary["n_selected"])
        for f in j["features"]:
            lon, lat = f["geometry"]["coordinates"]
            self.assertTrue(40 < lat < 41 and -75 < lon < -73)
        only = self.client.get("/api/map/outages?dispatch_status=deferred&limit=20").json()
        self.assertTrue(all(f["properties"]["dispatch_status"] == "deferred" for f in only["features"]))

    def test_map_limit_validation(self):
        self.assertEqual(self.client.get("/api/map/outages?limit=0").status_code, 422)
        self.assertEqual(self.client.get("/api/map/outages?limit=999999").status_code, 422)

    # ---- causal evidence
    def test_causal_values_from_csv(self):
        j = self.client.get("/api/causal/overview").json()
        csv = pd.read_csv(config.DISPLACEMENT_FILE).set_index("effect_name")
        self.assertAlmostEqual(j["periods"]["post"]["net"]["estimate"], round(csv.loc["net_post", "estimate"], 6))
        self.assertAlmostEqual(j["periods"]["post"]["displacement_proportion"],
                               round(csv.loc["displacement_proportion_post", "estimate"], 4))
        self.assertTrue(j["periods"]["post"]["net"]["ci_includes_zero"])
        self.assertFalse(j["periods"]["post"]["net"]["significant_at_5pct"])
        self.assertEqual(len(self.client.get("/api/causal/event-study").json()), 9)

    # ---- operator notes are session-local and validated
    def test_operator_note_roundtrip(self):
        top = str(self.plan[self.plan.optimization_rank == 1].outage_id.iloc[0])
        self.assertEqual(self.client.post(f"/api/outages/{top}/flag").json()["operator_note"], "Flagged")
        self.assertEqual(self.client.get(f"/api/outages/{top}").json()["operator_note"], "Flagged")
        self.assertEqual(self.client.post(f"/api/outages/{top}/reset").json()["operator_note"], "None")
        self.assertEqual(self.client.post(f"/api/outages/{top}/explode").status_code, 422)

    def test_overview_filter(self):
        a = self.client.get("/api/overview").json()
        b = self.client.get("/api/overview?borough=Queens").json()
        self.assertEqual(a["kpis"]["scored_outages"], a["priority_mix"]["total"])
        self.assertLess(b["kpis"]["scored_outages"], a["kpis"]["scored_outages"])
        self.assertIn("impact_note", a)

    def test_cors_preflight_allows_dev_origin_only(self):
        ok = self.client.options("/api/overview", headers={
            "Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
        self.assertEqual(ok.headers.get("access-control-allow-origin"), "http://localhost:5173")
        bad = self.client.options("/api/overview", headers={
            "Origin": "http://evil.example", "Access-Control-Request-Method": "GET"})
        self.assertNotEqual(bad.headers.get("access-control-allow-origin"), "http://evil.example")


class MissingDataTests(unittest.TestCase):
    """With no artifacts on disk the API must degrade, not crash or invent numbers."""

    def test_all_artifacts_missing(self):
        nowhere = Path(__file__).parent / "_does_not_exist"
        names = ["OUTAGES_SCORED_FILE", "PRIORITIZED_QUEUE_FILE", "FIFO_COMPARISON_FILE", "OPTIMAL_DISPATCH_FILE",
                 "OPTIMAL_DISPATCH_SUMMARY_FILE", "DID_SUMMARY_FILE", "EVENT_STUDY_FILE", "DISPLACEMENT_FILE"]
        patches = [mock.patch.object(config, n, nowhere / n) for n in names]
        for p in patches:
            p.start()
        try:
            store.load_all()
            with TestClient(app, raise_server_exceptions=True) as c:
                store.load_all()  # lifespan already ran; ensure it sees the patched paths
                self.assertEqual(c.get("/api/health").json()["status"], "degraded")
                self.assertEqual(c.get("/api/outages").json()["items"], [])
                self.assertEqual(c.get("/api/outages/123").status_code, 404)
                self.assertFalse(c.get("/api/optimization").json()["available"])
                self.assertFalse(c.get("/api/comparison").json()["available"])
                self.assertFalse(c.get("/api/causal/overview").json()["available"])
                self.assertEqual(c.get("/api/queue").json()["items"], [])
                self.assertEqual(c.get("/api/map/outages").json()["features"], [])
                self.assertEqual(c.get("/api/optimization/plan").json()["items"], [])
                ov = c.get("/api/overview")
                self.assertEqual(ov.status_code, 200)
                self.assertEqual(ov.json()["kpis"]["total_outages"], 0)
                self.assertEqual(c.get("/api/summary").json()["status"], "ARTIFACTS MISSING")
        finally:
            for p in patches:
                p.stop()
            store.load_all()


if __name__ == "__main__":
    unittest.main()
