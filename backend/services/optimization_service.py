"""Stage 14: ILP dispatch plan (selected vs deferred), budget, quotas, price of fairness."""
from typing import Any, Dict, Optional

from .. import config
from .common import borough_matches, is_all, num, paginate, records_for, title_borough
from .data_store import store


def get_optimization_summary() -> Dict[str, Any]:
    s = store.dispatch_summary
    if not s:
        return {"available": False}
    params = s.get("parameters", {})
    alloc = s.get("borough_allocation", {})
    unconstrained = s.get("unconstrained_borough_allocation", {})
    quotas = s.get("quotas", {})
    compliance = s.get("quota_compliance", {})
    boroughs = sorted(set(quotas) | set(alloc) | set(unconstrained))

    unc_obj, pof = s.get("unconstrained_objective"), s.get("price_of_fairness")
    return {
        "available": True,
        "model": s.get("model"),
        "solver_status": s.get("solver_status"),
        "objective_definition": s.get("benefit_definition"),
        "impact_unit": config.IMPACT_UNIT,
        "impact_note": config.IMPACT_UNIT_NOTE,
        "n_candidates": s.get("n_candidates"),
        "n_selected": s.get("n_selected"),
        "n_deferred": s.get("n_deferred"),
        "daily_budget": params.get("daily_budget"),
        "repair_cost": params.get("repair_cost"),
        "quota_fraction": params.get("quota_fraction"),
        "budget_used": s.get("budget_used"),
        "budget_remaining": s.get("budget_remaining"),
        "objective_value": num(s.get("objective_value"), 2),
        "unconstrained_objective": num(unc_obj, 2),
        "price_of_fairness": num(pof, 2),
        "price_of_fairness_pct": num(100.0 * pof / unc_obj, 2) if pof is not None and unc_obj else None,
        "all_quotas_satisfied": bool(compliance) and all(compliance.values()),
        "borough_table": [{
            "borough": title_borough(b),
            "quota_minimum": quotas.get(b),
            "selected": alloc.get(b, 0),
            "unconstrained_selected": unconstrained.get(b, 0),
            "quota_satisfied": compliance.get(b),
        } for b in boroughs],
    }


def get_optimization_plan(decision: Optional[str] = None, borough: Optional[str] = None,
                          page: int = 1, page_size: int = 50) -> Dict[str, Any]:
    page_size = min(max(page_size, 1), config.MAX_PAGE_SIZE)
    df = store.scored_sorted
    if df.empty or store.dispatch_df.empty:
        return {"items": [], **paginate(0, page, page_size)}
    if decision == "recommended":
        df = df[df["dispatch_status"] == "recommended"].sort_values("optimization_rank")
    elif decision == "deferred":
        df = df[df["dispatch_status"] == "deferred"]
    else:
        df = df.assign(_r=df["optimization_rank"].fillna(1e12)).sort_values("_r", kind="mergesort")
    if not is_all(borough):
        df = df[borough_matches(df["borough"], borough)]
    start = (page - 1) * page_size
    items = records_for(df.iloc[start:start + page_size])
    for it in items:
        it["impact_index"] = num(store.dispatch_df.loc[it["outage_id"], "benefit"], 2)
    return {"items": items, **paginate(len(df), page, page_size)}
