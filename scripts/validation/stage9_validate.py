"""
Stage 9 (DiD) validation for the Issue 4 panel.

Usage, from the repository root with the project venv:
    python scripts/validation/stage9_validate.py PANEL [PAIRS_FILE] [EXTRA_PANEL ...]

PANEL is a Stage 8 causal_panel.parquet; PAIRS_FILE the Stage 7 pairs
it was built from (for the dataset statistics); EXTRA_PANELs (placebo,
shifted pre-window) are only checked to run. Outputs go to a temporary
directory; outputs/ is not touched. Runtime about 2-3 minutes. Expected
last line: "FAILS: none".

Checks:
- the unit_id within-transformation FE equals a least-squares dummy
  variable (LSDV) regression with C(unit_id) + C(period) on a 400-pair
  subsample;
- the D20 paired mean equals the FE coefficient (all 4 terms) and a
  hand-computed mean of dd from the panel;
- the clustered SE of the paired mean equals the textbook CR1 formula
  sqrt(G/(G-1) * sum_g (sum_i e_i)^2) / n with G treatment_h3_res7 cells;
- dataset statistics and cluster counts match the panel/pairs;
- terciles and years partition the pairs; strict JSON; two runs give an
  identical summary; placebo / shifted panels run.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

sys.path.insert(0, "src/models")
import did_model as s9  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}: {name}{' - ' + detail if detail else ''}", flush=True)
    if not ok:
        FAILS.append(name)


def run(panel, out):
    proc = subprocess.run(
        [sys.executable, "src/models/did_model.py", "--panel", str(panel), "--out", str(out)],
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


panel_path = Path(sys.argv[1])
pairs_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None
extra = [Path(p) for p in sys.argv[3:]]
scratch = Path(tempfile.mkdtemp(prefix="s9_"))

code, out = run(panel_path, scratch / "a")
check("Stage 9 runs (exit 0)", code == 0, out.strip().splitlines()[-1][:80] if out.strip() else "")
text = (scratch / "a" / s9.SUMMARY_FILENAME).read_text(encoding="utf-8")
summary = json.loads(text, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
check("summary is strict JSON", True)

df = s9.load_panel(panel_path)

# LSDV equality on a subsample.
rng = np.random.default_rng(20260929)
keep = rng.choice(df.pair_id.unique(), 400, replace=False)
sub = df[df.pair_id.isin(keep)].copy()
ok = True
for outcome in s9.OUTCOMES:
    groups = pd.factorize(sub.treatment_h3_res7)[0]
    fe = s9.fit_fe_model(sub, outcome, groups)
    lsdv = smf.ols(f"{outcome} ~ treatment_x_during + treatment_x_post_period + C(unit_id) + C(period)",
                   data=sub).fit()
    for term in s9.PERIOD_TERMS:
        ok &= abs(fe.params[term] - lsdv.params[term]) < 1e-9
check("within-transformation FE == LSDV with C(unit_id) + C(period) (400 pairs)", bool(ok))

# Paired mean vs FE and hand computation.
pdf = s9.paired_differences(df)
ok = True
for outcome in s9.OUTCOMES:
    wide = df.pivot_table(index="pair_id", columns=["role", "period"], values=outcome, aggfunc="first")
    for period in ("during", "post"):
        hand = ((wide[("T", period)] - wide[("T", "pre")]) - (wide[("C", period)] - wide[("C", "pre")])).mean()
        reported = summary["paired_difference"]["overall"][f"{outcome}_dd_{period}"]["estimate"]
        term = "treatment_x_during" if period == "during" else "treatment_x_post_period"
        fe = summary["fixed_effects_did"][outcome][period]["coefficient"]
        ok &= abs(hand - reported) < 1e-12 and abs(fe - reported) < 1e-9
check("paired mean == hand-computed mean == FE coefficient (4 terms)", bool(ok))

# CR1 clustered SE of the paired mean.
ok = True
for column in ("crime_100m_dd_during", "crime_250m_dd_post"):
    x = pdf[column].to_numpy(dtype=float)
    g = pdf["cluster"].to_numpy()
    n, G = len(x), len(np.unique(g))
    e = x - x.mean()
    sums = pd.Series(e).groupby(g).sum().to_numpy()
    se = np.sqrt(G / (G - 1) * (n - 1) / (n - 1) * np.sum(sums ** 2)) / n
    reported = summary["paired_difference"]["overall"][column]["std_error"]
    ok &= abs(se - reported) / se < 1e-9
check("paired-mean SE == CR1 formula with treatment_h3_res7 clusters", bool(ok))

# Dataset statistics.
ds = summary["dataset"]
ok = (ds["rows"] == len(df) and ds["unique_units"] == df.unit_id.nunique()
      and ds["n_clusters"]["treatment_h3_res7"] == df.treatment_h3_res7.nunique()
      and ds["n_clusters"]["pair_id"] == df.pair_id.nunique()
      and ds["cluster_variable"] == "treatment_h3_res7" and ds["fixed_effects_unit"] == "unit_id"
      and ds["n_clusters"]["treatment_h3_res7"] >= s9.MIN_CLUSTERS)
if pairs_path is not None:
    pairs = pd.read_parquet(pairs_path, columns=["control_site_id"])
    uses = pairs.control_site_id.value_counts()
    ok &= ds["unique_control_sites"] == len(uses) and ds["control_reuse_max"] == int(uses.max())
check("dataset statistics and cluster counts", bool(ok), str(ds["n_clusters"]))

pdiff = summary["paired_difference"]
n = len(pdf)
col = "crime_100m_dd_during"
check("terciles and years partition the pairs",
      sum(v[col]["n_pairs"] for v in pdiff["by_baseline_tercile"].values()) == n
      and sum(v[col]["n_pairs"] for v in pdiff["by_year"].values()) == n,
      f"terciles {[v[col]['n_pairs'] for v in pdiff['by_baseline_tercile'].values()]}")
check("robustness schemes present with SEs",
      set(summary["robustness_clustering"]) == {"treatment_h3_res7", "pair_id",
                                                "twoway_treatment_control_h3_res7", "treatment_police_precinct"}
      and all(v["crime_100m"]["fe_during"]["std_error"] > 0 for v in summary["robustness_clustering"].values()))
check("primary robustness entry equals the main FE result",
      summary["robustness_clustering"]["treatment_h3_res7"]["crime_100m"]["fe_during"]
      == summary["fixed_effects_did"]["crime_100m"]["during"])

code, _ = run(panel_path, scratch / "b")
check("two runs give an identical summary",
      code == 0 and (scratch / "b" / s9.SUMMARY_FILENAME).read_text(encoding="utf-8") == text)

for panel in extra:
    code, out = run(panel, scratch / panel.stem)
    check(f"Stage 9 runs on {panel.name}", code == 0, out.strip().splitlines()[-1][:60] if out.strip() else "")

print(f"\nscratch: {scratch}")
print("FAILS:", FAILS or "none")
