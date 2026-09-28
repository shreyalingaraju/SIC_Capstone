"""
Stage 8 (causal panel) validation for the Issue 4 schema.

Usage, from the repository root with the project venv:
    python scripts/validation/stage8_validate.py PAIRS_DIR [PLACEBO_PAIRS_DIR]

PAIRS_DIR holds a Stage 7 output (control_area_pairs.parquet); an
optional second directory holds a placebo (--placebo-shift-days 90) run.
Panels are written to a temporary directory; data/processed is not
touched. Runtime about 1-2 minutes. Expected last line: "FAILS: none".

Checks:
- panel pre-window counts equal Stage 7's independently computed
  bal_*_pre_100m / bal_*_pre_250m for every pair (same window and radii);
- brute-force recount of all three periods for 300 random units from the
  raw crime file (explicit distance, no KD-tree);
- panel structure: 3 rows per unit_id, unit/role/location columns match
  the pair columns, base columns, pair-level H3 res-7 cells;
- --pre-window shifted uses [c - 21 d, c - 7 d) and changes only pre rows;
- a placebo pairs file builds (if given);
- the legacy backup copies an old-schema panel once, never overwrites;
- an old-schema pairs file is rejected.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer

sys.path.insert(0, "src/features")
import build_causal_panel as s8  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}: {name}{' - ' + detail if detail else ''}", flush=True)
    if not ok:
        FAILS.append(name)


def run(*args):
    proc = subprocess.run(
        [sys.executable, "src/features/build_causal_panel.py", *map(str, args)],
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


pairs_dir = Path(sys.argv[1])
placebo_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else None
scratch = Path(tempfile.mkdtemp(prefix="s8_"))
pairs_file = pairs_dir / "control_area_pairs.parquet"

code, out = run("--pairs", pairs_file, "--out", scratch / "panel.parquet")
check("canonical panel builds (exit 0)", code == 0, out.strip().splitlines()[-1][:80])
panel = pd.read_parquet(scratch / "panel.parquet")
pairs = pd.read_parquet(pairs_file)

# Cross-stage: Stage 7 bal pre counts over [c - 14 d, c] with the same radii.
pre = panel[panel.period == "pre"].set_index("unit_id")
ok = True
for role, prefix in (("T", "treatment"), ("C", "control")):
    rows = pre.loc[pairs.pair_id + f"_{role}"]
    ok &= np.array_equal(rows.crime_100m.to_numpy(), pairs[f"bal_{prefix}_pre_100m"].to_numpy())
    ok &= np.array_equal(rows.crime_250m.to_numpy(), pairs[f"bal_{prefix}_pre_250m"].to_numpy())
    ok &= np.array_equal(rows.base_100m.to_numpy(), pairs[f"{prefix}_base_100m"].to_numpy())
    ok &= np.array_equal(rows.location_x_m.to_numpy(), pairs[f"{prefix}_x_m"].to_numpy())
    ok &= (rows.location_key.to_numpy(dtype=object) == pairs[f"{prefix}_site_id"].to_numpy(dtype=object)).all()
check("pre counts == Stage 7 bal_*_pre_* for all pairs; unit columns == pair columns", bool(ok))

# Brute force from the raw crime file.
crime = pd.read_parquet(s8.CRIME_FILE, columns=["crime_datetime", "latitude", "longitude"]).dropna()
kx, ky = Transformer.from_crs("EPSG:4326", "EPSG:32118", always_xy=True).transform(
    crime.longitude.to_numpy(), crime.latitude.to_numpy())
kt = pd.to_datetime(crime.crime_datetime).to_numpy()
units = panel.drop_duplicates("unit_id").reset_index(drop=True)
rng = np.random.default_rng(20260929)
bad = 0
for i in rng.choice(len(units), 300, replace=False):
    u = units.iloc[i]
    d = np.sqrt((kx - u.location_x_m) ** 2 + (ky - u.location_y_m) ** 2)
    c, cl = np.datetime64(u.created_date), np.datetime64(u.closed_date)
    windows = {"pre": (c - np.timedelta64(14, "D"), c), "during": (c, cl), "post": (cl, cl + np.timedelta64(14, "D"))}
    rows = panel[panel.unit_id == u.unit_id].set_index("period")
    for period, (a, b) in windows.items():
        inw = (kt >= a) & (kt <= b)
        bad += int(((d <= 100) & inw).sum()) != rows.loc[period, "crime_100m"]
        bad += int(((d > 100) & (d <= 250) & inw).sum()) != rows.loc[period, "crime_250m"]
check("brute-force recount, 300 units x 3 periods x 2 radii", bad == 0, f"{bad} mismatches")

check("structure: 3 rows per unit, 6 per pair, roles T/C",
      panel.groupby("unit_id").size().eq(3).all() and panel.groupby("pair_id").size().eq(6).all()
      and set(panel.role.unique()) == {"T", "C"} and len(panel) == 6 * len(pairs))
check("pair-level clustering columns constant within pair",
      panel.groupby("pair_id")[["treatment_h3_res7", "control_h3_res7"]].nunique().eq(1).all().all())
check("baseline_crime_intensity = unit pre 100 m count",
      bool((panel.baseline_crime_intensity == panel.unit_id.map(pre.crime_100m)).all()))

# Shifted pre-window (D19).
code, _ = run("--pairs", pairs_file, "--out", scratch / "shifted.parquet", "--pre-window", "shifted")
shifted = pd.read_parquet(scratch / "shifted.parquet")
# baseline_crime_intensity is the unit's pre-window 100 m count, so it
# follows the shifted pre-window on every row; everything else outside
# the pre rows must be unchanged.
same_non_pre = shifted[shifted.period != "pre"].drop(columns="baseline_crime_intensity").reset_index(drop=True).equals(
    panel[panel.period != "pre"].drop(columns="baseline_crime_intensity").reset_index(drop=True))
same_non_pre &= bool((shifted.baseline_crime_intensity == shifted.unit_id.map(
    shifted[shifted.period == "pre"].set_index("unit_id").crime_100m)).all())
bad = 0
for i in rng.choice(len(units), 100, replace=False):
    u = units.iloc[i]
    d = np.sqrt((kx - u.location_x_m) ** 2 + (ky - u.location_y_m) ** 2)
    c = np.datetime64(u.created_date)
    inw = (kt >= c - np.timedelta64(21, "D")) & (kt < c - np.timedelta64(7, "D"))
    row = shifted[(shifted.unit_id == u.unit_id) & (shifted.period == "pre")].iloc[0]
    bad += int(((d <= 100) & inw).sum()) != row.crime_100m
check("shifted pre-window [c-21d, c-7d): pre recount, other rows unchanged, baseline follows pre",
      code == 0 and bad == 0 and same_non_pre, f"{bad} mismatches")

if placebo_dir is not None:
    code, out = run("--pairs", placebo_dir / "control_area_pairs.parquet", "--out", scratch / "placebo.parquet")
    check("placebo pairs build a panel", code == 0, out.strip().splitlines()[-1][:80])

# Legacy backup.
lg = scratch / "legacy"; lg.mkdir()
shutil.copy2(s8.OUTPUT_FILE, lg / "causal_panel.parquet") if s8.OUTPUT_FILE.exists() else None
if (lg / "causal_panel.parquet").exists():
    first = s8.preserve_legacy_panel(lg / "causal_panel.parquet")
    second = s8.preserve_legacy_panel(lg / "causal_panel.parquet")
    check("legacy panel backed up once, then kept", first.startswith("backed up") and second.startswith("existing backup kept")
          and s8._sha256(lg / s8.LEGACY_PANEL_BACKUP) == s8._sha256(s8.OUTPUT_FILE))
check("new-schema panel not backed up",
      s8.preserve_legacy_panel(scratch / "panel.parquet").startswith("existing panel already has"))

old_pairs = Path("data/processed/control_area_pairs.pre_issue4.parquet")
if not old_pairs.exists():
    old_pairs = Path("data/processed/control_area_pairs.parquet")
code, out = run("--pairs", old_pairs, "--out", scratch / "old.parquet")
check("old-schema pairs file rejected", code != 0 and "not an Issue 4 Stage 7 pairs file" in out)

print(f"\nscratch: {scratch}")
print("FAILS:", FAILS or "none")
