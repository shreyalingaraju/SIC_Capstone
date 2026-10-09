"""
Context outputs for the synthetic Karnataka demonstration.

Reads what the existing pipeline already produced (outages_scored.parquet, the causal panel, the Stage 11
estimates, the Stage 13 queue and the Stage 14 plan) plus the synthetic ward table, and writes the
tables the dashboard serves, to outputs/synthetic/context/:

    outage_table.csv         one row per scored outage: ward, pre-outage features, ML risk, priority, action
    ward_summary.csv         one row per ward: context, outages, crime, risk, priority, actions
    population_analysis.csv  results grouped by density / vulnerability / income / area class / city
    relationships.csv        Spearman relations between ward conditions and outcomes
    risk_model.json          ML risk model metrics (cross-fitted, ward-held-out) and feature importance
    causal_summary.json      DiD estimates, naive before/after, planted-effect check, group estimates
    policy_comparison.csv    dispatch policies x scenarios (dynamic daily-dispatch simulation)
    overview.json            KPI values
    traceability.json        UI metric -> endpoint -> file -> source columns

Nothing here is typed in by hand: every number is computed from the pipeline files. The only external
input that is not part of the pipeline is the planted ground truth, used ONLY to evaluate (never to fit
or rank), and labelled as such wherever it is shown.

Run (after the pipeline stages):   LIGHTSAFE_PROFILE=karnataka_synthetic python scripts/synthetic/build_context_outputs.py
"""
import heapq
import json
import os
import sys
from math import erf, sqrt
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree
from scipy.stats import spearmanr
from sklearn.model_selection import GroupKFold

os.environ.setdefault("LIGHTSAFE_PROFILE", "karnataka_synthetic")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src import profile as prof  # noqa: E402
from src.features import decision_priority as dp  # noqa: E402
from src.features.ward_context import assign_ward, load_wards  # noqa: E402
from src.models import prioritization_engine as stage13  # noqa: E402
from src.optimization import ilp_solver as stage14  # noqa: E402

if not prof.IS_SYNTHETIC:
    raise SystemExit("build_context_outputs.py is for the synthetic profile (LIGHTSAFE_PROFILE=karnataka_synthetic)")

PROCESSED = ROOT / prof.PROCESSED_DIR
OUTPUTS = ROOT / prof.OUTPUTS_DIR
OUT = OUTPUTS / "context"
DEC = OUTPUTS / "decision"          # Stage 13/14 re-run on the decision-layer score
RAW = ROOT / prof.RAW_DIR
TRUTH_FILE = ROOT / "LightSafe_Synthetic_Karnataka_Data" / "v2" / "ground_truth" / "outage_effect_truth.csv"

SEED = 20250103
DIRECT_RADIUS_M = 100.0
BASE_CAPACITY_K = 20            # same default daily repair capacity as Stage 13/14 (prioritization_engine.DEFAULT_CAPACITY)
DENSITY_CAUTION = 2.0
WITHIN_100M_SHARE = erf(100.0 / (45.0 * sqrt(2.0)))   # share of planted extra crimes within 100 m (generator: half-normal sigma 45 m)

WARD_FEATURES = ["pop_density_per_km2", "income_index", "vulnerable_pop_share", "rainfall_mm_year", "elevation_m",
                 "slope_pct", "dist_main_road_km", "dist_depot_km", "pole_age_years"]

SCENARIOS = {
    # name: (label, description, demand rule or None, capacity multiplier)
    "baseline": ("Baseline", "Outages and repair capacity as generated.", None, 1.0),
    "high_rainfall": ("High rainfall", "Wards with above-median rainfall get 50% more outages (monsoon faults).",
                      ("rainfall_mm_year", 1.5), 1.0),
    "high_density": ("High population density", "Wards with density in the top third get 50% more outages.",
                     ("pop_density_per_km2", 1.5), 1.0),
    "poor_infrastructure": ("Poor infrastructure", "Wards with pole age in the top third get 50% more outages.",
                            ("pole_age_years", 1.5), 1.0),
    "improved_intervention": ("Improved intervention", "Repair capacity raised by 50% (crews per day x1.5).", None, 1.5),
}


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------------------------- load
def load_inputs():
    wards = load_wards(RAW / "synthetic_wards.csv")
    outages = pd.read_parquet(PROCESSED / "outages_scored.parquet").drop(columns=["geometry"])
    crime = pd.read_parquet(PROCESSED / "clean_crime.parquet", columns=["crime_datetime", "latitude", "longitude", "crime_category"])
    plan = pd.read_csv(OUTPUTS / "optimal_dispatch_plan.csv")
    est = pd.read_csv(OUTPUTS / "displacement_estimates.csv")
    comparison = pd.read_csv(OUTPUTS / "fifo_vs_lightsafe_comparison.csv")
    return wards, outages, crime, plan, est, comparison


# ---------------------------------------------------------------------------------------------- features
def attach_ward(outages, crime, wards):
    outages = outages.copy()
    outages["ward_id"] = assign_ward(wards, outages["latitude"], outages["longitude"], outages["borough"])
    crime = crime.copy()
    crime["ward_id"] = assign_ward(wards, crime["latitude"], crime["longitude"])
    return outages, crime


def dark_period_crime(outages, crime):
    """Observed night crimes within 100 m while each outage was open (an outcome, never a feature)."""
    tf = Transformer.from_crs("EPSG:4326", prof.CRS, always_xy=True)
    cx, cy = tf.transform(crime["longitude"].to_numpy(), crime["latitude"].to_numpy())
    ox, oy = tf.transform(outages["longitude"].to_numpy(), outages["latitude"].to_numpy())
    tree = cKDTree(np.column_stack([cx, cy]))
    ct = crime["crime_datetime"].to_numpy("datetime64[us]")
    start = outages["created_date"].to_numpy("datetime64[us]")
    end = outages["closed_date"].to_numpy("datetime64[us]")
    counts = np.zeros(len(outages), dtype=int)
    neigh = tree.query_ball_point(np.column_stack([ox, oy]), r=DIRECT_RADIUS_M)
    for i, cand in enumerate(neigh):
        if cand:
            t = ct[np.asarray(cand)]
            counts[i] = int(((t >= start[i]) & (t <= end[i])).sum())
    return counts


# ---------------------------------------------------------------------------------------------- ML risk
XGB_PARAMS = dict(objective="count:poisson", max_depth=3, learning_rate=0.05, n_estimators=250, min_child_weight=5,
                  subsample=0.8, colsample_bytree=0.8, reg_lambda=1.0, tree_method="hist", n_jobs=1, random_state=SEED)


def fit_risk_model(df):
    """Cross-fitted (ward-held-out) XGBoost Poisson regression of the night-crime rate near an outage while it is dark.

    Target: observed night crimes within 100 m per day while the outage was open (an outcome).
    Features: all known when the outage is reported: ward conditions, the pre-outage local crime rate (Stage 12 look-back ends
    before created_date), city, and report month/hour/weekday. Duration, closure and crime counts during the outage are NOT features.
    Each outage is predicted by a model that never saw its ward (GroupKFold by ward). Rows are weighted by exposure days.
    """
    import xgboost as xgb
    d = df.copy()
    d["city_code"] = d["borough"].astype("category").cat.codes
    d["month"] = d["created_date"].dt.month
    d["hour"] = d["created_date"].dt.hour
    d["dow"] = d["created_date"].dt.dayofweek
    feats = WARD_FEATURES + ["local_crime_rate", "city_code", "month", "hour", "dow"]
    X = d[feats]
    expo = (d["outage_duration_hours"] / 24.0).clip(lower=0.25).to_numpy()
    y = d["dark_night_crimes_per_day"].to_numpy()
    groups = d["ward_id"].to_numpy()
    oof = np.zeros(len(d))
    shap = np.zeros((len(d), len(feats)))
    base_pred = np.zeros(len(d))
    imp = np.zeros(len(feats))
    folds = list(GroupKFold(n_splits=5).split(X, y, groups))
    rng = np.random.default_rng(SEED)
    for tr, te in folds:
        m = xgb.XGBRegressor(**XGB_PARAMS)
        m.fit(X.iloc[tr], y[tr], sample_weight=expo[tr])
        oof[te] = m.predict(X.iloc[te])
        base_pred[te] = np.average(y[tr], weights=expo[tr])
        contrib = m.get_booster().predict(xgb.DMatrix(X.iloc[te]), pred_contribs=True)   # log-rate scale; last column = bias
        shap[te] = contrib[:, :-1]
        base = _poisson_dev(y[te], oof[te])
        for j, f in enumerate(feats):
            Xp = X.iloc[te].copy()
            Xp[f] = rng.permutation(Xp[f].to_numpy())
            imp[j] += _poisson_dev(y[te], m.predict(Xp)) - base
    imp /= len(folds)
    dev_model, dev_null = _poisson_dev(y, oof), _poisson_dev(y, base_pred)
    top = oof >= np.quantile(oof, 0.9)
    mae = float(np.average(np.abs(y - oof), weights=expo))
    rmse = float(np.sqrt(np.average((y - oof) ** 2, weights=expo)))
    mae0 = float(np.average(np.abs(y - base_pred), weights=expo))
    rmse0 = float(np.sqrt(np.average((y - base_pred) ** 2, weights=expo)))
    mean_abs_shap = np.abs(shap).mean(axis=0)
    metrics = {
        "model": "XGBoost XGBRegressor (objective count:poisson, depth 3, 250 trees, learning rate 0.05)",
        "target": "observed night crimes within 100 m per day while the outage was open",
        "validation": "5-fold GroupKFold by ward: every prediction is made by a model that never saw that ward",
        "n_outages": int(len(d)),
        "features": feats,
        "metrics_unit": "night crimes per day within 100 m (exposure-weighted)",
        "mae": mae, "rmse": rmse, "mae_baseline_mean_rate": mae0, "rmse_baseline_mean_rate": rmse0,
        "mae_improvement_pct": float((1 - mae / mae0) * 100), "rmse_improvement_pct": float((1 - rmse / rmse0) * 100),
        "spearman_predicted_vs_observed": float(spearmanr(oof, y).statistic),
        "poisson_deviance_explained": float(1 - dev_model / dev_null),
        "top_decile_capture": float(y[top].sum() / y.sum()),
        "top_decile_lift": float((y[top].mean()) / y.mean()),
        "no_skill_top_decile_capture": 0.1,
        "interpretation": "Predictive association only. XGBoost learns which outages sit where night crime is high; feature "
                          "importance and SHAP values describe the model's behaviour, not causes. The causal question is answered "
                          "separately (causal_summary.json).",
    }
    metrics["feature_importance"] = [{"feature": f, "importance": float(v), "mean_abs_shap_log_rate": float(sh)}
                                     for f, v, sh in sorted(zip(feats, imp, mean_abs_shap), key=lambda t: -t[1])]
    # per-outage top contributions (SHAP, log-rate scale): positive = pushes predicted risk up
    names = np.array(feats)
    top_shap = []
    for i in range(len(d)):
        idx = np.argsort(-np.abs(shap[i]))[:3]
        top_shap.append("; ".join(f"{names[j]} ({'+' if shap[i, j] > 0 else '-'}{abs(shap[i, j]):.2f})" for j in idx))
    return oof, metrics, top_shap


def _poisson_dev(y, mu):
    mu = np.clip(mu, 1e-9, None)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(y > 0, y * np.log(y / mu), 0.0)
    return float(2 * np.sum(t - (y - mu)) / len(y))


# ---------------------------------------------------------------------------------------------- tables
def prior_complaints(scored):
    """Earlier 'Street Light Out' complaints within 50 m during the 90 days before each outage was reported (ex-ante)."""
    raw = pd.read_csv(RAW / "streetlight_complaints.csv", usecols=["created_date", "descriptor", "latitude", "longitude"])
    raw = raw[raw["descriptor"] == "Street Light Out"].copy()
    raw["created_date"] = pd.to_datetime(raw["created_date"])
    tf = Transformer.from_crs("EPSG:4326", prof.CRS, always_xy=True)
    rx, ry = tf.transform(raw["longitude"].to_numpy(), raw["latitude"].to_numpy())
    ox, oy = tf.transform(scored["longitude"].to_numpy(), scored["latitude"].to_numpy())
    tree = cKDTree(np.column_stack([rx, ry]))
    rt = raw["created_date"].to_numpy("datetime64[us]")
    ot = scored["created_date"].to_numpy("datetime64[us]")
    out = np.zeros(len(scored), dtype=int)
    for i, cand in enumerate(tree.query_ball_point(np.column_stack([ox, oy]), r=50.0)):
        if cand:
            t = rt[np.asarray(cand)]
            out[i] = int(((t < ot[i]) & (t >= ot[i] - np.timedelta64(90, "D"))).sum())
    return out


def action_for(row):
    if row["dispatch_decision"] == "repair":
        return "Repair now"
    if row["priority_tier"] in ("High", "Medium"):
        return "Schedule next"
    return "Monitor"


def build_outage_table(outages, crime, wards, plan, oof):
    d = outages[outages["scored"]].copy()
    d = d.merge(wards[["ward_id", "city", "area_class", "population"]], on="ward_id", how="left")   # ward features are already on `outages`
    d["duration_days"] = d["outage_duration_hours"] / 24.0
    d["predicted_risk"] = oof
    pr = plan.set_index(plan["outage_id"].astype(str))
    d["dispatch_decision"] = d["unique_key"].astype(str).map(pr["decision"]).fillna("defer")
    d["optimization_rank"] = d["unique_key"].astype(str).map(pr["optimization_rank"])
    d["recommended_action"] = d.apply(action_for, axis=1)
    d["risk_percentile"] = d["predicted_risk"].rank(pct=True) * 100
    # Ex-ante causal priority: causal effect x local crime rate in the 14 days BEFORE the outage. Unlike the Stage 12
    # score it does not multiply by the realised outage duration, which a dispatcher cannot know in advance.
    d["priority_ex_ante"] = d["tau_net"] * d["local_crime_rate"]
    return d


def build_ward_summary(otab, all_outages, crime, wards):
    nc = crime.groupby("ward_id").size().rename("night_crimes")
    n_out = all_outages.groupby("ward_id").size().rename("outages")
    g = otab.groupby("ward_id")
    agg = pd.DataFrame({
        "scored_outages": g.size(),
        "mean_outage_days": g["duration_days"].mean(),
        "mean_local_crime_rate": g["local_crime_rate"].mean(),
        "mean_predicted_risk": g["predicted_risk"].mean(),
        "mean_priority_score": g["priority_score"].mean(),
        "max_priority_score": g["priority_score"].max(),
        "high_medium_priority": g["priority_tier"].apply(lambda s: int(s.isin(["High", "Medium"]).sum())),
        "repair_now": g["recommended_action"].apply(lambda s: int((s == "Repair now").sum())),
    })
    w = wards.set_index("ward_id").join([n_out, nc, agg]).fillna(
        {"outages": 0, "night_crimes": 0, "scored_outages": 0, "high_medium_priority": 0, "repair_now": 0})
    w["outages_per_10k_pop"] = w["outages"] / w["population"] * 1e4
    w["outages_per_km2"] = w["outages"] / w["area_km2"]
    w["night_crimes_per_km2"] = w["night_crimes"] / w["area_km2"]
    r = w["mean_predicted_risk"]
    w["risk_score"] = 100 * (r - r.min()) / (r.max() - r.min())
    q67, q33 = r.quantile(0.67), r.quantile(0.33)
    w["risk_category"] = np.where(r >= q67, "High", np.where(r >= q33, "Moderate", "Low"))
    w.loc[r.isna(), "risk_category"] = "n/a"
    return w.reset_index()


def population_analysis(otab, wards_sum):
    w = wards_sum.copy()
    od = otab.merge(w[["ward_id"]], on="ward_id")

    def tercile(s, labels):
        return pd.qcut(s, 3, labels=labels)

    w["Population density"] = pd.qcut(w["pop_density_per_km2"], 4, labels=["Q1 lowest", "Q2", "Q3", "Q4 highest"])
    w["Vulnerable population share"] = tercile(w["vulnerable_pop_share"], ["Low", "Medium", "High"])
    w["Income level"] = tercile(w["income_index"], ["Low income", "Middle", "High income"])
    w["Urban / rural class"] = w["area_class"]
    w["City"] = w["city"]
    rows = []
    for gtype in ["Population density", "Vulnerable population share", "Income level", "Urban / rural class", "City"]:
        key = w[["ward_id", gtype]].rename(columns={gtype: "grp"})
        o = otab.merge(key, on="ward_id")
        for grp, wg in w.groupby(gtype, observed=True):
            og = o[o["grp"] == grp]
            pop = wg["population"].sum()
            rows.append({
                "group_type": gtype, "group": str(grp), "wards": len(wg), "population": int(pop),
                "outages": int(wg["outages"].sum()),
                "outages_per_10k_pop": float(wg["outages"].sum() / pop * 1e4),
                "night_crimes_per_km2": float(wg["night_crimes"].sum() / wg["area_km2"].sum()),
                "mean_predicted_risk": float(og["predicted_risk"].mean()) if len(og) else None,
                "mean_priority_score": float(og["priority_score"].mean()) if len(og) else None,
                "mean_outage_days": float(og["duration_days"].mean()) if len(og) else None,
                "high_medium_priority": int(og["priority_tier"].isin(["High", "Medium"]).sum()),
                "repair_now": int((og["recommended_action"] == "Repair now").sum()),
            })
    return pd.DataFrame(rows)


def relationships(w):
    pairs = [
        ("pop_density_per_km2", "mean_predicted_risk", "Population density -> risk"),
        ("pop_density_per_km2", "night_crimes_per_km2", "Population density -> night crime per km2"),
        ("vulnerable_pop_share", "mean_predicted_risk", "Vulnerable share -> risk"),
        ("income_index", "mean_predicted_risk", "Income -> risk"),
        ("rainfall_mm_year", "outages_per_km2", "Rainfall -> outages per km2"),
        ("pole_age_years", "outages_per_km2", "Pole age -> outages per km2"),
        ("dist_depot_km", "mean_outage_days", "Distance to depot -> outage length"),
        ("dist_main_road_km", "mean_outage_days", "Distance to main road -> outage length"),
        ("dist_main_road_km", "mean_priority_score", "Accessibility (road distance) -> priority"),
        ("mean_predicted_risk", "mean_priority_score", "Risk -> priority"),
    ]
    out = []
    for x, y, label in pairs:
        s = w[[x, y]].dropna()
        out.append({"label": label, "x": x, "y": y, "n_wards": len(s),
                    "spearman": float(spearmanr(s[x], s[y]).statistic)})
    return pd.DataFrame(out)


# ---------------------------------------------------------------------------------------------- causal
def decision_layer(otab, outages, est, risk_metrics):
    """Combine XGBoost risk, the causal estimate and population/geography context into the decision priority (see
    src/features/decision_priority.py), then re-run the existing Stage 13 queue and Stage 14 ILP on that score."""
    otab = otab.rename(columns={"priority_score": "stage12_score", "priority_tier": "stage12_tier"})
    pairs = pd.read_parquet(PROCESSED / "control_area_pairs.parquet", columns=["treatment_key", "outage_duration_hours"])
    treated = otab[otab["unique_key"].isin(pairs["treatment_key"])]
    row = est[est["effect_name"] == prof.TAU_EFFECT].iloc[0]
    mean_days = float(pairs["outage_duration_hours"].mean() / 24.0)
    per_day = float(row["estimate"]) / mean_days
    mean_rate = float(treated["predicted_risk"].mean())
    cw = dp.causal_weight(float(row["estimate"]), float(row["ci_lower"]), float(row["ci_upper"]), per_day, mean_rate)

    total, contrib = dp.score(otab, otab, cw)
    otab["priority_score"] = total.to_numpy()
    otab["priority_tier"] = dp.tiers(total).to_numpy()
    for k in contrib.columns:
        otab[f"contrib_{k}"] = contrib[k].round(3).to_numpy()
    otab["priority_reasons"] = dp.reasons(contrib, otab)
    otab["priority_ex_ante"] = otab["tau_net"] * otab["local_crime_rate"]

    # Existing Stage 13 (queue) and Stage 14 (ILP) logic, fed the decision score instead of the Stage 12 index.
    DEC.mkdir(parents=True, exist_ok=True)
    full = pd.read_parquet(PROCESSED / "outages_scored.parquet")
    m = otab.set_index("unique_key")
    ok = full["unique_key"].isin(m.index) & full["scored"]
    full.loc[ok, "priority_score"] = full.loc[ok, "unique_key"].map(m["priority_score"]).to_numpy()
    full.loc[ok, "priority_tier"] = full.loc[ok, "unique_key"].map(m["priority_tier"]).to_numpy()
    full.loc[ok, "raw_priority"] = full.loc[ok, "priority_score"]
    path = DEC / "decision_scored.parquet"
    full.to_parquet(path, index=False)
    stage13.main(["--scored", str(path), "--out", str(DEC)])
    stage14.main(["--scored", str(path), "--out", str(DEC)])
    plan = pd.read_csv(DEC / "optimal_dispatch_plan.csv")
    pr = plan.set_index(plan["outage_id"].astype(str))
    otab["dispatch_decision"] = otab["unique_key"].astype(str).map(pr["decision"]).fillna("defer")
    otab["optimization_rank"] = otab["unique_key"].astype(str).map(pr["optimization_rank"])
    otab["recommended_action"] = otab.apply(action_for, axis=1)

    sens = dp.sensitivity(otab, otab, cw)
    summary = {
        "formula": "score = 100 * sum(w_k * c_k) / sum(w_k), over the components available for the outage",
        "components": [{"key": k, "label": dp.LABEL[k], "input_column": dp.SOURCE[k], "base_weight": dp.BASE_WEIGHTS[k],
                        "effective_weight": dp.BASE_WEIGHTS[k] * (cw if k == "risk" else 1.0)} for k in dp.BASE_WEIGHTS],
        "causal_weight": {
            "value": cw, "effect": prof.TAU_EFFECT, "estimate_per_outage": float(row["estimate"]),
            "ci": [float(row["ci_lower"]), float(row["ci_upper"])], "mean_outage_days_matched": mean_days,
            "effect_per_outage_day": per_day, "mean_predicted_rate_matched_treated": mean_rate,
            "rule": "0 if the 95% interval includes zero; else (effect per outage-day) / (mean XGBoost predicted rate at matched treated "
                    "outages), clipped to [0, 1]. A population-average share of dark-period crime attributable to the outage. It scales "
                    "the weight of the risk component; it is not applied per ward because only an average effect is estimated.",
        },
        "tiers": "top 10% by score = High, next 20% = Medium, rest Low",
        "missing_data_rule": "components with missing inputs are dropped for that outage and the remaining weights are renormalised (never imputed as zero)",
        "weights_note": "Weights are documented judgement calls, not fitted or validated. Density, elevation, slope and rainfall act only through the XGBoost risk model.",
        "not_used": ["realised outage duration", "closure time", "crime counts during the outage", "ground-truth effect file"],
        "sensitivity": sens,
        "spearman_vs_stage12_hindsight_score": float(otab[["priority_score", "stage12_score"]].corr(method="spearman").iloc[0, 1]),
        "n_missing_inputs": {k: int(otab[dp.SOURCE[k]].isna().sum()) for k in dp.BASE_WEIGHTS},
    }
    (OUT / "decision_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    log(f"decision layer: causal_weight={cw:.3f}; sensitivity median spearman={sens['random_spearman_median']:.3f}")
    return otab


def causal_summary(est, wards, outages, otab):
    panel = pd.read_parquet(PROCESSED / "causal_panel.parquet")
    pairs = pd.read_parquet(PROCESSED / "control_area_pairs.parquet")
    wide = panel.pivot_table(index=["pair_id", "role"], columns="period", values="crime_100m", aggfunc="first")
    t = wide.xs("T", level="role")
    c = wide.xs("C", level="role")
    did = ((t["during"] - t["pre"]) - (c["during"] - c["pre"])).rename("did_direct_during")
    naive = (t["during"] - t["pre"]).rename("naive")
    pr = pairs.set_index("pair_id")
    pr = pr.join([did, naive])
    pr["ward_id"] = assign_ward(wards, pr["treatment_latitude"], pr["treatment_longitude"], pr["treatment_borough"])
    pr = pr.merge(wards[["ward_id", "pop_density_per_km2"]], left_on="ward_id", right_on="ward_id", how="left").set_index(pr.index)

    def row(name):
        r = est[est["effect_name"] == name].iloc[0]
        return {"effect": name, "estimate": float(r["estimate"]), "se": None if pd.isna(r["standard_error"]) else float(r["standard_error"]),
                "ci_lower": None if pd.isna(r["ci_lower"]) else float(r["ci_lower"]),
                "ci_upper": None if pd.isna(r["ci_upper"]) else float(r["ci_upper"]),
                "p_value": None if pd.isna(r["p_value"]) else float(r["p_value"]),
                "ring": r["outcome_ring"], "n_pairs": int(r["n_pairs"])}

    summary = {
        "estimates": [row(n) for n in ["direct_during", "displacement_during", "net_during", "direct_post", "displacement_post", "net_post"]],
        "estimator": "Matched-control difference-in-differences (Stages 7-11). Effect = change in night-crime count "
                     "(treated minus matched control) per outage; positive = more crime while the light was out.",
        "naive_before_after": {
            "description": "Treated sites only: crimes within 100 m while dark minus the 14 days before. No control group.",
            "mean_change": float(pr["naive"].mean()),
            "se": float(pr["naive"].std(ddof=1) / np.sqrt(pr["naive"].notna().sum())),
            "n_pairs": int(pr["naive"].notna().sum()),
        },
        "matched_did_direct_during": {
            "description": "Same quantity after subtracting the matched control's change (the project's estimate).",
            "mean": float(pr["did_direct_during"].mean()),
            "se_unclustered": float(pr["did_direct_during"].std(ddof=1) / np.sqrt(pr["did_direct_during"].notna().sum())),
        },
        "tau_used_for_priority": {"effect": prof.TAU_EFFECT, "value": row(prof.TAU_EFFECT)["estimate"]},
    }

    # Group estimates: effect by ward density tercile (indicative; unclustered SE)
    pr["density_group"] = pd.qcut(pr["pop_density_per_km2"], 3, labels=["Lower density", "Middle density", "Higher density"])
    grp = []
    for g_, sub in pr.groupby("density_group", observed=True):
        x = sub["did_direct_during"].dropna()
        grp.append({"group": str(g_), "n_pairs": int(len(x)), "estimate": float(x.mean()),
                    "se_unclustered": float(x.std(ddof=1) / np.sqrt(len(x)))})
    summary["effect_by_density"] = grp

    # Planted-effect check (evaluation only)
    if TRUTH_FILE.exists():
        truth = pd.read_csv(TRUTH_FILE)
        tk = pr.reset_index().merge(truth, left_on="treatment_key", right_on="unique_key", how="left")
        expected = float(tk["expected_extra_night_crimes_during"].mean() * WITHIN_100M_SHARE)
        summary["planted_effect_check"] = {
            "purpose": "Evaluation only. The generator knows how many extra night crimes it planted around each outage; "
                       "the pipeline never reads this file.",
            "expected_direct_effect_in_matched_sample": expected,
            "estimated_direct_during": row("direct_during")["estimate"],
            "estimated_ci": [row("direct_during")["ci_lower"], row("direct_during")["ci_upper"]],
            "ci_contains_truth": bool(row("direct_during")["ci_lower"] <= expected <= row("direct_during")["ci_upper"]),
            "n_matched_treated_with_truth": int(tk["expected_extra_night_crimes_during"].notna().sum()),
        }
        # ward-level truth by density group for the same grouping
        tk["density_group"] = pd.qcut(tk["pop_density_per_km2"], 3, labels=["Lower density", "Middle density", "Higher density"]) \
            if "pop_density_per_km2" in tk.columns else None
        if tk["density_group"] is not None:
            tg = tk.groupby("density_group", observed=True)["expected_extra_night_crimes_during"].mean() * WITHIN_100M_SHARE
            for item in summary["effect_by_density"]:
                item["planted_truth"] = float(tg.get(item["group"], np.nan))
    return summary


# ---------------------------------------------------------------------------------------------- dispatch simulation
def simulate_policy(df, score_key, K):
    """Daily dispatch at 08:00: repair the K best-scored open outages. Returns wait (days) per outage."""
    t0 = df["created_date"].min().normalize()
    created = df["created_date"].to_numpy("datetime64[us]")
    order = np.argsort(created, kind="mergesort")
    score = df[score_key].to_numpy(float)
    cr_days = (created - t0.to_datetime64()).astype("timedelta64[s]").astype(float) / 86400.0
    wait = np.full(len(df), np.nan)
    heap, nxt, day, n = [], 0, 0, len(df)
    done = 0
    while done < n and day < 1500:
        now = day + 8 / 24.0
        while nxt < n and cr_days[order[nxt]] <= now:
            i = order[nxt]
            heapq.heappush(heap, (-score[i], cr_days[i], i))
            nxt += 1
        for _ in range(K):
            if not heap:
                break
            _, c, i = heapq.heappop(heap)
            wait[i] = now - c
            done += 1
        day += 1
    return wait


POLICIES = [
    # name, score column (None = historical), uses_hindsight
    ("Observed (historical closures in the data)", None, False),
    ("FIFO (existing baseline)", "fifo_score", False),
    ("ML risk ranking", "predicted_risk", False),
    ("Causal priority, ex-ante", "priority_ex_ante", False),
    ("Causal priority (Stage 12 score)", "stage12_score", True),
    ("Decision priority (XGBoost + causal + population)", "priority_score", False),
]


def policy_table(otab, truth_rate):
    """Policies x scenarios through a daily dispatch simulation.

    `benchmark_extra_crimes` = sum(planted extra night crimes per day while dark x days waited). It uses the
    generator's ground truth, so it exists only for synthetic data and is used for evaluation, never for ranking.
    The Stage 12 score multiplies in the realised outage duration (hindsight), flagged by `uses_hindsight`.
    """
    base = otab[["unique_key", "created_date", "outage_duration_hours", "priority_score", "stage12_score", "priority_ex_ante", "predicted_risk",
                 "ward_id", "rainfall_mm_year", "pop_density_per_km2", "pole_age_years"]].copy()
    base["observed_wait"] = base["outage_duration_hours"] / 24.0
    base["fifo_score"] = -(base["created_date"] - base["created_date"].min()).dt.total_seconds()
    base["truth_rate"] = base["unique_key"].map(truth_rate) if truth_rate is not None else np.nan
    rng = np.random.default_rng(SEED)
    rows = []
    for key, (label, desc, rule, kmult) in SCENARIOS.items():
        df = base
        if rule is not None:
            col, mult = rule
            thr = base[col].median() if col == "rainfall_mm_year" else base[col].quantile(2 / 3)
            extra = base[base[col] >= thr].sample(frac=mult - 1.0, random_state=int(rng.integers(0, 2**31))).copy()
            extra["created_date"] = (extra["created_date"] + pd.to_timedelta(rng.integers(-5, 6, len(extra)), unit="D")
                                     ).clip(lower=base["created_date"].min())
            extra["unique_key"] = -extra["unique_key"]            # clone of an outage in the same ward: same scores and truth rate
            df = pd.concat([base, extra], ignore_index=True)
        K = int(round(BASE_CAPACITY_K * kmult))
        for pname, col, hindsight in POLICIES:
            if col is None and key != "baseline":
                continue          # historical closures do not respond to a scenario
            wait = df["observed_wait"].to_numpy() if col is None else simulate_policy(df, col, K)
            ok = np.isfinite(wait)
            w = wait[ok]
            tr = df["truth_rate"].to_numpy()[ok]
            rows.append({
                "scenario": key, "scenario_label": label, "scenario_description": desc,
                "policy": pname, "uses_hindsight": hindsight, "capacity_k_per_day": K, "outages": int(len(df)),
                "mean_wait_days": float(w.mean()), "p90_wait_days": float(np.quantile(w, 0.9)),
                "priority_weighted_wait_days": float((w * df["priority_score"].to_numpy()[ok]).sum() / df["priority_score"].to_numpy()[ok].sum()),
                "benchmark_extra_crimes": float(np.nansum(tr * w)) if np.isfinite(tr).any() else None,
            })
    out = pd.DataFrame(rows)
    ref = out[out["policy"] == "FIFO (existing baseline)"].set_index("scenario")["benchmark_extra_crimes"]
    out["benchmark_vs_fifo_pct"] = (out["benchmark_extra_crimes"] / out["scenario"].map(ref) - 1) * 100
    out["benchmark_vs_fifo_abs"] = out["benchmark_extra_crimes"] - out["scenario"].map(ref)
    return out


# ---------------------------------------------------------------------------------------------- main
def _bench(pol, scenario):
    r = pol[(pol["scenario"] == scenario) & (pol["policy"] == "Decision priority (XGBoost + causal + population)")]
    return float(r["benchmark_vs_fifo_pct"].iloc[0]) if len(r) else None


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    wards, outages, crime, plan, est, _legacy_comparison = load_inputs()
    outages, crime = attach_ward(outages, crime, wards)

    scored = outages[outages["scored"]].copy()
    scored["dark_night_crimes"] = dark_period_crime(scored, crime)
    scored["dark_night_crimes_per_day"] = scored["dark_night_crimes"] / (scored["outage_duration_hours"] / 24.0).clip(lower=0.25)
    scored = scored.merge(wards[["ward_id", *WARD_FEATURES]], on="ward_id", how="left")
    scored["prior_complaints_90d_50m"] = prior_complaints(scored)
    log(f"scored outages: {len(scored)}; wards: {scored['ward_id'].nunique()}")

    oof, risk_metrics, top_shap = fit_risk_model(scored)
    log(f"XGBoost risk model: spearman={risk_metrics['spearman_predicted_vs_observed']:.3f}, "
        f"D2={risk_metrics['poisson_deviance_explained']:.3f}, top-decile capture={risk_metrics['top_decile_capture']:.3f}")
    (OUT / "risk_model.json").write_text(json.dumps(risk_metrics, indent=1), encoding="utf-8")

    otab = build_outage_table(scored, crime, wards, plan, oof)
    otab["observed_dark_night_crimes_100m"] = otab["dark_night_crimes"]
    otab["xgb_top_features"] = top_shap
    otab = decision_layer(otab, outages, est, risk_metrics)
    ward_sum = build_ward_summary(otab, outages, crime, wards)
    pop = population_analysis(otab, ward_sum)
    rel = relationships(ward_sum)

    keep = ["unique_key", "ward_id", "city", "area_class", "created_date", "closed_date", "duration_days", "latitude", "longitude",
            "population", "pop_density_per_km2", "income_index", "vulnerable_pop_share", "rainfall_mm_year", "elevation_m", "slope_pct",
            "dist_main_road_km", "dist_depot_km", "pole_age_years", "local_crime_rate", "predicted_risk", "risk_percentile",
            "priority_score", "priority_tier", "stage12_score", "stage12_tier", "priority_ex_ante", "prior_complaints_90d_50m", "xgb_top_features",
            "priority_reasons", *[f"contrib_{k}" for k in dp.BASE_WEIGHTS], "dispatch_decision", "optimization_rank",
            "recommended_action", "observed_dark_night_crimes_100m"]
    otab[keep].to_csv(OUT / "outage_table.csv", index=False)
    ward_sum.to_csv(OUT / "ward_summary.csv", index=False)
    pop.to_csv(OUT / "population_analysis.csv", index=False)
    rel.to_csv(OUT / "relationships.csv", index=False)

    csum = causal_summary(est, wards, outages, otab)
    (OUT / "causal_summary.json").write_text(json.dumps(csum, indent=1), encoding="utf-8")

    truth_rate = None
    if TRUTH_FILE.exists():
        tr = pd.read_csv(TRUTH_FILE)
        dur = otab.set_index("unique_key")["duration_days"]
        tr = tr[tr["unique_key"].isin(dur.index)]
        # extra night crimes per day while dark = expected extra during / outage length (generator: Poisson(rate*u*duration))
        truth_rate = (tr.set_index("unique_key")["expected_extra_night_crimes_during"] / dur.reindex(tr["unique_key"]).clip(lower=0.25).to_numpy())
    pol = policy_table(otab, truth_rate)
    pol.to_csv(OUT / "policy_comparison.csv", index=False)

    # ---- KPIs
    wards_high = int((ward_sum["risk_category"] == "High").sum())
    comparison = pd.read_csv(DEC / "fifo_vs_lightsafe_comparison.csv")
    f30 = comparison[comparison["day"] == 30]
    kpis = {
        "population_covered": int(wards["population"].sum()),
        "wards": int(len(wards)),
        "cities": int(wards["city"].nunique()),
        "outages_cleaned": int(len(outages)),
        "outages_scored": int(len(otab)),
        "high_risk_wards": wards_high,
        "high_medium_priority_outages": int(otab["priority_tier"].isin(["High", "Medium"]).sum()),
        "causal_weight": json.loads((OUT / "decision_summary.json").read_text())["causal_weight"]["value"],
        "high_priority_outages": int((otab["priority_tier"] == "High").sum()),
        "repairs_recommended_per_day": int((otab["dispatch_decision"] == "repair").sum()),
        "daily_repair_capacity_k": BASE_CAPACITY_K,
        "tau_effect": prof.TAU_EFFECT,
        "tau_value": csum["tau_used_for_priority"]["value"],
        "priority_improvement_day30_pct": float(f30["improvement_pct"].iloc[0]) if len(f30) else None,
        "predicted_impact_priority_points_day30": float(f30["lightsafe_cumulative_impact"].iloc[0]) if len(f30) else None,
        "fifo_impact_priority_points_day30": float(f30["fifo_cumulative_impact"].iloc[0]) if len(f30) else None,
        "benchmark_vs_fifo_pct_baseline": _bench(pol, "baseline"),
        "benchmark_vs_fifo_pct_stress": _bench(pol, "high_density"),
        "data_period": [str(outages["created_date"].min().date()), str(outages["created_date"].max().date())],
    }
    (OUT / "overview.json").write_text(json.dumps(kpis, indent=1), encoding="utf-8")
    (OUT / "traceability.json").write_text(json.dumps(TRACEABILITY, indent=1), encoding="utf-8")
    log(json.dumps(kpis, indent=1))
    log(f"context outputs written to {OUT}")
    return 0


TRACEABILITY = [
    {"ui_metric": "Population covered", "endpoint": "/api/synthetic/overview", "file": "outputs/synthetic/context/overview.json (population_covered)",
     "source": "synthetic_wards.csv: sum(population) = pop_density_per_km2 x area_km2 per ward"},
    {"ui_metric": "Regions (wards) / cities", "endpoint": "/api/synthetic/overview", "file": "overview.json (wards, cities)", "source": "synthetic_wards.csv: ward_id, city"},
    {"ui_metric": "Outages cleaned / scored", "endpoint": "/api/synthetic/overview", "file": "data/synthetic/processed/outages_scored.parquet",
     "source": "synthetic_streetlights.csv: descriptor='Street Light Out', closed_date, latitude, longitude (Stage 3 cleaning, Stage 12 scoring)"},
    {"ui_metric": "High-risk wards", "endpoint": "/api/synthetic/overview, /api/synthetic/wards", "file": "ward_summary.csv (risk_category)",
     "source": "ML risk model (risk_model.json) on outage_table.csv: predicted_risk averaged per ward; top third = High"},
    {"ui_metric": "High/Medium-priority outages", "endpoint": "/api/synthetic/overview, /api/synthetic/prioritization", "file": "outages_scored.parquet -> outage_table.csv (priority_tier)",
     "source": "Stage 12: tau_net x local_crime_rate (clean_crime.parquet, 14-day pre-window) x duration; min-max 0-100"},
    {"ui_metric": "Predicted impact (priority points, day 30)", "endpoint": "/api/synthetic/overview", "file": "outputs/synthetic/decision/fifo_vs_lightsafe_comparison.csv",
     "source": "Stage 13 prioritization_engine run on the decision score: cumulative priority points of repaired outages"},
    {"ui_metric": "Repairs recommended per day", "endpoint": "/api/synthetic/overview, /api/synthetic/prioritization", "file": "outputs/synthetic/decision/optimal_dispatch_plan.csv",
     "source": "Stage 14 ILP (PuLP/CBC) on the decision score: selected_for_repair"},
    {"ui_metric": "Causal estimates (direct / ring / net)", "endpoint": "/api/synthetic/causal", "file": "outputs/synthetic/displacement_estimates.csv -> causal_summary.json",
     "source": "causal_panel.parquet: crime_100m, crime_250m; control_area_pairs.parquet (Stage 7 matching)"},
    {"ui_metric": "Naive before/after vs matched DiD", "endpoint": "/api/synthetic/causal", "file": "causal_summary.json", "source": "causal_panel.parquet (periods pre/during, roles T/C)"},
    {"ui_metric": "Planted-effect check", "endpoint": "/api/synthetic/causal", "file": "causal_summary.json (planted_effect_check)",
     "source": "LightSafe_Synthetic_Karnataka_Data/v2/ground_truth/outage_effect_truth.csv (evaluation only; never an input)"},
    {"ui_metric": "XGBoost risk, MAE / RMSE, feature importance, SHAP", "endpoint": "/api/synthetic/risk-model", "file": "risk_model.json, outage_table.csv (predicted_risk, xgb_top_features)",
     "source": "ward conditions (synthetic_wards.csv) + pre-outage local_crime_rate -> observed night crimes within 100 m per day while dark (clean_crime.parquet); ward-held-out folds"},
    {"ui_metric": "Decision priority score, components, 'Why this location?'", "endpoint": "/api/synthetic/prioritization, /api/synthetic/decision", "file": "outage_table.csv (priority_score, contrib_*), decision_summary.json",
     "source": "predicted_risk x causal weight, ward population, vulnerable_pop_share, prior complaints (streetlight_complaints.csv), dist_depot_km; src/features/decision_priority.py"},
    {"ui_metric": "Causal weight (share of dark-period crime attributable to outages)", "endpoint": "/api/synthetic/decision, /api/synthetic/overview", "file": "decision_summary.json",
     "source": "displacement_estimates.csv direct_during, control_area_pairs.parquet outage_duration_hours, XGBoost predicted_risk at matched treated outages"},
    {"ui_metric": "Population vs risk vs priority chart", "endpoint": "/api/synthetic/population-risk", "file": "ward_summary.csv", "source": "ward population, mean predicted_risk, mean priority_score (percentiles computed by the API)"},
    {"ui_metric": "Repair pressure by city", "endpoint": "/api/ml/regime", "file": "outputs/synthetic/ml/regime_snapshot.json (synthetic profile)",
     "source": "streetlight_complaints.csv: created_date, closed_date, descriptor (share unresolved after 168 h; scripts/synthetic/build_repair_pressure.py)"},
    {"ui_metric": "Ward map and table", "endpoint": "/api/synthetic/wards", "file": "ward_summary.csv", "source": "synthetic_wards.csv joined to outage_table.csv and clean_crime.parquet by geography"},
    {"ui_metric": "Population analysis", "endpoint": "/api/synthetic/population", "file": "population_analysis.csv", "source": "ward_summary.csv grouped by density / vulnerability / income / area class / city"},
    {"ui_metric": "Relationship charts", "endpoint": "/api/synthetic/wards, /api/synthetic/relationships", "file": "ward_summary.csv, relationships.csv", "source": "ward columns (see names on each chart axis)"},
    {"ui_metric": "Ranked priority list", "endpoint": "/api/synthetic/prioritization", "file": "outage_table.csv", "source": "outages_scored.parquet + decision/optimal_dispatch_plan.csv + synthetic_wards.csv"},
    {"ui_metric": "Policy / scenario comparison", "endpoint": "/api/synthetic/scenarios", "file": "policy_comparison.csv",
     "source": "outage_table.csv replayed through a daily dispatch simulation (build_context_outputs.simulate_policy)"},
]

if __name__ == "__main__":
    sys.exit(main())
