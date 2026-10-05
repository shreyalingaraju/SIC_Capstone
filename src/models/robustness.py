"""
Step 5 (deferred in Q6): the D18 / D19 robustness, sensitivity and placebo grid.

Runs the accepted pipeline Stage 7 -> Stage 8 -> Stage 9/11 estimators once
per specification and writes everything to outputs/robustness/<run_id>/
(git-ignored, Q8). It never writes to data/processed/ or to the primary
Stage 8-11 outputs, and it changes no methodology: every run is the accepted
code invoked with exactly one D19 parameter (or the D18 placebo shift)
changed. Stage 7 and Stage 8 run as their own CLIs; the estimates come from
src/models/displacement_model.run, which uses the Stage 9 paired-difference /
two-way FE estimator, CR1 SEs clustered on treatment_h3_res7, and the Stage 11
net-effect definition (tau_net = tau_direct + tau_displacement).

Specifications (docs/issue4_design.md section 3.8, decisions D18 and D19;
one change at a time):

  primary_rerun       all defaults (reproduces the canonical run)
  exclusion_250/500   --exclusion-radius-m 250 / 500
  band_max_2000       --match-band-max-m 2000
  caliper_0.2         --caliper-sd 0.2
  reuse_never         --reuse-policy never
  report_lag_0        --report-lag-days 0       ("pre-buffer 0")
  duration_50/720     --imputed-duration-hours 50 / 720
  control_pre_only    --control-selection pre_only   ("ITT controls", M4)
  seed_101/202/303/404  --match-seed (the default 42 is primary_rerun)
  pre_window_shifted  Stage 8 --pre-window shifted on the canonical pairs
  placebo_90          --placebo-shift-days 90 (D18 placebo)

Classification: "primary" (reproduction), "sensitivity", "placebo". A
placebo estimate is NOT a causal effect: dates are moved 90 days earlier, so
the expected result is a null; the output records whether each placebo
interval contains zero. Sensitivity rows record the difference from the
primary estimate and whether the sign agrees.

Usage (repository root):
    python src/models/robustness.py [--run-id ID] [--only SPEC ...] [--keep-intermediates]
"""

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.models import displacement_model as s11  # noqa: E402

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
ROBUSTNESS_DIR = PROJECT_ROOT / "outputs" / "robustness"
CANONICAL_PANEL = PROCESSED_DIR / "causal_panel.parquet"
CANONICAL_PAIRS = PROCESSED_DIR / "control_area_pairs.parquet"

STAGE7 = PROJECT_ROOT / "src" / "features" / "match_controls.py"
STAGE8 = PROJECT_ROOT / "src" / "features" / "build_causal_panel.py"

ESTIMATES_FILENAME = "robustness_estimates.csv"
SUMMARY_FILENAME = "robustness_summary.json"
DEFAULT_RUN_ID = "d19_grid"

# (spec_id, classification, parameter, value, stage 7 CLI args, stage 8 pre-window)
SPECIFICATIONS = (
    ("primary_rerun", "primary", "none", "defaults", [], "canonical"),
    ("exclusion_250", "sensitivity", "exclusion_radius_m", 250, ["--exclusion-radius-m", "250"], "canonical"),
    ("exclusion_500", "sensitivity", "exclusion_radius_m", 500, ["--exclusion-radius-m", "500"], "canonical"),
    ("band_max_2000", "sensitivity", "match_band_max_m", 2000, ["--match-band-max-m", "2000"], "canonical"),
    ("caliper_0.2", "sensitivity", "caliper_sd", 0.2, ["--caliper-sd", "0.2"], "canonical"),
    ("reuse_never", "sensitivity", "reuse_policy", "never", ["--reuse-policy", "never"], "canonical"),
    ("report_lag_0", "sensitivity", "report_lag_days", 0, ["--report-lag-days", "0"], "canonical"),
    ("duration_50", "sensitivity", "imputed_duration_hours", 50, ["--imputed-duration-hours", "50"], "canonical"),
    ("duration_720", "sensitivity", "imputed_duration_hours", 720, ["--imputed-duration-hours", "720"], "canonical"),
    ("control_pre_only", "sensitivity", "control_selection", "pre_only", ["--control-selection", "pre_only"], "canonical"),
    ("seed_101", "sensitivity", "match_seed", 101, ["--match-seed", "101"], "canonical"),
    ("seed_202", "sensitivity", "match_seed", 202, ["--match-seed", "202"], "canonical"),
    ("seed_303", "sensitivity", "match_seed", 303, ["--match-seed", "303"], "canonical"),
    ("seed_404", "sensitivity", "match_seed", 404, ["--match-seed", "404"], "canonical"),
    ("pre_window_shifted", "sensitivity", "pre_window", "shifted", None, "shifted"),
    ("placebo_90", "placebo", "placebo_shift_days", 90, ["--placebo-shift-days", "90"], "canonical"),
)

SPEC_IDS = tuple(spec[0] for spec in SPECIFICATIONS)

# Only the effect rows with inference; the proportion is a point estimate.
EFFECTS = ("direct", "displacement", "net", "displacement_proportion")

COLUMNS = [
    "spec_id", "classification", "parameter", "value", "status", "period",
    "effect", "outcome_ring", "estimate", "standard_error", "ci_lower",
    "ci_upper", "n_observations", "n_units", "n_pairs", "n_clusters",
    "estimator", "clustering", "diff_vs_primary", "same_sign_as_primary",
    "ci_contains_zero", "placebo_expectation", "panel_sha256", "note",
]

ESTIMATOR = "Stage 9 paired-difference DiD (= two-way FE by unit_id), Stage 11 net = direct + displacement"
CLUSTERING = f"CR1 on {s11.CLUSTER}"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(2 ** 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(command, log_path):
    proc = subprocess.run(command, capture_output=True, text=True, cwd=PROJECT_ROOT)
    log_path.write_text(proc.stdout + proc.stderr, encoding="utf-8")
    return proc.returncode, (proc.stdout + proc.stderr).strip().splitlines()[-1:]


def run_spec(spec, run_dir, keep):
    """Run one specification; returns (estimate table or None, metadata)."""

    spec_id, classification, parameter, value, stage7_args, pre_window = spec
    out = run_dir / spec_id
    out.mkdir(parents=True, exist_ok=True)
    meta = {"spec_id": spec_id, "classification": classification,
            "parameter": parameter, "value": value, "status": "ok", "note": ""}
    started = time.time()

    if stage7_args is None:
        pairs = CANONICAL_PAIRS  # read-only: Stage 8 shifted pre-window on the canonical pairs
    else:
        code, tail = _run(
            [sys.executable, str(STAGE7), *stage7_args, "--out-dir", str(out)],
            out / "stage7.log",
        )
        if code != 0:
            meta.update(status="failed_stage7", note=" ".join(tail)[:300])
            return None, meta
        pairs = out / "control_area_pairs.parquet"

    panel = out / "causal_panel.parquet"
    code, tail = _run(
        [sys.executable, str(STAGE8), "--pairs", str(pairs), "--out", str(panel),
         "--pre-window", pre_window],
        out / "stage8.log",
    )
    if code != 0:
        meta.update(status="failed_stage8", note=" ".join(tail)[:300])
        return None, meta

    table, _, _ = s11.run(panel)
    meta["panel_sha256"] = sha256(panel)
    meta["pairs_sha256"] = sha256(pairs)
    meta["seconds"] = round(time.time() - started, 1)
    if not keep:
        panel.unlink()
    return table, meta


def long_rows(table, meta):
    rows = []
    for _, r in table.iterrows():
        name, period = r["effect_name"].rsplit("_", 1)
        rows.append({
            "spec_id": meta["spec_id"], "classification": meta["classification"],
            "parameter": meta["parameter"], "value": meta["value"],
            "status": meta["status"], "period": period, "effect": name,
            "outcome_ring": r["outcome_ring"], "estimate": r["estimate"],
            "standard_error": r["standard_error"], "ci_lower": r["ci_lower"],
            "ci_upper": r["ci_upper"], "n_observations": r["n_observations"],
            "n_units": r["n_units"], "n_pairs": r["n_pairs"],
            "n_clusters": r["n_clusters"], "estimator": ESTIMATOR,
            "clustering": CLUSTERING, "panel_sha256": meta.get("panel_sha256", ""),
            "note": r["note"] if name == "displacement_proportion" else "",
        })
    return rows


def annotate(frame):
    """diff vs primary_rerun, sign agreement, CI-contains-zero, placebo expectation."""

    primary = (frame[frame["spec_id"] == "primary_rerun"]
               .set_index(["period", "effect"])["estimate"])

    def diff(row):
        key = (row["period"], row["effect"])
        if row["classification"] == "primary" or key not in primary.index:
            return float("nan")
        return row["estimate"] - primary[key]

    frame["diff_vs_primary"] = frame.apply(diff, axis=1)

    def same_sign(row):
        key = (row["period"], row["effect"])
        if row["classification"] != "sensitivity" or key not in primary.index:
            return ""
        return bool(math.copysign(1, row["estimate"]) == math.copysign(1, primary[key]))

    frame["same_sign_as_primary"] = pd.Series(
        [same_sign(r) for _, r in frame.iterrows()], dtype=object, index=frame.index)
    inferential = frame["ci_lower"].notna()
    contains = (frame["ci_lower"] <= 0) & (frame["ci_upper"] >= 0)
    frame["ci_contains_zero"] = pd.Series(
        [bool(c) if i else "" for c, i in zip(contains, inferential)], dtype=object)
    messages = {True: "null expected; consistent (CI contains 0)",
                False: "null expected; NOT consistent (CI excludes 0)"}
    frame["placebo_expectation"] = pd.Series(
        [messages[bool(c)] if (i and k == "placebo") else ""
         for c, i, k in zip(contains, inferential, frame["classification"])], dtype=object)
    return frame[COLUMNS]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="D18/D19 robustness grid (Step 5)")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID,
                        help="output goes to outputs/robustness/<run-id>/")
    parser.add_argument("--only", nargs="+", choices=SPEC_IDS,
                        help="run only these specifications (primary_rerun is always added)")
    parser.add_argument("--keep-intermediates", action="store_true",
                        help="keep each specification's causal_panel.parquet")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if Path(args.run_id).name != args.run_id or args.run_id in ("", ".", ".."):
        raise ValueError("--run-id must be a plain directory name")
    run_dir = ROBUSTNESS_DIR / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    wanted = set(args.only or SPEC_IDS) | {"primary_rerun"}
    panel_hash_before = sha256(CANONICAL_PANEL)

    rows, metas = [], []
    for spec in SPECIFICATIONS:
        if spec[0] not in wanted:
            continue
        print(f"[{spec[0]}] running...", flush=True)
        table, meta = run_spec(spec, run_dir, args.keep_intermediates)
        metas.append(meta)
        if table is None:
            print(f"[{spec[0]}] {meta['status']}: {meta['note']}", flush=True)
            continue
        rows.extend(long_rows(table, meta))

    frame = annotate(pd.DataFrame(rows))
    frame.to_csv(run_dir / ESTIMATES_FILENAME, index=False)

    panel_hash_after = sha256(CANONICAL_PANEL)
    summary = {
        "classification_note": (
            "primary = reproduction with defaults; sensitivity = one D19 "
            "parameter changed; placebo = D18 shifted dates, expected null, "
            "not a causal estimate"),
        "canonical_panel_sha256_before": panel_hash_before,
        "canonical_panel_sha256_after": panel_hash_after,
        "canonical_pairs_sha256": sha256(CANONICAL_PAIRS),
        "specifications": metas,
    }
    (run_dir / SUMMARY_FILENAME).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if panel_hash_before != panel_hash_after:
        raise RuntimeError("canonical causal panel changed during the robustness run")

    print(f"\nRobustness estimates:\n{run_dir / ESTIMATES_FILENAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
