"""Stage 13: FIFO vs LightSafe queues and the cumulative-impact comparison."""
from typing import Any, Dict, Optional

import numpy as np

from .. import config
from .common import borough_matches, is_all, location_desc, num, paginate, text, title_borough
from .data_store import store

METHODS = {"fifo": "FIFO", "lightsafe": "LightSafe"}


def get_queue(method: str = "LightSafe", borough: Optional[str] = None, priority_tier: Optional[str] = None,
              page: int = 1, page_size: int = 50) -> Dict[str, Any]:
    method = METHODS[method.lower()]
    q = store.queue_df
    page_size = min(max(page_size, 1), config.MAX_PAGE_SIZE)
    if q.empty:
        return {"method": method, "items": [], **paginate(0, page, page_size)}
    df = q[q["method"] == method]
    if not is_all(borough):
        df = df[borough_matches(df["borough"].fillna("Unspecified"), borough)]
    if not is_all(priority_tier):
        df = df[df["priority_tier"].fillna("").str.lower() == priority_tier.strip().lower()]
    start = (page - 1) * page_size
    items = []
    for r in df.iloc[start:start + page_size].itertuples():
        out = store.outages_df.loc[r.outage_id] if r.outage_id in store.outages_df.index else None
        items.append({
            "queue_rank": int(r.queue_rank),
            "repair_day": int(r.repair_day),
            "outage_id": r.outage_id,
            "borough": title_borough(r.borough),
            "location_desc": location_desc(out) if out is not None else None,
            "created_date": text(r.created_date),
            "priority_score": num(r.priority_score, 2),
            "priority_tier": text(r.priority_tier),
            "impact_index": num(r.expected_impact, 2),
        })
    return {"method": method, "items": items, **paginate(len(df), page, page_size)}


def _days_to_fraction(days: np.ndarray, cumulative: np.ndarray, frac: float) -> Optional[int]:
    total = cumulative[-1]
    if total <= 0:
        return None
    return int(days[int(np.argmax(cumulative >= frac * total))])


def get_comparison(points: int = 150) -> Dict[str, Any]:
    c = store.comparison_df
    if c.empty:
        return {"available": False}
    c = c.sort_values("day")
    days = c["day"].to_numpy()
    ls = c["lightsafe_cumulative_impact"].to_numpy(dtype=float)
    fifo = c["fifo_cumulative_impact"].to_numpy(dtype=float)
    reps = c["cumulative_repairs"].to_numpy()

    n = len(c)
    idx = np.unique(np.linspace(0, n - 1, num=min(points, n)).round().astype(int))
    curve = [{
        "day": int(days[i]), "cumulative_repairs": int(reps[i]),
        "lightsafe": round(float(ls[i]), 2), "fifo": round(float(fifo[i]), 2),
    } for i in idx]

    first = c.iloc[0]
    return {
        "available": True,
        "impact_unit": config.IMPACT_UNIT,
        "impact_note": config.IMPACT_UNIT_NOTE,
        "repair_capacity_per_day": int(first["repair_capacity_k"]),
        "total_days": int(days[-1]),
        "total_repairs": int(reps[-1]),
        "total_impact_index": round(float(ls[-1]), 2),
        "day_one": {
            "repairs": int(first["cumulative_repairs"]),
            "lightsafe": round(float(first["lightsafe_cumulative_impact"]), 2),
            "fifo": round(float(first["fifo_cumulative_impact"]), 2),
            "absolute_difference": round(float(first["absolute_difference"]), 2),
            "improvement_pct": num(first["improvement_pct"], 1),
        },
        "milestones": {
            "time_to_50pct_impact_days": {
                "lightsafe": _days_to_fraction(days, ls, 0.5), "fifo": _days_to_fraction(days, fifo, 0.5)},
            "time_to_90pct_impact_days": {
                "lightsafe": _days_to_fraction(days, ls, 0.9), "fifo": _days_to_fraction(days, fifo, 0.9)},
            "time_to_clear_days": {"lightsafe": int(days[-1]), "fifo": int(days[-1])},
        },
        "curve": curve,
    }
