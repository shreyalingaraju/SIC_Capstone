from pathlib import Path

import geopandas as gpd
import pandas as pd


RAW_FILE = Path("data/raw/streetlight_complaints.csv")
OUTPUT_FILE = Path("data/processed/clean_streetlights.parquet")


def clean_streetlights():

    df = pd.read_csv(RAW_FILE)

    print("Initial rows:", len(df))

    # Keep only Street Light Out complaints
    df = df[df["descriptor"] == "Street Light Out"]

    print("After descriptor filter:", len(df))

    # Drop missing values
    df = df.dropna(
        subset=[
            "created_date",
            "closed_date",
            "latitude",
            "longitude"
        ]
    )

    print("After null removal:", len(df))

    # Datetime conversion
    df["created_date"] = pd.to_datetime(
        df["created_date"],
        errors="coerce"
    )

    df["closed_date"] = pd.to_datetime(
        df["closed_date"],
        errors="coerce"
    )

    df = df.dropna(
        subset=[
            "created_date",
            "closed_date"
        ]
    )

    # Remove invalid durations
    df = df[
        df["closed_date"] >= df["created_date"]
    ]

    # Duration calculation
    df["outage_duration_hours"] = (
        (
            df["closed_date"]
            - df["created_date"]
        ).dt.total_seconds()
        / 3600
    )

    # Remove duration outliers
    df = df[
        (df["outage_duration_hours"] >= 0.5)
        &
        (df["outage_duration_hours"] <= 8760)
    ]

    # NYC bounding box
    df = df[
        (df["latitude"] >= 40.49)
        &
        (df["latitude"] <= 40.92)
        &
        (df["longitude"] >= -74.26)
        &
        (df["longitude"] <= -73.69)
    ]

    print("After cleaning:", len(df))

    # GeoDataFrame
    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(
            df["longitude"],
            df["latitude"]
        ),
        crs="EPSG:4326"
    )

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    gdf.to_parquet(
        OUTPUT_FILE,
        index=False
    )

    print("\nSaved:")
    print(OUTPUT_FILE)

    return gdf


if __name__ == "__main__":
    clean_streetlights()