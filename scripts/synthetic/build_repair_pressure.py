"""
Repair-pressure snapshot for the synthetic Karnataka profile -> outputs/synthetic/ml/regime_snapshot.json

The NYC snapshot (scripts/ml/build_regime_snapshot.py) scores 311 complaints with the frozen NYC XGBoost model. That model is
trained on NYC boroughs and is not applied here. For the synthetic profile repair pressure is measured directly from the
complaint timestamps in the synthetic data, with the same definition of "slow": a complaint still unresolved 7 days (168 h)
after it was reported.

Per city: slow-repair share of complaints reported in the trailing 28-day window, ranked (percentile) against the same
statistic in the city's own earlier 28-day windows (stepped weekly). The window ends 7 days before the end of the data so
every complaint in it has had 7 days to be resolved (no censoring). Values are synthetic repair histories, not real ones.

    LIGHTSAFE_PROFILE=karnataka_synthetic python scripts/synthetic/build_repair_pressure.py
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

os.environ.setdefault("LIGHTSAFE_PROFILE", "karnataka_synthetic")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src import profile as prof  # noqa: E402

if not prof.IS_SYNTHETIC:
    raise SystemExit("synthetic profile only (LIGHTSAFE_PROFILE=karnataka_synthetic)")

RAW = ROOT / prof.RAW_DIR / "streetlight_complaints.csv"
OUT = ROOT / prof.OUTPUTS_DIR / "ml" / "regime_snapshot.json"
WINDOW_DAYS, STEP_DAYS, SLOW_HOURS, MIN_ROWS = 28, 7, 168.0, 30
HIGH_PCT, MODERATE_PCT = 67.0, 33.0


def main() -> int:
    df = pd.read_csv(RAW, usecols=["created_date", "closed_date", "descriptor", "borough"], parse_dates=["created_date", "closed_date"])
    df = df[df["descriptor"] == "Street Light Out"].copy()
    data_end = max(df["created_date"].max(), df["closed_date"].max())
    as_of = (data_end - pd.Timedelta(hours=SLOW_HOURS)).normalize()
    hours = (df["closed_date"] - df["created_date"]).dt.total_seconds() / 3600
    # closed: slow if it took longer than 168 h. Still open: slow if already older than 168 h at the end of the data.
    df["slow"] = np.where(df["closed_date"].notna(), hours > SLOW_HOURS, (data_end - df["created_date"]).dt.total_seconds() / 3600 > SLOW_HOURS)
    df["days"] = hours / 24

    first_end = df["created_date"].min().normalize() + pd.Timedelta(days=WINDOW_DAYS)
    ref_ends = pd.date_range(first_end, as_of - pd.Timedelta(days=WINDOW_DAYS), freq=f"{STEP_DAYS}D")

    def window(sub, end):
        w = sub[(sub["created_date"] > end - pd.Timedelta(days=WINDOW_DAYS)) & (sub["created_date"] <= end)]
        return (float(w["slow"].mean()), int(len(w)), float(w["days"].median())) if len(w) >= MIN_ROWS else (None, int(len(w)), None)

    cities = []
    for city, sub in df.groupby("borough"):
        ref = np.array([v for v, _, _ in (window(sub, e) for e in ref_ends) if v is not None])
        cur, n, med = window(sub, as_of)
        if cur is None or len(ref) < 10:
            cities.append({"borough": city, "available": False, "observation_count": n})
            continue
        pct = float((ref <= cur).mean() * 100)
        cities.append({
            "borough": city, "available": True,
            "category": "High" if pct >= HIGH_PCT else "Moderate" if pct >= MODERATE_PCT else "Low",
            "relative_score": round(pct, 1), "above_reference_range": bool(cur > ref.max()),
            "observation_count": n, "reference_windows": int(len(ref)),
            "slow_share": round(cur, 4), "median_days_to_close": None if med is None or np.isnan(med) else round(med, 2),
            "reference_median_slow_share": round(float(np.median(ref)), 4),
        })
    snap = {
        "generated_by": "scripts/synthetic/build_repair_pressure.py", "kind": "observed_synthetic", "model_sha256": None,
        "window": {"days": WINDOW_DAYS, "end": as_of.strftime("%Y-%m-%d"), "start": (as_of - pd.Timedelta(days=WINDOW_DAYS - 1)).strftime("%Y-%m-%d")},
        "reference": {"start": first_end.strftime("%Y-%m-%d"), "end": ref_ends[-1].strftime("%Y-%m-%d"), "step_days": STEP_DAYS,
                      "description": "The city's own earlier 28-day windows in the synthetic year."},
        "definition": f"Share of 'Street Light Out' complaints still unresolved {int(SLOW_HOURS)} hours after being reported, "
                      f"for complaints reported in the trailing {WINDOW_DAYS} days.",
        "boroughs": cities,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snap, indent=1), encoding="utf-8")
    print(json.dumps(snap, indent=1)[:1500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
