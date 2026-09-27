from pathlib import Path

import geopandas as gpd
import pandas as pd
import h3
from pyproj import CRS


# -------------------------------------------------------------------
# File paths
# ------------------------------------------------------------------

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

# NAD83 / New York Long Island in metres. (EPSG:2263 is the same
# projection in US survey feet and must not be used with the metre
# distances below.)
PROJECTED_CRS = "EPSG:32118"
WGS84_CRS = "EPSG:4326"

# Distances in metres
TREATMENT_DISTANCE = 100
DISPLACEMENT_DISTANCE = 250

if CRS(PROJECTED_CRS).axis_info[0].unit_name != "metre":
    raise ValueError(f"{PROJECTED_CRS} must use metre units")

# Crimes are joined to outage zones in chunks to bound memory use.
CRIME_CHUNK_SIZE = 50_000


# -------------------------------------------------------------------
# Chunked crime counting
# -------------------------------------------------------------------

def count_crimes_during_outage(crime_points, zones, count_name):
    """
    Count crimes that fall within each outage zone while the
    outage was active (created_date <= crime_datetime <= closed_date).

    crime_points must contain only crime_datetime and geometry, and
    zones only created_date, closed_date and geometry. Crimes are
    processed in chunks so the spatial join never materialises the
    full crime x zone match table; results are identical to a single
    join.
    """

    counts = pd.Series(
        0,
        index=zones.index,
        dtype="int64",
        name=count_name,
    )

    spatial_matches = 0
    outage_matches = 0

    for start in range(0, len(crime_points), CRIME_CHUNK_SIZE):

        joined = gpd.sjoin(
            crime_points.iloc[start:start + CRIME_CHUNK_SIZE],
            zones,
            how="inner",
            predicate="within",
        )

        spatial_matches += len(joined)

        joined = joined[
            (joined["crime_datetime"] >= joined["created_date"])
            &
            (joined["crime_datetime"] <= joined["closed_date"])
        ]

        outage_matches += len(joined)

        counts = counts.add(
            joined.groupby("index_right").size(),
            fill_value=0,
        ).astype("int64")

    # Series.add drops the name, which the merge in step 14 needs.
    return counts.rename(count_name), spatial_matches, outage_matches


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
    # 5. Convert to the projected CRS for metre-based distances
    # ===============================================================

    print(
        f"Converting data to {PROJECTED_CRS}..."
    )

    lights_proj = lights_wgs84.to_crs(
        PROJECTED_CRS
    )

    # The spatial joins only need the crime timestamp and location.
    crime_proj = crime_wgs84[
        [
            "crime_datetime",
            "geometry",
        ]
    ].to_crs(
        PROJECTED_CRS
    )

    # Release the full-width crime copies before the joins.
    del crime, crime_wgs84

    # ===============================================================
    # 6. Create 0–100 m treatment zones
    # ===============================================================

    print(
        "Creating 0–100 m treatment zones..."
    )

    treatment_zones = lights_proj[
        [
            "created_date",
            "closed_date",
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

    displacement_zones = lights_proj[
        [
            "created_date",
            "closed_date",
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
    # 8–9. Crimes within 100 m during the outage (chunked join)
    # ===============================================================

    print(
        "Linking crimes within 100 m..."
    )

    crime_counts_100m, spatial_100m, during_100m = (
        count_crimes_during_outage(
            crime_proj,
            treatment_zones,
            "crimes_during_outage_100m",
        )
    )

    print(
        "100 m spatial matches:",
        spatial_100m,
    )

    print(
        "100 m matches during outage:",
        during_100m,
    )

    # ===============================================================
    # 10–11. Crimes within 100–250 m during the outage (chunked join)
    # ===============================================================

    print(
        "Linking crimes within 100–250 m..."
    )

    crime_counts_250m, spatial_250m, during_250m = (
        count_crimes_during_outage(
            crime_proj,
            displacement_zones,
            "crimes_during_outage_250m",
        )
    )

    print(
        "100–250 m spatial matches:",
        spatial_250m,
    )

    print(
        "100–250 m matches during outage:",
        during_250m,
    )

    # ===============================================================
    # 14. Create final linked dataset
    # ===============================================================

    linked = lights_proj.copy()

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