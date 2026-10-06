"""
Stage 12 (operational priority) + Stage 13 (rolling dispatch) validation.

    python scripts/validation/stage12_13_validate.py

A  decision-time leakage: only allowed 311 columns are read; activity
   brute-force recount uses crimes before created - 30 d; calibration uses
   pre-evaluation data only; repeat counts equal a brute-force count of
   arrivals before t; decisions up to a cutoff are identical when later
   arrivals are removed (jobs and site history)
B  chronological arrivals: job arrival = the complaint's created time;
   jobs are selected only at decision times after arrival
C  finite crew capacity: <= K selections per decision; every job served once
D  no historical closure: closure columns never read; resolution =
   selection + the assumed service time for every job
E  FIFO baseline independent: FIFO schedule unchanged when the score inputs
   are permuted; an earlier arrival is never selected after a later one
F  score independent from metrics: the metric function rejects score
   columns; stored metrics equal a recomputation from (job id, arrival,
   selection time, borough) only
G  reproducibility: Stage 12 features and both Stage 13 runs reproduce
   the stored outputs exactly
H  common evaluation population: both policies received exactly the same
   immutable job ids, arrival times and site ids, equal to the Stage 12 job
   set rebuilt from the evaluation-period non-artifact arrivals; equal
   counts. Any difference is a hard failure.
I  frozen policy definition: the frozen policy is FIFO; its ordering
   function takes only (arrival, job id); its log carries no score, repeat
   or guard values; age_only is schedule-identical to FIFO; the score
   functions are recorded as audited and rejected
All checks run over every policy in POLICY_SPECS (frozen and audited).

Exit 0 when every check passes, 1 otherwise.
"""

import inspect
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.features import operational_priority as s12  # noqa: E402
from src.models import dispatch_simulation as s13  # noqa: E402

SEED = 20261006
RESULTS = []


def check(name, ok, detail):
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def main():
    arrivals, jobs, calibration = s13.load_inputs()
    capacity, _ = s13.capacity_from_calibration(arrivals)
    history = s12.SiteHistory(arrivals)
    stored = {p: pd.read_parquet(s13.JOBS_DIR / f"jobs_{p}.parquet") for p in s13.POLICIES}
    series = {p: pd.read_parquet(s13.JOBS_DIR / f"backlog_{p}.parquet") for p in s13.POLICIES}
    summary = json.loads((s13.OUTPUT_DIR / s13.SUMMARY_FILENAME).read_text())
    rng = np.random.default_rng(SEED)
    ident = list(s13.IDENTITY_COLUMNS)

    # ---------------- H: common evaluation population (hard) ----------------
    ev = arrivals[(arrivals["period"] == "evaluation") & ~arrivals["is_artifact_pre"]]
    rebuilt_jobs = s12.build_jobs(arrivals)
    h_ok = (rebuilt_jobs.equals(jobs)
            and set(jobs["job_id"]) == set(ev["unique_key"].astype(np.int64))
            and len(jobs) == len(ev)
            and all(len(stored[p]) == len(jobs) for p in s13.POLICIES)
            and all(stored[p][ident].equals(jobs[ident]) for p in s13.POLICIES)
            and all(stored[p][ident].equals(stored["fifo"][ident]) for p in s13.POLICIES)
            and summary["common_population"]["jobs_sha256"] == s13.frame_hash(jobs))
    check("H common evaluation population", h_ok,
          f"jobs {len(jobs):,} = evaluation non-artifact arrivals {len(ev):,}; "
          f"per policy {sorted({len(v) for v in stored.values()})}; "
          f"identical (job_id, arrival_s, site_id) across policies and vs Stage 12 rebuild={h_ok}")

    # ---------------- A: decision-time leakage ----------------
    read_columns = []
    original = pd.read_csv

    def spy(*args, **kwargs):
        read_columns.extend(kwargs.get("usecols") or ["<ALL COLUMNS>"])
        return original(*args, **kwargs)

    pd.read_csv = spy
    try:
        rebuilt, _ = s12.load_arrivals()
    finally:
        pd.read_csv = original
    a1 = not (set(read_columns) - set(s12.ALLOWED_311_COLUMNS)) and "<ALL COLUMNS>" not in read_columns

    crime = pd.read_parquet(s12.CRIME_FILE, columns=["crime_datetime", "latitude", "longitude"]).dropna()
    cx, cy = Transformer.from_crs("EPSG:4326", "EPSG:32118", always_xy=True).transform(
        crime["longitude"].to_numpy(), crime["latitude"].to_numpy())
    sample = ev.iloc[rng.choice(len(ev), 300, replace=False)]
    mism = 0
    for _, r in sample.iterrows():
        lo = r["created_date"] - pd.Timedelta(days=395)
        hi = r["created_date"] - pd.Timedelta(days=30)
        in_t = ((crime["crime_datetime"] >= lo) & (crime["crime_datetime"] < hi)).to_numpy()
        d = np.sqrt((cx - r["site_x"]) ** 2 + (cy - r["site_y"]) ** 2)
        mism += int(((d <= 250) & in_t).sum() != r["activity_count"])

    cal = arrivals[(arrivals["period"] == "calibration") & ~arrivals["is_artifact_pre"]]
    cal_values = np.sort(cal["activity_count"].to_numpy())
    a3 = (cal["created_date"].max() < s12.EVALUATION_START
          and cal_values.astype(int).tolist() == calibration["calibration_values"]
          and np.allclose(arrivals["activity_pct"],
                          np.searchsorted(cal_values, arrivals["activity_count"], side="right") / len(cal_values)))

    # Repeats at selection == brute-force count of arrivals strictly before t.
    op = stored["operational"]
    na = arrivals[~arrivals["is_artifact_pre"]]
    na_t = na["created_date"].to_numpy("datetime64[s]").astype(np.int64)
    na_site = na["site_id"].astype(str).to_numpy()
    rep_mism = 0
    for i in rng.choice(len(op), 500, replace=False):
        r = op.iloc[i]
        brute = int(((na_site == r["site_id"]) & (na_t >= r["arrival_s"] - s12.REPEAT_WINDOW_DAYS * 86400)
                     & (na_t < r["selected_s"])).sum()) - 1
        rep_mism += int(brute != r["repeats_at_selection"])

    # Prior-only repeats (age_r) use complaints created strictly before arrival.
    ar = stored["age_r"]
    prior = history.prior_repeats(history.site_codes(ar["site_id"].astype(str)), ar["arrival_s"].to_numpy())
    for i in rng.choice(len(ar), 500, replace=False):
        r = ar.iloc[i]
        brute = int(((na_site == r["site_id"]) & (na_t >= r["arrival_s"] - s12.REPEAT_WINDOW_DAYS * 86400)
                     & (na_t < r["arrival_s"])).sum())
        rep_mism += int(brute != prior[i] or brute != r["repeats_at_selection"])

    cutoff = s13._s("2025-03-15") + s13.DECISION_HOUR * 3600
    key = ["job_id", "selected_s"]
    trunc_history = s12.SiteHistory(arrivals, before_s=cutoff)
    a5 = True
    for p in s13.POLICIES:
        trunc, _ = s13.simulate(jobs, trunc_history, p, capacity, cutoff_s=cutoff)
        full = stored[p]
        a5 &= (full.loc[full["selected_s"] <= cutoff, key].sort_values(key).reset_index(drop=True)
               .equals(trunc.loc[trunc["selected_s"] <= cutoff, key].sort_values(key).reset_index(drop=True)))
    check("A decision-time leakage", a1 and mism == 0 and a3 and rep_mism == 0 and a5,
          f"311 columns read {sorted(set(read_columns))}; activity brute-force mismatches {mism}/300; "
          f"calibration pre-2024 and ECDF reproduced={a3}; repeat-count mismatches {rep_mism}/1000 "
          f"(current + prior-only); decisions to 2025-03-15 identical without later arrivals, "
          f"all {len(s13.POLICIES)} policies={a5}")

    # ---------------- B: chronological arrivals ----------------
    created = dict(zip(ev["unique_key"].astype(np.int64),
                       ev["created_date"].to_numpy("datetime64[s]").astype(np.int64)))
    t0 = s13.first_decision_s()
    b_ok, notes = True, []
    for p, log in stored.items():
        arr_ok = (log["job_id"].map(created) == log["arrival_s"]).all()
        after = (log["selected_s"] > log["arrival_s"]).all()
        grid = ((log["selected_s"] - t0) % s13.DAY_S == 0).all()
        b_ok &= bool(arr_ok and after and grid and log["arrival_s"].is_monotonic_increasing)
        notes.append(f"{p}: arrival=complaint time {arr_ok}, selected after arrival {after}, on grid {grid}")
    check("B chronological arrivals", b_ok, "; ".join(notes))

    # ---------------- C: finite capacity ----------------
    c_ok, notes = True, []
    for p in s13.POLICIES:
        c_ok &= bool(series[p]["selected"].max() <= capacity and series[p]["selected"].sum() == len(stored[p])
                     and stored[p]["selected_s"].ge(0).all())
        notes.append(f"{p}: max {series[p]['selected'].max()} <= K={capacity}, "
                     f"served {series[p]['selected'].sum():,} = jobs {len(stored[p]):,}")
    check("C finite crew capacity", c_ok, "; ".join(notes))

    # ---------------- D: no historical closure ----------------
    closure_like = [c for frame in (*stored.values(), arrivals, jobs) for c in frame.columns
                    if any(w in c for w in ("closed", "duration", "status", "resolution_action"))]
    service_ok = all(((log["resolved_s"] - log["selected_s"]) == s13.SERVICE_DAYS * s13.DAY_S).all()
                     for log in stored.values())
    check("D no historical closure as repair time", a1 and not closure_like and service_ok,
          f"closure fields read: none ({a1}); closure-like columns {closure_like}; "
          f"resolution = selection + {s13.SERVICE_DAYS} d for all jobs={service_ok}")

    # ---------------- E: FIFO independent ----------------
    shuffled = jobs.copy()
    shuffled["activity_pct"] = rng.permutation(shuffled["activity_pct"].to_numpy())
    fifo2, _ = s13.simulate(shuffled, history, "fifo", capacity)
    same = fifo2[key].equals(stored["fifo"][key])
    j = stored["fifo"].sort_values(["arrival_s", "job_id"])
    monotone = j["selected_s"].is_monotonic_increasing
    check("E FIFO baseline independent", same and monotone,
          f"schedule unchanged with permuted score inputs={same}; earlier arrival never served after a later one={monotone}")

    # ---------------- F: score independent from metrics ----------------
    split_s = s13._s(summary["regime_split"]["overload_start"])
    split_ok = split_s == s13.overload_start_s(jobs, capacity)
    try:
        s13.metrics(stored["operational"], series["operational"], capacity, split_s)
        rejects = False
    except AssertionError:
        rejects = True
    stored_metrics = pd.read_csv(s13.OUTPUT_DIR / s13.METRICS_FILENAME).set_index("policy")
    recomputed = True
    for p in s13.POLICIES:
        m, _ = s13.metrics(stored[p][list(s13.METRIC_COLUMNS)], series[p], capacity, split_s)
        recomputed &= all(np.isclose(stored_metrics.loc[p, k], v) for k, v in m.items())
    check("F score independent from evaluation metrics", rejects and recomputed and split_ok,
          f"metric function rejects score columns={rejects}; metrics recomputed from "
          f"{list(s13.METRIC_COLUMNS)} equal stored={recomputed}; regime split from arrivals only "
          f"({summary['regime_split']['overload_start']})={split_ok}")

    # ---------------- G: reproducibility ----------------
    g_ok, notes = True, []
    for p in s13.POLICIES:
        log, ser = s13.simulate(jobs, history, p, capacity)
        ok = (s13.frame_hash(log) == summary["logs"][p]["jobs_sha256"]
              and s13.frame_hash(ser) == summary["logs"][p]["backlog_sha256"])
        g_ok &= ok
        notes.append(f"{p} rerun hashes equal={ok}")
    rebuilt = rebuilt.copy()
    rebuilt["activity_count"] = s12.activity_counts(rebuilt)
    stage12_same = (rebuilt["unique_key"].tolist() == arrivals["unique_key"].tolist()
                    and (rebuilt["activity_count"].to_numpy() == arrivals["activity_count"].to_numpy()).all()
                    and (rebuilt["is_artifact_pre"].to_numpy() == arrivals["is_artifact_pre"].to_numpy()).all())
    notes.append(f"Stage 12 features rebuilt equal={stage12_same}")
    check("G reproducibility", g_ok and stage12_same, "; ".join(notes))

    # ---------------- I: frozen policy definition ----------------
    fifo_log = stored[s13.FROZEN_POLICY]
    i_ok = (s13.FROZEN_POLICY == "fifo" and summary["frozen_policy"] == "fifo"
            and s13.POLICY_SPECS["fifo"] is None
            and list(inspect.signature(s13.fifo_order).parameters) == ["arrival_s", "job_id"]
            and fifo_log["score_at_selection"].isna().all()
            and (fifo_log["repeats_at_selection"] == -1).all()
            and not fifo_log["guard_at_selection"].any()
            and stored["age_only"]["selected_s"].equals(fifo_log["selected_s"])
            and all(v == "audited_rejected" for k, v in summary["policy_status"].items() if k != "fifo")
            and "rejected" in calibration["audited_candidate_score"])
    check("I frozen policy definition", i_ok,
          "frozen policy fifo uses (arrival_s, job_id) only; no score/repeat/guard values in its log; "
          f"age_only schedule identical to FIFO; scored policies recorded as audited_rejected={i_ok}")

    # Informational: order-invariant quantities under this simulation design.
    print(f"\n[INFO] backlog series identical across policies: {series['fifo'].equals(series['operational'])}; "
          f"mean wait fifo {stored_metrics.loc['fifo', 'wait_mean_days']:.6f} / "
          f"operational {stored_metrics.loc['operational', 'wait_mean_days']:.6f}")

    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
