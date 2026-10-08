"""
Stage 8: causal panel (Issue 4 schema).

Reads the Stage 7 pairs (control_area_pairs.parquet) and the Stage 5
crime file and writes causal_panel.parquet: one row per unit and period,
where a unit is one role (treatment T or control C) of one pair.

Geometry, borough, precinct and H3 cells come from the Stage 7 pair
columns for both roles (Q5: the pair columns are the single source of
truth; Stage 7 H1 guarantees the treatment point agrees with Stage 3
within 1e-6 degrees). No outage table is read.

Windows (unchanged), all inclusive:
    pre     [created - 14 d, created]      (--pre-window canonical)
            [created - 21 d, created - 7 d) (--pre-window shifted, D19)
    during  [created, closed]
    post    [closed, closed + 14 d]
Outcomes (unchanged): crime_100m = crimes with d <= 100 m; crime_250m =
crimes with 100 < d <= 250 m (displacement ring). KD-tree queries at
r + 1e-6 m only generate candidates; the explicit distance formula
decides (A13).

See docs/issue4_migration_s8_s10.md for every change from the old schema.
"""

import argparse
import hashlib
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import CRS, Transformer
from scipy.spatial import cKDTree


# ============================================================
# Paths and constants
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from src import profile as _profile  # noqa: E402  (dataset profile: paths, bbox, CRS)

PROCESSED_DIR = PROJECT_ROOT / _profile.PROCESSED_DIR

PAIRS_FILE = PROCESSED_DIR / "control_area_pairs.parquet"
CRIME_FILE = PROCESSED_DIR / "clean_crime.parquet"

OUTPUT_FILE = PROCESSED_DIR / "causal_panel.parquet"
LEGACY_PANEL_BACKUP = "causal_panel.pre_issue4.parquet"

# NAD83 / New York Long Island in metres (EPSG:2263 is US feet); must
# equal Stage 7's PROJECTED_CRS.
TARGET_CRS = _profile.CRS

if CRS(TARGET_CRS).axis_info[0].unit_name != "metre":
    raise ValueError(f"{TARGET_CRS} must use metre units")

# Outcome radii (Stage 7 DIRECT_RADIUS_M / OUTCOME_RADIUS_M).
DIRECT_RADIUS_M = 100.0
OUTCOME_RADIUS_M = 250.0

# Stage 7 MATCH_BAND_MIN_M (pair distance assert).
MATCH_BAND_MIN_M = 500.0

KD_QUERY_TOLERANCE_M = 1e-6

PRE_WINDOW_DAYS = 14
POST_WINDOW_DAYS = 14

# D19 robustness pre-window [created - 21 d, created - 7 d).
SHIFTED_PRE_WINDOW = (21, 7)

PRE_WINDOW_CHOICES = ("canonical", "shifted")

# Pair columns Stage 8 needs (Stage 7 PAIRS_DTYPES).
PAIR_COLUMNS = (
    "pair_id",
    "treatment_key",
    "treatment_site_id",
    "control_site_id",
    "created_date",
    "closed_date",
    "outage_duration_hours",
    "treatment_latitude",
    "treatment_longitude",
    "treatment_x_m",
    "treatment_y_m",
    "control_latitude",
    "control_longitude",
    "control_x_m",
    "control_y_m",
    "treatment_borough",
    "control_borough",
    "treatment_police_precinct",
    "treatment_h3_res7",
    "treatment_h3_res9",
    "treatment_h3_res10",
    "control_h3_res7",
    "control_h3_res9",
    "control_h3_res10",
    "distance_m",
    "treatment_base_100m",
    "treatment_base_250m",
    "control_base_100m",
    "control_base_250m",
)

PERIODS = (("pre", 0, 0), ("during", 1, 1), ("post", 2, 1))

ROLES = (("T", "treatment", 1), ("C", "control", 0))


# ============================================================
# Loading
# ============================================================

def load_pairs(path):
    pairs = pd.read_parquet(path)

    missing = [column for column in PAIR_COLUMNS if column not in pairs.columns]
    if missing:
        raise ValueError(
            f"{path} is not an Issue 4 Stage 7 pairs file; missing {missing}"
        )

    pairs = pairs[list(PAIR_COLUMNS)].copy()

    if not pairs["pair_id"].is_unique:
        raise ValueError("pair_id is not unique in the pairs file")

    return pairs


def load_crime(path):
    """
    Crime times and projected points. Rows with a null crime_datetime,
    latitude or longitude are dropped (the Stage 7 rule); the points are
    projected with pyproj (always_xy), as in Stage 7.
    """

    crime = pd.read_parquet(
        path, columns=["crime_datetime", "latitude", "longitude"]
    )
    crime = crime.dropna(subset=["crime_datetime", "latitude", "longitude"])

    x, y = Transformer.from_crs(
        "EPSG:4326", TARGET_CRS, always_xy=True
    ).transform(
        crime["longitude"].to_numpy(dtype=np.float64),
        crime["latitude"].to_numpy(dtype=np.float64),
    )

    times = pd.to_datetime(crime["crime_datetime"]).to_numpy()
    coords = np.column_stack([np.asarray(x), np.asarray(y)])

    return times, coords


# ============================================================
# Windows and coverage
# ============================================================

def add_windows(pairs, pre_window):
    """
    Period windows as (start, end, end_inclusive). The canonical
    pre-window is closed; the shifted D19 pre-window is half-open.
    """

    created = pairs["created_date"]
    closed = pairs["closed_date"]

    if pre_window == "canonical":
        pairs["pre_start"] = created - pd.Timedelta(days=PRE_WINDOW_DAYS)
        pairs["pre_end"] = created
        pairs["pre_end_inclusive"] = True
    else:
        start_days, end_days = SHIFTED_PRE_WINDOW
        pairs["pre_start"] = created - pd.Timedelta(days=start_days)
        pairs["pre_end"] = created - pd.Timedelta(days=end_days)
        pairs["pre_end_inclusive"] = False

    pairs["during_start"] = created
    pairs["during_end"] = closed
    pairs["during_end_inclusive"] = True

    pairs["post_start"] = closed
    pairs["post_end"] = closed + pd.Timedelta(days=POST_WINDOW_DAYS)
    pairs["post_end_inclusive"] = True

    return pairs


def check_coverage(pairs, crime_times):
    """
    D3 guard (kept): every window must lie inside the crime coverage.
    Stage 7 already enforces this, so any uncovered pair stops the run.
    """

    coverage_start = crime_times.min()
    coverage_end = crime_times.max()

    covered = (
        (pairs["pre_start"].to_numpy() >= coverage_start)
        & (pairs["post_end"].to_numpy() <= coverage_end)
    )

    print(f"Crime coverage: {coverage_start} to {coverage_end}")
    print("Pairs outside crime coverage (D3 guard, must be 0):", int((~covered).sum()))

    if not covered.all():
        raise AssertionError(
            f"D3 guard: {int((~covered).sum())} pairs outside crime coverage; "
            "Stage 7 should have removed them"
        )


# ============================================================
# Crime counts
# ============================================================

def count_crimes(points, windows, crime_tree, crime_coords, crime_times):
    """
    Crimes around each point during each window: d <= 100 m and
    100 < d <= 250 m, KD-tree candidates at 250 + 1e-6 m, explicit
    formula for membership (A13).

    windows: {period: (start array, end array, end_inclusive array)}.
    Returns {period: (direct counts, ring counts)}.
    """

    counts = {
        period: (np.zeros(len(points), dtype=np.int64),
                 np.zeros(len(points), dtype=np.int64))
        for period in windows
    }

    neighbours = crime_tree.query_ball_point(
        points, r=OUTCOME_RADIUS_M + KD_QUERY_TOLERANCE_M
    )

    for unit, candidates in enumerate(neighbours):
        if not candidates:
            continue

        candidates = np.asarray(candidates, dtype=np.int64)
        distance = np.sqrt(
            (crime_coords[candidates, 0] - points[unit, 0]) ** 2
            + (crime_coords[candidates, 1] - points[unit, 1]) ** 2
        )
        keep = distance <= OUTCOME_RADIUS_M
        candidates, distance = candidates[keep], distance[keep]
        times = crime_times[candidates]
        direct = distance <= DIRECT_RADIUS_M

        for period, (start, end, inclusive) in windows.items():
            before_end = (
                times <= end[unit] if inclusive[unit] else times < end[unit]
            )
            in_window = (times >= start[unit]) & before_end
            counts[period][0][unit] = int(np.sum(in_window & direct))
            counts[period][1][unit] = int(np.sum(in_window & ~direct))

    return counts


# ============================================================
# Panel
# ============================================================

def build_panel(pairs, crime_times, crime_coords):
    crime_tree = cKDTree(crime_coords)

    windows = {
        period: (
            pairs[f"{period}_start"].to_numpy(),
            pairs[f"{period}_end"].to_numpy(),
            pairs[f"{period}_end_inclusive"].to_numpy(),
        )
        for period, _, _ in PERIODS
    }

    frames = []
    for role, prefix, is_treatment in ROLES:
        points = pairs[[f"{prefix}_x_m", f"{prefix}_y_m"]].to_numpy(np.float64)
        counts = count_crimes(points, windows, crime_tree, crime_coords, crime_times)
        pre_100m = counts["pre"][0]

        for period, period_order, post in PERIODS:
            frames.append(pd.DataFrame({
                "pair_id": pairs["pair_id"].to_numpy(),
                "unit_id": pairs["pair_id"].to_numpy() + f"_{role}",
                "role": role,
                "created_date": pairs["created_date"].to_numpy(),
                "closed_date": pairs["closed_date"].to_numpy(),
                "location_key": pairs[f"{prefix}_site_id"].to_numpy(),
                "location_latitude": pairs[f"{prefix}_latitude"].to_numpy(),
                "location_longitude": pairs[f"{prefix}_longitude"].to_numpy(),
                "location_x_m": pairs[f"{prefix}_x_m"].to_numpy(),
                "location_y_m": pairs[f"{prefix}_y_m"].to_numpy(),
                "period": period,
                "period_order": period_order,
                "treatment": is_treatment,
                "post": post,
                "treatment_x_post": is_treatment * post,
                "crime_100m": counts[period][0],
                "crime_250m": counts[period][1],
                # Unit's pre-window 100 m count (known bad control, M8).
                "baseline_crime_intensity": pre_100m,
                "base_100m": pairs[f"{prefix}_base_100m"].to_numpy(),
                "base_250m": pairs[f"{prefix}_base_250m"].to_numpy(),
                "borough": pairs[f"{prefix}_borough"].to_numpy(),
                # Treatment outage duration is the common exposure
                # duration for the pair.
                "outage_duration_hours": pairs["outage_duration_hours"].to_numpy(),
                "h3_res9": pairs[f"{prefix}_h3_res9"].to_numpy(),
                "h3_res10": pairs[f"{prefix}_h3_res10"].to_numpy(),
                "treatment_h3_res7": pairs["treatment_h3_res7"].to_numpy(),
                "control_h3_res7": pairs["control_h3_res7"].to_numpy(),
                "treatment_police_precinct": pairs[
                    "treatment_police_precinct"
                ].to_numpy(),
            }))

    role_order = {"T": 0, "C": 1}
    panel = pd.concat(frames, ignore_index=True)
    panel = (
        panel.assign(_role=panel["role"].map(role_order))
        .sort_values(["pair_id", "period_order", "_role"], kind="mergesort")
        .drop(columns="_role")
        .reset_index(drop=True)
    )

    for column in ("crime_100m", "crime_250m", "baseline_crime_intensity",
                   "base_100m", "base_250m", "period_order", "treatment",
                   "post", "treatment_x_post"):
        panel[column] = panel[column].astype("int64")

    for column in ("pair_id", "unit_id", "role", "location_key", "period",
                   "borough", "h3_res9", "h3_res10", "treatment_h3_res7",
                   "control_h3_res7", "treatment_police_precinct"):
        panel[column] = panel[column].astype("string")

    return panel


# ============================================================
# Validation
# ============================================================

def validate(pairs, panel):
    n_pairs = len(pairs)

    distance = np.sqrt(
        (pairs["treatment_x_m"] - pairs["control_x_m"]) ** 2
        + (pairs["treatment_y_m"] - pairs["control_y_m"]) ** 2
    )
    assert (distance >= MATCH_BAND_MIN_M - 1e-6).all(), (
        "a control is closer than the Stage 7 band minimum"
    )

    assert panel["treatment"].value_counts().to_dict() == {
        1: 3 * n_pairs,
        0: 3 * n_pairs,
    }
    assert panel["period"].value_counts().to_dict() == {
        "pre": 2 * n_pairs,
        "during": 2 * n_pairs,
        "post": 2 * n_pairs,
    }

    per_unit = panel.groupby("unit_id")["period"].agg(["size", "nunique"])
    assert len(per_unit) == 2 * n_pairs, "unit_id count is not 2 x pairs"
    assert ((per_unit["size"] == 3) & (per_unit["nunique"] == 3)).all(), (
        "a unit_id does not have exactly one row per period"
    )
    assert panel.groupby("unit_id")["treatment"].nunique().eq(1).all(), (
        "treatment status varies within a unit"
    )

    required = [
        "pair_id", "unit_id", "location_key", "location_x_m", "location_y_m",
        "crime_100m", "crime_250m", "baseline_crime_intensity", "base_100m",
        "base_250m", "borough", "treatment_h3_res7", "control_h3_res7",
    ]
    nulls = panel[required].isna().sum()
    assert nulls.sum() == 0, f"nulls in required panel columns: {nulls[nulls > 0].to_dict()}"


# ============================================================
# Output
# ============================================================

def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(2 ** 20), b""):
            digest.update(block)
    return digest.hexdigest()


def preserve_legacy_panel(out_path):
    """
    Q9: before the first write, keep a pre-Issue-4 panel (no unit_id) as
    causal_panel.pre_issue4.parquet next to it (copy, sha256 verified).
    An existing backup is never overwritten.
    """

    import pyarrow.parquet as pq

    out_path = Path(out_path)
    backup = out_path.parent / LEGACY_PANEL_BACKUP

    if backup.exists():
        return f"existing backup kept ({backup})"
    if not out_path.exists():
        return "no existing panel"
    if "unit_id" in pq.read_schema(out_path).names:
        return "existing panel already has the Issue 4 schema; not backed up"

    shutil.copy2(out_path, backup)
    if _sha256(out_path) != _sha256(backup):
        backup.unlink()
        raise RuntimeError(f"legacy panel backup failed: sha256 of {backup} differs")
    return f"backed up to {backup} (sha256 {_sha256(backup)[:16]}...)"


def write_panel(panel, out_path):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("Legacy panel backup:", preserve_legacy_panel(out_path))

    temporary = out_path.with_name(out_path.name + ".tmp")
    panel.to_parquet(temporary, index=False)
    os.replace(temporary, out_path)

    return _sha256(out_path)


# ============================================================
# Main
# ============================================================

def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--pairs", type=Path, default=PAIRS_FILE,
                        help="Stage 7 control_area_pairs.parquet")
    parser.add_argument("--crime", type=Path, default=CRIME_FILE,
                        help="Stage 5 clean_crime.parquet")
    parser.add_argument("--out", type=Path, default=OUTPUT_FILE,
                        help="output causal_panel.parquet")
    parser.add_argument("--pre-window", choices=PRE_WINDOW_CHOICES,
                        default="canonical",
                        help="canonical [c-14d, c] or shifted [c-21d, c-7d) (D19)")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    print("Loading Stage 8 input data...")
    pairs = load_pairs(args.pairs)
    crime_times, crime_coords = load_crime(args.crime)
    print(f"Pairs: {len(pairs):,}; crimes: {len(crime_times):,}")
    print(f"Pre-window: {args.pre_window}")

    pairs = add_windows(pairs, args.pre_window)
    check_coverage(pairs, crime_times)

    print("Calculating crime counts...")
    panel = build_panel(pairs, crime_times, crime_coords)

    print("\nValidating causal panel...")
    print("Panel shape:", panel.shape)
    print("Unique pairs:", panel["pair_id"].nunique())
    print("Unique units:", panel["unit_id"].nunique())
    print("Unique locations:", panel["location_key"].nunique())
    print("\nPeriod counts:")
    print(panel["period"].value_counts())
    validate(pairs, panel)

    digest = write_panel(panel, args.out)
    print(f"\nCausal panel saved to:\n{args.out}\nsha256 {digest}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
