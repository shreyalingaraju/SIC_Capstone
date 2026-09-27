from pathlib import Path

import geopandas as gpd
import pandas as pd


# -------------------------------------------------------------------
# File paths
# -------------------------------------------------------------------

INPUT_FILE = Path("data/raw/nypd_crime.csv")
OUTPUT_FILE = Path("data/processed/clean_crime.parquet")


# -------------------------------------------------------------------
# Analysis period
#
# Keep in sync with src/data/download_data.py ANALYSIS_START.
# -------------------------------------------------------------------

ANALYSIS_START = pd.Timestamp("2019-11-01")


# -------------------------------------------------------------------
# Crime categories required by the project
#
# Filter on the NYPD offense key code (ky_cd) rather than ofns_desc:
# labels are truncated ("CRIMINAL MISCHIEF & RELATED OF") and several
# codes share a category (FELONY ASSAULT and ASSAULT 3).
# -------------------------------------------------------------------

KY_CD_CATEGORIES = {
    105: "ROBBERY",
    106: "ASSAULT",        # FELONY ASSAULT
    107: "BURGLARY",
    109: "GRAND LARCENY",
    121: "MISCHIEF",       # CRIMINAL MISCHIEF & RELATED OF
    341: "PETIT LARCENY",
    344: "ASSAULT",        # ASSAULT 3 & RELATED OFFENSES
    351: "MISCHIEF",       # CRIMINAL MISCHIEF & RELATED OF
}

# Offenses whose labels match the project categories but are
# deliberately excluded.
EXCLUDED_KY_CD = {
    110: "GRAND LARCENY OF MOTOR VEHICLE",
    342: "PETIT LARCENY OF MOTOR VEHICLE",
}

# Any record whose label contains one of these words must have a
# ky_cd listed above; otherwise cleaning stops.
CATEGORY_KEYWORDS = "ROBBERY|BURGLARY|LARCENY|ASSAULT|MISCHIEF"


# ------------------------------------------------------------------
# NYC coordinate bounds
# -------------------------------------------------------------------

MIN_LAT = 40.49
MAX_LAT = 40.92
MIN_LON = -74.26
MAX_LON = -73.69


def clean_crime():
    """
    Load, clean, filter, and spatialize NYPD crime data.
    """

    # Load raw NYPD data
    # cmplnt_num is an identifier: read it as text so values from the
    # Historic and YTD sources compare equal during de-duplication.
    crime_df = pd.read_csv(
        INPUT_FILE,
        dtype={"cmplnt_num": str},
    )

    print("Raw rows:", len(crime_df))

    # ---------------------------------------------------------------
    # Remove rows missing critical fields
    # ---------------------------------------------------------------

    required_columns = [
        "cmplnt_fr_dt",
        "cmplnt_fr_tm",
        "latitude",
        "longitude",
    ]

    crime_df = crime_df.dropna(
        subset=required_columns
    ).copy()

    print(
        "Rows after removing missing values:",
        len(crime_df)
    )

    # ---------------------------------------------------------------
    # Remove records present in both the Historic and YTD sources
    # ---------------------------------------------------------------

    duplicate_count = crime_df["cmplnt_num"].duplicated().sum()

    print(
        "Duplicate complaint numbers removed:",
        duplicate_count
    )

    crime_df = crime_df.drop_duplicates(
        subset=["cmplnt_num"]
    ).copy()

    # ---------------------------------------------------------------
    # Create crime_datetime
    # ---------------------------------------------------------------

    crime_df["crime_datetime"] = pd.to_datetime(
        crime_df["cmplnt_fr_dt"].astype(str)
        + " "
        + crime_df["cmplnt_fr_tm"].astype(str),
        format="mixed",
        errors="coerce",
    )

    invalid_datetime_count = crime_df["crime_datetime"].isna().sum()

    print(
        "Invalid/unparseable crime_datetime:",
        invalid_datetime_count
    )

    # Remove records where date/time could not be parsed
    crime_df = crime_df.dropna(
        subset=["crime_datetime"]
    ).copy()

    # Keep the analysis period only. This also removes invalid
    # historical dates (e.g. year 1014) that would otherwise distort
    # the data-coverage checks in later stages.
    before_start_count = (
        crime_df["crime_datetime"] < ANALYSIS_START
    ).sum()

    print(
        f"Records before {ANALYSIS_START.date()}:",
        before_start_count
    )

    crime_df = crime_df[
        crime_df["crime_datetime"] >= ANALYSIS_START
    ].copy()

    print(
        "Rows after date validation:",
        len(crime_df)
    )

    # ---------------------------------------------------------------
    # Keep required offense categories
    # ---------------------------------------------------------------

    crime_df["ky_cd"] = pd.to_numeric(
        crime_df["ky_cd"],
        errors="coerce",
    )

    keyword_match = (
        crime_df["ofns_desc"]
        .astype(str)
        .str.upper()
        .str.contains(CATEGORY_KEYWORDS, na=False)
    )

    known_codes = set(KY_CD_CATEGORIES) | set(EXCLUDED_KY_CD)

    unmapped = crime_df[
        keyword_match
        & ~crime_df["ky_cd"].isin(known_codes)
    ]

    if len(unmapped) > 0:
        raise ValueError(
            "Offense records match a project category but have an "
            "unmapped ky_cd. Add them to KY_CD_CATEGORIES or "
            "EXCLUDED_KY_CD:\n"
            + unmapped.groupby(
                ["ky_cd", "ofns_desc"],
                dropna=False,
            ).size().to_string()
        )

    crime_df = crime_df[
        crime_df["ky_cd"].isin(KY_CD_CATEGORIES)
    ].copy()

    crime_df["crime_category"] = crime_df["ky_cd"].map(
        KY_CD_CATEGORIES
    )

    print(
        "Rows after offense filtering:",
        len(crime_df)
    )

    # ---------------------------------------------------------------
    # Create nighttime indicator
    # ---------------------------------------------------------------

    crime_df["is_nighttime"] = (
        (crime_df["crime_datetime"].dt.hour >= 18)
        |
        (crime_df["crime_datetime"].dt.hour <= 6)
    )

    # Keep nighttime crimes only
    crime_df = crime_df[
        crime_df["is_nighttime"]
    ].copy()

    print(
        "Rows after nighttime filtering:",
        len(crime_df)
    )

    # ---------------------------------------------------------------
    # Apply NYC coordinate bounds
    # ---------------------------------------------------------------

    crime_df = crime_df[
        crime_df["latitude"].between(
            MIN_LAT,
            MAX_LAT
        )
        &
        crime_df["longitude"].between(
            MIN_LON,
            MAX_LON
        )
    ].copy()

    print(
        "Rows after coordinate filtering:",
        len(crime_df)
    )

    # ---------------------------------------------------------------
    # Convert to GeoDataFrame
    # ---------------------------------------------------------------

    crime_gdf = gpd.GeoDataFrame(
        crime_df,
        geometry=gpd.points_from_xy(
            crime_df["longitude"],
            crime_df["latitude"],
        ),
        crs="EPSG:4326",
    )

    # ---------------------------------------------------------------
    # Report coverage
    # ---------------------------------------------------------------

    print(
        "Crime coverage:",
        crime_gdf["crime_datetime"].min(),
        "to",
        crime_gdf["crime_datetime"].max(),
    )

    print("\nRows by category:")
    print(crime_gdf["crime_category"].value_counts().to_string())

    monthly_counts = (
        crime_gdf["crime_datetime"]
        .dt.to_period("M")
        .value_counts()
        .sort_index()
    )

    sparse_months = monthly_counts[
        monthly_counts < 0.5 * monthly_counts.median()
    ]

    if len(sparse_months) > 0:
        print(
            "\nWARNING: months with fewer than half the median "
            "monthly count:"
        )
        print(sparse_months.to_string())

    # ---------------------------------------------------------------
    # Save output
    # ---------------------------------------------------------------

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    crime_gdf.to_parquet(
        OUTPUT_FILE,
        index=False,
    )

    print(
        f"Saved cleaned crime data to {OUTPUT_FILE}"
    )

    return crime_gdf


if __name__ == "__main__":
    clean_crime()