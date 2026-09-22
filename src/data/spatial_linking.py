from pathlib import Path

import geopandas as gpd
import pandas as pd
import h3


# -------------------------------------------------------------------
# File paths
# -------------------------------------------------------------------

STREETLIGHT_FILE = Path(
    "data/processed/clean_streetlights.parquet"
)

CRIME_FILE = Path(
    "data/processed/clean_crime.parquet"
)

OUTPUT_FILE = Path(
    "data/processed/outage_crime_linked.parquet"
)


# -------------------------------------------------------------------
# Spatial settings
# -------------------------------------------------------------------

PROJECTED_CRS = "EPSG:2263"
WGS84_CRS = "EPSG:4326"

TREATMENT_DISTANCE = 100
DISPLACEMENT_DISTANCE = 250


# -------------------------------------------------------------------
# H3 helper
# -------------------------------------------------------------------

def add_h3_indices(gdf):
    """
    Add H3 spatial indices at resolutions 9 and 10.

    H3 uses latitude/longitude, so the input GeoDataFrame
    must be in EPSG:4326.
    """

    gdf = gdf.copy()

    gdf["h3_res9"] = gdf.geometry.apply(
        lambda geom: h3.latlng_to_cell(
            geom.y,
            geom.x,
            9,
        )
    )

    gdf["h3_res10"] = gdf.geometry.apply(
        lambda geom: h3.latlng_to_cell(
            geom.y,
            geom.x,
            10,
        )
    )

    return gdf


# -------------------------------------------------------------------
# Main spatial-linking function
# -------------------------------------------------------------------

def spatial_linking():

    # ===============================================================
    # 1. Load cleaned datasets
    # ===============================================================

    print("Loading cleaned streetlight data...")

    lights = gpd.read_parquet(
        STREETLIGHT_FILE
    )

    print(
        "Streetlight rows:",
        len(lights)
    )

    print("Loading cleaned crime data...")

    crime = gpd.read_parquet(
        CRIME_FILE
    )

    print(
        "Crime rows:",
        len(crime)
    )

    # ===============================================================
    # 2. Make sure date columns are datetime
    # ===============================================================

    lights["created_date"] = pd.to_datetime(
        lights["created_date"],
        errors="coerce",
    )

    lights["closed_date"] = pd.to_datetime(
        lights["closed_date"],
        errors="coerce",
    )

    crime["crime_datetime"] = pd.to_datetime(
        crime["crime_datetime"],
        errors="coerce",
    )

    # Remove records where the outage window is incomplete
    lights = lights.dropna(
        subset=[
            "created_date",
            "closed_date",
            "geometry",
        ]
    ).copy()

    # Remove crime records without a valid timestamp
    crime = crime.dropna(
        subset=[
            "crime_datetime",
            "geometry",
        ]
    ).copy()

    print(
        "Streetlights after date validation:",
        len(lights),
    )

    print(
        "Crime records after datetime validation:",
        len(crime),
    )

    # ===============================================================
    # 3. Create WGS84 copies for H3
    # ===============================================================

    lights_wgs84 = lights.to_crs(
        WGS84_CRS
    )

    crime_wgs84 = crime.to_crs(
        WGS84_CRS
    )

    # ===============================================================
    # 4. Add H3 resolution 9 and 10
    # ===============================================================

    print("Creating H3 indices...")

    lights_wgs84 = add_h3_indices(
        lights_wgs84
    )

    crime_wgs84 = add_h3_indices(
        crime_wgs84
    )

    # ===============================================================
    # 5. Convert to EPSG:2263 for meter-based distances
    # ===============================================================

    print(
        "Converting data to EPSG:2263..."
    )

    lights_2263 = lights_wgs84.to_crs(
        PROJECTED_CRS
    )

    crime_2263 = crime_wgs84.to_crs(
        PROJECTED_CRS
    )

    # ===============================================================
    # 6. Create 0–100 m treatment zones
    # ===============================================================

    print(
        "Creating 0–100 m treatment zones..."
    )

    treatment_zones = lights_2263[
        [
            "created_date",
            "closed_date",
            "h3_res9",
            "h3_res10",
            "geometry",
        ]
    ].copy()

    treatment_zones["geometry"] = (
        treatment_zones.geometry.buffer(
            TREATMENT_DISTANCE
        )
    )

    # ===============================================================
    # 7. Create 100–250 m displacement zones
    # ===============================================================

    print(
        "Creating 100–250 m displacement zones..."
    )

    displacement_zones = lights_2263[
        [
            "created_date",
            "closed_date",
            "h3_res9",
            "h3_res10",
            "geometry",
        ]
    ].copy()

    displacement_zones["geometry"] = (
        displacement_zones.geometry.buffer(
            DISPLACEMENT_DISTANCE
        )
        .difference(
            displacement_zones.geometry.buffer(
                TREATMENT_DISTANCE
            )
        )
    )

    # ===============================================================
    # 8. Spatial join: crimes within 100 m
    # ===============================================================

    print(
        "Linking crimes within 100 m..."
    )

    crime_100m = gpd.sjoin(
        crime_2263,
        treatment_zones,
        how="inner",
        predicate="within",
    )

    print(
        "100 m spatial matches:",
        len(crime_100m),
    )

    # ===============================================================
    # 9. Keep only crimes occurring during outage
    # ===============================================================

    crime_100m = crime_100m[
        (
            crime_100m["crime_datetime"]
            >= crime_100m["created_date"]
        )
        &
        (
            crime_100m["crime_datetime"]
            <= crime_100m["closed_date"]
        )
    ].copy()

    print(
        "100 m matches during outage:",
        len(crime_100m),
    )

    # ===============================================================
    # 10. Spatial join: crimes within 100–250 m
    # ===============================================================

    print(
        "Linking crimes within 100–250 m..."
    )

    crime_250m = gpd.sjoin(
        crime_2263,
        displacement_zones,
        how="inner",
        predicate="within",
    )

    print(
        "100–250 m spatial matches:",
        len(crime_250m),
    )

    # ===============================================================
    # 11. Keep only crimes occurring during outage
    # ===============================================================

    crime_250m = crime_250m[
        (
            crime_250m["crime_datetime"]
            >= crime_250m["created_date"]
        )
        &
        (
            crime_250m["crime_datetime"]
            <= crime_250m["closed_date"]
        )
    ].copy()

    print(
        "100–250 m matches during outage:",
        len(crime_250m),
    )

    # ===============================================================
    # 12. Count crimes per streetlight outage — 0–100 m
    # ===============================================================

    crime_counts_100m = (
        crime_100m
        .groupby("index_right")
        .size()
        .rename(
            "crimes_during_outage_100m"
        )
    )

    # ===============================================================
    # 13. Count crimes per streetlight outage — 100–250 m
    # ===============================================================

    crime_counts_250m = (
        crime_250m
        .groupby("index_right")
        .size()
        .rename(
            "crimes_during_outage_250m"
        )
    )

    # ===============================================================
    # 14. Create final linked dataset
    # ===============================================================

    linked = lights_2263.copy()

    # Preserve the original outage index so the crime counts
    # can be merged back to the correct streetlight record.
    linked["outage_index"] = linked.index

    # ---------------------------------------------------------------
    # Merge 0–100 m crime counts
    # ---------------------------------------------------------------

    linked = linked.merge(
        crime_counts_100m,
        left_on="outage_index",
        right_index=True,
        how="left",
    )

    # ---------------------------------------------------------------
    # Merge 100–250 m crime counts
    # ---------------------------------------------------------------

    linked = linked.merge(
        crime_counts_250m,
        left_on="outage_index",
        right_index=True,
        how="left",
    )

    # ===============================================================
    # 15. Replace missing crime counts with zero
    # ===============================================================

    linked[
        "crimes_during_outage_100m"
    ] = (
        linked[
            "crimes_during_outage_100m"
        ]
        .fillna(0)
        .astype(int)
    )

    linked[
        "crimes_during_outage_250m"
    ] = (
        linked[
            "crimes_during_outage_250m"
        ]
        .fillna(0)
        .astype(int)
    )

    # ===============================================================
    # 16. Remove temporary columns
    # ===============================================================

    linked = linked.drop(
        columns=[
            "outage_index",
        ],
        errors="ignore",
    )

    # ===============================================================
    # 17. Return final geometry to WGS84
    # ===============================================================

    linked = linked.to_crs(
        WGS84_CRS
    )

    # ===============================================================
    # 18. Save output
    # ===============================================================

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    linked.to_parquet(
        OUTPUT_FILE,
        index=False,
    )

    print(
        f"Saved spatially linked data to {OUTPUT_FILE}"
    )

    # ===============================================================
    # 19. Print validation information
    # ===============================================================

    print("\nFinal dataset:")
    print(
        "Rows:",
        len(linked),
    )

    print(
        "Columns:",
        len(linked.columns),
    )

    print(
        "CRS:",
        linked.crs,
    )

    print(
        "\nOutages with >= 1 crime within 100 m:",
        (
            linked[
                "crimes_during_outage_100m"
            ] > 0
        ).sum(),
    )

    print(
        "Outages with >= 1 crime within 100–250 m:",
        (
            linked[
                "crimes_during_outage_250m"
            ] > 0
        ).sum(),
    )

    return linked


# -------------------------------------------------------------------
# Run the pipeline
# -------------------------------------------------------------------

if __name__ == "__main__":
    spatial_linking()