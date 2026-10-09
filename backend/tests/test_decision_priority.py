"""
Unit tests for the decision layer (src/features/decision_priority.py). Run from the repository root:

    .venv\\Scripts\\python.exe -m unittest backend.tests.test_decision_priority -v
"""
import unittest

import numpy as np
import pandas as pd

from src.features import decision_priority as dp


def reference(n=500, seed=1):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "predicted_risk": rng.gamma(2, 0.05, n), "population": rng.lognormal(11, 0.8, n),
        "vulnerable_pop_share": rng.uniform(0.1, 0.4, n), "prior_complaints_90d_50m": rng.poisson(0.3, n),
        "dist_depot_km": rng.uniform(0.5, 12, n),
    })


def row(**kw):
    base = dict(predicted_risk=0.10, population=60000, vulnerable_pop_share=0.25, prior_complaints_90d_50m=0, dist_depot_km=5.0)
    base.update(kw)
    return pd.DataFrame([base])


class DecisionPriorityTests(unittest.TestCase):
    def setUp(self):
        self.ref = reference()

    def test_more_population_raises_priority_other_factors_equal(self):
        lo, _ = dp.score(row(population=10000), self.ref, 0.4)
        hi, _ = dp.score(row(population=200000), self.ref, 0.4)
        self.assertGreater(hi.iloc[0], lo.iloc[0])

    def test_low_population_can_outrank_high_population(self):
        small = row(population=15000, predicted_risk=0.40, vulnerable_pop_share=0.38, prior_complaints_90d_50m=3, dist_depot_km=1.0)
        large = row(population=250000, predicted_risk=0.02, vulnerable_pop_share=0.12, prior_complaints_90d_50m=0, dist_depot_km=11.0)
        s_small, _ = dp.score(small, self.ref, 1.0)
        s_large, _ = dp.score(large, self.ref, 1.0)
        self.assertGreater(s_small.iloc[0], s_large.iloc[0])

    def test_score_independent_of_batch_with_fixed_reference(self):
        a, _ = dp.score(row(), self.ref, 0.4)
        b, _ = dp.score(pd.concat([row(), row(population=1e6)], ignore_index=True), self.ref, 0.4)
        self.assertAlmostEqual(a.iloc[0], b.iloc[0])

    def test_contributions_add_up_to_score(self):
        total, contrib = dp.score(reference(50, 3), self.ref, 0.4)
        np.testing.assert_allclose(contrib.sum(axis=1), total)

    def test_missing_inputs_do_not_crash_and_are_not_zero_imputed(self):
        full, _ = dp.score(row(), self.ref, 1.0)
        miss, contrib = dp.score(row(vulnerable_pop_share=np.nan), self.ref, 1.0)
        self.assertTrue(np.isfinite(miss.iloc[0]))
        self.assertEqual(contrib["vulnerable"].iloc[0], 0.0)
        everything_missing, _ = dp.score(row(predicted_risk=np.nan, population=np.nan, vulnerable_pop_share=np.nan,
                                             prior_complaints_90d_50m=np.nan, dist_depot_km=np.nan), self.ref, 1.0)
        self.assertTrue(np.isnan(everything_missing.iloc[0]))     # explicit NaN, never a fake number

    def test_deterministic_and_finite(self):
        f = reference(200, 9)
        a, _ = dp.score(f, self.ref, 0.38)
        b, _ = dp.score(f, self.ref, 0.38)
        pd.testing.assert_series_equal(a, b)
        self.assertTrue(np.isfinite(a).all())
        self.assertTrue(((a >= 0) & (a <= 100)).all())

    def test_causal_weight_gates_risk_component(self):
        self.assertEqual(dp.causal_weight(0.05, -0.02, 0.12, 0.01, 0.06), 0.0)      # interval includes zero
        self.assertEqual(dp.causal_weight(-0.1, -0.2, -0.01, -0.01, 0.06), 0.0)     # wrong sign
        w = dp.causal_weight(0.17, 0.07, 0.27, 0.02, 0.06)
        self.assertAlmostEqual(w, 1 / 3, places=6)
        hi_risk, _ = dp.score(row(predicted_risk=0.5), self.ref, w)
        lo_risk, _ = dp.score(row(predicted_risk=0.01), self.ref, w)
        gap_with = hi_risk.iloc[0] - lo_risk.iloc[0]
        hi0, _ = dp.score(row(predicted_risk=0.5), self.ref, 0.0)
        lo0, _ = dp.score(row(predicted_risk=0.01), self.ref, 0.0)
        self.assertGreater(gap_with, 0)
        self.assertAlmostEqual(hi0.iloc[0], lo0.iloc[0])          # no causal evidence -> crime risk does not move the score

    def test_no_outcome_columns_are_used(self):
        for col in dp.SOURCE.values():
            for banned in ("duration", "closed", "dark_night", "observed"):
                self.assertNotIn(banned, col)

    def test_sensitivity_runs(self):
        f = reference(300, 5)
        s = dp.sensitivity(f, f, 0.4, n_draws=5, top_n=30)
        self.assertGreater(s["random_spearman_min"], 0.5)


if __name__ == "__main__":
    unittest.main()
