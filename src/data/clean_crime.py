from pathlib import Path

import geopandas as gpd
import pandas as pd


# -------------------------------------------------------------------
# File paths
# -------------------------------------------------------------------

INPUT_FILE = Path("data/raw/nypd_crime.csv")
OUTPUT_FILE = Path("data/processed/clean_crime.parquet")


# -------------------------------------------------------------------
# Crime categories required by the project
# -------------------------------------------------------------------

ALLOWED_OFFENSES = [
    "ROBBERY",
    "BURGLARY",
    "GRAND LARCENY",
    "ASSAULT",
    "PETIT LARCENY",
    "MISCHIEF",
]


# -------------------------------------------------------------------
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
    crime_df = pd.read_csv(INPUT_FILE)

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

    # Remove obviously invalid historical dates
    invalid_year_count = (
        crime_df["crime_datetime"].dt.year < 1900
    ).sum()

    print(
        "Records with year before 1900:",
        invalid_year_count
    )

    crime_df = crime_df[
        crime_df["crime_datetime"].dt.year >= 1900
    ].copy()

    print(
        "Rows after date validation:",
        len(crime_df)
    )

    # ---------------------------------------------------------------
    # Keep required offense categories
    # ---------------------------------------------------------------

    crime_df = crime_df[
        crime_df["ofns_desc"].isin(ALLOWED_OFFENSES)
    ].copy()

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