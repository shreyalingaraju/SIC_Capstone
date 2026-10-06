"""
Stage 12: decision-time operational priority for unresolved reported outages.

Decision-support for prioritizing unresolved reported streetlight outages
using operational need and exposure features. This is NOT a crime-benefit
score: it claims no crime reduction, no crimes prevented and no causal repair
benefit (the frozen Stage 11 result is null and is not used here). "Open"
means reported-open (311), not physically dark.

Writes data/processed/stage12/: arrivals.parquet (one row per arriving
complaint with its decision-time features), jobs.parquet (the immutable
evaluation job set) and operational_priority_calibration.json. The score
depends on the decision time t and is computed by
operational_priority_score() inside the Stage 13 simulation.

Job definition (fixed before any policy is simulated): every evaluation-
period complaint at a non-artifact site is one queue job; job_id is its
unique_key and its arrival is its created_date. Complaints are never merged
into another job, so the job set cannot depend on simulated selection or
resolution times and is identical for every policy.

Information boundary (asserted):
- Only these raw 311 columns are ever read: unique_key, created_date,
  latitude, longitude, borough. closed_date, status, resolution fields,
  due_date and every Stage 7/11 output are never loaded.
- Activity for a complaint uses crimes in [created - 395 d, created - 30 d):
  a 365-day window ending at a 30-day reporting-lag buffer.
- The artifact-site rule uses complaints created before the evaluation
  period only.
- The activity normalization (ECDF) is fitted on complaints created in the
  calibration year 2023, before the evaluation period starts.

SCORE AUDIT DECISION: no score is frozen; FIFO is the operational baseline
(see AUDIT_DECISION and docs/stage12_13_operational_dispatch.md). The
definitions below are the audited, rejected candidate, kept to reproduce
the audit. Score at decision time t for a waiting job j (components in [0, 1]):
    A_j(t) = min(age_j(t) / AGE_SCALE_DAYS, 1),  age = t - arrival
    C_j    = ECDF_2023(activity at the job's complaint)
    R_j(t) = min(repeats_j(t), REPEAT_CAP) / REPEAT_CAP, where repeats_j(t)
             = other non-artifact complaints at the same site created in
             [arrival_j - REPEAT_WINDOW_DAYS, t) (arrivals only; no
             simulation state)
    operational_priority_score = (A + C + R) / 3
Jobs waiting >= GUARD_DAYS are served first (oldest first); the remaining
capacity goes by score, ties by arrival then job id.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.features import match_controls as s7  # noqa: E402  (locked CRS, bbox, site, artifact conventions)

RAW_COMPLAINTS_FILE = PROJECT_ROOT / s7.RAW_COMPLAINTS_FILE
CRIME_FILE = PROJECT_ROOT / s7.CRIME_FILE
OUT_DIR = PROJECT_ROOT / "data" / "processed" / "stage12"
ARRIVALS_FILENAME = "arrivals.parquet"
CALIBRATION_FILENAME = "operational_priority_calibration.json"

AUDIT_DECISION = (
    "Score audit (option C): no operational priority score is frozen. R double-counts "
    "same-site complaints that are already separate jobs (current form) or cannot separate "
    "duplicates from recurrences at decision time (prior-only form); C encodes an unsupported "
    "preference for high-activity locations that shifts waiting onto low-activity boroughs; "
    "A alone is schedule-identical to FIFO. FIFO is the frozen operational baseline. The "
    "score functions are kept only to reproduce the audit.")

FRAMING = ("Decision-support for prioritizing unresolved reported streetlight outages "
           "using operational need and exposure features. Not a crime-reduction, "
           "crime-prevention or causal repair-benefit estimate.")

# Information boundary: the only raw 311 columns this pipeline may read.
ALLOWED_311_COLUMNS = ("unique_key", "created_date", "latitude", "longitude", "borough")
FORBIDDEN_COLUMNS = ("closed_date", "status", "resolution_description",
                     "resolution_action_updated_date", "due_date", "outage_duration_hours",
                     "priority_score", "tau_net", "raw_priority")

CALIBRATION_START = pd.Timestamp("2023-01-01")
EVALUATION_START = pd.Timestamp("2024-01-01")
EVALUATION_END = pd.Timestamp("2026-06-29")   # exclusive; crime data end 2026-06-30

ACTIVITY_RADIUS_M = 250.0
ACTIVITY_WINDOW_DAYS = 365
CRIME_LAG_DAYS = 30
AGE_SCALE_DAYS = 30.0
GUARD_DAYS = 30.0
REPEAT_CAP = 2
REPEAT_WINDOW_DAYS = 30
JOBS_FILENAME = "jobs.parquet"
JOB_COLUMNS = ("job_id", "arrival_s", "site_id", "borough", "activity_pct")
WEIGHTS = (1 / 3, 1 / 3, 1 / 3)   # A, C, R


def assert_no_forbidden(columns, where):
    bad = sorted(set(columns) & set(FORBIDDEN_COLUMNS))
    if bad:
        raise AssertionError(f"information boundary: forbidden columns {bad} in {where}")


# ============================================================
# Arrivals and decision-time features
# ============================================================

def load_arrivals():
    """Raw 311 complaints (allowed columns only), sites and pre-period artifact flags."""

    raw = pd.read_csv(RAW_COMPLAINTS_FILE, usecols=list(ALLOWED_311_COLUMNS), low_memory=False)
    assert_no_forbidden(raw.columns, "raw 311 read")
    raw["created_date"] = pd.to_datetime(raw["created_date"], format="ISO8601", errors="coerce")
    lat_min, lat_max, lon_min, lon_max = s7.NYC_BBOX
    keep = (raw["created_date"].notna() & raw["latitude"].between(lat_min, lat_max)
            & raw["longitude"].between(lon_min, lon_max))
    c = raw.loc[keep].sort_values(["created_date", "unique_key"], kind="mergesort").reset_index(drop=True)
    c["borough"] = s7._normalise_label(c["borough"])
    x, y = s7._project(c["longitude"], c["latitude"], s7.PROJECTED_CRS)
    c["site_x"] = np.rint(x).astype(np.int64)
    c["site_y"] = np.rint(y).astype(np.int64)
    c["site_id"] = "E" + c["site_x"].astype(str) + "_N" + c["site_y"].astype(str)

    # Artifact rule (Stage 7 form) on complaints created before evaluation only.
    pre = c[c["created_date"] < EVALUATION_START]
    counts = pre.groupby("site_id").size()
    threshold = int(np.quantile(counts.to_numpy(), s7.ARTIFACT_QUANTILE, method="higher"))
    artifacts = set(counts[counts > threshold].index)
    c["is_artifact_pre"] = c["site_id"].isin(artifacts)

    c = c[(c["created_date"] >= CALIBRATION_START) & (c["created_date"] < EVALUATION_END)]
    c = c.reset_index(drop=True)
    c["period"] = np.where(c["created_date"] < EVALUATION_START, "calibration", "evaluation")
    info = {"artifact_threshold_pre": threshold, "n_artifact_sites_pre": len(artifacts)}
    return c, info


def activity_counts(c):
    """Night crimes within ACTIVITY_RADIUS_M of the site in [created-395d, created-30d)."""

    crime = pd.read_parquet(CRIME_FILE, columns=list(s7.CRIME_COLUMNS)).dropna()
    cx, cy = s7._project(crime["longitude"], crime["latitude"], s7.PROJECTED_CRS)
    ct = crime["crime_datetime"].to_numpy("datetime64[s]").astype(np.int64)
    crime_min = int(ct.min())

    created = c["created_date"].to_numpy("datetime64[s]").astype(np.int64)
    day = s7.SECONDS_PER_DAY
    w_end = created - CRIME_LAG_DAYS * day
    w_start = w_end - ACTIVITY_WINDOW_DAYS * day
    if np.any(w_end > created):
        raise AssertionError("information boundary: activity window ends after the report")
    if np.any(w_start < crime_min):
        raise AssertionError("activity window starts before crime coverage")

    tree = cKDTree(np.c_[cx, cy])
    sx = c["site_x"].to_numpy(float)
    sy = c["site_y"].to_numpy(float)
    lists = tree.query_ball_point(np.c_[sx, sy], ACTIVITY_RADIUS_M + s7.KD_QUERY_TOLERANCE_M)
    out = np.zeros(len(c), dtype=np.int64)
    for i, idx in enumerate(lists):
        if not idx:
            continue
        idx = np.asarray(idx)
        d = np.sqrt((cx[idx] - sx[i]) ** 2 + (cy[idx] - sy[i]) ** 2)
        t = ct[idx[d <= ACTIVITY_RADIUS_M]]
        out[i] = int(((t >= w_start[i]) & (t < w_end[i])).sum())
    return out


def fit_calibration(c):
    cal = c[(c["period"] == "calibration") & ~c["is_artifact_pre"]]
    values = np.sort(cal["activity_count"].to_numpy())
    if len(values) == 0:
        raise AssertionError("empty calibration population")
    if cal["created_date"].max() >= EVALUATION_START:
        raise AssertionError("information boundary: calibration uses evaluation-period data")
    return values


def activity_percentile(counts, calibration_values):
    """Right-continuous ECDF of the 2023 calibration activity counts."""

    return np.searchsorted(calibration_values, counts, side="right") / len(calibration_values)


# ============================================================
# Score (used by Stage 13 at each decision time)
# ============================================================

def components(age_days, activity_pct, repeats):
    a = np.minimum(np.asarray(age_days, float) / AGE_SCALE_DAYS, 1.0)
    r = np.minimum(np.asarray(repeats, float), REPEAT_CAP) / REPEAT_CAP
    return a, np.asarray(activity_pct, float), r


def weighted_score(age_days, activity_pct, repeats, weights):
    """Weighted mean of the A, C, R components (weights sum to 1)."""

    if np.any(np.asarray(age_days) < 0):
        raise AssertionError("information boundary: job scored before its arrival")
    if not np.isclose(sum(weights), 1.0):
        raise AssertionError("score weights must sum to 1")
    a, cpt, r = components(age_days, activity_pct, repeats)
    return weights[0] * a + weights[1] * cpt + weights[2] * r


def operational_priority_score(age_days, activity_pct, repeats):
    """The audited (A + C + R) / 3 candidate; rejected, see AUDIT_DECISION."""

    return weighted_score(age_days, activity_pct, repeats, WEIGHTS)


# ============================================================
# Immutable job set and arrival-only repeat counts
# ============================================================

def build_jobs(arrivals):
    """One job per evaluation-period complaint at a non-artifact site."""

    ev = arrivals[(arrivals["period"] == "evaluation") & ~arrivals["is_artifact_pre"]]
    ev = ev.sort_values(["created_date", "unique_key"], kind="mergesort")
    jobs = pd.DataFrame({
        "job_id": ev["unique_key"].astype(np.int64).to_numpy(),
        "arrival_s": ev["created_date"].to_numpy("datetime64[s]").astype(np.int64),
        "site_id": ev["site_id"].astype(str).to_numpy(),
        "borough": ev["borough"].to_numpy(),
        "activity_pct": ev["activity_pct"].to_numpy(float),
    })
    if not jobs["job_id"].is_unique:
        raise AssertionError("job ids are not unique")
    return jobs


class SiteHistory:
    """
    Arrival times of non-artifact complaints per site (calibration and
    evaluation periods), for repeats_j(t). before_s (validation only)
    drops arrivals at or after that time.
    """

    _BASE = int(pd.Timestamp(CALIBRATION_START).value // 10**9)
    _SHIFT = 2 ** 33

    def __init__(self, arrivals, before_s=None):
        h = arrivals[~arrivals["is_artifact_pre"]]
        created = h["created_date"].to_numpy("datetime64[s]").astype(np.int64)
        if before_s is not None:
            keep = created < before_s
            h, created = h[keep], created[keep]
        self.codes = {s: i for i, s in enumerate(sorted(set(h["site_id"].astype(str))))}
        site = np.array([self.codes[s] for s in h["site_id"].astype(str)], dtype=np.int64)
        rel = created - self._BASE
        if rel.min() < 0 or rel.max() >= self._SHIFT:
            raise AssertionError("site history times outside the key range")
        self.keys = np.sort(site * self._SHIFT + rel)

    def site_codes(self, site_ids):
        return np.array([self.codes[s] for s in site_ids], dtype=np.int64)

    def repeats(self, site_codes, arrival_s, t_s):
        """Other complaints at the site created in [arrival - window, t)."""

        lo = site_codes * self._SHIFT + (arrival_s - REPEAT_WINDOW_DAYS * 86400 - self._BASE)
        hi = site_codes * self._SHIFT + (t_s - self._BASE)
        n = np.searchsorted(self.keys, hi, side="left") - np.searchsorted(self.keys, lo, side="left") - 1
        if np.any(n < 0):
            raise AssertionError("focal complaint missing from site history (scored before arrival?)")
        return n

    def prior_repeats(self, site_codes, arrival_s):
        """Complaints at the site created in [arrival - window, arrival): history before the job."""

        lo = site_codes * self._SHIFT + (arrival_s - REPEAT_WINDOW_DAYS * 86400 - self._BASE)
        hi = site_codes * self._SHIFT + (arrival_s - self._BASE)
        return np.searchsorted(self.keys, hi, side="left") - np.searchsorted(self.keys, lo, side="left")


# ============================================================
# Run
# ============================================================

def run(out_dir):
    c, info = load_arrivals()
    c["activity_count"] = activity_counts(c)
    calibration_values = fit_calibration(c)
    c["activity_pct"] = activity_percentile(c["activity_count"].to_numpy(), calibration_values)

    arrivals = c[["unique_key", "created_date", "site_id", "site_x", "site_y", "borough",
                  "is_artifact_pre", "period", "activity_count", "activity_pct"]].copy()
    assert_no_forbidden(arrivals.columns, "arrivals output")

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / (ARRIVALS_FILENAME + ".tmp")
    arrivals.to_parquet(tmp, index=False)
    os.replace(tmp, out_dir / ARRIVALS_FILENAME)

    jobs = build_jobs(arrivals)
    tmp = out_dir / (JOBS_FILENAME + ".tmp")
    jobs.to_parquet(tmp, index=False)
    os.replace(tmp, out_dir / JOBS_FILENAME)

    calibration = {
        "framing": FRAMING,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "job_definition": "one job per evaluation-period complaint at a non-artifact site; "
                          "job_id = unique_key, arrival = created_date; no merging",
        "audit_decision": AUDIT_DECISION,
        "frozen_policy": "fifo (earliest arrival first, ties by job id; no score)",
        "audited_candidate_score": "operational_priority_score = (A + C + R) / 3 (rejected)",
        "components": {
            "A": f"min(age_days / {AGE_SCALE_DAYS}, 1); age = t - arrival",
            "C": "ECDF over 2023 calibration complaints of night crimes within "
                 f"{ACTIVITY_RADIUS_M} m in [created - {ACTIVITY_WINDOW_DAYS + CRIME_LAG_DAYS} d, "
                 f"created - {CRIME_LAG_DAYS} d), at the job's complaint",
            "R": f"min(other non-artifact complaints at the same site created in "
                 f"[arrival - {REPEAT_WINDOW_DAYS} d, t), {REPEAT_CAP}) / {REPEAT_CAP}",
        },
        "guard": f"jobs waiting >= {GUARD_DAYS} days are served first, oldest first",
        "weights": list(WEIGHTS),
        "calibration_period": [str(CALIBRATION_START.date()), str(EVALUATION_START.date())],
        "evaluation_period": [str(EVALUATION_START.date()), str(EVALUATION_END.date())],
        "calibration_n": int(len(calibration_values)),
        "calibration_values_sha256": hashlib.sha256(calibration_values.astype(np.int64).tobytes()).hexdigest(),
        "calibration_quantiles": {str(q): float(np.quantile(calibration_values, q))
                                  for q in (0.1, 0.25, 0.5, 0.75, 0.9, 0.99)},
        "calibration_values": calibration_values.astype(int).tolist(),
        "allowed_311_columns": list(ALLOWED_311_COLUMNS),
        "forbidden_columns": list(FORBIDDEN_COLUMNS),
        **info,
        "n_arrivals": {k: int(v) for k, v in arrivals.groupby("period").size().items()},
        "inputs": {"raw_311": s7._file_fingerprint(RAW_COMPLAINTS_FILE)["sha256"],
                   "clean_crime": s7._file_fingerprint(CRIME_FILE)["sha256"]},
        "arrivals_sha256": s7._file_fingerprint(out_dir / ARRIVALS_FILENAME)["sha256"],
        "n_jobs": int(len(jobs)),
        "jobs_sha256": s7._file_fingerprint(out_dir / JOBS_FILENAME)["sha256"],
        "git": s7._git_state(),
    }
    tmp = out_dir / (CALIBRATION_FILENAME + ".tmp")
    tmp.write_text(json.dumps(calibration, indent=1, allow_nan=False))
    os.replace(tmp, out_dir / CALIBRATION_FILENAME)
    return arrivals, calibration


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Stage 12 operational priority features")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    arrivals, cal = run(args.out_dir)
    print(f"Arrivals: {cal['n_arrivals']}; artifact threshold (pre) {cal['artifact_threshold_pre']}, "
          f"{cal['n_artifact_sites_pre']} artifact sites")
    print(f"Calibration activity quantiles: {cal['calibration_quantiles']}")
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
