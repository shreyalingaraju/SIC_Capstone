"""Overview KPIs: every number comes straight from the Stage 12-14 artifacts."""
from typing import Any, Dict, Optional

from .. import config
from .common import is_all, borough_matches, num, title_borough
from .data_store import store
from .dispatch_service import get_comparison
from .optimization_service import get_optimization_plan, get_optimization_summary
from .pipeline_service import get_pipeline_status


def get_overview_data(borough: Optional[str] = None, priority_tier: Optional[str] = None) -> Dict[str, Any]:
    scored = store.scored_sorted
    filtered = scored
    if not scored.empty:
        if not is_all(borough):
            filtered = filtered[borough_matches(filtered["borough"], borough)]
        if not is_all(priority_tier):
            filtered = filtered[filtered["priority_tier"].fillna("").str.lower() == priority_tier.strip().lower()]

    tiers = filtered["priority_tier"].value_counts().to_dict() if not filtered.empty else {}
    opt = get_optimization_summary()
    comparison = get_comparison(points=2)
    plan = get_optimization_plan(decision="recommended", borough=borough, page=1, page_size=10)

    day_one = comparison.get("day_one") if comparison.get("available") else None
    return {
        "kpis": {
            "total_outages": int(len(store.outages_df)),
            "scored_outages": int(len(filtered)),
            "excluded_outages": int((~store.outages_df["scored"]).sum()) if not store.outages_df.empty else 0,
            "high_priority": int(tiers.get("High", 0)),
            "medium_priority": int(tiers.get("Medium", 0)),
            "low_priority": int(tiers.get("Low", 0)),
            "recommended_repairs": opt.get("n_selected"),
            "daily_budget": opt.get("daily_budget"),
            "budget_used": opt.get("budget_used"),
            "budget_remaining": opt.get("budget_remaining"),
            "plan_objective_index": opt.get("objective_value"),
        },
        "plan_vs_fifo": {
            "repairs": day_one["repairs"],
            "lightsafe_index": day_one["lightsafe"],
            "fifo_index": day_one["fifo"],
            "absolute_difference": day_one["absolute_difference"],
            "improvement_pct": day_one["improvement_pct"],
            "impact_unit": config.IMPACT_UNIT,
        } if day_one else None,
        "priority_mix": {
            "high": int(tiers.get("High", 0)),
            "medium": int(tiers.get("Medium", 0)),
            "low": int(tiers.get("Low", 0)),
            "total": int(len(filtered)),
        },
        "recommended_repairs": plan["items"],
        "optimization": {
            "solver_status": opt.get("solver_status"),
            "price_of_fairness": opt.get("price_of_fairness"),
            "borough_table": opt.get("borough_table", []),
            "all_quotas_satisfied": opt.get("all_quotas_satisfied"),
        } if opt.get("available") else None,
        "impact_note": config.IMPACT_UNIT_NOTE,
        "data_status": get_pipeline_status(),
    }
