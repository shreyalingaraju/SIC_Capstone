"""Stage 12 priority summary and the priority-ranked outage list."""
from typing import Any, Dict

from .common import num, title_borough
from .data_store import store
from .outage_service import get_outages


def get_priority_summary() -> Dict[str, Any]:
    all_df, scored = store.outages_df, store.scored_sorted
    if all_df.empty:
        return {"available": False}
    excluded = all_df[~all_df["scored"]]
    scores = scored["priority_score"]
    return {
        "available": True,
        "total_outages": int(len(all_df)),
        "total_scored": int(len(scored)),
        "total_excluded": int(len(excluded)),
        "exclusion_reasons": {
            str(k): int(v) for k, v in excluded["exclusion_reason"].fillna("unspecified").value_counts().items()
        },
        "tier_counts": {str(k): int(v) for k, v in scored["priority_tier"].value_counts().items()},
        "borough_counts": {title_borough(k): int(v) for k, v in scored["borough"].value_counts().items()},
        "score_stats": {
            "min": num(scores.min(), 3), "max": num(scores.max(), 3),
            "mean": num(scores.mean(), 3), "median": num(scores.median(), 3),
        },
        "tier_definition": "High: score >= 80; Medium: score >= 40; Low: otherwise (Stage 12).",
    }


def get_priority_queue(**kwargs) -> Dict[str, Any]:
    return {"summary": get_priority_summary(), **get_outages(**kwargs)}
