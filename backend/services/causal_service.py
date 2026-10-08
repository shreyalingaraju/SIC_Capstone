"""Stage 9-11 evidence, read directly from the estimate files (no hardcoded fallbacks)."""
from typing import Any, Dict, List

from .. import config
from .common import num, text
from .data_store import store

EFFECTS = ("direct", "displacement", "net")


def _effect(row) -> Dict[str, Any]:
    p = num(row.get("p_value"), 4)
    lo, hi = num(row.get("ci_lower"), 6), num(row.get("ci_upper"), 6)
    return {
        "effect_name": text(row.get("effect_name")),
        "estimate": num(row.get("estimate"), 6),
        "standard_error": num(row.get("standard_error"), 6),
        "ci_lower": lo,
        "ci_upper": hi,
        "p_value": p,
        "significant_at_5pct": (p is not None and p < 0.05),
        "ci_includes_zero": (lo is not None and hi is not None and lo <= 0 <= hi),
        "n_observations": num(row.get("n_observations")),
        "n_pairs": num(row.get("n_pairs")),
        "outcome_ring": text(row.get("outcome_ring")),
        "model": text(row.get("model")),
        "sign_convention": text(row.get("sign_convention")),
    }


def get_event_study_data() -> List[Dict[str, Any]]:
    df = store.event_study_df
    if df.empty:
        return []
    return [{
        "rel_week": int(r.rel_week),
        "coefficient": num(r.coefficient, 6),
        "std_error": num(r.std_error, 6),
        "ci_lower": num(r.ci_lower, 6),
        "ci_upper": num(r.ci_upper, 6),
        "p_value": num(r.p_value, 4),
    } for r in df.itertuples()]


def get_causal_overview() -> Dict[str, Any]:
    disp = store.displacement_df
    if disp.empty:
        return {"available": False}
    by_name = {r["effect_name"]: r for _, r in disp.iterrows()}
    table = [_effect(r) for _, r in disp.iterrows() if not str(r["effect_name"]).startswith("displacement_proportion")]

    out: Dict[str, Any] = {"available": True, "periods": {}}
    for period in ("post", "during"):
        out["periods"][period] = {
            e: _effect(by_name[f"{e}_{period}"]) for e in EFFECTS if f"{e}_{period}" in by_name
        }
        prop = by_name.get(f"displacement_proportion_{period}")
        out["periods"][period]["displacement_proportion"] = num(prop["estimate"], 4) if prop is not None else None

    first = disp.iloc[0]
    out.update({
        "method": text(first.get("model")),
        "specification": text(first.get("specification")),
        "sign_convention": text(first.get("sign_convention")),
        "dataset_summary": store.did_summary.get("dataset", {}),
        "event_study": get_event_study_data(),
        "estimates_table": table,
        "did_specifications": store.did_summary,
        "score_input": f"Stage 12 uses {config.dataset_profile.TAU_EFFECT} as tau_net, a single global constant.",
    })
    return out
