"""
Read-only access to the synthetic-profile context outputs (outputs/synthetic/context/).

Every value served here is read from files the pipeline generated (scripts/synthetic/build_context_outputs.py).
Nothing is computed from constants in this module: the only text defined here is documentation of the
simulation's assumptions. When the files are missing the endpoints answer ``available: false``.
"""
import json
from typing import Any, Dict, List, Optional

import pandas as pd

from .. import config
from .common import clean

UNAVAILABLE = {"available": False,
               "message": "Synthetic context outputs are not generated. Run scripts/synthetic/run_pipeline.py "
                          "and start the API with LIGHTSAFE_PROFILE=karnataka_synthetic."}

_cache: Dict[Any, Any] = {}


def _load(name: str):
    path = config.CONTEXT_DIR / name
    if not path.exists():
        return None
    key = (name, path.stat().st_mtime_ns)
    if key not in _cache:
        for old in [k for k in _cache if k[0] == name]:
            del _cache[old]
        if name.endswith(".json"):
            _cache[key] = json.loads(path.read_text(encoding="utf-8"))
        else:
            _cache[key] = pd.read_csv(path)
    return _cache[key]


def _records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    return [{k: clean(v) for k, v in row.items()} for row in df.to_dict("records")]


def profile() -> Dict[str, Any]:
    outages = _load("outage_table.csv")
    return {
        "profile": config.PROFILE_NAME,
        "synthetic": config.IS_SYNTHETIC,
        "synthetic_label": config.SYNTHETIC_LABEL if config.IS_SYNTHETIC else None,
        "region": config.REGION_LABEL,
        "map_center": list(config.MAP_CENTER),
        "map_zoom": config.MAP_ZOOM,
        "boroughs": sorted(outages["city"].dropna().unique().tolist()) if outages is not None else [],
        "context_available": outages is not None,
        "notice": config.PIPELINE_NOTICE,
    }


def overview() -> Dict[str, Any]:
    kpis = _load("overview.json")
    if kpis is None:
        return UNAVAILABLE
    risk = _load("risk_model.json") or {}
    return {"available": True, "synthetic": config.IS_SYNTHETIC, "label": config.SYNTHETIC_LABEL, "kpis": kpis,
            "risk_model": {k: risk.get(k) for k in ("spearman_predicted_vs_observed", "top_decile_capture", "top_decile_lift")}}


def wards(city: Optional[str] = None, area_class: Optional[str] = None, risk_category: Optional[str] = None) -> Dict[str, Any]:
    df = _load("ward_summary.csv")
    if df is None:
        return UNAVAILABLE
    if city and city != "All":
        df = df[df["city"] == city]
    if area_class and area_class != "All":
        df = df[df["area_class"] == area_class]
    if risk_category and risk_category != "All":
        df = df[df["risk_category"] == risk_category]
    return {"available": True, "count": int(len(df)), "items": _records(df.round(4))}


def population() -> Dict[str, Any]:
    df = _load("population_analysis.csv")
    if df is None:
        return UNAVAILABLE
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for gtype, sub in df.groupby("group_type", sort=False):
        groups[gtype] = _records(sub.drop(columns=["group_type"]).round(4))
    return {"available": True, "groups": groups}


def relationships() -> Dict[str, Any]:
    rel = _load("relationships.csv")
    w = _load("ward_summary.csv")
    if rel is None or w is None:
        return UNAVAILABLE
    cols = ["ward_id", "city", "population", "pop_density_per_km2", "income_index", "vulnerable_pop_share", "rainfall_mm_year",
            "pole_age_years", "dist_main_road_km", "dist_depot_km", "outages_per_km2", "night_crimes_per_km2",
            "mean_predicted_risk", "mean_priority_score", "mean_outage_days", "risk_score"]
    return {"available": True, "relations": _records(rel.round(4)), "points": _records(w[cols].dropna().round(4))}


def causal() -> Dict[str, Any]:
    c = _load("causal_summary.json")
    if c is None:
        return UNAVAILABLE
    return {"available": True, **c,
            "reading_guide": {
                "prediction": "The ML risk model finds where night crime is high around outages. It is an association: "
                              "it cannot say how much a repair would change.",
                "causal": "The matched-control difference-in-differences estimate compares each outage site with a similar site "
                          "that stayed lit, so it targets the change in crime that the dark period itself added.",
            }}


def risk_model() -> Dict[str, Any]:
    m = _load("risk_model.json")
    return UNAVAILABLE if m is None else {"available": True, **m}


def prioritization(city: Optional[str], action: Optional[str], page: int, page_size: int, sort: str) -> Dict[str, Any]:
    df = _load("outage_table.csv")
    if df is None:
        return UNAVAILABLE
    if city and city != "All":
        df = df[df["city"] == city]
    if action and action != "All":
        df = df[df["recommended_action"] == action]
    sort_col = sort if sort in ("priority_score", "predicted_risk", "priority_ex_ante", "stage12_score") else "priority_score"
    df = df.sort_values([sort_col, "created_date"], ascending=[False, True], kind="mergesort")
    total = len(df)
    start = (page - 1) * page_size
    page_df = df.iloc[start:start + page_size].copy()
    page_df.insert(0, "rank", range(start + 1, start + 1 + len(page_df)))
    cols = ["rank", "unique_key", "ward_id", "city", "area_class", "created_date", "population", "pop_density_per_km2",
            "vulnerable_pop_share", "elevation_m", "slope_pct", "rainfall_mm_year", "dist_depot_km", "prior_complaints_90d_50m",
            "local_crime_rate", "predicted_risk", "risk_percentile", "priority_score", "priority_tier", "stage12_score",
            "recommended_action", "optimization_rank", "priority_reasons", "xgb_top_features",
            "contrib_risk", "contrib_exposure", "contrib_vulnerable", "contrib_recurrence", "contrib_efficiency"]
    return {"available": True, "total": int(total), "page": page, "page_size": page_size, "sorted_by": sort_col,
            "items": _records(page_df[cols].round(4)),
            "note": "Priority score = decision-layer composite (0-100): XGBoost risk (weighted by causal evidence), population exposure, "
                    "vulnerability, repeat failures and repair efficiency. It uses no post-report information. The Stage 12 index "
                    "(which uses the realised duration) is shown for comparison."}


def scenarios(scenario: Optional[str]) -> Dict[str, Any]:
    df = _load("policy_comparison.csv")
    if df is None:
        return UNAVAILABLE
    names = df[["scenario", "scenario_label", "scenario_description", "capacity_k_per_day", "outages"]].drop_duplicates("scenario")
    chosen = scenario if scenario in set(df["scenario"]) else "baseline"
    sub = df[df["scenario"] == chosen]
    return {"available": True, "selected": chosen, "scenarios": _records(names),
            "policies": _records(sub.drop(columns=["scenario", "scenario_label", "scenario_description"]).round(4)),
            "notes": [
                "Policies are replayed through a daily-dispatch simulation on the generated outages (capacity shown per scenario).",
                "'Extra crimes (benchmark)' uses the generator's planted ground truth, so it exists only for synthetic data "
                "and is used for evaluation, never for ranking.",
                "The Stage 12 score uses the realised outage duration (hindsight); the ex-ante causal priority does not.",
            ]}


def methodology() -> Dict[str, Any]:
    trace = _load("traceability.json")
    ov = _load("overview.json")
    if trace is None:
        return UNAVAILABLE
    return {
        "available": True,
        "label": config.SYNTHETIC_LABEL,
        "why_synthetic": "Real municipal outage logs and crime data are not available for this demonstration. A simulated dataset "
                         "lets us check that the pipeline works end to end and, because the simulation knows how much crime it "
                         "planted, whether the causal method recovers it.",
        "simulated": [
            "Seven Karnataka cities and their wards (neighbourhood clusters), each with population, density, income, "
            "vulnerable-population share, rainfall, elevation, slope, distance to main road and repair depot, pole age and "
            "urban/rural class.",
            "Streetlight complaints (mostly 'Street Light Out') and night/day crime records over calendar year 2025.",
        ],
        "assumed_relationships": [
            "Heavier rainfall and older poles make lights fail more often.",
            "Denser, lower-income wards have more baseline crime, so outage-prone places are also crime-prone (confounding).",
            "Repairs take longer far from the depot and from main roads, and are quicker in dense wards.",
            "Planted causal effect: an active outage adds night-time crime within about 100 m while the light is out; the uplift is "
            "larger in denser wards and fades after repair. Daytime crime is unaffected.",
        ],
        "model_derived": [
            "Matched-control difference-in-differences estimates (existing Stages 7-11).",
            "Priority index and tiers (Stage 12), FIFO vs priority queue (Stage 13), constrained dispatch plan (Stage 14).",
            "XGBoost night-crime risk model (Poisson objective, ward-held-out validation) and its SHAP explanations.",
            "Decision-layer priority: XGBoost risk (weighted by the share of crime the causal estimate attributes to outages) combined with "
            "population exposure, vulnerability, repeat failures and repair efficiency; Stage 13/14 queue and ILP re-run on that score.",
            "Repair pressure: share of complaints unresolved after 7 days, measured from the simulated complaint dates (no model).",
            "Dispatch-policy and scenario simulation.",
        ],
        "synthetic_values": [
            "Every population, density, income, weather, infrastructure, outage and crime value is simulated.",
            "The 'planted truth' shown next to causal estimates is the simulation's own ground truth, used only to check the estimate.",
        ],
        "limitations": [
            "A simulation shows that the pipeline works, not what happens in real Karnataka.",
            "The planted effect is stronger than a realistic one so that a small demo sample can detect it.",
            "The Stage 12 index uses realised outage duration (hindsight); the decision-layer score does not, but its weights are judgement calls, not validated.",
            "Only one average causal effect is estimated; it is not a ward-specific effect and cannot rank outages against each other.",
            "XGBoost explanations (importance, SHAP) describe the model, not causes.",
            "Ward-level variables are shared by all outages in a ward, so ranks cluster by ward.",
        ],
        "data_period": ov.get("data_period") if ov else None,
        "traceability": trace,
    }


def repair_pressure(city: Optional[str] = None) -> Dict[str, Any]:
    """Synthetic-profile repair pressure, read from the snapshot generated by scripts/synthetic/build_repair_pressure.py.
    Same response shape as the NYC /api/ml/regime so one panel renders both; labels say which kind it is."""
    status = {"state": "ready", "reason": None, "model": "None: measured from synthetic complaint timestamps"}
    labels = {"unit": "City", "reference": "the 2025 synthetic year", "kind": "observed_synthetic",
              "footnote": "Synthetic repair histories: these delays were simulated, not observed in Karnataka."}
    path = config.ML_REGIME_SNAPSHOT_FILE
    if not path.exists():
        return {"available": False, "model_status": status, "boroughs": [], "labels": labels,
                "note": "The synthetic repair-pressure snapshot has not been generated. Run scripts/synthetic/run_pipeline.py "
                        "(step 'pressure') or scripts/synthetic/build_repair_pressure.py."}
    snap = json.loads(path.read_text(encoding="utf-8"))
    rows = snap["boroughs"]
    if city and city.strip().lower() != "all":
        rows = [r for r in rows if r["borough"].lower() == city.strip().lower()]
    return {
        "available": True, "model_status": status, "window": snap["window"], "reference": snap["reference"],
        "boroughs": rows, "labels": labels, "role": "context_only",
        "explanation": "Repair pressure is the share of street-light complaints still unresolved a week after they were reported, "
                       "compared with the same city's earlier weeks in the synthetic year. " + snap.get("definition", "")
                       + " It is measured directly from the simulated complaint dates; no model is involved. It says nothing about crime "
                         "and does not change the priority score or the dispatch plan.",
        "limitations": ["Synthetic repair histories, not real Karnataka performance.",
                        "Measured per city, because single wards have too few complaints in a 28-day window."],
    }


def decision() -> Dict[str, Any]:
    d = _load("decision_summary.json")
    return UNAVAILABLE if d is None else {"available": True, **d}


def population_risk(top: int = 25) -> Dict[str, Any]:
    """Wards with population, ML risk and priority each expressed as a 0-100 percentile among wards, so the three
    different quantities can be compared on one scale (they are not comparable in raw units)."""
    w = _load("ward_summary.csv")
    if w is None:
        return UNAVAILABLE
    w = w.dropna(subset=["mean_predicted_risk", "mean_priority_score"]).copy()
    for src, dst in (("population", "population_pct"), ("mean_predicted_risk", "risk_pct"), ("mean_priority_score", "priority_pct")):
        w[dst] = w[src].rank(pct=True) * 100
    w = w.sort_values("mean_priority_score", ascending=False).head(top)
    cols = ["ward_id", "city", "population", "mean_predicted_risk", "mean_priority_score", "population_pct", "risk_pct", "priority_pct"]
    return {"available": True, "scale": "Percentile among wards (0-100); raw units differ and are not directly comparable.",
            "items": _records(w[cols].round(3))}
