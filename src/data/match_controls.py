from pathlib import Path

import geopandas as gpd
import pandas as pd


# ---------------------------------------------------------
# File paths
# ---------------------------------------------------------

INPUT_FILE = Path(
    "data/processed/outage_crime_linked.parquet"
)

OUTPUT_FILE = Path(
    "data/processed/control_area_pairs.parquet"
)


# ---------------------------------------------------------
# Spatial parameters
# ---------------------------------------------------------

PROJECTED_CRS = "EPSG:2263"

TREATMENT_DISTANCE = 100
CONTROL_MAX_DISTANCE = 250


# ---------------------------------------------------------
# Main function
# ---------------------------------------------------------

def match_controls():

    print("Loading spatially linked outage data...")

    linked = gpd.read_parquet(INPUT_FILE)

    print("Input rows:", len(linked))
    print("Input CRS:", linked.crs)


    # -----------------------------------------------------
    # 1. Prepare analysis table
    # -----------------------------------------------------

    analysis_df = linked[
        [
            "unique_key",
            "created_date",
            "closed_date",
            "h3_res9",
            "h3_res10",
            "crimes_during_outage_100m",
            "crimes_during_outage_250m",
            "geometry",
        ]
    ].copy()


    # -----------------------------------------------------
    # 2. Validate dates
    # -----------------------------------------------------

    analysis_df["created_date"] = pd.to_datetime(
        analysis_df["created_date"],
        errors="coerce",
    )

    analysis_df["closed_date"] = pd.to_datetime(
        analysis_df["closed_date"],
        errors="coerce",
    )

    analysis_df = analysis_df.dropna(
        subset=[
            "created_date",
            "closed_date",
            "geometry",
        ]
    ).copy()


    # -----------------------------------------------------
    # 3. Calculate outage duration
    # -----------------------------------------------------

    analysis_df["outage_duration_hours"] = (
        analysis_df["closed_date"]
        - analysis_df["created_date"]
    ).dt.total_seconds() / 3600


    print(
        "Valid outage records:",
        len(analysis_df)
    )


    # -----------------------------------------------------
    # 4. Convert to projected CRS
    # -----------------------------------------------------

    analysis_projected = analysis_df.to_crs(
        PROJECTED_CRS
    )


    # -----------------------------------------------------
    # 5. Create 250 m candidate areas
    # -----------------------------------------------------

    control_candidates = analysis_projected[
        [
            "unique_key",
            "created_date",
            "closed_date",
            "h3_res9",
            "h3_res10",
            "geometry",
        ]
    ].copy()

    control_candidates["candidate_geometry"] = (
        control_candidates.geometry.buffer(
            CONTROL_MAX_DISTANCE
        )
    )

    control_candidates = gpd.GeoDataFrame(
        control_candidates,
        geometry="candidate_geometry",
        crs=PROJECTED_CRS,
    )


    # -----------------------------------------------------
    # 6. Create outage point GeoDataFrame
    # -----------------------------------------------------

    outage_points = analysis_projected[
        [
            "unique_key",
            "created_date",
            "closed_date",
            "h3_res9",
            "h3_res10",
            "geometry",
        ]
    ].copy()

    outage_points = gpd.GeoDataFrame(
        outage_points,
        geometry="geometry",
        crs=PROJECTED_CRS,
    )


    # -----------------------------------------------------
    # 7. Find nearby candidate controls
    # -----------------------------------------------------

    print("Finding nearby control candidates...")

    nearby_controls = gpd.sjoin(
        outage_points,
        control_candidates[
            [
                "unique_key",
                "created_date",
                "closed_date",
                "h3_res9",
                "h3_res10",
                "candidate_geometry",
            ]
        ],
        how="inner",
        predicate="within",
    )

    print(
        "Nearby candidate pairs:",
        len(nearby_controls)
    )


    # -----------------------------------------------------
    # 8. Remove self-matches
    # -----------------------------------------------------

    nearby_controls = nearby_controls[
        nearby_controls["unique_key_left"]
        != nearby_controls["unique_key_right"]
    ].copy()

    print(
        "Candidate pairs after removing self-matches:",
        len(nearby_controls)
    )


    # -----------------------------------------------------
    # 9. Recover control-point geometry
    # -----------------------------------------------------

    nearby_controls["control_geometry"] = (
        nearby_controls["index_right"]
        .map(outage_points["geometry"])
    )

    missing_control_geometry = (
        nearby_controls["control_geometry"]
        .isna()
        .sum()
    )

    print(
        "Missing control geometries:",
        missing_control_geometry
    )


    # -----------------------------------------------------
    # 10. Calculate treatment-control distance
    # -----------------------------------------------------

    nearby_controls["distance_m"] = (
        nearby_controls.geometry
        .distance(
            nearby_controls["control_geometry"]
        )
    )


    # -----------------------------------------------------
    # 11. Keep controls between 100 m and 250 m
    # -----------------------------------------------------

    candidate_pairs = nearby_controls[
        (
            nearby_controls["distance_m"]
            > TREATMENT_DISTANCE
        )
        &
        (
            nearby_controls["distance_m"]
            <= CONTROL_MAX_DISTANCE
        )
    ].copy()

    print(
        "Candidates between 100 and 250 m:",
        len(candidate_pairs)
    )


    # -----------------------------------------------------
    # 12. Check temporal overlap
    # -----------------------------------------------------

    candidate_pairs["temporal_overlap"] = (
        (
            candidate_pairs["created_date_left"]
            <= candidate_pairs["closed_date_right"]
        )
        &
        (
            candidate_pairs["closed_date_left"]
            >= candidate_pairs["created_date_right"]
        )
    )


    # -----------------------------------------------------
    # 13. Keep temporally non-overlapping controls
    # -----------------------------------------------------

    control_pairs = candidate_pairs[
        ~candidate_pairs["temporal_overlap"]
    ].copy()

    print(
        "Non-overlapping control candidates:",
        len(control_pairs)
    )


    # -----------------------------------------------------
    # 14. Select nearest eligible control
    # -----------------------------------------------------

    control_pairs = control_pairs.sort_values(
        [
            "unique_key_left",
            "distance_m",
        ]
    )

    selected_controls = (
        control_pairs
        .drop_duplicates(
            subset=["unique_key_left"],
            keep="first",
        )
        .copy()
    )

    print(
        "Selected treatment-control pairs:",
        len(selected_controls)
    )


    # -----------------------------------------------------
    # 15. Rename treatment/control IDs
    # -----------------------------------------------------

    selected_controls = selected_controls.rename(
        columns={
            "unique_key_left": "treatment_key",
            "unique_key_right": "control_key",
        }
    )


    # -----------------------------------------------------
    # 16. Prepare treatment crime data
    # -----------------------------------------------------

    treatment_data = analysis_df[
        [
            "unique_key",
            "created_date",
            "closed_date",
            "outage_duration_hours",
            "h3_res9",
            "h3_res10",
            "crimes_during_outage_100m",
            "crimes_during_outage_250m",
        ]
    ].copy()

    treatment_data = treatment_data.rename(
        columns={
            "unique_key": "treatment_key",
            "crimes_during_outage_100m":
                "treatment_crime_100m",
            "crimes_during_outage_250m":
                "treatment_crime_250m",
        }
    )


    # -----------------------------------------------------
    # 17. Prepare control crime data
    # -----------------------------------------------------

    control_data = analysis_df[
        [
            "unique_key",
            "crimes_during_outage_100m",
            "crimes_during_outage_250m",
        ]
    ].copy()

    control_data = control_data.rename(
        columns={
            "unique_key": "control_key",
            "crimes_during_outage_100m":
                "control_crime_100m",
            "crimes_during_outage_250m":
                "control_crime_250m",
        }
    )


    # -----------------------------------------------------
    # 18. Merge treatment information
    # -----------------------------------------------------

    paired_analysis = selected_controls.merge(
        treatment_data,
        on="treatment_key",
        how="inner",
    )


    # -----------------------------------------------------
    # 19. Merge control information
    # -----------------------------------------------------

    paired_analysis = paired_analysis.merge(
        control_data,
        on="control_key",
        how="inner",
    )


    # -----------------------------------------------------
    # 20. Calculate crime differences
    # -----------------------------------------------------

    paired_analysis[
        "crime_difference_100m"
    ] = (
        paired_analysis["treatment_crime_100m"]
        - paired_analysis["control_crime_100m"]
    )

    paired_analysis[
        "crime_difference_250m"
    ] = (
        paired_analysis["treatment_crime_250m"]
        - paired_analysis["control_crime_250m"]
    )


    # -----------------------------------------------------
    # 21. Final validation
    # -----------------------------------------------------

    self_matches = (
        paired_analysis["treatment_key"]
        == paired_analysis["control_key"]
    ).sum()

    invalid_distances = (
        (
            paired_analysis["distance_m"]
            <= TREATMENT_DISTANCE
        )
        |
        (
            paired_analysis["distance_m"]
            > CONTROL_MAX_DISTANCE
        )
    ).sum()

    print()
    print("========================================")
    print("       STAGE 7 CONTROL MATCHING")
    print("========================================")
    print(
        "Original outage records:",
        len(analysis_df)
    )
    print(
        "Treatment-control pairs:",
        len(paired_analysis)
    )
    print(
        "Unique treatment outages:",
        paired_analysis["treatment_key"].nunique()
    )
    print(
        "Unique control outages:",
        paired_analysis["control_key"].nunique()
    )
    print(
        "Self matches:",
        self_matches
    )
    print(
        "Invalid distances:",
        invalid_distances
    )

    if len(paired_analysis) > 0:
        print(
            "Minimum control distance:",
            paired_analysis["distance_m"].min()
        )
        print(
            "Maximum control distance:",
            paired_analysis["distance_m"].max()
        )
        print(
            "Average control distance:",
            paired_analysis["distance_m"].mean()
        )

    print("========================================")


    # -----------------------------------------------------
    # 22. Save output
    # -----------------------------------------------------

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    paired_analysis.to_parquet(
        OUTPUT_FILE,
        index=False,
    )

    print(
        f"Saved control pairs to {OUTPUT_FILE}"
    )

    return paired_analysis


# ---------------------------------------------------------
# Run script
# ---------------------------------------------------------

if __name__ == "__main__":
    match_controls()