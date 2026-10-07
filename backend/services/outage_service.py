"""Outage list/detail (Stage 12 scores joined with the Stage 13 queue ranks and Stage 14 decision)."""
from typing import Any, Dict, Optional

import pandas as pd

from .. import config
from .common import (
    borough_matches, is_all, num, outage_record, paginate, records_for, text,
)
from .data_store import store

VALID_ACTIONS = {"approve": "Approved", "defer": "Deferred", "flag": "Flagged", "reset": "None"}
VALID_SCOPES = ("scored", "excluded", "all")
VALID_DISPATCH = ("recommended", "deferred")


def _excluded() -> pd.DataFrame:
    df = store.outages_df
    return df[~df["scored"]] if not df.empty else df


def filter_outages(
    borough: Optional[str] = None,
    priority_tier: Optional[str] = None,
    search: Optional[str] = None,
    dispatch_status: Optional[str] = None,
    min_score: Optional[float] = None,
    scope: str = "scored",
) -> pd.DataFrame:
    if scope == "scored":
        df = store.scored_sorted
    elif scope == "excluded":
        df = _excluded()
    else:
        df = pd.concat([store.scored_sorted, _excluded()])
    if df.empty:
        return df

    mask = pd.Series(True, index=df.index)
    if not is_all(borough):
        mask &= borough_matches(df["borough"], borough)
    if not is_all(priority_tier):
        mask &= df["priority_tier"].fillna("").str.lower() == priority_tier.strip().lower()
    if dispatch_status:
        mask &= df["dispatch_status"] == dispatch_status
    if min_score is not None:
        mask &= df["priority_score"] >= min_score
    if search and search.strip():
        q = search.strip().lower()
        mask &= (
            df["outage_id"].str.lower().str.contains(q, regex=False)
            | df["incident_address"].fillna("").str.lower().str.contains(q, regex=False)
            | df["street_name"].fillna("").str.lower().str.contains(q, regex=False)
        )
    return df[mask]


def get_outages(
    borough=None, priority_tier=None, search=None, dispatch_status=None,
    min_score=None, scope="scored", page=1, page_size=50,
) -> Dict[str, Any]:
    page_size = min(max(page_size, 1), config.MAX_PAGE_SIZE)
    filtered = filter_outages(borough, priority_tier, search, dispatch_status, min_score, scope)
    filtered = _acted_last(filtered)
    start = (page - 1) * page_size
    page_df = filtered.iloc[start:start + page_size]
    return {"items": records_for(page_df), **paginate(len(filtered), page, page_size)}


def _acted_last(df: pd.DataFrame) -> pd.DataFrame:
    """Display order only: outages with a session operator action move to the end of the list,
    in the order the actions were taken. Scores, decisions and the underlying order are unchanged."""
    acted = [oid for oid in store.operator_actions if oid in df.index]
    if df.empty or not acted:
        return df
    return pd.concat([df[~df.index.isin(acted)], df.loc[acted]])


def get_outage_by_id(outage_id: str) -> Optional[Dict[str, Any]]:
    df = store.outages_df
    oid = str(outage_id)
    if df.empty or oid not in df.index:
        return None
    row = df.loc[oid]
    if isinstance(row, pd.DataFrame):  # defensive: duplicate ids
        row = row.iloc[0]
    record = outage_record(row)

    benefit = None
    if not store.dispatch_df.empty and oid in store.dispatch_df.index:
        benefit = num(store.dispatch_df.loc[oid, "benefit"], 2)

    rate = record["local_crime_rate"]
    record.update({
        "incident_address": text(row.get("incident_address")),
        "cross_street_1": text(row.get("cross_street_1")),
        "cross_street_2": text(row.get("cross_street_2")),
        "intersection_street_1": text(row.get("intersection_street_1")),
        "intersection_street_2": text(row.get("intersection_street_2")),
        "fifo_queue_rank": store.fifo_rank.get(oid),
        "lightsafe_queue_rank": store.lightsafe_rank.get(oid),
        "impact_index": benefit,
        # local_crime_rate is crimes/day over the 14-day look-back, so this is the exact count.
        "recent_crime_count_14d": int(round(rate * 14)) if rate is not None else None,
        "decomposition": {
            "tau_net": num(row.get("tau_net")),
            "tau_net_source": text(row.get("tau_net_source")),
            "local_crime_rate": rate,
            "lookback_days": 14,
            "duration_factor": num(row.get("duration_factor"), 4),
            "outage_duration_hours": record["outage_duration_hours"],
            "raw_priority": num(row.get("raw_priority"), 6),
            "priority_score": record["priority_score"],
            "priority_tier": record["priority_tier"],
        } if record["scored"] else None,
    })
    return record


def update_outage_action(outage_id: str, action: str) -> Optional[Dict[str, Any]]:
    if store.outages_df.empty or str(outage_id) not in store.outages_df.index:
        return None
    status = VALID_ACTIONS[action]
    if status == "None":
        store.operator_actions.pop(str(outage_id), None)
    else:
        # re-insert so the most recent action is last (Outages list display order)
        store.operator_actions.pop(str(outage_id), None)
        store.operator_actions[str(outage_id)] = status
    return {"outage_id": str(outage_id), "operator_note": status, "success": True}
