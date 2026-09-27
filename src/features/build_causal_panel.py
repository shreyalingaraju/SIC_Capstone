from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from pyproj import CRS
from scipy.spatial import cKDTree


# ============================================================
# Paths
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

CONTROL_FILE = PROCESSED_DIR / "control_area_pairs.parquet"
CRIME_FILE = PROCESSED_DIR / "clean_crime.parquet"
OUTAGE_FILE = PROCESSED_DIR / "clean_streetlights.parquet"

OUTPUT_FILE = PROCESSED_DIR / "causal_panel.parquet"


# ============================================================
# Load data
# ============================================================

print("Loading Stage 8 input data...")

control_pairs = pd.read_parquet(CONTROL_FILE)
crime = gpd.read_parquet(CRIME_FILE)
outages = gpd.read_parquet(OUTAGE_FILE)

print(f"Control pairs: {control_pairs.shape}")
print(f"Crime records: {crime.shape}")
print(f"Outage records: {outages.shape}")


# ============================================================
# Prepare dates and CRS
# ============================================================

control_pairs["created_date"] = pd.to_datetime(
    control_pairs["created_date"],
    errors="coerce"
)

control_pairs["closed_date"] = pd.to_datetime(
    control_pairs["closed_date"],
    errors="coerce"
)

crime["crime_datetime"] = pd.to_datetime(
    crime["crime_datetime"],
    errors="coerce"
)

outages["created_date"] = pd.to_datetime(
    outages["created_date"],
    errors="coerce"
)

outages["closed_date"] = pd.to_datetime(
    outages["closed_date"],
    errors="coerce"
)

# Projected CRS for metre-based spatial calculations:
# NAD83 / New York Long Island in metres (EPSG:2263 is US feet).
TARGET_CRS = "EPSG:32118"

if CRS(TARGET_CRS).axis_info[0].unit_name != "metre":
    raise ValueError(f"{TARGET_CRS} must use metre units")

crime = crime.to_crs(TARGET_CRS)
outages = outages.to_crs(TARGET_CRS)


# ============================================================
# Remove invalid outage dates
# ============================================================

control_pairs = control_pairs.dropna(
    subset=["created_date", "closed_date"]
).copy()


# ============================================================
# Build treatment/control lookup
# ============================================================

outage_lookup = outages[
    [
        "unique_key",
        "geometry",
        "borough",
        "latitude",
        "longitude",
        "outage_duration_hours",
    ]
].copy()

outage_lookup["unique_key"] = outage_lookup["unique_key"].astype(int)

treatment_lookup = outage_lookup.rename(
    columns={
        "unique_key": "treatment_key",
        "geometry": "treatment_geometry",
        "borough": "treatment_borough",
        "latitude": "treatment_latitude",
        "longitude": "treatment_longitude",
        "outage_duration_hours": "treatment_duration_hours",
    }
)

control_lookup = outage_lookup.rename(
    columns={
        "unique_key": "control_key",
        "geometry": "control_geometry_actual",
        "borough": "control_borough",
        "latitude": "control_latitude",
        "longitude": "control_longitude",
        "outage_duration_hours": "control_duration_hours",
    }
)

pairs = control_pairs.merge(
    treatment_lookup,
    on="treatment_key",
    how="left"
)

pairs = pairs.merge(
    control_lookup,
    on="control_key",
    how="left"
)


# ============================================================
# Define treatment outage windows
# ============================================================

pairs["pre_start"] = (
    pairs["created_date"] - pd.Timedelta(days=14)
)

pairs["pre_end"] = pairs["created_date"]

pairs["during_start"] = pairs["created_date"]

pairs["during_end"] = pairs["closed_date"]

pairs["post_start"] = pairs["closed_date"]

pairs["post_end"] = (
    pairs["closed_date"] + pd.Timedelta(days=14)
)


# ============================================================
# Crime coordinates
# ============================================================

crime_valid = crime.dropna(
    subset=["crime_datetime", "geometry"]
).copy()


# ============================================================
# Keep pairs whose windows are fully covered by crime data
#
# Windows extending beyond the available crime records would
# otherwise be counted as zero crime.
# ============================================================

coverage_start = crime_valid["crime_datetime"].min()
coverage_end = crime_valid["crime_datetime"].max()

covered = (
    (pairs["pre_start"] >= coverage_start)
    & (pairs["post_end"] <= coverage_end)
)

print(
    f"Crime coverage: {coverage_start} to {coverage_end}"
)

print(
    "Pairs dropped for incomplete crime coverage:",
    int((~covered).sum())
)

pairs = pairs[covered].copy()

crime_coords = np.column_stack(
    [
        crime_valid.geometry.x.to_numpy(),
        crime_valid.geometry.y.to_numpy(),
    ]
)

crime_tree = cKDTree(crime_coords)

crime_times = crime_valid["crime_datetime"].to_numpy()


# ============================================================
# Count crime for each pair and period
#
# 100m:
#   direct treatment/control area
#
# 100-250m:
#   displacement ring
#
# Controls use the SAME treatment outage calendar windows.
# ============================================================

def count_crime_for_location(
    geometry,
    start,
    end,
):
    """
    Count crimes within:
        0-100m
        100-250m

    during the supplied time window.
    """

    if geometry is None or pd.isna(geometry):
        return 0, 0

    x = geometry.x
    y = geometry.y

    candidate_indices = crime_tree.query_ball_point(
        [x, y],
        r=250
    )

    if not candidate_indices:
        return 0, 0

    candidate_indices = np.asarray(
        candidate_indices,
        dtype=int
    )

    candidate_times = crime_times[candidate_indices]

    # Stage 6/Stage 8 convention:
    # inclusive start and inclusive end.
    time_mask = (
        (candidate_times >= np.datetime64(start))
        & (candidate_times <= np.datetime64(end))
    )

    if not np.any(time_mask):
        return 0, 0

    candidate_indices = candidate_indices[time_mask]

    candidate_coords = crime_coords[candidate_indices]

    distances = np.sqrt(
        (candidate_coords[:, 0] - x) ** 2
        + (candidate_coords[:, 1] - y) ** 2
    )

    direct_count = int(
        np.sum(distances <= 100)
    )

    displacement_count = int(
        np.sum(
            (distances > 100)
            & (distances <= 250)
        )
    )

    return direct_count, displacement_count


# ============================================================
# Calculate period-specific crime counts
# ============================================================

print("Calculating crime counts...")

count_columns = {
    "treatment": {
        "pre": (
            "treatment_pre_100m",
            "treatment_pre_250m"
        ),
        "during": (
            "treatment_during_100m",
            "treatment_during_250m"
        ),
        "post": (
            "treatment_post_100m",
            "treatment_post_250m"
        ),
    },
    "control": {
        "pre": (
            "control_pre_100m",
            "control_pre_250m"
        ),
        "during": (
            "control_during_100m",
            "control_during_250m"
        ),
        "post": (
            "control_post_100m",
            "control_post_250m"
        ),
    },
}


for idx, row in pairs.iterrows():

    treatment_geometry = row["treatment_geometry"]
    control_geometry = row["control_geometry_actual"]

    windows = {
        "pre": (
            row["pre_start"],
            row["pre_end"]
        ),
        "during": (
            row["during_start"],
            row["during_end"]
        ),
        "post": (
            row["post_start"],
            row["post_end"]
        ),
    }

    for period, (start, end) in windows.items():

        treatment_direct, treatment_displacement = (
            count_crime_for_location(
                treatment_geometry,
                start,
                end
            )
        )

        control_direct, control_displacement = (
            count_crime_for_location(
                control_geometry,
                start,
                end
            )
        )

        treatment_cols = count_columns["treatment"][period]
        control_cols = count_columns["control"][period]

        pairs.loc[idx, treatment_cols[0]] = treatment_direct
        pairs.loc[idx, treatment_cols[1]] = treatment_displacement

        pairs.loc[idx, control_cols[0]] = control_direct
        pairs.loc[idx, control_cols[1]] = control_displacement


# ============================================================
# Convert count columns to integers
# ============================================================

count_columns_all = []

for group in count_columns.values():
    for period_columns in group.values():
        count_columns_all.extend(period_columns)

for col in count_columns_all:
    pairs[col] = (
        pairs[col]
        .fillna(0)
        .astype(int)
    )


# ============================================================
# Construct long-format panel
# ============================================================

panel_rows = []

for _, row in pairs.iterrows():

    pair_id = (
        f"{int(row['treatment_key'])}_"
        f"{int(row['control_key'])}"
    )

    common = {
        "pair_id": pair_id,
        "created_date": row["created_date"],
        "closed_date": row["closed_date"],
    }

    period_definitions = [
        (
            "pre",
            0,
            0,
            "treatment_pre_100m",
            "treatment_pre_250m",
            "control_pre_100m",
            "control_pre_250m",
        ),
        (
            "during",
            1,
            1,
            "treatment_during_100m",
            "treatment_during_250m",
            "control_during_100m",
            "control_during_250m",
        ),
        (
            "post",
            2,
            1,
            "treatment_post_100m",
            "treatment_post_250m",
            "control_post_100m",
            "control_post_250m",
        ),
    ]

    for (
        period,
        period_order,
        post,
        treatment_100_col,
        treatment_250_col,
        control_100_col,
        control_250_col,
    ) in period_definitions:

        # Treatment row
        panel_rows.append(
            {
                **common,
                "location_key": int(row["treatment_key"]),
                "period": period,
                "period_order": period_order,
                "treatment": 1,
                "post": post,
                "treatment_x_post": 1 * post,
                "crime_100m": int(row[treatment_100_col]),
                "crime_250m": int(row[treatment_250_col]),
                "baseline_crime_intensity": int(
                    row["treatment_pre_100m"]
                ),
                "borough": row["treatment_borough"],
                "outage_duration_hours": row[
                    "treatment_duration_hours"
                ],
                "h3_res9": row.get("h3_res9"),
                "h3_res10": row.get("h3_res10"),
            }
        )

        # Control row
        panel_rows.append(
            {
                **common,
                "location_key": int(row["control_key"]),
                "period": period,
                "period_order": period_order,
                "treatment": 0,
                "post": post,
                "treatment_x_post": 0,
                "crime_100m": int(row[control_100_col]),
                "crime_250m": int(row[control_250_col]),
                "baseline_crime_intensity": int(
                    row["control_pre_100m"]
                ),
                "borough": row["control_borough"],
                # Treatment outage duration is retained as
                # the common exposure duration for the pair.
                "outage_duration_hours": row[
                    "treatment_duration_hours"
                ],
                "h3_res9": row.get("h3_res9_right"),
                "h3_res10": row.get("h3_res10_right"),
            }
        )


panel = pd.DataFrame(panel_rows)


# ============================================================
# Final validation
# ============================================================

print("\nValidating causal panel...")

print("Panel shape:", panel.shape)

print(
    "Unique pairs:",
    panel["pair_id"].nunique()
)

print(
    "Treatment rows:",
    (panel["treatment"] == 1).sum()
)

print(
    "Control rows:",
    (panel["treatment"] == 0).sum()
)

print("\nPeriod counts:")
print(panel["period"].value_counts())

print("\nMissing values:")
print(
    panel.isna().sum()
    .sort_values(ascending=False)
    .head(10)
)

# Each pair contributes one treatment and one control row
# for each of the three periods.
n_pairs = len(pairs)

assert panel["treatment"].value_counts().to_dict() == {
    1: 3 * n_pairs,
    0: 3 * n_pairs,
}

assert panel["period"].value_counts().to_dict() == {
    "pre": 2 * n_pairs,
    "during": 2 * n_pairs,
    "post": 2 * n_pairs,
}

assert panel["baseline_crime_intensity"].isna().sum() == 0


# ============================================================
# Save
# ============================================================

panel.to_parquet(
    OUTPUT_FILE,
    index=False
)

print(
    f"\nCausal panel saved to:\n{OUTPUT_FILE}"
)