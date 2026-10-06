"""
Stage 14: capacity / service-level analysis of the frozen FIFO dispatch.

Replaces the retired Stage 14 ILP (src/optimization/ilp_solver.py, kept
untouched for audit history), which maximised the retired Stage 12
priority_score read from outages_scored.parquet. No benefit score, crime
effect or Stage 11 coefficient exists, so this stage optimises nothing.

Question. Given the observed chronological streetlight-report arrivals and
a finite repair capacity, how does the dispatch system behave under the
frozen FIFO policy, and which tested capacity, if any, meets explicitly
stated service-level targets? Descriptive and conditional on the Stage 13
simulation assumptions; it makes no claim about crime.

Method. The frozen Stage 13 simulation (dispatch_simulation.simulate,
policy "fifo") is run unchanged for each K in the pre-specified
CAPACITY_GRID on the same immutable Stage 12 job set: same arrivals, daily
08:00 decisions, 1-day service, empty start, drain after the last arrival.
K is the only thing that varies. The regime split (2025-07-01) is the
frozen Stage 13 one, computed from arrivals and the baseline K only, and is
the same for every K. Service-level targets are evaluated after all runs and
are descriptive; no K is selected or tuned.

Inputs (read only): data/processed/stage12/jobs.parquet (job_id,
arrival_s, site_id, borough) and arrivals.parquet (period,
is_artifact_pre, created_date) for the frozen capacity rule and the daily
arrival diagnostics. The rejected activity_pct score input is not read
(the FIFO simulation receives it as NaN). No 311 closure, status or
resolution field is read.

Outputs: per-K job logs and backlog series in data/processed/stage14/;
tables and summary in outputs/stage14_capacity/.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.features import match_controls as s7  # noqa: E402
from src.features import operational_priority as s12  # noqa: E402
from src.models import dispatch_simulation as s13  # noqa: E402

LOGS_DIR = PROJECT_ROOT / "data" / "processed" / "stage14"
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "stage14_capacity"
CAPACITY_FILENAME = "capacity_metrics.csv"
REGIME_FILENAME = "capacity_regime_metrics.csv"
ARRIVAL_FILENAME = "arrival_rate_diagnostics.csv"
TARGETS_FILENAME = "service_level_targets.csv"
SUMMARY_FILENAME = "stage14_summary.json"

POLICY = s13.FROZEN_POLICY            # "fifo"; the only policy run
BASELINE_K = 65                       # frozen Stage 13 capacity (asserted against its rule)
CAPACITY_GRID = (55, 60, 65, 70, 75, 80)   # pre-specified; never tuned on results
# (name, threshold days, minimum % of jobs served within the threshold)
SERVICE_TARGETS = (("T7_95", 7, 95.0), ("T14_99", 14, 99.0), ("T30_99", 30, 99.0))
FROZEN_OVERLOAD_START = pd.Timestamp("2025-07-01")
REGIMES = ("all", "stable", "overload")

JOB_READ_COLUMNS = ("job_id", "arrival_s", "site_id", "borough")
ARRIVAL_READ_COLUMNS = ("created_date", "period", "is_artifact_pre")
# Schedule columns compared with the frozen Stage 13 FIFO log.
SCHEDULE_COLUMNS = ("job_id", "arrival_s", "site_id", "borough", "selected_s", "resolved_s")


# ============================================================
# Inputs
# ============================================================

def load_inputs(stage12_dir=s12.OUT_DIR):
    """Job set and arrival flags, reading only the columns this stage needs."""

    jobs = pd.read_parquet(stage12_dir / s12.JOBS_FILENAME, columns=list(JOB_READ_COLUMNS))
    arrivals = pd.read_parquet(stage12_dir / s12.ARRIVALS_FILENAME, columns=list(ARRIVAL_READ_COLUMNS))
    s12.assert_no_forbidden(jobs.columns, "Stage 14 jobs")
    s12.assert_no_forbidden(arrivals.columns, "Stage 14 arrivals")
    # The simulation's input frame carries this column; FIFO never reads it.
    jobs["activity_pct"] = np.nan
    return jobs, arrivals


def baseline_capacity(arrivals):
    k, rate = s13.capacity_from_calibration(arrivals)
    if k != BASELINE_K:
        raise AssertionError(f"frozen capacity rule gives K={k}, expected {BASELINE_K}")
    return k, rate


def regime_split_s(jobs):
    """Frozen Stage 13 split: arrivals and the baseline K only, same for every K."""

    split = s13.overload_start_s(jobs, BASELINE_K)
    if split != s13._s(FROZEN_OVERLOAD_START):
        raise AssertionError(f"regime split {pd.to_datetime(split, unit='s')} != {FROZEN_OVERLOAD_START}")
    return split


# ============================================================
# Simulation (frozen Stage 13 FIFO; capacity is the only input varied)
# ============================================================

def simulate_fifo(jobs, capacity):
    return s13.simulate(jobs, None, POLICY, int(capacity))


# ============================================================
# Metrics
# ============================================================

def capacity_metrics(log, series, capacity, split_s):
    """Stage 13 metric definitions plus completion and served counts."""

    m, _ = s13.metrics(s13.metric_view(log), series, capacity, split_s)
    served = log["selected_s"] >= 0
    last_decision = series.loc[series["selected"] > 0, "decision_s"].max()
    return {
        "capacity_k": int(capacity),
        **m,
        "n_served": int(served.sum()),
        "last_dispatch": str(pd.to_datetime(last_decision, unit="s")),
        "last_resolution": str(pd.to_datetime(log["resolved_s"].max(), unit="s")),
        "selections_total": int(series["selected"].sum()),
    }


def regime_rows(log, series, capacity, split_s):
    """
    Waiting metrics by arrival regime and queue metrics by decision-time
    window (stable: decisions before split_s; overload: from split_s to the
    end of the arrival horizon; drain days excluded, as in Stage 13).
    """

    wait = (log["selected_s"] - log["arrival_s"]) / s13.DAY_S
    stable = (log["arrival_s"] < split_s).to_numpy()
    horizon = series[series["decision_s"] < s13._s(s12.EVALUATION_END)]
    stable_t = (horizon["decision_s"] < split_s).to_numpy()
    rows = []
    for regime, jmask, tmask in (("all", np.ones(len(wait), bool), np.ones(len(horizon), bool)),
                                 ("stable", stable, stable_t), ("overload", ~stable, ~stable_t)):
        h = horizon[tmask]
        rows.append({
            "capacity_k": int(capacity), "regime": regime,
            **s13._wait_block(wait[jmask]),
            "decision_days": int(len(h)),
            "backlog_mean": float(h["waiting_after"].mean()),
            "backlog_max": int(h["waiting_after"].max()),
            "backlog_at_window_end": int(h["waiting_after"].iloc[-1]),
            "throughput_per_day": float(h["selected"].mean()),
            "utilisation": float(h["selected"].sum() / (capacity * len(h))),
        })
    return rows


def daily_arrivals(arrivals, jobs):
    """Calendar-day arrival counts of the job set over the horizon, zero days included."""

    days = pd.date_range(s12.EVALUATION_START, s12.EVALUATION_END - pd.Timedelta(days=1), freq="D")
    when = pd.to_datetime(jobs["arrival_s"], unit="s").dt.normalize()
    counts = when.value_counts().reindex(days, fill_value=0).sort_index()
    # Cross-check against the Stage 12 arrival table (evaluation, non-artifact).
    ev = arrivals[(arrivals["period"] == "evaluation") & ~arrivals["is_artifact_pre"]]
    if int(counts.sum()) != len(ev) or len(ev) != len(jobs):
        raise AssertionError("daily arrival counts do not match the job set")
    return counts


def arrival_diagnostics(counts, split_s):
    split = pd.to_datetime(split_s, unit="s")
    scopes = [("all", counts), ("stable", counts[counts.index < split]),
              ("overload", counts[counts.index >= split])]
    for q, c in counts.groupby(counts.index.to_period("Q")):
        scopes.append((f"quarter:{q}", c))
    rows = []
    for scope, c in scopes:
        row = {"scope": scope, "first_day": str(c.index.min().date()), "last_day": str(c.index.max().date()),
               "n_days": int(len(c)), "n_arrivals": int(c.sum()),
               "arrivals_per_day_mean": float(c.mean()), "arrivals_per_day_median": float(c.median()),
               "arrivals_per_day_p90": float(c.quantile(0.9)), "arrivals_per_day_max": int(c.max()),
               "arrivals_per_day_min": int(c.min())}
        for k in CAPACITY_GRID:
            row[f"pct_days_arrivals_gt_{k}"] = float(100 * (c > k).mean())
            row[f"mean_load_ratio_k{k}"] = float(c.mean() / k)
        rows.append(row)
    return pd.DataFrame(rows)


def service_targets(regime_table):
    rows = []
    for _, r in regime_table.iterrows():
        for name, days, minimum in SERVICE_TARGETS:
            achieved = float(r[f"served_within_{days}d_pct"])
            rows.append({"capacity_k": int(r["capacity_k"]), "regime": r["regime"], "target": name,
                         "threshold_days": days, "required_pct": minimum,
                         "achieved_pct": achieved, "meets_target": bool(achieved >= minimum)})
    return pd.DataFrame(rows)


def minimum_tested_k(targets):
    """Smallest tested K meeting each target per regime (None if none). Descriptive only."""

    out = {}
    for regime in REGIMES:
        out[regime] = {}
        for name, _, _ in SERVICE_TARGETS:
            t = targets[(targets["regime"] == regime) & (targets["target"] == name) & targets["meets_target"]]
            out[regime][name] = int(t["capacity_k"].min()) if len(t) else None
    return out


# ============================================================
# Run
# ============================================================

def _write_csv(frame, path):
    tmp = path.with_name(path.name + ".tmp")
    frame.to_csv(tmp, index=False)
    os.replace(tmp, path)


def run(stage12_dir=s12.OUT_DIR, logs_dir=LOGS_DIR, out_dir=OUTPUT_DIR, grid=CAPACITY_GRID):
    jobs, arrivals = load_inputs(stage12_dir)
    k0, rate = baseline_capacity(arrivals)
    split_s = regime_split_s(jobs)

    logs, series = {}, {}
    for k in grid:
        logs[k], series[k] = simulate_fifo(jobs, k)
    s13.assert_common_population({f"K={k}": logs[k] for k in grid}, jobs)

    # Metrics only after every simulation has finished.
    cap_rows, reg_rows, log_info = [], [], {}
    logs_dir.mkdir(parents=True, exist_ok=True)
    for k in grid:
        cap_rows.append(capacity_metrics(logs[k], series[k], k, split_s))
        reg_rows.extend(regime_rows(logs[k], series[k], k, split_s))
        log = logs[k][list(SCHEDULE_COLUMNS)]
        log.to_parquet(logs_dir / f"jobs_K{k}.parquet", index=False)
        series[k].to_parquet(logs_dir / f"backlog_K{k}.parquet", index=False)
        log_info[str(k)] = {"schedule_sha256": s13.frame_hash(log), "backlog_sha256": s13.frame_hash(series[k])}
        m = cap_rows[-1]
        print(f"K={k}: mean={m['wait_mean_days']:.2f} med={m['wait_median_days']:.2f} "
              f"p90={m['wait_p90_days']:.2f} max={m['wait_max_days']:.2f} "
              f"<=7d={m['served_within_7d_pct']:.1f}% backlog_max={m['backlog_max']} "
              f"drain={m['drain_days_after_horizon']}d")

    cap_table = pd.DataFrame(cap_rows)
    reg_table = pd.DataFrame(reg_rows)
    counts = daily_arrivals(arrivals, jobs)
    arr_table = arrival_diagnostics(counts, split_s)
    tgt_table = service_targets(reg_table)

    summary = {
        "stage": "14 capacity / service-level analysis (frozen FIFO)",
        "question": ("Given the observed chronological streetlight-report arrivals and a finite repair "
                     "capacity, how does dispatch behave under the frozen FIFO policy, and which tested "
                     "capacity, if any, meets the stated service-level targets?"),
        "not_claimed": ["crime reduction or crimes prevented", "causal repair benefit",
                        "an optimal crew allocation", "physical repair times"],
        "replaces": "the retired Stage 14 ILP (docs/stage14_optimization.md); not imported or read",
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "policy": POLICY,
        "assumptions": {
            "decision_cadence": f"daily at {s13.DECISION_HOUR:02d}:00",
            "service_days": s13.SERVICE_DAYS,
            "initial_queue": "empty at 2024-01-01",
            "horizon": [str(s12.EVALUATION_START.date()), str(s12.EVALUATION_END.date())],
            "drain": "decisions continue after the last arrival until the queue is empty",
            "baseline_capacity": k0,
            "baseline_capacity_rule": f"ceil(2023 daily non-artifact arrivals {rate:.4f} / {s13.TARGET_UTILISATION})",
        },
        "capacity_grid": list(grid),
        "capacity_grid_prespecified": list(CAPACITY_GRID),
        "service_targets": [{"name": n, "threshold_days": d, "required_pct": p} for n, d, p in SERVICE_TARGETS],
        "regime_split": {"overload_start": str(pd.to_datetime(split_s, unit="s")),
                         "rule": "frozen Stage 13: first calendar quarter whose mean daily arrivals exceed K=65; "
                                 "arrivals only; identical for every tested K"},
        "common_population": {"n_jobs": int(len(jobs)),
                              "stage12_jobs_file_sha256": s7._file_fingerprint(stage12_dir / s12.JOBS_FILENAME)["sha256"]},
        "minimum_tested_k_meeting_target": minimum_tested_k(tgt_table),
        "minimum_tested_k_note": ("descriptive, computed after all simulations; conditional on the 1-day service, "
                                  "daily cadence, empty start and drain assumptions; not an optimum"),
        "capacity_metrics": cap_rows,
        "logs": log_info,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    for frame, name in ((cap_table, CAPACITY_FILENAME), (reg_table, REGIME_FILENAME),
                        (arr_table, ARRIVAL_FILENAME), (tgt_table, TARGETS_FILENAME)):
        _write_csv(frame, out_dir / name)
    tmp = out_dir / (SUMMARY_FILENAME + ".tmp")
    tmp.write_text(json.dumps(summary, indent=2, allow_nan=False, default=str))
    os.replace(tmp, out_dir / SUMMARY_FILENAME)
    return cap_table, reg_table, arr_table, tgt_table, summary, logs, series


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Stage 14 capacity / service-level analysis (frozen FIFO)")
    parser.add_argument("--stage12-dir", type=Path, default=s12.OUT_DIR)
    parser.add_argument("--logs-dir", type=Path, default=LOGS_DIR)
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    run(args.stage12_dir, args.logs_dir, args.out)
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
