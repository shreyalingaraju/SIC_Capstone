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
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import GroupKFold

os.environ.setdefault("LIGHTSAFE_PROFILE", "karnataka_synthetic")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src import profile as prof  # noqa: E402
from src.features.ward_context import assign_ward, load_wards  # noqa: E402

if not prof.IS_SYNTHETIC:
    raise SystemExit("build_context_outputs.py is for the synthetic profile (LIGHTSAFE_PROFILE=karnataka_synthetic)")

PROCESSED = ROOT / prof.PROCESSED_DIR
OUTPUTS = ROOT / prof.OUTPUTS_DIR
OUT = OUTPUTS / "context"
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
def fit_risk_model(df):
    """Cross-fitted (ward-held-out) Poisson gradient boosting for the night-crime rate near an outage while dark.

    Features are all known when the outage is reported: ward conditions, the pre-outage local crime rate
    (Stage 12, look-back ends before created_date), city, and the report month/hour/weekday. Duration and the
    crime counts during the outage are outcomes and are NOT features.
    """
    d = df.copy()
    d["city_code"] = d["borough"].astype("category").cat.codes
    d["month"] = d["created_date"].dt.month
    d["hour"] = d["created_date"].dt.hour
    d["dow"] = d["created_date"].dt.dayofweek
    feats = WARD_FEATURES + ["local_crime_rate", "city_code", "month", "hour", "dow"]
    X = d[feats]
    y = d["dark_night_crimes_per_day"].to_numpy()
    groups = d["ward_id"].to_numpy()
    oof = np.zeros(len(d))
    imp = np.zeros(len(feats))
    folds = list(GroupKFold(n_splits=5).split(X, y, groups))
    rng = np.random.default_rng(SEED)
    for tr, te in folds:
        m = HistGradientBoostingRegressor(loss="poisson", max_depth=3, learning_rate=0.05, max_iter=200,
                                          min_samples_leaf=40, l2_regularization=1.0, random_state=SEED)
        m.fit(X.iloc[tr], y[tr])
        oof[te] = m.predict(X.iloc[te])
        # permutation importance on the held-out wards: increase in Poisson deviance
        base = _poisson_dev(y[te], oof[te])
        for j, f in enumerate(feats):
            Xp = X.iloc[te].copy()
            Xp[f] = rng.permutation(Xp[f].to_numpy())
            imp[j] += _poisson_dev(y[te], m.predict(Xp)) - base
    imp /= len(folds)
    dev_model = _poisson_dev(y, oof)
    dev_null = _poisson_dev(y, np.full(len(y), y.mean()))
    top = oof >= np.quantile(oof, 0.9)
    metrics = {
        "model": "HistGradientBoostingRegressor (Poisson loss, depth 3, 200 iterations)",
        "target": "observed night crimes within 100 m per day while the outage was open",
        "validation": "5-fold GroupKFold by ward: every prediction is made by a model that never saw that ward",
        "n_outages": int(len(d)),
        "features": feats,
        "spearman_predicted_vs_observed": float(spearmanr(oof, y).statistic),
        "poisson_deviance_explained": float(1 - dev_model / dev_null),
        "top_decile_capture": float(y[top].sum() / y.sum()),
        "top_decile_lift": float((y[top].mean()) / y.mean()),
        "no_skill_top_decile_capture": 0.1,
        "interpretation": "Association only. The model learns which outages sit where crime is high; it cannot "
                          "tell how much of that crime a repair would remove (see causal_summary.json).",
    }
    importance = pd.DataFrame({"feature": feats, "importance_deviance_increase": imp}).sort_values(
        "importance_deviance_increase", ascending=False)
    metrics["feature_importance"] = [{"feature": r.feature, "importance": float(r.importance_deviance_increase)}
                                     for r in importance.itertuples()]
    return oof, metrics


def _poisson_dev(y, mu):
    mu = np.clip(mu, 1e-9, None)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(y > 0, y * np.log(y / mu), 0.0)
    return float(2 * np.sum(t - (y - mu)) / len(y))


# ---------------------------------------------------------------------------------------------- tables
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
    ("Causal priority (Stage 12 score)", "priority_score", True),
]


def policy_table(otab, truth_rate):
    """Policies x scenarios through a daily dispatch simulation.

    `benchmark_extra_crimes` = sum(planted extra night crimes per day while dark x days waited). It uses the
    generator's ground truth, so it exists only for synthetic data and is used for evaluation, never for ranking.
    The Stage 12 score multiplies in the realised outage duration (hindsight), flagged by `uses_hindsight`.
    """
    base = otab[["unique_key", "created_date", "outage_duration_hours", "priority_score", "priority_ex_ante", "predicted_risk",
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
    r = pol[(pol["scenario"] == scenario) & (pol["policy"] == "Causal priority, ex-ante")]
    return float(r["benchmark_vs_fifo_pct"].iloc[0]) if len(r) else None


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    wards, outages, crime, plan, est, comparison = load_inputs()
    outages, crime = attach_ward(outages, crime, wards)

    scored = outages[outages["scored"]].copy()
    scored["dark_night_crimes"] = dark_period_crime(scored, crime)
    scored["dark_night_crimes_per_day"] = scored["dark_night_crimes"] / (scored["outage_duration_hours"] / 24.0).clip(lower=0.25)
    scored = scored.merge(wards[["ward_id", *WARD_FEATURES]], on="ward_id", how="left")
    log(f"scored outages: {len(scored)}; wards: {scored['ward_id'].nunique()}")

    oof, risk_metrics = fit_risk_model(scored)
    log(f"ML risk model: spearman={risk_metrics['spearman_predicted_vs_observed']:.3f}, "
        f"D2={risk_metrics['poisson_deviance_explained']:.3f}, top-decile capture={risk_metrics['top_decile_capture']:.3f}")
    (OUT / "risk_model.json").write_text(json.dumps(risk_metrics, indent=1), encoding="utf-8")

    otab = build_outage_table(scored, crime, wards, plan, oof)
    otab["observed_dark_night_crimes_100m"] = otab["dark_night_crimes"]
    ward_sum = build_ward_summary(otab, outages, crime, wards)
    pop = population_analysis(otab, ward_sum)
    rel = relationships(ward_sum)

    keep = ["unique_key", "ward_id", "city", "area_class", "created_date", "closed_date", "duration_days", "latitude", "longitude",
            "population", "pop_density_per_km2", "income_index", "vulnerable_pop_share", "rainfall_mm_year", "elevation_m", "slope_pct",
            "dist_main_road_km", "dist_depot_km", "pole_age_years", "local_crime_rate", "predicted_risk", "risk_percentile",
            "priority_score", "priority_ex_ante", "priority_tier", "dispatch_decision", "optimization_rank", "recommended_action",
            "observed_dark_night_crimes_100m"]
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
    f30 = comparison[comparison["day"] == 30]
    kpis = {
        "population_covered": int(wards["population"].sum()),
        "wards": int(len(wards)),
        "cities": int(wards["city"].nunique()),
        "outages_cleaned": int(len(outages)),
        "outages_scored": int(len(otab)),
        "high_risk_wards": wards_high,
        "high_medium_priority_outages": int(otab["priority_tier"].isin(["High", "Medium"]).sum()),
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
    {"ui_metric": "Predicted impact (priority points, day 30)", "endpoint": "/api/synthetic/overview", "file": "outputs/synthetic/fifo_vs_lightsafe_comparison.csv",
     "source": "Stage 13 prioritization_engine: cumulative priority_score of repaired outages"},
    {"ui_metric": "Repairs recommended per day", "endpoint": "/api/synthetic/overview, /api/synthetic/prioritization", "file": "outputs/synthetic/optimal_dispatch_plan.csv",
     "source": "Stage 14 ILP (PuLP/CBC): selected_for_repair"},
    {"ui_metric": "Causal estimates (direct / ring / net)", "endpoint": "/api/synthetic/causal", "file": "outputs/synthetic/displacement_estimates.csv -> causal_summary.json",
     "source": "causal_panel.parquet: crime_100m, crime_250m; control_area_pairs.parquet (Stage 7 matching)"},
    {"ui_metric": "Naive before/after vs matched DiD", "endpoint": "/api/synthetic/causal", "file": "causal_summary.json", "source": "causal_panel.parquet (periods pre/during, roles T/C)"},
    {"ui_metric": "Planted-effect check", "endpoint": "/api/synthetic/causal", "file": "causal_summary.json (planted_effect_check)",
     "source": "LightSafe_Synthetic_Karnataka_Data/v2/ground_truth/outage_effect_truth.csv (evaluation only; never an input)"},
    {"ui_metric": "ML risk score and model quality", "endpoint": "/api/synthetic/risk-model", "file": "risk_model.json, outage_table.csv",
     "source": "ward conditions + pre-outage local_crime_rate -> observed night crimes within 100 m per day while dark (clean_crime.parquet)"},
    {"ui_metric": "Ward map and table", "endpoint": "/api/synthetic/wards", "file": "ward_summary.csv", "source": "synthetic_wards.csv joined to outage_table.csv and clean_crime.parquet by geography"},
    {"ui_metric": "Population analysis", "endpoint": "/api/synthetic/population", "file": "population_analysis.csv", "source": "ward_summary.csv grouped by density / vulnerability / income / area class / city"},
    {"ui_metric": "Relationship charts", "endpoint": "/api/synthetic/wards, /api/synthetic/relationships", "file": "ward_summary.csv, relationships.csv", "source": "ward columns (see names on each chart axis)"},
    {"ui_metric": "Ranked priority list", "endpoint": "/api/synthetic/prioritization", "file": "outage_table.csv", "source": "outages_scored.parquet + optimal_dispatch_plan.csv + synthetic_wards.csv"},
    {"ui_metric": "Policy / scenario comparison", "endpoint": "/api/synthetic/scenarios", "file": "policy_comparison.csv",
     "source": "outage_table.csv replayed through a daily dispatch simulation (build_context_outputs.simulate_policy)"},
]

if __name__ == "__main__":
    sys.exit(main())
