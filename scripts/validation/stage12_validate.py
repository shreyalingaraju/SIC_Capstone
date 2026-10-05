"""
Stage 12 (priority score generation) validation.

Usage, from the repository root with the project venv:
    python scripts/validation/stage12_validate.py

Reads data/processed/outages_scored.parquet (create it with
`python src/features/priority_score.py`), re-runs the pipeline into a
temporary directory to test determinism, and recomputes local_crime_rate
by brute force for a 300-outage sample. Writes nothing to data/processed/ or
outputs/. Expected last line: "FAILS: none".
"""

import hashlib
import math
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer

sys.path.insert(0, ".")
from src.features import priority_score as ps  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}: {name}{' - ' + detail if detail else ''}", flush=True)
    if not ok:
        FAILS.append(name)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def close(a, b, tol=1e-12):
    return math.isclose(a, b, rel_tol=tol, abs_tol=tol)


PANEL = Path("data/processed/causal_panel.parquet")
panel_before, stage11_before = sha(PANEL), sha(ps.STAGE11_FILE)

# ---- synthetic formula cases ---------------------------------------------
raw = ps.raw_priority(0.02, np.array([2.0, 0.0, 5.0]), np.array([3.0, 4.0, 0.0]))
check("case 1: positive tau, rate, duration -> tau*rate*duration", close(raw[0], 0.12))
check("case 3: zero crime rate -> 0", raw[1] == 0.0)
check("case 4: zero duration factor -> 0", raw[2] == 0.0)
raw = ps.raw_priority(-0.02, np.array([2.0, 7.0]), np.array([3.0, 1.0]))
check("case 2: negative tau_net is clipped to 0, never negative", (raw == 0.0).all() and not (raw < 0).any())
check("case 2b: all-zero raw -> score 0 (degenerate), no NaN", (ps.normalise(raw) == 0.0).all())
check("case 5: all raw equal and positive -> score 0, no division by zero",
      (ps.normalise(np.array([0.5, 0.5, 0.5])) == 0.0).all())
s = ps.normalise(np.array([1.0, 2.0, 3.0, 5.0]))
check("min-max: min -> 0, max -> 100, linear in between", close(s[0], 0) and close(s[3], 100) and close(s[1], 25) and close(s[2], 50))
check("normalise keeps NaN (unscored) as NaN", np.isnan(ps.normalise(np.array([np.nan, 1.0, 2.0]))[0]))
check("raw_priority NaN input stays NaN, not 0", np.isnan(ps.raw_priority(0.02, np.array([np.nan]), np.array([1.0]))[0]))
t = ps.assign_tier(np.array([0, 39.999, 40, 79.999, 80, 100, np.nan]))
check("tier boundaries (>=80 High, >=40 Medium, else Low)",
      list(t[:6]) == ["Low", "Low", "Medium", "Medium", "High", "High"] and t[6] is None)
d = ps.duration_factor([24.0, 0.0, -5.0, np.nan, 9000.0, 0.5])
check("duration factor = hours/24; zero/negative/NaN/too-long are NaN, not high priority",
      close(d[0], 1.0) and np.isnan(d[1:5]).all() and close(d[5], 0.5 / 24))
try:
    ps._require(pd.DataFrame({"a": [1]}), ("a", "b"), "x")
    check("case 6: missing required input raises", False)
except ValueError:
    check("case 6: missing required input raises", True)
bad = Path(tempfile.mkdtemp(prefix="s12_")) / "bad.csv"
pd.DataFrame({"effect_name": ["direct_post"], "estimate": [1.0], "standard_error": [1.0]}).to_csv(bad, index=False)
try:
    ps.load_tau_net(bad)
    check("missing tau_net row raises", False)
except ValueError:
    check("missing tau_net row raises", True)

# ---- inputs and output ----------------------------------------------------
for f in (ps.OUTAGES_FILE, ps.CRIME_FILE, ps.STAGE11_FILE, PANEL):
    check(f"input exists: {f.name}", f.exists())
check("output exists", ps.SCORED_FILE.exists())
out = pd.read_parquet(ps.SCORED_FILE)
check("output readable", len(out) > 0)
check("required columns", all(c in out.columns for c in (*ps.ADDED_COLUMNS, ps.OUTAGE_ID, "outage_duration_hours")))
src = ps.load_outages()
check("original outage fields preserved", all(c in out.columns for c in src.columns))
check("one row per outage; ids valid and unique", out[ps.OUTAGE_ID].notna().all() and out[ps.OUTAGE_ID].is_unique
      and set(out[ps.OUTAGE_ID]) == set(src[ps.OUTAGE_ID]))
tau, _ = ps.load_tau_net()
check("tau_net equals the Stage 11 net_post estimate, finite", np.isfinite(out.tau_net).all() and (out.tau_net == tau).all())

sc = out[out.scored]
ex = out[~out.scored]
check("scored rows: rate finite and >= 0", np.isfinite(sc.local_crime_rate).all() and (sc.local_crime_rate >= 0).all())
check("scored rows: duration factor finite and > 0", np.isfinite(sc.duration_factor).all() and (sc.duration_factor > 0).all())
check("scored rows: raw finite and >= 0 (clipped)", np.isfinite(sc.raw_priority).all() and (sc.raw_priority >= 0).all())
check("raw = max(0, tau*rate*factor) recomputed",
      np.allclose(sc.raw_priority, np.maximum(0, sc.tau_net * sc.local_crime_rate * sc.duration_factor), rtol=1e-12, atol=0))
check("scores finite and in [0,100]", np.isfinite(sc.priority_score).all() and sc.priority_score.between(0, 100).all())
check("normalisation: recomputed scores match", np.allclose(sc.priority_score, ps.normalise(sc.raw_priority.to_numpy())))
check("tiers only High/Medium/Low", set(sc.priority_tier) <= set(ps.TIERS) and sc.priority_tier.notna().all())
check("tier boundaries match scores",
      (sc[sc.priority_tier == "High"].priority_score >= 80).all()
      and sc[sc.priority_tier == "Medium"].priority_score.between(40, 80, inclusive="left").all()
      and (sc[sc.priority_tier == "Low"].priority_score < 40).all())
check("excluded rows: reason given, no score or tier",
      ex.exclusion_reason.notna().all() and ex.priority_score.isna().all() and ex.priority_tier.isna().all())
check("scored + excluded = all records", len(sc) + len(ex) == len(src))

# ---- brute-force local crime rate on a sample --------------------------------
crime = ps.load_crime()
tr = Transformer.from_crs("EPSG:4326", ps.TARGET_CRS, always_xy=True)
cx, cy = tr.transform(crime.longitude.to_numpy(), crime.latitude.to_numpy())
ct = crime.crime_datetime.to_numpy("datetime64[us]")
sample = sc.sample(300, random_state=12)
sx, sy = tr.transform(sample.longitude.to_numpy(), sample.latitude.to_numpy())
bad_n = 0
leak = 0
for (_, r), x, y in zip(sample.iterrows(), sx, sy):
    c0 = np.datetime64(r.created_date, "us")
    m = (ct >= c0 - np.timedelta64(ps.LOOKBACK_DAYS, "D")) & (ct < c0) & (np.hypot(cx - x, cy - y) <= ps.RADIUS_M)
    bad_n += int(not close(m.sum() / ps.LOOKBACK_DAYS, r.local_crime_rate))
    leak += int((ct[m] >= c0).any())
check("local_crime_rate equals brute force on 300 outages", bad_n == 0, f"{bad_n} mismatches")
check("no crime at or after created_date is used (no leakage)", leak == 0)

# ---- determinism ---------------------------------------------------------------
tmp = Path(tempfile.mkdtemp(prefix="s12_")) / "again.parquet"
check("pipeline re-run succeeds", ps.main(["--out", str(tmp)]) == 0)
check("re-run output is identical", pd.read_parquet(tmp).equals(out) and sha(tmp) == sha(ps.SCORED_FILE))

# ---- integrity -------------------------------------------------------------------
check("Stage 11 output unchanged", sha(ps.STAGE11_FILE) == stage11_before)
check("causal_panel.parquet unchanged",
      sha(PANEL) == panel_before == "eaa0cd74b75d5f99007ff36a96a562c6fde3c02e264d61cbd25aa652c08d1e35")

print("FAILS:", FAILS or "none")
sys.exit(1 if FAILS else 0)
