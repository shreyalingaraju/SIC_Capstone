from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt
import statsmodels.api as sm
from scipy.spatial import cKDTree


# ============================================================
# LightSafe - Stage 10: Event Study Analysis
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUT_DIR = PROJECT_ROOT / "outputs"

CAUSAL_FILE = PROCESSED_DIR / "causal_panel.parquet"
CRIME_FILE = PROCESSED_DIR / "clean_crime.parquet"
OUTAGE_FILE = PROCESSED_DIR / "clean_streetlights.parquet"

COEFFICIENT_FILE = OUTPUT_DIR / "event_study_coefficients.csv"
PLOT_FILE = OUTPUT_DIR / "event_study_plot.png"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Event-study periods.
# -1 is the omitted reference period.
RELATIVE_WEEKS = [-4, -3, -2, -1, 0, 1, 2, 3, 4]

# Same weekly windows used in the Stage 10 notebook.
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


def build_event_study_data():
    """Build the event-study panel from the causal panel and source data."""

    print("Loading source data...")

    panel = pd.read_parquet(CAUSAL_FILE)
    crime = gpd.read_parquet(CRIME_FILE)
    outages = gpd.read_parquet(OUTAGE_FILE)

    print("Causal panel:", panel.shape)
    print("Crime data:", crime.shape)
    print("Outage data:", outages.shape)

    # --------------------------------------------------------
    # Treatment/control event units
    # --------------------------------------------------------
    event_units = (
        panel[
            [
                "pair_id",
                "location_key",
                "created_date",
                "closed_date",
                "treatment",
                "borough",
                "h3_res9",
                "h3_res10",
            ]
        ]
        .drop_duplicates()
        .copy()
    )

    outage_geometry = outages[["unique_key", "geometry"]].copy()

    event_units = event_units.merge(
        outage_geometry.rename(
            columns={
                "unique_key": "location_key",
                "geometry": "location_geometry",
            }
        ),
        on="location_key",
        how="left",
    )

    missing_geometry = event_units["location_geometry"].isna().sum()

    if missing_geometry:
        raise ValueError(
            f"Found {missing_geometry} event-study units without geometry."
        )

    event_units_gdf = gpd.GeoDataFrame(
        event_units,
        geometry="location_geometry",
        crs=outages.crs,
    ).to_crs(epsg=32118)  # NAD83 / NY Long Island, metres

    # --------------------------------------------------------
    # Crime data
    # --------------------------------------------------------
    crime_es = crime[["crime_datetime", "geometry"]].copy()
    crime_es["crime_datetime"] = pd.to_datetime(
        crime_es["crime_datetime"],
        errors="coerce",
    )

    crime_es = crime_es.dropna(
        subset=["crime_datetime", "geometry"]
    ).copy()

    crime_es = crime_es.to_crs(epsg=32118)  # metres

    # Keep crime timestamps as a NumPy datetime array for faster
    # repeated event-window counting.
    crime_times = crime_es["crime_datetime"].to_numpy()

    crime_coords = np.column_stack(
        [
            crime_es.geometry.x.to_numpy(),
            crime_es.geometry.y.to_numpy(),
        ]
    )

    crime_tree = cKDTree(crime_coords)

    # --------------------------------------------------------
    # Restrict to events with complete -35 to +35 day coverage
    # --------------------------------------------------------
    crime_min = crime_es["crime_datetime"].min()
    crime_max = crime_es["crime_datetime"].max()

    event_units_gdf["earliest_required"] = (
        event_units_gdf["created_date"] - pd.Timedelta(days=35)
    )

    event_units_gdf["latest_required"] = (
        event_units_gdf["created_date"] + pd.Timedelta(days=35)
    )

    event_units_gdf["complete_event_window"] = (
        (event_units_gdf["earliest_required"] >= crime_min)
        & (event_units_gdf["latest_required"] <= crime_max)
    )

    complete_pairs = (
        event_units_gdf
        .groupby("pair_id")["complete_event_window"]
        .all()
    )

    complete_pair_ids = complete_pairs[
        complete_pairs
    ].index

    event_units_complete = event_units_gdf[
        event_units_gdf["pair_id"].isin(complete_pair_ids)
    ].copy()

    print(
        "Complete treatment-control pairs:",
        len(complete_pair_ids),
    )

    print(
        "Complete event-study units:",
        len(event_units_complete),
    )

    # --------------------------------------------------------
    # Construct event-study crime counts
    # --------------------------------------------------------
    print("Constructing event-study crime counts...")

    event_rows = []

    for row in event_units_complete.itertuples(index=False):
        point_geometry = row.location_geometry

        if point_geometry is None or point_geometry.is_empty:
            continue

        point = [
            point_geometry.x,
            point_geometry.y,
        ]

        neighbor_idx = crime_tree.query_ball_point(
            point,
            r=100,
        )

        if not neighbor_idx:
            for rel_week in RELATIVE_WEEKS:
                event_rows.append(
                    {
                        "pair_id": row.pair_id,
                        "location_key": row.location_key,
                        "created_date": row.created_date,
                        "closed_date": row.closed_date,
                        "treatment": row.treatment,
                        "borough": row.borough,
                        "h3_res9": row.h3_res9,
                        "h3_res10": row.h3_res10,
                        "rel_week": rel_week,
                        "crime_count": 0,
                    }
                )
            continue

        nearby_times = crime_times[neighbor_idx]

        elapsed_days = (
            nearby_times
            - np.datetime64(row.created_date)
        ) / np.timedelta64(1, "D")

        for rel_week, (start_day, end_day) in WEEK_WINDOWS.items():
            count = int(
                np.sum(
                    (elapsed_days >= start_day)
                    & (elapsed_days < end_day)
                )
            )

            event_rows.append(
                {
                    "pair_id": row.pair_id,
                    "location_key": row.location_key,
                    "created_date": row.created_date,
                    "closed_date": row.closed_date,
                    "treatment": row.treatment,
                    "borough": row.borough,
                    "h3_res9": row.h3_res9,
                    "h3_res10": row.h3_res10,
                    "rel_week": rel_week,
                    "crime_count": count,
                }
            )

    event_study = pd.DataFrame(event_rows)

    if event_study.empty:
        raise ValueError("Event-study dataset is empty.")

    print("Event-study observations:", len(event_study))

    # --------------------------------------------------------
    # Treatment-by-event-time interactions
    # --------------------------------------------------------
    for week in EVENT_WEEKS:
        safe_name = SAFE_EVENT_NAMES[week]

        event_study[safe_name] = (
            (event_study["treatment"] == 1)
            & (event_study["rel_week"] == week)
        ).astype(int)

    # --------------------------------------------------------
    # Efficient two-way fixed-effects estimation
    #
    # This removes:
    #   - location fixed effects
    #   - relative-week fixed effects
    #
    # without creating tens of thousands of dummy columns.
    # --------------------------------------------------------
    interaction_terms = list(SAFE_EVENT_NAMES.values())

    model_data = event_study[
        ["crime_count", "location_key", "rel_week"]
        + interaction_terms
    ].copy()

    variables = ["crime_count"] + interaction_terms

    grand_means = model_data[variables].mean()

    location_means = (
        model_data
        .groupby("location_key")[variables]
        .transform("mean")
    )

    week_means = (
        model_data
        .groupby("rel_week")[variables]
        .transform("mean")
    )

    demeaned = (
        model_data[variables]
        - location_means
        - week_means
        + grand_means
    )

    y = demeaned["crime_count"]
    X = demeaned[interaction_terms]

    print("Estimating event-study model...")

    event_model = sm.OLS(y, X).fit(
        cov_type="cluster",
        cov_kwds={
            "groups": model_data["location_key"]
        },
    )

    print("Event-study regression completed.")
    print("Observations:", int(event_model.nobs))
    print(
        "Location clusters:",
        model_data["location_key"].nunique(),
    )

    # --------------------------------------------------------
    # Extract coefficient table
    # --------------------------------------------------------
    results_rows = []

    confidence_intervals = event_model.conf_int()

    for week, safe_name in SAFE_EVENT_NAMES.items():
        coef = event_model.params[safe_name]
        se = event_model.bse[safe_name]
        ci_low, ci_high = confidence_intervals.loc[safe_name]
        p_value = event_model.pvalues[safe_name]

        results_rows.append(
            {
                "rel_week": week,
                "coefficient": coef,
                "std_error": se,
                "ci_lower": ci_low,
                "ci_upper": ci_high,
                "p_value": p_value,
            }
        )

    event_study_results = pd.DataFrame(
        results_rows
    )

    # Add omitted -1 reference period.
    baseline_row = pd.DataFrame(
        [
            {
                "rel_week": -1,
                "coefficient": 0.0,
                "std_error": np.nan,
                "ci_lower": np.nan,
                "ci_upper": np.nan,
                "p_value": np.nan,
            }
        ]
    )

    event_study_results = (
        pd.concat(
            [event_study_results, baseline_row],
            ignore_index=True,
        )
        .sort_values("rel_week")
        .reset_index(drop=True)
    )

    # --------------------------------------------------------
    # Joint parallel-trends test
    # H0: coefficients at -4, -3, -2 are jointly zero.
    # --------------------------------------------------------
    pretrend_test = event_model.wald_test(
        "treat_event_m4 = 0, "
        "treat_event_m3 = 0, "
        "treat_event_m2 = 0",
        use_f=True,
    )

    test_statistic = float(np.asarray(pretrend_test.statistic).squeeze())
    test_p_value = float(np.asarray(pretrend_test.pvalue).squeeze())

    print("\nJoint parallel-trends test")
    print("--------------------------")
    print("F-statistic:", test_statistic)
    print("p-value:", test_p_value)

    # --------------------------------------------------------
    # Save coefficient CSV
    # --------------------------------------------------------
    event_study_results.to_csv(
        COEFFICIENT_FILE,
        index=False,
    )

    print(
        "\nCoefficients saved to:",
        COEFFICIENT_FILE,
    )

    # --------------------------------------------------------
    # Event-study plot
    # --------------------------------------------------------
    plot_data = event_study_results.copy()

    non_baseline = plot_data["rel_week"] != -1

    fig, ax = plt.subplots(figsize=(10, 6))

    ax.errorbar(
        plot_data.loc[non_baseline, "rel_week"],
        plot_data.loc[non_baseline, "coefficient"],
        yerr=[
            (
                plot_data.loc[non_baseline, "coefficient"]
                - plot_data.loc[non_baseline, "ci_lower"]
            ),
            (
                plot_data.loc[non_baseline, "ci_upper"]
                - plot_data.loc[non_baseline, "coefficient"]
            ),
        ],
        fmt="o-",
        capsize=4,
        linewidth=1.5,
    )

    ax.scatter(
        -1,
        0,
        marker="o",
        s=60,
        label="Reference period (t = -1)",
    )

    ax.axhline(
        0,
        linestyle="--",
        linewidth=1,
    )

    ax.axvline(
        -1,
        linestyle=":",
        linewidth=1,
    )

    ax.set_xlabel("Relative Week")
    ax.set_ylabel("Crime Effect")
    ax.set_title(
        "Event-Study Estimates Around Outage Start"
    )

    ax.set_xticks(
        [-4, -3, -2, -1, 0, 1, 2, 3, 4]
    )

    ax.legend(
        loc="upper left"
    )

    ax.grid(
        True,
        alpha=0.3,
    )

    plt.tight_layout()

    fig.savefig(
        PLOT_FILE,
        dpi=300,
        bbox_inches="tight",
    )

    plt.close(fig)

    print(
        "Event-study plot saved to:",
        PLOT_FILE,
    )

    print("\nFinal event-study results:")
    print(event_study_results.to_string(index=False))

    return event_study_results, test_statistic, test_p_value


if __name__ == "__main__":
    build_event_study_data()
