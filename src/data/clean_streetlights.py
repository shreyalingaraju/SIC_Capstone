from pathlib import Path

import geopandas as gpd
import pandas as pd

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from src import profile as _profile  # noqa: E402  (dataset profile: paths, bbox, CRS)


RAW_FILE = Path(_profile.RAW_DIR) / "streetlight_complaints.csv"
OUTPUT_FILE = Path(_profile.PROCESSED_DIR) / "clean_streetlights.parquet"


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

    # Region bounding box (NYC by default; see src/profile.py)
    df = df[
        (df["latitude"] >= _profile.BBOX[0])
        &
        (df["latitude"] <= _profile.BBOX[1])
        &
        (df["longitude"] >= _profile.BBOX[2])
        &
        (df["longitude"] <= _profile.BBOX[3])
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