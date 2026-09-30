"""
Stage 7 Commit 6 validation: spatial indexes, A13 and the lookup helpers.

Usage, from the repository root with the project venv:
    python scripts/validation/commit06_validate.py

Source: the Commit 6 (24f0c2f) scratchpad validator, rewritten to use the
current module through _pipeline.py; the checks are unchanged. Nothing is
written to disk. Runtime about 2-3 minutes (the crime-cell brute force
recomputes an H3 cell for every crime). Expected last line: "FAILS: none".

Checks:
- dirty flags (20 sites x 50 windows) and crime direct/ring counts
  (20 sites x 25 windows, half-open and closed) against brute force;
- crime res-9 cell counts (20 cells) against cells recomputed from the
  crime file;
- A13 edge cases: touching endpoints, empty segments, a > b windows,
  [a, b) == [a, b - 1], broadcasting, out-of-range guards;
- start ties and per-site running maximum;
- synthetic points at exactly 100 / 250 / 350 m and 1e-7 m beyond;
- determinism (byte-identical indexes) and placebo invariance.
"""

import contextlib
import dataclasses
import hashlib
import io

import h3
import numpy as np
import pandas as pd

from _pipeline import check, finish, m, run

p = m.parse_args([])
P = run(p, until="indexes")
c, s, cr, idx = P["c"], P["s"], P["crime_raw"], P["idx"]
rng = np.random.default_rng(20260927)
DAY = 86400

cx, cy = c.x_m.to_numpy(), c.y_m.to_numpy()
ds, de = c.dark_start_s.to_numpy(), c.dark_end_s.to_numpy()
kx, ky, kt = cr.x_m.to_numpy(), cr.y_m.to_numpy(), cr.t_s.to_numpy()
eligible = np.flatnonzero(s.eligible_control.to_numpy())
sites20 = rng.choice(eligible, 20, replace=False)

# 1. Dirty flags vs brute force: 20 sites x 50 random closed windows.
bad = 0
for site in sites20:
    x0, y0 = float(s.x_m[site]), float(s.y_m[site])
    near = np.sqrt((cx - x0) ** 2 + (cy - y0) ** 2) <= p.exclusion_radius_m
    for _ in range(50):
        a = int(rng.integers(1_580_000_000, 1_780_000_000))
        b = a + int(rng.integers(0, 90 * DAY))
        brute = bool(np.any(near & (ds <= b) & (de >= a)))
        got = bool(m._dirty_mask(idx, np.array([site]), a, b)[0])
        bad += brute != got
check("dirty flags, 20 sites x 50 windows vs brute force", bad == 0, f"{bad} mismatches")

# 2. Crime direct/ring counts vs brute force: half-open and closed windows.
bad = 0
for site in sites20:
    x0, y0 = float(s.x_m[site]), float(s.y_m[site])
    d = np.sqrt((kx - x0) ** 2 + (ky - y0) ** 2)
    direct, ring = d <= 100, (d > 100) & (d <= 250)
    for _ in range(25):
        lo = int(rng.integers(1_575_000_000, 1_780_000_000))
        hi = lo + int(rng.integers(0, 400 * DAY))
        for inclusive in (False, True):
            in_window = (kt >= lo) & ((kt <= hi) if inclusive else (kt < hi))
            for mask, key in ((direct, idx.crime_direct_key), (ring, idx.crime_ring_key)):
                got = int(m._count_in_window(key, np.array([site]), lo, hi, inclusive)[0])
                bad += got != int((mask & in_window).sum())
check("crime direct/ring counts, 20 sites x 25 windows x 2 closures", bad == 0, f"{bad} mismatches")

# 3. Crime cell counts vs brute force (cells recomputed from the crime file).
raw = pd.read_parquet(m.CRIME_FILE, columns=["crime_datetime", "latitude", "longitude"])
raw_t = raw.crime_datetime.astype("datetime64[s]").astype("int64").to_numpy()
cells20 = rng.choice(np.array(list(idx.crime_cell_rank)), 20, replace=False)
lat, lon = raw.latitude.to_numpy(), raw.longitude.to_numpy()
raw_cells = np.array([h3.str_to_int(h3.latlng_to_cell(a, b, 9)) for a, b in zip(lat, lon)], dtype=np.uint64)
bad = 0
for cell in cells20:
    in_cell = raw_cells == np.uint64(cell)
    lo = int(rng.integers(1_575_000_000, 1_750_000_000)); hi = lo + 365 * DAY
    got = int(m._count_in_window(idx.crime_cell_key, np.array([idx.crime_cell_rank[int(cell)]]), lo, hi, False)[0])
    bad += got != int((in_cell & (raw_t >= lo) & (raw_t < hi)).sum())
check("crime cell counts, 20 cells vs brute force", bad == 0, f"{bad} mismatches")

# 4. Contract edge cases on the real index.
site = int(sites20[0])
lo_seg, hi_seg = idx.dirty_offsets[site], idx.dirty_offsets[site + 1]
starts = idx.dirty_key[lo_seg:hi_seg] % m.KEY_TIME_LIMIT
ends_all = de[np.sqrt((cx - s.x_m[site]) ** 2 + (cy - s.y_m[site]) ** 2) <= p.exclusion_radius_m]
s0 = int(starts[0])
check("window ending exactly at an interval start is dirty", bool(m._dirty_mask(idx, [site], s0 - 10, s0)[0]))
e_last = int(ends_all.max())
check("window starting exactly at an interval end is dirty", bool(m._dirty_mask(idx, [site], e_last, e_last + 10)[0]))
check("window starting 1 s after the last end is clean", not bool(m._dirty_mask(idx, [site], e_last + 1, e_last + 10)[0]) or bool(np.any(starts > e_last)))
ineligible = np.flatnonzero(~s.eligible_control.to_numpy())
check("empty segment (ineligible sites) is clean",
      not m._dirty_mask(idx, ineligible, 1_580_000_000, 1_780_000_000).any(),
      f"{len(ineligible)} sites")
check("empty window a > b is clean", not m._dirty_mask(idx, [site], s0 + 5, s0)[0])
check("empty window a > b counts 0",
      int(m._count_in_window(idx.crime_ring_key, [site], 1_700_000_000, 1_600_000_000, True)[0]) == 0)
t_hit = int(kt[0])
g_hit = np.flatnonzero(s.eligible_control.to_numpy())
check("start == end, half-open counts 0",
      int(m._count_in_window(idx.crime_cell_key, [0], t_hit, t_hit, False)[0]) == 0)
check("group -1 counts 0",
      int(m._count_in_window(idx.crime_cell_key, [-1], 0, m.KEY_TIME_LIMIT - 1, True)[0]) == 0)
# Half-open [a, b) == closed [a, b - 1] on counts.
ok = True
for site_ in sites20[:5]:
    for _ in range(20):
        lo = int(rng.integers(1_580_000_000, 1_750_000_000)); hi = lo + int(rng.integers(1, 30 * DAY))
        ok &= int(m._count_in_window(idx.crime_ring_key, [site_], lo, hi, False)[0]) == \
              int(m._count_in_window(idx.crime_ring_key, [site_], lo, hi - 1, True)[0])
check("half-open [a, b) equals closed [a, b - 1]", ok)
# Broadcasting: one window for many sites and one window per site agree.
many = eligible[:500]
wa = rng.integers(1_580_000_000, 1_750_000_000, len(many)); wb = wa + 35 * DAY
per_element = m._dirty_mask(idx, many, wa, wb)
one_by_one = np.array([m._dirty_mask(idx, [x], a, b)[0] for x, a, b in zip(many, wa, wb)])
check("per-element windows equal one-by-one calls", bool((per_element == one_by_one).all()))
scalar = m._dirty_mask(idx, many, 1_650_000_000, 1_653_000_000)
check("scalar window equals broadcast array window",
      bool((scalar == m._dirty_mask(idx, many, np.full(500, 1_650_000_000), np.full(500, 1_653_000_000))).all()))
for label, call in (
    ("site out of range", lambda: m._dirty_mask(idx, [len(s)], 0, 1)),
    ("negative time", lambda: m._dirty_mask(idx, [site], -1, 5)),
    ("time >= 2**34", lambda: m._count_in_window(idx.crime_ring_key, [site], 0, m.KEY_TIME_LIMIT, True)),
    ("group < -1", lambda: m._count_in_window(idx.crime_ring_key, [-2], 0, 1, True)),
):
    try:
        call(); check(f"{label} raises ValueError", False)
    except ValueError as error:
        check(f"{label} raises ValueError", True, str(error)[:60])
for label, call, category in (
    ("build guard: negative time", lambda: m._build_sorted_index([0], [-1], 1), "invalid time range"),
    ("build guard: group >= 2**17", lambda: m._build_sorted_index([m.KEY_MAX_GROUP], [0], 1), "invalid group id"),
    ("build guard: end >= 2**34", lambda: m._build_sorted_index([0], [0], 1, [m.KEY_TIME_LIMIT]), "invalid time range"),
):
    try:
        call(); check(label, False)
    except m.HardCheckError as error:
        check(label, category in str(error), str(error)[:70])

# 5. Start ties: several intervals with the same start, different ends.
key, runmax, offsets = m._build_sorted_index([0, 0, 0, 1], [100, 100, 100, 50], 2, [150, 400, 200, 60])
tiny = m.SpatialIndexes(None, None, None, key, runmax, offsets, np.array([], np.int64), np.array([], np.int64), np.array([], np.int64), {})
check("start ties: window [350, 360] sees the longest tied interval", bool(m._dirty_mask(tiny, [0], 350, 360)[0]))
check("start ties: window [401, 500] is clean", not bool(m._dirty_mask(tiny, [0], 401, 500)[0]))
check("running max resets per site", not bool(m._dirty_mask(tiny, [1], 61, 500)[0]))

# 6. Synthetic boundary points at exactly 100 / 250 / 350 m.
params = dataclasses.replace(p)
complaints = pd.DataFrame({"x_m": [350.0, 350.0000001, 0.0], "y_m": [0.0, 0.0, 350.0],
                           "dark_start_s": [1000, 1000, 5000], "dark_end_s": [2000, 2000, 6000]})
sites_df = pd.DataFrame({"x_m": [0, 10_000], "y_m": [0, 10_000], "eligible_control": [True, True]})
crime_df = pd.DataFrame({"x_m": [100.0, 100.0000001, 250.0, 250.0000001, 0.0], "y_m": [0.0, 0.0, 0.0, 0.0, 99.9999999],
                         "t_s": [10, 20, 30, 40, 50], "latitude": [40.7] * 5, "longitude": [-73.9] * 5})
with contextlib.redirect_stdout(io.StringIO()):
    syn, _ = m.build_spatial_indexes(params, complaints, sites_df, crime_df)
check("complaint at exactly 350 m is dirty", bool(m._dirty_mask(syn, [0], 1500, 1500)[0]))
check("complaint 1e-7 m beyond 350 m is excluded (only 2 entries at site 0)",
      int(syn.dirty_offsets[1] - syn.dirty_offsets[0]) == 2)
d_direct = int(m._count_in_window(syn.crime_direct_key, [0], 0, 100, True)[0])
d_ring = int(m._count_in_window(syn.crime_ring_key, [0], 0, 100, True)[0])
check("crime at exactly 100 m is direct; 1e-7 m beyond is ring", d_direct == 2 and d_ring == 2,
      f"direct {d_direct} (100 m, 99.9999999 m), ring {d_ring} (100.0000001 m, 250 m)")
check("crime 1e-7 m beyond 250 m is excluded", d_direct + d_ring == 4)

# 7. Determinism and placebo.
def digest(index):
    parts = [index.dirty_key, index.dirty_runmax_end, index.dirty_offsets,
             index.crime_direct_key, index.crime_ring_key, index.crime_cell_key]
    return hashlib.sha256(b"".join(a.tobytes() for a in parts) + repr(sorted(index.crime_cell_rank.items())).encode()).hexdigest()[:16]

idx2 = run(p, until="indexes")["idx"]
check("determinism: two builds are byte-identical", digest(idx) == digest(idx2), digest(idx))
idxp = run(dataclasses.replace(p, placebo_shift_days=90), until="indexes")["idx"]
check("placebo: indexes identical (real dates, C4)", digest(idxp) == digest(idx))


finish()
