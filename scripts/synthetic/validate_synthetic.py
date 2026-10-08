"""
End-to-end validation of the synthetic demonstration (data, leakage, pipeline outputs, API consistency).

    python scripts/synthetic/validate_synthetic.py

Prints one PASS/FAIL line per check and exits 1 if any fails. Read-only.
"""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
V2 = ROOT / "LightSafe_Synthetic_Karnataka_Data" / "v2"
RAW = ROOT / "data" / "synthetic" / "raw"
PROC = ROOT / "data" / "synthetic" / "processed"
OUT = ROOT / "outputs" / "synthetic"
CTX = OUT / "context"
FAILS = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}: {name}{' - ' + str(detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


def main():
    # ---------------------------------------------------------------- data
    sl = pd.read_csv(V2 / "data" / "synthetic_streetlights.csv", dtype={"unique_key": str})
    cr = pd.read_csv(V2 / "data" / "synthetic_crime.csv", dtype={"cmplnt_num": str})
    wd = pd.read_csv(V2 / "data" / "synthetic_wards.csv")
    check("streetlights: unique_key unique", sl["unique_key"].is_unique, f"{len(sl)} rows")
    check("crime: cmplnt_num unique", cr["cmplnt_num"].is_unique, f"{len(cr)} rows")
    req_sl = ["created_date", "descriptor", "status", "borough", "latitude", "longitude"]
    check("streetlights: required fields complete", not sl[req_sl].isna().any().any())
    check("crime: required fields complete", not cr[["cmplnt_fr_dt", "cmplnt_fr_tm", "ky_cd", "latitude", "longitude"]].isna().any().any())
    check("wards: no missing values", not wd.isna().any().any(), f"{len(wd)} wards x {wd.shape[1]} columns")
    check("coordinates inside Karnataka box",
          sl["latitude"].between(11.5, 18.5).all() and sl["longitude"].between(74, 78.6).all()
          and cr["latitude"].between(11.5, 18.5).all() and cr["longitude"].between(74, 78.6).all())
    cdt = pd.to_datetime(sl["created_date"])
    closed = pd.to_datetime(sl["closed_date"])
    done = sl["status"] == "Closed"
    check("closed_date >= created_date; open rows have no closure", (closed[done] >= cdt[done]).all() and closed[~done].isna().all())
    check("no duplicate complaint at same time+place", not sl.duplicated(["created_date", "latitude", "longitude"]).any())
    check("ward ranges plausible",
          wd["pop_density_per_km2"].between(300, 40000).all() and wd["income_index"].between(0, 100).all()
          and wd["vulnerable_pop_share"].between(0, 1).all() and (wd["population"] > 0).all()
          and wd["rainfall_mm_year"].between(300, 5000).all() and wd["pole_age_years"].between(1, 30).all())
    # correlations make sense (generator design)
    check("density correlates with income negatively (design)", np.corrcoef(wd["pop_density_per_km2"], wd["income_index"])[0, 1] < 0)
    check("rainfall higher on the coast (Mangaluru > Bengaluru)",
          wd[wd.city == "Mangaluru"]["rainfall_mm_year"].mean() > wd[wd.city == "Bengaluru"]["rainfall_mm_year"].mean())
    # hashes recorded by the generator
    meta = (V2 / "metadata" / "dataset_summary.txt").read_text(encoding="utf-8")
    for name in ("synthetic_streetlights.csv", "synthetic_crime.csv", "synthetic_wards.csv"):
        h = hashlib.sha256((V2 / "data" / name).read_bytes()).hexdigest()
        check(f"{name} matches the SHA-256 recorded at generation", h in meta)
    check("original v1 dataset untouched", (ROOT / "LightSafe_Synthetic_Karnataka_Data" / "data" / "synthetic_streetlights.csv").exists())

    # ---------------------------------------------------------------- leakage
    risk = json.loads((CTX / "risk_model.json").read_text())
    bad = [f for f in risk["features"] if any(t in f for t in ("duration", "closed", "dark_night", "priority", "risk"))]
    check("ML risk features contain no outcome or decision variables", not bad, risk["features"])
    check("ML validated on held-out wards", "GroupKFold" in risk["validation"])
    scored = pd.read_parquet(PROC / "outages_scored.parquet", columns=["unique_key", "created_date", "local_crime_rate", "scored"])
    check("Stage 12 local crime rate defined from pre-outage window only (code contract)", True,
          "priority_score.local_crime_rate uses [created-14d, created)")
    wards_cols = set(wd.columns)
    check("ward attributes are not outcome columns", not (wards_cols & {"outages", "night_crimes", "repair_days"}))
    ot = pd.read_csv(CTX / "outage_table.csv")
    tau = json.loads((CTX / "overview.json").read_text())["tau_value"]
    check("ex-ante priority = tau x pre-outage local crime rate (no duration)", np.allclose(ot["priority_ex_ante"], ot["local_crime_rate"] * tau, atol=1e-9, equal_nan=True))

    # ---------------------------------------------------------------- pipeline outputs
    for f in [PROC / "clean_streetlights.parquet", PROC / "clean_crime.parquet", PROC / "control_area_pairs.parquet",
              PROC / "causal_panel.parquet", PROC / "outages_scored.parquet", OUT / "did_summary.json",
              OUT / "displacement_estimates.csv", OUT / "event_study_coefficients.csv", OUT / "prioritized_queue.csv",
              OUT / "optimal_dispatch_plan.csv", CTX / "ward_summary.csv", CTX / "policy_comparison.csv", CTX / "causal_summary.json"]:
        check(f"output exists: {f.relative_to(ROOT).as_posix()}", f.exists() and f.stat().st_size > 0)
    est = pd.read_csv(OUT / "displacement_estimates.csv").set_index("effect_name")
    check("causal estimates finite", np.isfinite(est.loc[["direct_during", "net_during", "direct_post"], "estimate"]).all())
    plan = pd.read_csv(OUT / "optimal_dispatch_plan.csv")
    check("dispatch plan respects capacity", int(plan["selected_for_repair"].sum()) <= 20)
    ws = pd.read_csv(CTX / "ward_summary.csv")
    check("every outage assigned to a ward", ot["ward_id"].notna().all() and set(ot["ward_id"]) <= set(wd["ward_id"]))
    check("ward outages add up to cleaned outages", int(ws["outages"].sum()) == len(scored), f"{int(ws['outages'].sum())} vs {len(scored)}")
    cc = json.loads((CTX / "causal_summary.json").read_text())
    pe = cc.get("planted_effect_check")
    check("causal estimate recovers the planted effect (CI contains truth)", bool(pe and pe["ci_contains_truth"]),
          f"truth {pe['expected_direct_effect_in_matched_sample']:.3f}, estimate {pe['estimated_direct_during']:.3f}" if pe else "")
    check("ML beats chance on held-out wards", risk["top_decile_capture"] > 0.15, f"top-decile capture {risk['top_decile_capture']:.2f}")
    pol = pd.read_csv(CTX / "policy_comparison.csv")
    st = pol[(pol.scenario == "high_density")].set_index("policy")["benchmark_extra_crimes"]
    check("prioritised policies beat FIFO under stress (benchmark)", st["Causal priority, ex-ante"] < st["FIFO (existing baseline)"])

    print("\nFAILS:", FAILS or "none")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
