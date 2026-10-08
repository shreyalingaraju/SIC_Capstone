"""
Ward context for the synthetic Karnataka profile.

Joins the ward table (population, density, income, rainfall, ...) to any point
by geography, the way a real analysis would join census/weather layers to
complaints. A point belongs to the ward whose centroid is nearest after dividing
by the ward's spatial spread (sigma_km), restricted to the point's own city when
the city is known. The context never uses outcomes (outages, crime, repair time).
"""
from pathlib import Path

import numpy as np
import pandas as pd

WARD_FILE_NAME = "synthetic_wards.csv"


def load_wards(path) -> pd.DataFrame:
    wards = pd.read_csv(path)
    required = {"ward_id", "city", "latitude", "longitude", "sigma_km", "population", "pop_density_per_km2"}
    missing = required - set(wards.columns)
    if missing:
        raise ValueError(f"{path} is missing ward columns: {sorted(missing)}")
    if not wards["ward_id"].is_unique:
        raise ValueError("ward_id must be unique")
    return wards.reset_index(drop=True)


def assign_ward(wards: pd.DataFrame, lat, lon, city=None) -> np.ndarray:
    """Return the ward_id of each point (array of str)."""
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    w_lat = wards["latitude"].to_numpy()
    w_lon = wards["longitude"].to_numpy()
    sigma = wards["sigma_km"].to_numpy()
    cities = wards["city"].to_numpy()
    out = np.empty(len(lat), dtype=object)
    chunk = 4000
    for start in range(0, len(lat), chunk):
        sl = slice(start, start + chunk)
        dy = (lat[sl, None] - w_lat[None, :]) * 110.574
        dx = (lon[sl, None] - w_lon[None, :]) * 111.320 * np.cos(np.radians(lat[sl, None]))
        d = np.hypot(dx, dy) / sigma[None, :]
        if city is not None:
            c = np.asarray(city)[sl]
            d = np.where(c[:, None] == cities[None, :], d, np.inf)
        out[sl] = wards["ward_id"].to_numpy()[d.argmin(1)]
    return out
