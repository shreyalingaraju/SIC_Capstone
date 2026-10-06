"""
Stage 13: chronological rolling dispatch simulation (FIFO vs operational priority).

Decision-support for prioritizing unresolved reported streetlight outages
using operational need and exposure features. The simulation compares
queue disciplines on waiting-time metrics only; it makes no claim about
crime, crime prevention or causal repair benefit.

Reads data/processed/stage12/ (arrivals, the immutable job set, calibration).
Writes job logs to data/processed/stage13/ and metrics to
outputs/stage13_dispatch/.

Common evaluation population. The job set is built in Stage 12 before any
policy runs: one job per evaluation-period complaint at a non-artifact site
(job_id = unique_key, arrival = created_date). Jobs are never merged, so
every policy receives exactly the same job ids, arrival times and sites
(asserted).

Simulation assumptions (labelled; none is inferred from 311 closure dates,
which are never loaded):
- Decisions: daily at 08:00. A job is known once its arrival is strictly
  before the decision time; later arrivals are not.
- Capacity: K jobs per decision, K = ceil(2023 daily non-artifact
  complaint arrivals / TARGET_UTILISATION), fixed from the calibration year.
  The rule is in complaint units, which are now also the job units.
- Service: a selected job leaves the queue at selection and is logged as
  resolved SERVICE_DAYS later. Resolution does not change any other job.
- The queue starts empty on 2024-01-01; arrivals stop at the end of the
  evaluation period; decisions continue until the queue drains.
Policies (identical jobs, capacity, service time and horizon):
- fifo (FROZEN operational baseline): earliest arrival first, ties by job
  id. Uses no score input.
- Audited and rejected Stage 12 score policies (POLICY_SPECS): 30-day guard
  first (oldest first), then a pre-specified weighted score of A (age), C
  (local activity), R (repeats) descending, then arrival, job id:
  age_only (A), age_c ((A + C) / 2), age_r ((A + R_prior) / 2) and
  operational ((A + C + R) / 3). See docs/stage12_13_operational_dispatch.md.
Metrics use only job id, arrival, selection time and borough.
"""

import argparse
import hashlib
import json
import math
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

JOBS_DIR = PROJECT_ROOT / "data" / "processed" / "stage13"
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "stage13_dispatch"
METRICS_FILENAME = "dispatch_metrics.csv"
BOROUGH_FILENAME = "dispatch_by_borough.csv"
SUMMARY_FILENAME = "dispatch_summary.json"

DAY_S = s7.SECONDS_PER_DAY
DECISION_HOUR = 8
SERVICE_DAYS = 1
TARGET_UTILISATION = 0.9
MAX_DRAIN_DAYS = 3 * 365
# Pre-specified Stage 12 score audit (no weight tuning). None = FIFO (no score).
# r: "current" = repeats in [arrival - 30 d, t); "prior" = [arrival - 30 d, arrival).
POLICY_SPECS = {
    "fifo": None,
    "age_only": {"weights": (1.0, 0.0, 0.0), "r": None},
    "age_c": {"weights": (0.5, 0.5, 0.0), "r": None},
    "age_r": {"weights": (0.5, 0.0, 0.5), "r": "prior"},
    "operational": {"weights": s12.WEIGHTS, "r": "current"},
}
POLICIES = tuple(POLICY_SPECS)
# Stage 12 score audit decision (option C): no score component met the
# retention criterion, so FIFO is the frozen operational baseline. The scored
# policies are kept only as the audited, rejected comparison (age_only is
# schedule-identical to FIFO).
FROZEN_POLICY = "fifo"
POLICY_STATUS = {p: ("frozen" if p == FROZEN_POLICY else "audited_rejected") for p in POLICIES}
THRESHOLDS_DAYS = (7, 14, 30)

# Immutable job identity: identical across policies (Check H).
IDENTITY_COLUMNS = ("job_id", "arrival_s", "site_id")
# Metric inputs may contain only these columns (no score or score components).
METRIC_COLUMNS = ("job_id", "arrival_s", "selected_s", "borough")


def _s(ts):
    return int(pd.Timestamp(ts).value // 10**9)


def first_decision_s():
    return _s(s12.EVALUATION_START) + DECISION_HOUR * 3600


# ============================================================
# Inputs and capacity
# ============================================================

def load_inputs(stage12_dir=s12.OUT_DIR):
    arrivals = pd.read_parquet(stage12_dir / s12.ARRIVALS_FILENAME)
    jobs = pd.read_parquet(stage12_dir / s12.JOBS_FILENAME)
    s12.assert_no_forbidden(arrivals.columns, "Stage 12 arrivals")
    s12.assert_no_forbidden(jobs.columns, "Stage 12 jobs")
    if tuple(jobs.columns) != s12.JOB_COLUMNS:
        raise AssertionError(f"unexpected job columns {list(jobs.columns)}")
    calibration = json.loads((stage12_dir / s12.CALIBRATION_FILENAME).read_text())
    return arrivals, jobs, calibration


def capacity_from_calibration(arrivals):
    cal = arrivals[(arrivals["period"] == "calibration") & ~arrivals["is_artifact_pre"]]
    days = (s12.EVALUATION_START - s12.CALIBRATION_START).days
    rate = len(cal) / days
    return int(math.ceil(rate / TARGET_UTILISATION)), rate


# ============================================================
# Policies
# ============================================================

def fifo_order(arrival_s, job_id):
    """Earliest arrival first; ties by job id. Takes no score input."""

    return np.lexsort((job_id, arrival_s))


def operational_order(t_s, arrival_s, job_id, activity_pct, repeats, weights=s12.WEIGHTS):
    """30-day guard (oldest first), then the weighted score descending."""

    age_days = (t_s - arrival_s) / DAY_S
    score = s12.weighted_score(age_days, activity_pct, repeats, weights)
    guard = age_days >= s12.GUARD_DAYS
    # lexsort: the last key is primary.
    order = np.lexsort((job_id, arrival_s, -np.where(guard, 0.0, score),
                        np.where(guard, arrival_s, 0), ~guard))
    return order, score


# ============================================================
# Simulation
# ============================================================

def simulate(jobs, history, policy, capacity, cutoff_s=None):
    """
    Rolling simulation over the fixed job set. cutoff_s (validation only)
    stops arrivals at that time; pass a SiteHistory built with the same
    cutoff. Returns (job log, backlog series).
    """

    arrival = jobs["arrival_s"].to_numpy(np.int64)
    job_id = jobs["job_id"].to_numpy(np.int64)
    activity = jobs["activity_pct"].to_numpy(float)
    if np.any(np.diff(arrival) < 0):
        raise AssertionError("jobs are not in arrival order")
    n = len(jobs) if cutoff_s is None else int(np.searchsorted(arrival, cutoff_s, side="left"))
    spec = POLICY_SPECS[policy]
    site_code = (history.site_codes(jobs["site_id"].iloc[:n].astype(str))
                 if spec is not None and spec["r"] is not None else None)
    if spec is not None and spec["r"] == "prior":
        prior = history.prior_repeats(site_code, arrival[:n])

    t0 = first_decision_s()
    horizon_end = _s(s12.EVALUATION_END)
    selected = np.full(len(jobs), -1, dtype=np.int64)
    score_at = np.full(len(jobs), np.nan)
    repeats_at = np.full(len(jobs), -1, dtype=np.int64)
    guard_at = np.zeros(len(jobs), dtype=bool)

    waiting = np.empty(0, dtype=np.int64)
    in_service = []
    backlog = []
    i = 0
    k = 0
    while True:
        t = t0 + k * DAY_S
        # 1. Jobs known at t: arrival strictly before t.
        j_end = int(np.searchsorted(arrival[:n], t, side="left"))
        if j_end > i:
            waiting = np.concatenate([waiting, np.arange(i, j_end)])
            i = j_end
        # 2. Logged resolutions (no effect on any other job).
        in_service = [r for r in in_service if r > t]
        # 3. Selection with information available at t.
        if len(waiting):
            if np.any(arrival[waiting] >= t):
                raise AssertionError("information boundary: job in queue before its arrival")
            if spec is None:
                order = fifo_order(arrival[waiting], job_id[waiting])
                score = reps = None
            else:
                if spec["r"] == "current":
                    reps = history.repeats(site_code[waiting], arrival[waiting], t)
                elif spec["r"] == "prior":
                    reps = prior[waiting]
                else:
                    reps = np.zeros(len(waiting), dtype=np.int64)
                order, score = operational_order(t, arrival[waiting], job_id[waiting],
                                                 activity[waiting], reps, spec["weights"])
            chosen = order[:capacity]
            picked = waiting[chosen]
            selected[picked] = t
            if score is not None:
                score_at[picked] = score[chosen]
                repeats_at[picked] = reps[chosen]
                guard_at[picked] = (t - arrival[picked]) / DAY_S >= s12.GUARD_DAYS
            in_service.extend([t + SERVICE_DAYS * DAY_S] * len(picked))
            keep = np.ones(len(waiting), dtype=bool)
            keep[chosen] = False
            waiting = waiting[keep]
            n_selected = len(picked)
        else:
            n_selected = 0
        backlog.append((t, len(waiting), n_selected, len(in_service)))
        k += 1
        if i >= n and not len(waiting):
            break
        if k > (horizon_end - t0) // DAY_S + MAX_DRAIN_DAYS:
            raise RuntimeError("queue did not drain")

    log = jobs.iloc[:n][list(IDENTITY_COLUMNS) + ["borough", "activity_pct"]].copy()
    log["selected_s"] = selected[:n]
    log["resolved_s"] = selected[:n] + SERVICE_DAYS * DAY_S
    log["repeats_at_selection"] = repeats_at[:n]
    log["score_at_selection"] = score_at[:n]
    log["guard_at_selection"] = guard_at[:n]
    series = pd.DataFrame(backlog, columns=["decision_s", "waiting_after", "selected", "in_service"])
    return log.reset_index(drop=True), series


def assert_common_population(logs, jobs):
    """Check H (hard): every policy ran on exactly the Stage 12 job set."""

    ref = jobs[list(IDENTITY_COLUMNS)].reset_index(drop=True)
    for policy, log in logs.items():
        if len(log) != len(ref) or not log[list(IDENTITY_COLUMNS)].equals(ref):
            raise AssertionError(f"{policy}: job set differs from the common evaluation population")


# ============================================================
# Metrics (no score input)
# ============================================================

def overload_start_s(jobs, capacity):
    """
    Start of the first calendar quarter whose mean daily arrivals exceed K.
    Uses arrivals only (identical for every policy); None if never exceeded.
    """

    when = pd.to_datetime(jobs["arrival_s"], unit="s")
    quarter = when.dt.to_period("Q")
    for q, n in jobs.groupby(quarter).size().items():
        days = (q.end_time.normalize() - q.start_time).days + 1
        if n / days > capacity:
            return _s(q.start_time)
    return None


def _wait_block(wait, prefix=""):
    out = {
        f"{prefix}n_jobs": int(len(wait)),
        f"{prefix}wait_mean_days": float(wait.mean()),
        f"{prefix}wait_median_days": float(wait.median()),
        f"{prefix}wait_p90_days": float(wait.quantile(0.9)),
        f"{prefix}wait_max_days": float(wait.max()),
    }
    for d in THRESHOLDS_DAYS:
        out[f"{prefix}served_within_{d}d_pct"] = float(100 * (wait <= d).mean())
    return out


def metrics(jobs_view, series, capacity, split_s):
    """
    Waiting-time metrics overall and by regime (arrival before / at or after
    split_s), queue metrics, and borough waits. Inputs: METRIC_COLUMNS only.
    """

    extra = sorted(set(jobs_view.columns) - set(METRIC_COLUMNS))
    if extra:
        raise AssertionError(f"metric input must not contain {extra}")
    wait = (jobs_view["selected_s"] - jobs_view["arrival_s"]) / DAY_S
    if (wait <= 0).any():
        raise AssertionError("job selected before its arrival")
    stable = jobs_view["arrival_s"] < split_s
    horizon = series[series["decision_s"] < _s(s12.EVALUATION_END)]
    out = {
        **_wait_block(wait),
        "backlog_mean": float(horizon["waiting_after"].mean()),
        "backlog_max": int(horizon["waiting_after"].max()),
        "backlog_at_horizon_end": int(horizon["waiting_after"].iloc[-1]),
        "throughput_per_day": float(horizon["selected"].mean()),
        "utilisation": float(horizon["selected"].sum() / (capacity * len(horizon))),
        "drain_days_after_horizon": int(len(series) - len(horizon)),
        **_wait_block(wait[stable], "stable_"),
        **_wait_block(wait[~stable], "overload_"),
    }
    frames = []
    for regime, mask in (("all", np.ones(len(wait), bool)), ("stable", stable.to_numpy()),
                         ("overload", ~stable.to_numpy())):
        f = (pd.DataFrame({"borough": jobs_view["borough"].fillna("<missing>")[mask], "wait": wait[mask]})
             .groupby("borough")["wait"]
             .agg(n_jobs="size", wait_mean_days="mean", wait_median_days="median",
                  wait_p90_days=lambda x: x.quantile(0.9), wait_max_days="max")
             .reset_index())
        f.insert(0, "regime", regime)
        frames.append(f)
    return out, pd.concat(frames, ignore_index=True)


def metric_view(log):
    return log[list(METRIC_COLUMNS)].copy()


def frame_hash(frame):
    return hashlib.sha256(pd.util.hash_pandas_object(frame, index=False).to_numpy().tobytes()).hexdigest()


# ============================================================
# Run
# ============================================================

def run(stage12_dir, jobs_dir, out_dir):
    arrivals, jobs, calibration = load_inputs(stage12_dir)
    if frame_hash(jobs) != frame_hash(s12.build_jobs(arrivals)):
        raise AssertionError("Stage 12 jobs.parquet does not match its arrivals")
    capacity, rate = capacity_from_calibration(arrivals)
    history = s12.SiteHistory(arrivals)
    split_s = overload_start_s(jobs, capacity)
    if split_s is None:
        split_s = _s(s12.EVALUATION_END)

    rows, borough_rows, logs, series_by_policy, log_info = [], [], {}, {}, {}
    for policy in POLICIES:
        log, series = simulate(jobs, history, policy, capacity)
        logs[policy], series_by_policy[policy] = log, series
    assert_common_population(logs, jobs)

    jobs_dir.mkdir(parents=True, exist_ok=True)
    for policy in POLICIES:
        log, series = logs[policy], series_by_policy[policy]
        m, by_b = metrics(metric_view(log), series, capacity, split_s)
        guard = log["guard_at_selection"].to_numpy()
        stable_sel = log["arrival_s"].to_numpy() < split_s
        rows.append({"policy": policy, "status": POLICY_STATUS[policy], **m,
                     "guard_selections": int(guard.sum()),
                     "guard_share_pct": float(100 * guard.mean()),
                     "stable_guard_selections": int(guard[stable_sel].sum()),
                     "overload_guard_selections": int(guard[~stable_sel].sum())})
        by_b.insert(0, "policy", policy)
        borough_rows.append(by_b)
        log.to_parquet(jobs_dir / f"jobs_{policy}.parquet", index=False)
        series.to_parquet(jobs_dir / f"backlog_{policy}.parquet", index=False)
        log_info[policy] = {"jobs_sha256": frame_hash(log), "backlog_sha256": frame_hash(series),
                            "guard_selections": int(log["guard_at_selection"].sum())}
        print(f"{policy}: n={m['n_jobs']}, mean={m['wait_mean_days']:.3f}, "
              f"stable mean/med/p90/max={m['stable_wait_mean_days']:.2f}/{m['stable_wait_median_days']:.2f}/"
              f"{m['stable_wait_p90_days']:.2f}/{m['stable_wait_max_days']:.2f}, guard={int(guard.sum())}")

    table = pd.DataFrame(rows)
    by_borough = pd.concat(borough_rows, ignore_index=True)
    same_series = all(series_by_policy[p].equals(series_by_policy["fifo"]) for p in POLICIES)
    summary = {
        "stage": "13 rolling dispatch simulation",
        "framing": s12.FRAMING,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "job_definition": calibration["job_definition"],
        "assumptions": {
            "decision_cadence": f"daily at {DECISION_HOUR:02d}:00",
            "capacity_jobs_per_decision": capacity,
            "capacity_rule": f"ceil(2023 daily non-artifact complaint arrivals {rate:.2f} / {TARGET_UTILISATION})",
            "service_days": SERVICE_DAYS,
            "service_time_note": "simulation assumption; not inferred from 311 closure dates (never loaded)",
            "initial_queue": "empty at 2024-01-01",
            "horizon": [str(s12.EVALUATION_START.date()), str(s12.EVALUATION_END.date())],
            "drain": "decisions continue after the last arrival until the queue is empty",
        },
        "frozen_policy": FROZEN_POLICY,
        "policy_specs": {p: (None if v is None else {"weights": list(v["weights"]), "r": v["r"]})
                         for p, v in POLICY_SPECS.items()},
        "policy_status": POLICY_STATUS,
        "regime_split": {"overload_start": str(pd.to_datetime(split_s, unit="s")),
                         "rule": "start of the first calendar quarter whose mean daily arrivals exceed K"},
        "common_population": {"n_jobs": int(len(jobs)), "jobs_sha256": frame_hash(jobs),
                              "identity_columns": list(IDENTITY_COLUMNS)},
        "backlog_series_identical_across_policies": bool(same_series),
        "n_artifact_arrivals_excluded": int(((arrivals["period"] == "evaluation")
                                             & arrivals["is_artifact_pre"]).sum()),
        "metrics": rows,
        "logs": log_info,
        "stage12_calibration_sha256": s7._file_fingerprint(stage12_dir / s12.CALIBRATION_FILENAME)["sha256"],
        "stage12_jobs_sha256": s7._file_fingerprint(stage12_dir / s12.JOBS_FILENAME)["sha256"],
        "git": s7._git_state(),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    for frame, name in ((table, METRICS_FILENAME), (by_borough, BOROUGH_FILENAME)):
        tmp = out_dir / (name + ".tmp")
        frame.to_csv(tmp, index=False)
        os.replace(tmp, out_dir / name)
    tmp = out_dir / (SUMMARY_FILENAME + ".tmp")
    tmp.write_text(json.dumps(summary, indent=2, allow_nan=False, default=str))
    os.replace(tmp, out_dir / SUMMARY_FILENAME)
    return table, by_borough, summary


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Stage 13 rolling dispatch simulation")
    parser.add_argument("--stage12-dir", type=Path, default=s12.OUT_DIR)
    parser.add_argument("--jobs-dir", type=Path, default=JOBS_DIR)
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    run(args.stage12_dir, args.jobs_dir, args.out)
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
