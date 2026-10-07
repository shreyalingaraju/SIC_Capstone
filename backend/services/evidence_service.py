"""
Evidence explorer: frozen Stage 11 exposure estimates, the Stage 11c sensitivity grid and the
retired Stage 7-10 pair design, read straight from the stored output files.

Nothing is re-estimated here. Intervals are the stored 95% CIs; where a file stores no interval
(e.g. the displacement proportion) none is returned.
"""
from functools import lru_cache
import json
from typing import Any, Dict, List, Optional

import pandas as pd

from .. import config
from .common import num, text
from .data_store import store

BAND_LABELS = {
    "x_0_100": "0–100 m",
    "x_100_250": "100–250 m",
    "x_250_500": "250–500 m",
    "x_500_750": "500–750 m",
}

# Stage 11 specification / scenario descriptions (from src/models/exposure_model.py,
# src/models/stage11_sensitivity.py and docs/stage11_exposure_analysis.md).
VARIANTS = [
    # key, source, group, label, description, comparable to primary
    ("primary", "spec", "Baseline", "Primary (frozen)",
     "Reported-open interval [created, closed or snapshot), capped at 8,760 h; H3 res-10 cell x ISO week; "
     "bands 0–100 / 100–250 / 250–500 m.", True),
    ("cutoff_750", "spec", "Specification", "Outer cutoff 750 m",
     "Primary model with an added 500–750 m band.", True),
    ("fortnight", "spec", "Specification", "Fortnight periods",
     "Cell x fortnight instead of cell x week. An extra light-night raises one of two weeks, so the "
     "coefficient is about half the weekly one and is not directly comparable.", False),
    ("strict", "sens", "Timing", "Strict validity rule",
     "Open-without-closure intervals and closures > 8,760 h excluded (Stage 3 validity rule).", True),
    ("start_L7", "sens", "Timing", "Start 7 days earlier",
     "Reported-open interval starts 7 days before the 311 report.", True),
    ("end_early", "sens", "Timing", "Early end (50%)",
     "Interval ends halfway between the report and the primary end.", True),
    ("merge_g7", "sens", "Timing", "Merge gaps ≤ 7 nights",
     "At each site, gaps of up to 7 nights between reported-open runs are filled.", True),
    ("merge_g14", "sens", "Timing", "Merge gaps ≤ 14 nights",
     "At each site, gaps of up to 14 nights between reported-open runs are filled.", True),
    ("deadline:deadline", "sens", "Timing", "Deadline closures only",
     "Exposure split: nights from complaints closed 222–240 h after the report (deadline closures). "
     "Estimated jointly with the 'other' part.", True),
    ("deadline:other", "sens", "Timing", "All other closures",
     "Exposure split: all non-deadline reported-open nights. Estimated jointly with the deadline part.", True),
]


def _read_csv(path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def _read_json(path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _row(r: pd.Series, band: str) -> Dict[str, Any]:
    lo, hi = num(r.get("ci_lower")), num(r.get("ci_upper"))
    p = num(r.get("p_value"))
    return {
        "band": band,
        "band_label": BAND_LABELS.get(band, band),
        "estimate": num(r.get("estimate")),
        "std_error": num(r.get("std_error")),
        "ci_lower": lo,
        "ci_upper": hi,
        "p_value": p,
        "pct_per_7_nights": num(r.get("pct_change_per_7_nights")),
        "pct_ci_lower": num(r.get("pct_ci_lower")),
        "pct_ci_upper": num(r.get("pct_ci_upper")),
        "ci_includes_zero": lo is not None and hi is not None and lo <= 0 <= hi,
        "significant_at_5pct": p is not None and p < 0.05,
        "n_obs": num(r.get("n_obs")),
        "n_cells": num(r.get("n_cells")),
        "n_clusters": num(r.get("n_clusters")),
        "exposure_total": num(r.get("exposure_total")),
        "period": text(r.get("period")),
    }


@lru_cache(maxsize=1)
def get_stage11() -> Dict[str, Any]:
    """Frozen Stage 11 estimates, ring definitions and the full variant grid."""
    est = _read_csv(config.STAGE11_ESTIMATES_FILE)
    summary = _read_json(config.STAGE11_SUMMARY_FILE)
    sens = _read_csv(config.STAGE11_SENSITIVITY_FILE)
    if est.empty or not summary:
        return {"available": False}

    defs = summary.get("stage11a_definitions", {})
    primary_bands = defs.get("primary_bands", [])
    bands_m = defs.get("bands_m", {})

    rows: List[Dict[str, Any]] = []
    for key, source, group, label, description, comparable in VARIANTS:
        if source == "spec":
            sub = est[est["spec"] == key]
            pairs = [(str(r["band"]), r) for _, r in sub.iterrows()]
        else:
            if sens.empty:
                continue
            scen, part = (key.split(":") + [None])[:2]
            sub = sens[sens["spec"] == scen]
            pairs = []
            for _, r in sub.iterrows():
                b = str(r["band"])
                if part is not None:
                    if not b.endswith("_" + part):
                        continue
                    b = b[: -len(part) - 1]
                pairs.append((b, r))
        for band, r in pairs:
            rows.append({"variant": key, "group": group, "label": label, "description": description,
                         "comparable_to_primary": comparable, **_row(r, band)})

    # Sign / ratio relative to the primary estimate of the same band (computed from the stored rows).
    base = {r["band"]: r["estimate"] for r in rows if r["variant"] == "primary"}
    for r in rows:
        b0 = base.get(r["band"])
        if r["variant"] == "primary" or b0 is None or r["estimate"] is None:
            r["sign_matches_primary"] = None if r["variant"] != "primary" else True
        else:
            r["sign_matches_primary"] = (r["estimate"] >= 0) == (b0 >= 0)

    # E0 is the primary exposure rule rebuilt in Stage 11c; it must equal the stored primary rows.
    e0_matches = None
    if not sens.empty:
        e0 = sens[sens["spec"] == "E0"].set_index("band")["estimate"]
        p0 = est[est["spec"] == "primary"].set_index("band")["estimate"]
        e0_matches = bool(len(e0) and e0.reindex(p0.index).sub(p0).abs().max() < 1e-12)

    sens_summary = _read_json(config.STAGE11_SENSITIVITY_FILE.with_name("stage11_sensitivity_summary.json"))
    diff = sens_summary.get("results", {}).get("deadline", {}).get("difference_deadline_minus_other", {})
    deadline_diff = [{"band": b, "band_label": BAND_LABELS.get(b, b), "difference": num(v.get("difference")),
                      "std_error": num(v.get("std_error")), "p_value": num(v.get("p_value"))}
                     for b, v in diff.items()]

    # Single-exposure timing scenarios; the deadline split (two jointly estimated parts) is reported separately.
    timing = [r for r in rows if r["group"] == "Timing" and not r["variant"].startswith("deadline")]
    comparable = [r for r in rows if r["variant"] != "primary" and r["comparable_to_primary"]]
    stats = {
        "n_estimates": len(rows),
        "n_ci_excluding_zero": sum(1 for r in rows if not r["ci_includes_zero"]),
        "min_p_value": min((r["p_value"] for r in rows if r["p_value"] is not None), default=None),
        "sign_flips_vs_primary": sum(1 for r in comparable if r["sign_matches_primary"] is False),
        "n_compared": len(comparable),
        "timing_sign_flips_0_100": sum(1 for r in timing if r["band"] == "x_0_100" and r["sign_matches_primary"] is False),
        "n_timing_0_100": sum(1 for r in timing if r["band"] == "x_0_100"),
    }

    sample = summary.get("results", {}).get("primary", {}).get("sample", {})
    return {
        "available": True,
        "estimand": ("Change in log expected night crime in an H3 res-10 cell per additional reported-open "
                     "light-night (311 open status) in a distance band. An association, not the effect of "
                     "physical darkness, a repair, or dispatch."),
        "interpretation_source": "docs/stage11_exposure_analysis.md (supersedes the stored estimand string)",
        "model": text(est.iloc[0].get("estimator")),
        "fixed_effects": text(est.iloc[0].get("fixed_effects")),
        "cluster": text(est.iloc[0].get("cluster")),
        "rings": {
            "unit": f"H3 res-{defs.get('h3_resolution', 10)} cell (centre)",
            "universe_radius_m": defs.get("universe_radius_m"),
            "bands": [{"band": b, "label": BAND_LABELS.get(b, b), "inner_m": bands_m[b][0], "outer_m": bands_m[b][1],
                       "primary": b in primary_bands} for b in bands_m],
            "night_window": defs.get("night_window"),
            "interval_rule": defs.get("interval_rule"),
            "panel": f"{defs.get('first_week')} to {defs.get('last_week')} ({defs.get('n_weeks')} weeks)",
        },
        "sample": {k: sample.get(k) for k in ("n_obs", "n_cells", "n_periods", "n_clusters", "crime_total_estimation")},
        "rows": rows,
        "deadline_difference": deadline_diff,
        "e0_matches_primary": e0_matches,
        "stats": stats,
    }


def get_legacy_pair_design() -> Dict[str, Any]:
    """Retired Stage 7-10 paired DiD (outputs/displacement_estimates.csv), labelled provisional."""
    disp = store.displacement_df
    if disp.empty:
        return {"available": False}
    from src.features import match_controls as s7  # ring/matching constants as defined in the Stage 7 code

    rows = []
    for _, r in disp.iterrows():
        name = str(r["effect_name"])
        lo, hi = num(r.get("ci_lower")), num(r.get("ci_upper"))
        effect, _, period = name.rpartition("_")
        rows.append({
            "effect_name": name, "effect": effect, "period": period,
            "estimate": num(r.get("estimate")), "std_error": num(r.get("standard_error")),
            "ci_lower": lo, "ci_upper": hi, "p_value": num(r.get("p_value")),
            "interval_available": lo is not None and hi is not None,
            "ci_includes_zero": lo is not None and hi is not None and lo <= 0 <= hi,
            "outcome_ring": text(r.get("outcome_ring")),
            "note": text(r.get("note")),
        })
    first = disp.iloc[0]
    return {
        "available": True,
        "status": ("Retired exploratory design (Stages 7-10). Provisional; M1-M8 open; not used by the "
                   "frozen Stage 11-14 pipeline."),
        "model": text(first.get("model")),
        "sign_convention": text(first.get("sign_convention")),
        "n_pairs": num(first.get("n_pairs")),
        "n_clusters": num(first.get("n_clusters")),
        "rings": {
            "treatment_radius_m": s7.DIRECT_RADIUS_M,
            "displacement_radius_m": s7.OUTCOME_RADIUS_M,
            "control_band_min_m": s7.MATCH_BAND_MIN_M,
            "control_band_max_m": s7.MATCH_BAND_MAX_M,
            "exclusion_radius_m": s7.EXCLUSION_RADIUS_M,
            "source": "src/features/match_controls.py constants (current Stage 7 code defaults)",
        },
        "rows": rows,
    }
