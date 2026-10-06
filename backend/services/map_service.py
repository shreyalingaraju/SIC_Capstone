"""Map layers. Coordinates come only from the project's own data; invalid points are skipped."""
import math
from typing import Any, Dict, Optional

import pandas as pd

from .. import config
from .common import num, text, title_borough, location_desc
from .data_store import store
from .outage_service import filter_outages

_crimes_cache: Optional[Dict[str, Any]] = None
EMPTY = {"type": "FeatureCollection", "features": []}


def _valid(lat, lon) -> bool:
    return (
        lat is not None and lon is not None
        and not (math.isnan(lat) or math.isnan(lon))
        and -90 <= lat <= 90 and -180 <= lon <= 180
    )


def get_map_outages(borough: Optional[str] = None, priority_tier: Optional[str] = None,
                    dispatch_status: Optional[str] = None, limit: int = 500) -> Dict[str, Any]:
    limit = min(max(limit, 1), config.MAX_MAP_POINTS)
    df = filter_outages(borough=borough, priority_tier=priority_tier, dispatch_status=dispatch_status)
    if df.empty:
        return {**EMPTY, "total_matching": 0, "returned": 0}

    # Recommended repairs are always drawn; the rest of the budget is filled by highest score.
    selected = df[df["dispatch_status"] == "recommended"]
    rest = df[df["dispatch_status"] != "recommended"].head(max(0, limit - len(selected)))
    sub = pd.concat([selected, rest])

    features = []
    for _, row in sub.iterrows():
        lat, lon = num(row["latitude"]), num(row["longitude"])
        if not _valid(lat, lon):
            continue
        rank = num(row.get("optimization_rank"))
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "id": str(row["outage_id"]),
                "score": num(row["priority_score"], 1),
                "tier": text(row["priority_tier"]),
                "dispatch_status": text(row["dispatch_status"]),
                "optimization_rank": int(rank) if rank is not None else None,
                "duration_days": round(float(row["outage_duration_hours"]) / 24.0, 1)
                if pd.notna(row["outage_duration_hours"]) else None,
                "local_crime_rate": num(row["local_crime_rate"], 3),
                "borough": title_borough(row["borough"]),
                "location": location_desc(row),
                "created_date": text(row["created_date"]),
            },
        })
    return {"type": "FeatureCollection", "features": features,
            "total_matching": int(len(df)), "returned": len(features)}


def get_map_crimes(night_only: bool = True, limit: int = 600) -> Dict[str, Any]:
    """A sample of the most recent night-time crimes from the cleaned crime file (context layer)."""
    global _crimes_cache
    if not config.CLEAN_CRIME_FILE.exists():
        return EMPTY
    key = (night_only, limit)
    if _crimes_cache is not None and _crimes_cache.get("_key") == key:
        return _crimes_cache["data"] if "data" in _crimes_cache else EMPTY

    cols = ["latitude", "longitude", "crime_category", "is_nighttime", "crime_datetime"]
    crime = pd.read_parquet(config.CLEAN_CRIME_FILE, columns=cols)
    if night_only:
        crime = crime[crime["is_nighttime"] == True]  # noqa: E712
    sample = crime.dropna(subset=["latitude", "longitude"]).tail(limit)
    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [float(r.longitude), float(r.latitude)]},
        "properties": {"category": text(r.crime_category) or "CRIME", "datetime": text(r.crime_datetime)},
    } for r in sample.itertuples() if _valid(float(r.latitude), float(r.longitude))]
    data = {"type": "FeatureCollection", "features": features}
    _crimes_cache = {"_key": key, "data": data}
    return data


def get_buffer_rings(outage_id: str) -> Optional[Dict[str, Any]]:
    """100 m and 250 m analysis rings (the Stage 8 outcome radii) around an outage."""
    df = store.outages_df
    if df.empty or str(outage_id) not in df.index:
        return None
    row = df.loc[str(outage_id)]
    lat, lon = num(row["latitude"]), num(row["longitude"])
    if not _valid(lat, lon):
        return EMPTY

    def circle(radius_m: float, n: int = 36):
        dlat = radius_m / 111320.0
        dlon = radius_m / (111320.0 * math.cos(math.radians(lat)))
        pts = [[lon + dlon * math.cos(2 * math.pi * i / n), lat + dlat * math.sin(2 * math.pi * i / n)]
               for i in range(n + 1)]
        return [pts]

    def ring(kind, radius, label):
        return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": circle(radius)},
                "properties": {"type": kind, "radius": radius, "label": label}}

    return {"type": "FeatureCollection", "features": [
        ring("inner_ring", 100, "0-100 m analysis ring (direct)"),
        ring("outer_ring", 250, "100-250 m analysis ring (displacement)"),
    ]}
