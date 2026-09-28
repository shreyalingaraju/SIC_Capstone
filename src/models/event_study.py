"""
Stage 10: event study (Issue 4 panel).

Reads causal_panel.parquet (Stage 8) and the Stage 5 crime file and
writes event_study_coefficients.csv, event_study_plot.png and
event_study_summary.json to --out (default outputs/).

Event units are the panel's units (unit_id = pair x role). Geometry comes
from the panel's location_x_m / location_y_m, which are the Stage 7 pair
columns (EPSG:32118); the outage table is not read. Crime points are
projected from latitude/longitude with pyproj (always_xy), as in Stages 7
and 8. KD-tree candidates at 100 + 1e-6 m; the explicit distance formula
decides (A13).

Event time: relative weeks -4 ... +4 around the treatment's created
date (the pair's control uses the same calendar), week -1 = days
[-14, -7) is the omitted reference. Week 0 starts at day 0, so days
[-7, 0) belong to no week: this known gap is flagged, not fixed (M8).
Only pairs whose [created - 35 d, created + 35 d] lies inside the crime
coverage are used. Outcome: crimes within 100 m.

Estimation: unit_id and relative-week fixed effects (two-way within
transformation), SEs clustered on treatment_h3_res7 (D13, D15), and the
joint pre-trend F-test on weeks -4, -3, -2. Results are provisional (M8).
"""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import statsmodels.api as sm  # noqa: E402
from pyproj import Transformer  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402


# ============================================================
# LightSafe - Stage 10: Event Study Analysis
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUT_DIR = PROJECT_ROOT / "outputs"

CAUSAL_FILE = PROCESSED_DIR / "causal_panel.parquet"
CRIME_FILE = PROCESSED_DIR / "clean_crime.parquet"

COEFFICIENT_FILENAME = "event_study_coefficients.csv"
PLOT_FILENAME = "event_study_plot.png"
SUMMARY_FILENAME = "event_study_summary.json"

TARGET_CRS = "EPSG:32118"

RADIUS_M = 100.0
KD_QUERY_TOLERANCE_M = 1e-6

EVENT_WINDOW_DAYS = 35

CLUSTER_COLUMN = "treatment_h3_res7"

# Event-study periods.
# -1 is the omitted reference period.
RELATIVE_WEEKS = [-4, -3, -2, -1, 0, 1, 2, 3, 4]

# Weekly windows in days relative to created (start inclusive, end
# exclusive). Days [-7, 0) are not covered (known gap, M8).
WEEK_WINDOWS = {
    -4: (-35, -28),
    -3: (-28, -21),
    -2: (-21, -14),
    -1: (-14, -7),
     0: (0, 7),
     1: (7, 14),
     2: (14, 21),
     3: (21, 28),
     4: (28, 35),
}

EVENT_WEEKS = [-4, -3, -2, 0, 1, 2, 3, 4]

SAFE_EVENT_NAMES = {
    -4: "treat_event_m4",
    -3: "treat_event_m3",
    -2: "treat_event_m2",
     0: "treat_event_0",
     1: "treat_event_p1",
     2: "treat_event_p2",
     3: "treat_event_p3",
     4: "treat_event_p4",
}

UNIT_COLUMNS = [
    "pair_id",
    "unit_id",
    "location_key",
    "location_x_m",
    "location_y_m",
    "created_date",
    "closed_date",
    "treatment",
    "borough",
    "h3_res9",
    "h3_res10",
    CLUSTER_COLUMN,
]


# ============================================================
# Data
# ============================================================

def load_units(panel_path):
    """One row per unit_id, with its geometry, from the Stage 8 panel."""

    panel = pd.read_parquet(panel_path)

    missing = [column for column in UNIT_COLUMNS if column not in panel.columns]
    if missing:
        raise ValueError(
            f"Missing panel columns {missing}. Is {panel_path} an Issue 4 "
            "Stage 8 panel?"
        )

    units = (
        panel[UNIT_COLUMNS]
        .drop_duplicates("unit_id")
        .sort_values("unit_id", kind="mergesort")
        .reset_index(drop=True)
    )

    if units[["location_x_m", "location_y_m"]].isna().any().any():
        raise ValueError("event-study units without geometry")

    return units


def load_crime(crime_path):
    crime = pd.read_parquet(
        crime_path, columns=["crime_datetime", "latitude", "longitude"]
    )
    crime = crime.dropna(subset=["crime_datetime", "latitude", "longitude"])

    x, y = Transformer.from_crs(
        "EPSG:4326", TARGET_CRS, always_xy=True
    ).transform(
        crime["longitude"].to_numpy(dtype=np.float64),
        crime["latitude"].to_numpy(dtype=np.float64),
    )

    times = pd.to_datetime(crime["crime_datetime"]).to_numpy()
    return times, np.column_stack([np.asarray(x), np.asarray(y)])


def complete_units(units, crime_times):
    """Pairs whose full [-35, +35] day window lies inside crime coverage."""

    crime_min = crime_times.min()
    crime_max = crime_times.max()

    earliest = units["created_date"] - pd.Timedelta(days=EVENT_WINDOW_DAYS)
    latest = units["created_date"] + pd.Timedelta(days=EVENT_WINDOW_DAYS)
    complete = (earliest.to_numpy() >= crime_min) & (latest.to_numpy() <= crime_max)

    complete_pairs = (
        pd.Series(complete, index=units["pair_id"]).groupby(level=0).all()
    )
    keep = units["pair_id"].map(complete_pairs).to_numpy(dtype=bool)

    print("Complete treatment-control pairs:", int(complete_pairs.sum()))
    print("Complete event-study units:", int(keep.sum()))

    return units.loc[keep].reset_index(drop=True)


def build_event_rows(units, crime_times, crime_coords):
    """Crime counts within 100 m per unit and relative week."""

    tree = cKDTree(crime_coords)
    points = units[["location_x_m", "location_y_m"]].to_numpy(dtype=np.float64)
    neighbours = tree.query_ball_point(points, r=RADIUS_M + KD_QUERY_TOLERANCE_M)
    created = units["created_date"].to_numpy()

    counts = np.zeros((len(units), len(RELATIVE_WEEKS)), dtype=np.int64)
    for unit, candidates in enumerate(neighbours):
        if not candidates:
            continue
        candidates = np.asarray(candidates, dtype=np.int64)
        distance = np.sqrt(
            (crime_coords[candidates, 0] - points[unit, 0]) ** 2
            + (crime_coords[candidates, 1] - points[unit, 1]) ** 2
        )
        candidates = candidates[distance <= RADIUS_M]
        elapsed_days = (
            crime_times[candidates] - created[unit]
        ) / np.timedelta64(1, "D")
        for k, week in enumerate(RELATIVE_WEEKS):
            start_day, end_day = WEEK_WINDOWS[week]
            counts[unit, k] = int(
                np.sum((elapsed_days >= start_day) & (elapsed_days < end_day))
            )

    rows = units.loc[units.index.repeat(len(RELATIVE_WEEKS))].reset_index(drop=True)
    rows["rel_week"] = np.tile(RELATIVE_WEEKS, len(units))
    rows["crime_count"] = counts.reshape(-1)

    for week in EVENT_WEEKS:
        rows[SAFE_EVENT_NAMES[week]] = (
            (rows["treatment"] == 1) & (rows["rel_week"] == week)
        ).astype(int)

    return rows


# ============================================================
# Estimation
# ============================================================

def estimate(event_study):
    """
    unit_id and relative-week fixed effects by the two-way within
    transformation; SEs clustered on treatment_h3_res7.
    """

    interaction_terms = list(SAFE_EVENT_NAMES.values())
    variables = ["crime_count"] + interaction_terms
    data = event_study[variables]

    grand_means = data.mean()
    unit_means = data.groupby(event_study["unit_id"]).transform("mean")
    week_means = data.groupby(event_study["rel_week"]).transform("mean")
    demeaned = data - unit_means - week_means + grand_means

    groups = pd.factorize(event_study[CLUSTER_COLUMN].astype(object), sort=True)[0]
    model = sm.OLS(demeaned["crime_count"], demeaned[interaction_terms]).fit(
        cov_type="cluster", cov_kwds={"groups": groups},
    )

    confidence_intervals = model.conf_int()
    rows = []
    for week, name in SAFE_EVENT_NAMES.items():
        ci_low, ci_high = confidence_intervals.loc[name]
        rows.append({
            "rel_week": week,
            "coefficient": model.params[name],
            "std_error": model.bse[name],
            "ci_lower": ci_low,
            "ci_upper": ci_high,
            "p_value": model.pvalues[name],
        })
    rows.append({"rel_week": -1, "coefficient": 0.0, "std_error": np.nan,
                 "ci_lower": np.nan, "ci_upper": np.nan, "p_value": np.nan})

    results = (
        pd.DataFrame(rows).sort_values("rel_week").reset_index(drop=True)
    )

    # Joint parallel-trends test, H0: weeks -4, -3, -2 are jointly zero.
    pretrend = model.wald_test(
        "treat_event_m4 = 0, treat_event_m3 = 0, treat_event_m2 = 0",
        use_f=True,
        scalar=True,
    )

    return (
        model,
        results,
        float(np.asarray(pretrend.statistic).squeeze()),
        float(np.asarray(pretrend.pvalue).squeeze()),
        int(len(np.unique(groups))),
    )


# ============================================================
# Output
# ============================================================

def save_plot(results, path):
    non_baseline = results["rel_week"] != -1
    fig, ax = plt.subplots(figsize=(10, 6))

    ax.errorbar(
        results.loc[non_baseline, "rel_week"],
        results.loc[non_baseline, "coefficient"],
        yerr=[
            results.loc[non_baseline, "coefficient"]
            - results.loc[non_baseline, "ci_lower"],
            results.loc[non_baseline, "ci_upper"]
            - results.loc[non_baseline, "coefficient"],
        ],
        fmt="o-",
        capsize=4,
        linewidth=1.5,
    )
    ax.scatter(-1, 0, marker="o", s=60, label="Reference period (t = -1)")
    ax.axhline(0, linestyle="--", linewidth=1)
    ax.axvline(-1, linestyle=":", linewidth=1)
    ax.set_xlabel("Relative Week")
    ax.set_ylabel("Crime Effect (crimes within 100 m)")
    ax.set_title("Event-Study Estimates Around Outage Start (provisional)")
    ax.set_xticks(RELATIVE_WEEKS)
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    fig.savefig(path, dpi=300, bbox_inches="tight", metadata={"Software": None})
    plt.close(fig)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Stage 10 event study")
    parser.add_argument("--panel", type=Path, default=CAUSAL_FILE,
                        help="Stage 8 causal_panel.parquet")
    parser.add_argument("--crime", type=Path, default=CRIME_FILE,
                        help="Stage 5 clean_crime.parquet")
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR,
                        help="output directory")
    return parser.parse_args(argv)


def build_event_study_data(panel_path=CAUSAL_FILE, crime_path=CRIME_FILE,
                           out_dir=OUTPUT_DIR):
    print("Loading source data...")
    units = load_units(panel_path)
    crime_times, crime_coords = load_crime(crime_path)
    print("Event-study units:", len(units))
    print("Crime records:", len(crime_times))

    units = complete_units(units, crime_times)

    print("Constructing event-study crime counts...")
    event_study = build_event_rows(units, crime_times, crime_coords)
    if event_study.empty:
        raise ValueError("Event-study dataset is empty.")
    print("Event-study observations:", len(event_study))

    print("Estimating event-study model...")
    model, results, statistic, p_value, n_clusters = estimate(event_study)
    print("Observations:", int(model.nobs))
    print("Unit fixed effects (unit_id):", event_study["unit_id"].nunique())
    print(f"Clusters ({CLUSTER_COLUMN}):", n_clusters)

    print("\nJoint parallel-trends test")
    print("--------------------------")
    print("F-statistic:", statistic)
    print("p-value:", p_value)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results.to_csv(out_dir / COEFFICIENT_FILENAME, index=False)
    save_plot(results, out_dir / PLOT_FILENAME)

    summary = {
        "status": "provisional (M8 open; days [-7, 0) not covered by any week)",
        "panel": Path(panel_path).as_posix(),
        "observations": int(model.nobs),
        "units": int(event_study["unit_id"].nunique()),
        "pairs": int(event_study["pair_id"].nunique()),
        "fixed_effects": ["unit_id", "rel_week"],
        "cluster_variable": CLUSTER_COLUMN,
        "n_clusters": n_clusters,
        "reference_week": -1,
        "week_windows_days": {str(k): list(v) for k, v in WEEK_WINDOWS.items()},
        "pretrend_test": {"weeks": [-4, -3, -2], "f_statistic": statistic,
                          "p_value": p_value},
        "coefficients": [
            {key: (None if isinstance(value, float) and not math.isfinite(value)
                   else value)
             for key, value in row.items()}
            for row in results.to_dict("records")
        ],
    }
    (out_dir / SUMMARY_FILENAME).write_text(
        json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8"
    )

    print("\nOutputs written to:", out_dir)
    print("\nFinal event-study results:")
    print(results.to_string(index=False))

    return results, statistic, p_value


def main(argv=None):
    args = parse_args(argv)
    build_event_study_data(args.panel, args.crime, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
