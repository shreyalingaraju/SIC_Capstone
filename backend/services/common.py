"""Shared helpers: NaN-safe conversion, borough handling and outage record serialization."""
from typing import Any, Dict, List, Optional
import math

import pandas as pd

from .data_store import store

BOROUGHS = ["MANHATTAN", "BROOKLYN", "QUEENS", "BRONX", "STATEN ISLAND"]

DISPATCH_RECOMMENDED = "recommended"
DISPATCH_DEFERRED = "deferred"
DISPATCH_NOT_SCORED = "not_scored"


def clean(value: Any) -> Any:
    """Convert pandas/numpy scalars to JSON-safe python values (NaN/NaT/inf -> None)."""
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        value = value.item()
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
    return value


def num(value: Any, digits: Optional[int] = None) -> Optional[float]:
    v = clean(value)
    if v is None:
        return None
    v = float(v)
    return round(v, digits) if digits is not None else v


def text(value: Any) -> Optional[str]:
    v = clean(value)
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def title_borough(value: Any) -> str:
    s = text(value)
    return s.title() if s else "Unspecified"


def borough_matches(series: pd.Series, borough: Optional[str]) -> pd.Series:
    return series.str.upper() == borough.strip().upper()


def is_all(value: Optional[str]) -> bool:
    return value is None or value.strip() == "" or value.strip().lower() == "all"


def location_desc(row: pd.Series) -> str:
    parts = []
    address = text(row.get("incident_address"))
    s1, s2 = text(row.get("intersection_street_1")), text(row.get("intersection_street_2"))
    street = text(row.get("street_name"))
    if address:
        parts.append(address)
    elif s1 and s2:
        parts.append(f"{s1} & {s2}")
    elif street:
        parts.append(street)
    borough = text(row.get("borough"))
    if borough and borough.upper() != "UNSPECIFIED":
        parts.append(borough.title())
    return " · ".join(parts) if parts else "Location unspecified"


def outage_record(row: pd.Series) -> Dict[str, Any]:
    """Serialize one outage (Stage 12 columns + Stage 14 decision) for the API."""
    oid = str(row["outage_id"])
    scored = bool(row.get("scored", False))
    hours = num(row.get("outage_duration_hours"), 1)
    lat, lon = num(row.get("latitude")), num(row.get("longitude"))
    if lat is not None and lon is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180):
        lat = lon = None
    rank = num(row.get("optimization_rank"))
    return {
        "outage_id": oid,
        "borough": title_borough(row.get("borough")),
        "police_precinct": text(row.get("police_precinct")),
        "location_desc": location_desc(row),
        "latitude": lat,
        "longitude": lon,
        "created_date": text(row.get("created_date")),
        "closed_date": text(row.get("closed_date")),
        "outage_duration_hours": hours,
        "duration_days": round(hours / 24.0, 1) if hours is not None else None,
        "scored": scored,
        "exclusion_reason": text(row.get("exclusion_reason")),
        "local_crime_rate": num(row.get("local_crime_rate"), 4),
        "priority_score": num(row.get("priority_score"), 2),
        "priority_tier": text(row.get("priority_tier")),
        "dispatch_status": text(row.get("dispatch_status")),
        "optimization_rank": int(rank) if rank is not None else None,
        "operator_note": store.operator_actions.get(oid, "None"),
    }


def records_for(df: pd.DataFrame) -> List[Dict[str, Any]]:
    return [outage_record(row) for _, row in df.iterrows()]


def paginate(total: int, page: int, page_size: int) -> Dict[str, int]:
    pages = max(1, math.ceil(total / page_size)) if page_size else 1
    return {"total_count": total, "page": page, "page_size": page_size, "total_pages": pages}
