"""
Stage 7 Commit 8 validation: treatment baselines, standardisation and H15.

Usage, from the repository root with the project venv:
    python scripts/validation/commit08_validate.py

Source: the Commit 8 (0bd8d2d) scratchpad validator, rewritten to use the
current module through _pipeline.py; the checks are unchanged. Nothing is
written to disk. Runtime about 2-4 minutes (brute force on 2 x 2,000
treatments). Expected last line: "FAILS: none".

Checks:
- canonical and placebo: base_100m (d <= 100) and base_250m
  (100 < d <= 250, ring only) against a brute force from the raw crime
  file, Stage 3 coordinates and pyproj, over the half-open B;
- sample SD (ddof = 1); z = log1p(count) / SD with no centring; B = 365 d;
- synthetic radius and B-boundary cases;
- H15 negatives (zero, NaN, inf SD; no treatments; one treatment).
"""

import contextlib
import dataclasses
import io

import numpy as np
import pandas as pd
from pyproj import Transformer

from _pipeline import check, finish, m, run

# Independent inputs: raw crime reread, Stage 3 coordinates, pyproj.
to_metres = Transformer.from_crs("EPSG:4326", "EPSG:32118", always_xy=True)
raw = pd.read_parquet(m.CRIME_FILE, columns=["crime_datetime", "latitude", "longitude"])
kx, ky = to_metres.transform(raw.longitude.to_numpy(), raw.latitude.to_numpy())
kt = raw.crime_datetime.to_numpy()
s3 = pd.read_parquet(m.TREATMENT_FILE, columns=["unique_key", "latitude", "longitude"]).set_index("unique_key")

rng = np.random.default_rng(20260927)
for label, shift in (("canonical", 0), ("placebo", 90)):
    params = dataclasses.replace(m.parse_args([]), placebo_shift_days=shift)
    P = run(params, until="baselines")
    el, scales = P["el"], P["sc"]
    sample = rng.choice(len(el), 2000, replace=False)
    bad = 0
    for i in sample:
        row = el.iloc[i]
        lon, lat = s3.loc[int(row.treatment_key), ["longitude", "latitude"]]
        x0, y0 = to_metres.transform(lon, lat)
        d = np.sqrt((kx - x0) ** 2 + (ky - y0) ** 2)
        start, end = np.datetime64(row.baseline_start), np.datetime64(row.baseline_end)
        in_b = (kt >= start) & (kt < end)  # native half-open B
        want_100 = int((in_b & (d <= 100)).sum())
        want_250 = int((in_b & (d > 100) & (d <= 250)).sum())
        bad += (want_100 != int(row.base_100m)) + (want_250 != int(row.base_250m))
    check(f"{label}: 2,000-treatment brute force matches base_100m and base_250m", bad == 0,
          f"{bad} mismatched counts")

    log100 = np.log1p(el.base_100m.to_numpy(float)); log250 = np.log1p(el.base_250m.to_numpy(float))
    sd100 = float(pd.Series(log100).std()); sd250 = float(pd.Series(log250).std())  # pandas default ddof=1
    check(f"{label}: SDs equal an independent sample SD (pandas, ddof=1)",
          abs(sd100 - scales.sd_100) < 1e-12 and abs(sd250 - scales.sd_250) < 1e-12,
          f"sd_100 {scales.sd_100:.9f}, sd_250 {scales.sd_250:.9f}")
    check(f"{label}: z = log1p(count) / SD with no centring",
          bool(np.allclose(el.z100, log100 / sd100, rtol=0, atol=1e-15))
          and bool(np.allclose(el.z250, log250 / sd250, rtol=0, atol=1e-15)))
    check(f"{label}: B is exactly 365 days for every treatment",
          bool(((el.b_end_s - el.b_start_s) == 365 * 86400).all()))

# Synthetic boundary cases.
B0, B1 = 1_600_000_000, 1_600_000_000 + 365 * 86400
crimes = pd.DataFrame({
    "x_m": [100.0, 100.0000001, 250.0, 250.0000001, 50.0, 50.0, 50.0, 50.0],
    "y_m": [0.0] * 8,
    "t_s": [B0 + 10, B0 + 10, B0 + 10, B0 + 10, B1 - 1, B1, B0, B0 - 1],
})
tr = pd.DataFrame({"x_m": [0.0], "y_m": [0.0], "b_start_s": [B0], "b_end_s": [B1]})
idx = m.SpatialIndexes(None, None, m.cKDTree(crimes[["x_m", "y_m"]].to_numpy()),
                       *(np.array([], np.int64),) * 6, {})
with contextlib.redirect_stdout(io.StringIO()):
    out, _ = m.compute_treatment_baselines(m.parse_args([]), tr.copy(), idx, crimes)
# direct: 100.0 (in), 50 m at b_end-1 (in), 50 m at b_start (in) -> 3;
#         50 m at b_end (out), 50 m at b_start-1 (out)
# ring:   100.0000001 (in), 250.0 (in) -> 2; 250.0000001 excluded
check("synthetic: d = 100 is direct; 100 + 1e-7 is ring; 250 is ring; 250 + 1e-7 excluded; "
      "t = b_start and b_end - 1 counted; b_end and b_start - 1 not",
      int(out.base_100m[0]) == 3 and int(out.base_250m[0]) == 2,
      f"base_100m {int(out.base_100m[0])} (expect 3), base_250m {int(out.base_250m[0])} (expect 2)")

# H15 negatives.
for label, sc, ok in (
    ("valid scales pass", m.Scales(1.1, 1.2, 10), True),
    ("sd_100 = 0 fails", m.Scales(0.0, 1.2, 10), False),
    ("sd_250 = nan fails", m.Scales(1.1, float("nan"), 10), False),
    ("sd_100 = inf fails", m.Scales(float("inf"), 1.2, 10), False),
    ("no eligible treatments fails", m.Scales(1.1, 1.2, 0), False),
):
    r = m._check_h15(sc)
    check(f"H15: {label}", r.passed == ok, "; ".join(r.examples))
with contextlib.redirect_stdout(io.StringIO()):
    one, sc1 = m.standardise(m.parse_args([]), pd.DataFrame({"base_100m": [3], "base_250m": [5]}))
check("H15: a single treatment gives a non-finite sample SD and fails", not m._check_h15(sc1).passed)


finish()
