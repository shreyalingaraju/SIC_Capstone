"""
Robustness (D18/D19 grid, Step 5) validation.

Usage, from the repository root with the project venv:
    python scripts/validation/robustness_validate.py [RUN_DIR]

RUN_DIR is a finished grid, default outputs/robustness/d19_grid (create it
with `python src/models/robustness.py`). The script re-runs two
specifications into a scratch run id to test determinism (about 3-4 min) and
removes it afterwards. Expected last line: "FAILS: none".

Checks: the full expected grid exists once each; no duplicate
(spec, period, effect) rows; all specs ran; finite estimates and SEs for the
inferential effects; valid CIs; classifications and placebo labelling; the
primary_rerun equals the canonical Stage 11 output exactly; the canonical
panel hash is unchanged and equals the pre-run hash; primary outputs and
data/processed were not overwritten; a re-run is deterministic.
"""

import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from src.models import robustness as rb  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}: {name}{' - ' + detail if detail else ''}", flush=True)
    if not ok:
        FAILS.append(name)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else rb.ROBUSTNESS_DIR / rb.DEFAULT_RUN_ID
panel_before = sha(rb.CANONICAL_PANEL)
stage11_csv = Path("outputs/displacement_estimates.csv")
stage11_before = sha(stage11_csv)
processed_before = {p.name: p.stat().st_mtime_ns for p in rb.PROCESSED_DIR.iterdir()}

t = pd.read_csv(run_dir / rb.ESTIMATES_FILENAME)
summary = json.loads((run_dir / rb.SUMMARY_FILENAME).read_text(encoding="utf-8"))

check("all expected specifications present exactly", set(t.spec_id) == set(rb.SPEC_IDS), str(sorted(set(rb.SPEC_IDS) ^ set(t.spec_id))))
check("no duplicate (spec, period, effect) rows", not t.duplicated(["spec_id", "period", "effect"]).any())
check("8 rows per specification", (t.groupby("spec_id").size() == 8).all())
check("all specifications ran ok", all(m["status"] == "ok" for m in summary["specifications"]) and len(summary["specifications"]) == len(rb.SPEC_IDS))
check("one specification-id per metadata entry", len({m["spec_id"] for m in summary["specifications"]}) == len(summary["specifications"]))

inf = t[t.effect != "displacement_proportion"]
check("estimates finite", np.isfinite(t.estimate).all())
check("SEs finite and positive (inferential effects)", np.isfinite(inf.standard_error).all() and (inf.standard_error > 0).all())
check("CIs valid: lower <= estimate <= upper, = est +/- 1.96 SE",
      ((inf.ci_lower <= inf.estimate) & (inf.estimate <= inf.ci_upper)).all()
      and np.allclose(inf.ci_upper - inf.estimate, 1.959963984540054 * inf.standard_error))
prop = t[t.effect == "displacement_proportion"]
check("proportion rows carry no SE/CI", prop[["standard_error", "ci_lower", "ci_upper"]].isna().all().all())
check("net = direct + displacement in every spec/period",
      all(np.isclose(g.set_index("effect").estimate["net"],
                     g.set_index("effect").estimate["direct"] + g.set_index("effect").estimate["displacement"])
          for _, g in t.groupby(["spec_id", "period"])))

cls = t.groupby("spec_id").classification.first()
check("classifications correct",
      cls["primary_rerun"] == "primary" and cls["placebo_90"] == "placebo"
      and (cls.drop(["primary_rerun", "placebo_90"]) == "sensitivity").all())
placebo = t[(t.spec_id == "placebo_90") & (t.effect != "displacement_proportion")]
check("placebo rows labelled with a null expectation", placebo.placebo_expectation.str.startswith("null expected").all())
check("non-placebo rows have no placebo expectation", t[t.classification != "placebo"].placebo_expectation.isna().all())
check("sensitivity rows have diff_vs_primary, primary has none",
      t[t.classification == "sensitivity"].diff_vs_primary.notna().all() and t[t.classification == "primary"].diff_vs_primary.isna().all())

# primary reproduces the canonical Stage 11 output
canon = pd.read_csv(stage11_csv).set_index("effect_name").estimate
prim = t[t.spec_id == "primary_rerun"]
ok = all(np.isclose(r.estimate, canon[f"{r.effect}_{r.period}"], rtol=0, atol=1e-12) for r in prim.itertuples())
check("primary_rerun equals the canonical Stage 11 estimates", ok)
check("primary_rerun pairs file equals the canonical pairs file",
      next(m for m in summary["specifications"] if m["spec_id"] == "primary_rerun")["pairs_sha256"] == summary["canonical_pairs_sha256"])
check("grid recorded unchanged canonical panel hash",
      summary["canonical_panel_sha256_before"] == summary["canonical_panel_sha256_after"] == panel_before)

# determinism: re-run two specs into a scratch run id
scratch_id = "_validate_scratch"
shutil.rmtree(rb.ROBUSTNESS_DIR / scratch_id, ignore_errors=True)
check("scratch re-run succeeds", rb.main(["--run-id", scratch_id, "--only", "seed_101"]) == 0)
t2 = pd.read_csv(rb.ROBUSTNESS_DIR / scratch_id / rb.ESTIMATES_FILENAME)
for spec in ("primary_rerun", "seed_101"):
    a = t[t.spec_id == spec].drop(columns=["panel_sha256"]).reset_index(drop=True)
    b = t2[t2.spec_id == spec].drop(columns=["panel_sha256"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, check_exact=False, atol=1e-12, check_dtype=False)
    check(f"{spec}: re-run is identical", True)
    check(f"{spec}: panel sha256 identical",
          set(t[t.spec_id == spec].panel_sha256) == set(t2[t2.spec_id == spec].panel_sha256))
shutil.rmtree(rb.ROBUSTNESS_DIR / scratch_id, ignore_errors=True)

check("canonical panel hash unchanged", sha(rb.CANONICAL_PANEL) == panel_before)
check("Stage 11 primary CSV unchanged", sha(stage11_csv) == stage11_before)
check("data/processed files untouched",
      {p.name: p.stat().st_mtime_ns for p in rb.PROCESSED_DIR.iterdir()} == processed_before)

print("FAILS:", FAILS or "none")
sys.exit(1 if FAILS else 0)
