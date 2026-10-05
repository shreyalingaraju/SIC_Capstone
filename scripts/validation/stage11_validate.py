"""
Stage 11 (spatial displacement) validation.

Usage, from the repository root with the project venv:
    python scripts/validation/stage11_validate.py [PANEL]

PANEL defaults to data/processed/causal_panel.parquet. Output goes to a
temporary directory; outputs/ is not touched. Expected last line:
"FAILS: none".

Checks:
- unit tests of net effect, variance (with covariance), confidence interval
  and displacement proportion, including zero, positive and negative effects;
- the net variance equals Var(A) + Var(B) + 2 Cov(A, B) and the cluster-robust
  variance of the summed paired differences, and differs from the
  independence formula when the covariance is non-zero;
- the CSV exists, is finite, has non-negative variances and internally
  consistent intervals, and its net rows equal direct + displacement.
"""

import math
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from src.models import displacement_model as s11  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}: {name}{' - ' + detail if detail else ''}", flush=True)
    if not ok:
        FAILS.append(name)


def close(a, b, tol=1e-12):
    return math.isclose(a, b, rel_tol=tol, abs_tol=tol)


# --- net effect and variance -------------------------------------------
net, var = s11.combine_net(-0.5, 0.2, 0.04, 0.01, 0.005)
check("net = direct + displacement", close(net, -0.3))
check("net variance = Va + Vb + 2 Cov", close(var, 0.04 + 0.01 + 0.01))
net0, var0 = s11.combine_net(-0.5, 0.2, 0.04, 0.01, 0.0)
check("zero covariance reduces to Va + Vb", close(var0, 0.05))

# --- confidence interval ------------------------------------------------
lo, hi = s11.confidence_interval(1.0, 0.25)
check("CI = est +/- 1.96 SE", close(lo, 1 - 1.959963984540054 * 0.5) and close(hi, 1 + 1.959963984540054 * 0.5))
check("CI symmetric about the estimate", close((lo + hi) / 2, 1.0))
lo, hi = s11.confidence_interval(1.0, float("nan"))
check("CI is NaN for an unusable variance", math.isnan(lo) and math.isnan(hi))

# --- displacement proportion --------------------------------------------
p, v = s11.displacement_proportion(-1.0, 0.25, 0.01, 0.01, 0.0)
check("direct reduction, ring increase: proportion +0.25 (offset)", close(p, 0.25))
p, _ = s11.displacement_proportion(1.0, -0.25, 0.01, 0.01, 0.0)
check("direct increase, ring decrease: proportion +0.25 (offset)", close(p, 0.25))
p, _ = s11.displacement_proportion(-1.0, -0.5, 0.01, 0.01, 0.0)
check("same sign in the ring: proportion negative (diffusion)", close(p, -0.5))
p, _ = s11.displacement_proportion(-1.0, 1.5, 0.01, 0.01, 0.0)
check("over-displacement: proportion > 1", close(p, 1.5))
p, v = s11.displacement_proportion(0.0, 0.3, 0.01, 0.01, 0.0)
check("zero denominator gives NaN, not inf", math.isnan(p) and math.isnan(v))
p, _ = s11.displacement_proportion(-1.0, 0.0, 0.01, 0.01, 0.0)
check("zero displacement gives proportion 0", close(p, 0.0))
p, v = s11.displacement_proportion(-2.0, 1.0, 0.04, 0.01, 0.003)
# delta method by hand: g = (b/a^2, -1/a) = (0.25, 0.5)
check("delta-method variance", close(v, 0.25 ** 2 * 0.04 + 0.5 ** 2 * 0.01 + 2 * 0.25 * 0.5 * 0.003))

# --- cluster moments on synthetic data -----------------------------------
rng = np.random.default_rng(0)
clusters = np.repeat(np.arange(60), 5)
shared = rng.normal(size=60)[clusters]
x = shared + rng.normal(size=300)
y = 0.8 * shared + rng.normal(size=300)
means, cov, g = s11.cluster_moments(x, y, clusters)
n_, v_ = s11.combine_net(means[0], means[1], cov[0, 0], cov[1, 1], cov[0, 1])
_, cov_sum, _ = s11.cluster_moments(x + y, x + y, clusters)
check("net variance equals cluster variance of x + y", close(v_, cov_sum[0, 0], 1e-9))
check("covariance is non-zero for correlated clusters", abs(cov[0, 1]) > 0)
check("independence formula differs when covariance is non-zero",
      not close(cov[0, 0] + cov[1, 1], v_, 1e-6))

# --- output on the canonical panel --------------------------------------
panel = Path(sys.argv[1]) if len(sys.argv) > 1 else s11.CAUSAL_FILE
out = Path(tempfile.mkdtemp(prefix="s11_"))
check("CLI runs", s11.main(["--panel", str(panel), "--out", str(out)]) == 0)
csv = out / s11.OUTPUT_FILENAME
check("CSV exists", csv.exists())
t = pd.read_csv(csv)
required = ["effect_name", "estimate", "standard_error", "variance", "ci_lower",
            "ci_upper", "n_observations", "n_units", "model", "specification",
            "outcome_ring", "sign_convention"]
check("required columns present", all(c in t.columns for c in required))
check("one row per effect and period", len(t) == 8 and t["effect_name"].is_unique)
prop = t["effect_name"].str.startswith("displacement_proportion")
check("proportion rows: point estimate only, no SE/CI",
      prop.sum() == 2 and t.loc[prop, ["standard_error", "variance", "ci_lower", "ci_upper"]].isna().all().all()
      and t.loc[prop, "estimate"].notna().all())
t_all = t
t = t[~prop]
check("variances non-negative", (t["variance"] >= 0).all())
check("SE = sqrt(variance)", np.allclose(t["standard_error"], np.sqrt(t["variance"])))
check("CI = estimate +/- 1.96 SE", np.allclose(t["ci_lower"], t["estimate"] - s11.Z_95 * t["standard_error"])
      and np.allclose(t["ci_upper"], t["estimate"] + s11.Z_95 * t["standard_error"]))
check("ci_lower <= estimate <= ci_upper", ((t["ci_lower"] <= t["estimate"]) & (t["estimate"] <= t["ci_upper"])).all())
check("all numeric fields finite (non-proportion rows)", np.isfinite(t[["estimate", "standard_error", "variance", "ci_lower", "ci_upper"]].to_numpy()).all())
t = t_all
for period in s11.PERIODS:
    r = t.set_index("effect_name")["estimate"]
    check(f"net_{period} = direct + displacement", close(r[f"net_{period}"], r[f"direct_{period}"] + r[f"displacement_{period}"], 1e-9))
    check(f"proportion_{period} = -displacement / direct",
          close(r[f"displacement_proportion_{period}"], -r[f"displacement_{period}"] / r[f"direct_{period}"], 1e-9))
check("direct and displacement share one specification",
      t.groupby(t["effect_name"].str.split("_").str[-1])["specification"].nunique().eq(1).all())

print(f"\nscratch: {out}")
print("FAILS:", FAILS or "none")
sys.exit(1 if FAILS else 0)
