"""
Stage 12: priority score generation.

Reads the cleaned outages (Stage 3), the cleaned crime file (Stage 5) and the
accepted Stage 11 net effect, and writes data/processed/outages_scored.parquet.

    raw_priority   = max(0, tau_net * local_crime_rate * duration_factor)
    priority_score = 100 * (raw - min) / (max - min)         (min-max, 0-100)
    tier           = High if score >= 80, Medium if score >= 40, else Low

Definitions (documented in docs/stage12_priority_scoring.md):
- Outages scored: every cleaned Stage 3 outage record (project owner's
  decision; the cleaned file holds only closed records, so there is no
  "currently open" subset). Records that cannot be scored are kept in the
  output with scored = False and an exclusion_reason.
- tau_net: the Stage 11 `net_post` estimate in outputs/displacement_estimates.csv
  (project owner's decision), read unchanged. Stage 11's sign convention holds:
  positive = more crime, net = direct + displacement.
- local_crime_rate: crimes per day within 250 m (the Stage 8 direct + ring
  outcome radius, same projection and A13 distance rule) of the outage
  location during the Stage 8 canonical pre-window length (14 days) ending
  strictly before created_date. Only information available before the outage
  starts is used.
- duration_factor: outage_duration_hours / 24 (days), valid only for
  0 < hours <= 8,760 (the Stage 3 validity rule is 0.5-8,760 h); anything else
  is excluded, never scored.

The max(0, .) clip means a non-positive tau_net gives raw_priority 0 for every
outage. If all raw priorities are equal the score is 0 (documented degenerate
case). The score is a decision-support index, not a probability.
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.features import build_causal_panel as s8  # noqa: E402  (accepted Stage 8 constants)

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUT_DIR = PROJECT_ROOT / "outputs"

OUTAGES_FILE = PROCESSED_DIR / "clean_streetlights.parquet"
CRIME_FILE = PROCESSED_DIR / "clean_crime.parquet"
STAGE11_FILE = OUTPUT_DIR / "displacement_estimates.csv"
SCORED_FILE = PROCESSED_DIR / "outages_scored.parquet"

OUTAGE_ID = "unique_key"
TAU_NET_EFFECT = "net_post"

LOOKBACK_DAYS = s8.PRE_WINDOW_DAYS
RADIUS_M = s8.OUTCOME_RADIUS_M
KD_TOLERANCE_M = s8.KD_QUERY_TOLERANCE_M
TARGET_CRS = s8.TARGET_CRS

# Stage 3 validity rule (0.5-8,760 h): an outage outside it is not scored.
MAX_DURATION_HOURS = 8760.0
HOURS_PER_DAY = 24.0

HIGH_MIN = 80.0
MEDIUM_MIN = 40.0
TIERS = ("High", "Medium", "Low")

CHUNK = 5000

REQUIRED_OUTAGE_COLUMNS = (OUTAGE_ID, "created_date", "closed_date",
                           "latitude", "longitude", "outage_duration_hours")
REQUIRED_CRIME_COLUMNS = ("crime_datetime", "latitude", "longitude")

ADDED_COLUMNS = (
    "scored", "exclusion_reason", "tau_net", "tau_net_source",
    "local_crime_rate", "duration_factor", "raw_priority", "priority_score",
    "priority_tier",
)


# ============================================================
# Inputs
# ============================================================

def _require(frame, columns, label):
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise ValueError(f"{label} is missing required columns: {missing}")


def load_outages(path=OUTAGES_FILE):
    outages = pd.read_parquet(path)
    _require(outages, REQUIRED_OUTAGE_COLUMNS, str(path))
    if outages[OUTAGE_ID].isna().any() or not outages[OUTAGE_ID].is_unique:
        raise ValueError(f"{OUTAGE_ID} must be unique and non-null")
    return outages.reset_index(drop=True)


def load_crime(path=CRIME_FILE):
    crime = pd.read_parquet(path, columns=list(REQUIRED_CRIME_COLUMNS))
    crime = crime.dropna(subset=list(REQUIRED_CRIME_COLUMNS))
    return crime.reset_index(drop=True)


def load_tau_net(path=STAGE11_FILE, effect=TAU_NET_EFFECT):
    """The accepted Stage 11 net effect, unchanged. Returns (value, SE)."""

    table = pd.read_csv(path)
    _require(table, ("effect_name", "estimate", "standard_error"), str(path))
    row = table[table["effect_name"] == effect]
    if len(row) != 1:
        raise ValueError(f"{path} must contain exactly one {effect!r} row")
    tau, se = float(row["estimate"].iloc[0]), float(row["standard_error"].iloc[0])
    if not np.isfinite(tau):
        raise ValueError(f"{effect} is not finite")
    return tau, se


# ============================================================
# Active / scorable outages
# ============================================================

def select_scorable(outages, crime_start, crime_end):
    """
    Exclusion reason per outage ("" = scorable), first failing rule wins:
    invalid location, invalid duration, closed before created, look-back
    window not covered by the crime data.
    """

    hours = outages["outage_duration_hours"].astype(float)
    created = outages["created_date"]
    closed = outages["closed_date"]
    reason = pd.Series("", index=outages.index, dtype=object)

    rules = (
        ("missing_location",
         outages[["latitude", "longitude"]].isna().any(axis=1)),
        ("invalid_duration",
         hours.isna() | ~np.isfinite(hours) | (hours <= 0) | (hours > MAX_DURATION_HOURS)),
        ("closed_before_created", closed.isna() | (closed < created)),
        ("created_missing", created.isna()),
        ("lookback_not_covered_by_crime_data",
         created.isna()
         | (created - pd.Timedelta(days=LOOKBACK_DAYS) < crime_start)
         | (created > crime_end)),
    )
    for name, failed in rules:
        reason = reason.where((reason != "") | ~failed, name)
    return reason


# ============================================================
# Components
# ============================================================

def local_crime_rate(outages, crime):
    """
    Crimes per day within RADIUS_M of each outage in
    [created - LOOKBACK_DAYS, created) (end exclusive: nothing at or after
    the outage start is used). Distances follow Stage 8 (KD candidates at
    r + 1e-6 m, membership decided by the explicit formula).
    """

    to_metres = Transformer.from_crs("EPSG:4326", TARGET_CRS, always_xy=True)
    cx, cy = to_metres.transform(crime["longitude"].to_numpy(),
                                 crime["latitude"].to_numpy())
    crime_xy = np.column_stack([cx, cy])
    crime_times = crime["crime_datetime"].to_numpy("datetime64[us]")
    tree = cKDTree(crime_xy)

    ox, oy = to_metres.transform(outages["longitude"].to_numpy(),
                                 outages["latitude"].to_numpy())
    points = np.column_stack([ox, oy])
    end = outages["created_date"].to_numpy("datetime64[us]")
    start = end - np.timedelta64(LOOKBACK_DAYS, "D")

    counts = np.zeros(len(outages), dtype=np.int64)
    for lo in range(0, len(outages), CHUNK):
        hi = min(lo + CHUNK, len(outages))
        neighbours = tree.query_ball_point(points[lo:hi], r=RADIUS_M + KD_TOLERANCE_M)
        for i, cand in enumerate(neighbours, start=lo):
            if not cand:
                continue
            cand = np.asarray(cand, dtype=np.int64)
            when = crime_times[cand]
            cand = cand[(when >= start[i]) & (when < end[i])]
            if len(cand) == 0:
                continue
            d = np.hypot(crime_xy[cand, 0] - points[i, 0], crime_xy[cand, 1] - points[i, 1])
            counts[i] = int((d <= RADIUS_M).sum())
    return counts / float(LOOKBACK_DAYS)


def duration_factor(hours):
    """Outage duration in days. Invalid values (<= 0, NaN, > 8,760 h) give NaN."""

    hours = np.asarray(hours, dtype=float)
    valid = np.isfinite(hours) & (hours > 0) & (hours <= MAX_DURATION_HOURS)
    return np.where(valid, hours / HOURS_PER_DAY, np.nan)


def raw_priority(tau_net, crime_rate, factor):
    """max(0, tau_net * local_crime_rate * duration_factor); NaN if any input is NaN."""

    product = tau_net * np.asarray(crime_rate, dtype=float) * np.asarray(factor, dtype=float)
    return np.where(np.isnan(product), np.nan, np.maximum(0.0, product))


def normalise(raw):
    """
    Min-max to 0-100 over the scored outages. If every raw value is equal
    (including all zero) there is no spread: the score is 0 for all.
    """

    raw = np.asarray(raw, dtype=float)
    out = np.full(raw.shape, np.nan)
    valid = np.isfinite(raw)
    if not valid.any():
        return out
    lo, hi = raw[valid].min(), raw[valid].max()
    if hi == lo:
        out[valid] = 0.0
    else:
        out[valid] = np.clip(100.0 * (raw[valid] - lo) / (hi - lo), 0.0, 100.0)
    return out


def assign_tier(score):
    """High if score >= 80, Medium if >= 40, else Low; NaN score gives None."""

    score = np.asarray(score, dtype=float)
    tier = np.where(score >= HIGH_MIN, "High",
                    np.where(score >= MEDIUM_MIN, "Medium", "Low")).astype(object)
    tier[np.isnan(score)] = None
    return tier


# ============================================================
# Pipeline
# ============================================================

def score_outages(outages, crime, tau_net, tau_source=TAU_NET_EFFECT):
    scored = outages.copy()
    reason = select_scorable(scored, crime["crime_datetime"].min(),
                             crime["crime_datetime"].max())
    ok = (reason == "").to_numpy()

    scored["scored"] = ok
    scored["exclusion_reason"] = reason.where(~reason.eq(""), None)
    scored["tau_net"] = float(tau_net)
    scored["tau_net_source"] = f"stage11:{tau_source}"

    rate = np.full(len(scored), np.nan)
    if ok.any():
        rate[ok] = local_crime_rate(scored.loc[ok], crime)
    scored["local_crime_rate"] = rate
    scored["duration_factor"] = duration_factor(
        np.where(ok, scored["outage_duration_hours"], np.nan))
    scored["raw_priority"] = raw_priority(tau_net, scored["local_crime_rate"],
                                          scored["duration_factor"])
    scored["priority_score"] = normalise(scored["raw_priority"])
    scored["priority_tier"] = pd.Series(assign_tier(scored["priority_score"]),
                                        index=scored.index, dtype=object)
    return scored


def write_scored(scored, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    scored.to_parquet(temporary, index=False)
    temporary.replace(path)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Stage 12 priority score generation")
    parser.add_argument("--outages", type=Path, default=OUTAGES_FILE)
    parser.add_argument("--crime", type=Path, default=CRIME_FILE)
    parser.add_argument("--stage11", type=Path, default=STAGE11_FILE,
                        help="Stage 11 displacement_estimates.csv (read only)")
    parser.add_argument("--out", type=Path, default=SCORED_FILE)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    print("Loading inputs...")
    outages = load_outages(args.outages)
    crime = load_crime(args.crime)
    tau_net, tau_se = load_tau_net(args.stage11)
    print(f"Outages: {len(outages):,}; crimes: {len(crime):,}; "
          f"tau_net ({TAU_NET_EFFECT}) = {tau_net:+.6f} (SE {tau_se:.6f})")

    scored = score_outages(outages, crime, tau_net)

    print(f"Scored: {int(scored['scored'].sum()):,}; excluded: {int((~scored['scored']).sum()):,}")
    print(scored["exclusion_reason"].value_counts().to_string())
    print(scored["priority_tier"].value_counts(dropna=False).to_string())

    write_scored(scored, args.out)
    print(f"\nScored outages:\n{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
