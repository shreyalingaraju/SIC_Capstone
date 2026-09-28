"""
Stage 10 (event study) validation for the Issue 4 panel.

Usage, from the repository root with the project venv:
    python scripts/validation/stage10_validate.py PANEL [EXTRA_PANEL ...]

PANEL is a Stage 8 causal_panel.parquet; EXTRA_PANELs (placebo, shifted)
are only checked to run. Outputs go to a temporary directory; outputs/ is
not touched. Runtime about 1-2 minutes. Expected last line: "FAILS: none".

Checks:
- event units are exactly the panel's unit_ids with the panel geometry;
  the module no longer reads the Stage 3 outage table;
- brute-force weekly counts (explicit distance, raw crime file) for 200
  units x 9 weeks;
- the within-transformation estimate equals an LSDV regression with
  C(unit_id) + C(rel_week) on a 300-pair subsample;
- cluster count = distinct treatment_h3_res7 cells; weeks -4..+4 with
  the [-7, 0) gap recorded; strict JSON;
- two runs give identical CSV, JSON and PNG; extra panels run; a
  legacy-schema panel is rejected.
"""

import hashlib
import inspect
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from pyproj import Transformer

sys.path.insert(0, "src/models")
import event_study as s10  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}: {name}{' - ' + detail if detail else ''}", flush=True)
    if not ok:
        FAILS.append(name)


def run(panel, out):
    proc = subprocess.run(
        [sys.executable, "src/models/event_study.py", "--panel", str(panel), "--out", str(out)],
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


panel_path = Path(sys.argv[1])
extra = [Path(p) for p in sys.argv[2:]]
scratch = Path(tempfile.mkdtemp(prefix="s10_"))

code, out = run(panel_path, scratch / "a")
check("Stage 10 runs (exit 0)", code == 0)
summary = json.loads((scratch / "a" / s10.SUMMARY_FILENAME).read_text(encoding="utf-8"),
                     parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))

panel = pd.read_parquet(panel_path)
units = s10.load_units(panel_path)
source = inspect.getsource(s10)
check("units == panel unit_ids with panel geometry; no outage table",
      set(units.unit_id) == set(panel.unit_id) and len(units) == panel.unit_id.nunique()
      and "clean_streetlights" not in source and "OUTAGE" not in source
      and units.set_index("unit_id").location_x_m.equals(
          panel.drop_duplicates("unit_id").set_index("unit_id").location_x_m.loc[units.unit_id]))

times, coords = s10.load_crime(s10.CRIME_FILE)
units_c = s10.complete_units(units, times)
rows = s10.build_event_rows(units_c, times, coords)

crime = pd.read_parquet(s10.CRIME_FILE, columns=["crime_datetime", "latitude", "longitude"]).dropna()
kx, ky = Transformer.from_crs("EPSG:4326", "EPSG:32118", always_xy=True).transform(
    crime.longitude.to_numpy(), crime.latitude.to_numpy())
kt = pd.to_datetime(crime.crime_datetime).to_numpy()
rng = np.random.default_rng(20260929)
bad = 0
for i in rng.choice(len(units_c), 200, replace=False):
    u = units_c.iloc[i]
    near = np.sqrt((kx - u.location_x_m) ** 2 + (ky - u.location_y_m) ** 2) <= 100
    days = (kt[near] - np.datetime64(u.created_date)) / np.timedelta64(1, "D")
    got = rows[rows.unit_id == u.unit_id].set_index("rel_week").crime_count
    for week, (a, b) in s10.WEEK_WINDOWS.items():
        bad += int(((days >= a) & (days < b)).sum()) != got.loc[week]
check("brute-force weekly counts, 200 units x 9 weeks", bad == 0, f"{bad} mismatches")

keep = rng.choice(rows.pair_id.unique(), 300, replace=False)
sub = rows[rows.pair_id.isin(keep)].reset_index(drop=True)
_, results, _, _, _ = s10.estimate(sub)
terms = " + ".join(s10.SAFE_EVENT_NAMES.values())
lsdv = smf.ols(f"crime_count ~ {terms} + C(unit_id) + C(rel_week)", data=sub).fit()
ok = all(abs(results.set_index("rel_week").coefficient[w] - lsdv.params[n]) < 1e-9
         for w, n in s10.SAFE_EVENT_NAMES.items())
check("within estimate == LSDV C(unit_id) + C(rel_week) (300 pairs)", ok)

check("clusters, fixed effects, weeks and gap recorded",
      summary["n_clusters"] == panel.treatment_h3_res7.nunique()
      and summary["fixed_effects"] == ["unit_id", "rel_week"]
      and sorted(int(k) for k in summary["week_windows_days"]) == list(range(-4, 5))
      and "[-7, 0)" in summary["status"],
      f"clusters {summary['n_clusters']}, F {summary['pretrend_test']['f_statistic']:.4f}")

code, _ = run(panel_path, scratch / "b")
same = {name: sha(scratch / "a" / name) == sha(scratch / "b" / name)
        for name in (s10.COEFFICIENT_FILENAME, s10.SUMMARY_FILENAME, s10.PLOT_FILENAME)}
check("two runs give identical CSV, JSON and PNG", code == 0 and all(same.values()), str(same))

for p in extra:
    code, out = run(p, scratch / p.stem)
    check(f"Stage 10 runs on {p.name}", code == 0, out.strip().splitlines()[-1][:60] if out.strip() else "")

legacy = Path("data/processed/causal_panel.pre_issue4.parquet")
if not legacy.exists():
    legacy = Path("data/processed/causal_panel.parquet")
code, out = run(legacy, scratch / "legacy")
check("legacy-schema panel rejected", code != 0 and "Is " in out and "Issue 4" in out)

print(f"\nscratch: {scratch}")
print("FAILS:", FAILS or "none")
