"""
Stage 7 Commit 11 validation: balance and contamination diagnostics.

Usage, from the repository root with the project venv:
    python scripts/validation/commit11_validate.py

Source: the Commit 11 (c2bb341) scratchpad validator, rewritten to use the
current module through _pipeline.py; the checks are unchanged. Nothing is
written to disk. Expected last line: "FAILS: none".

Checks:
- bal_* columns (prior episodes, res-9 density, S8 pre-window counts)
  against a brute force for 300 pairs x 2 roles (B3-B5, A9-A12);
- control pre counts from the ball query equal the site crime index;
- SMD and VR against an independent formula, and the zero-variance rules
  (B1, B2);
- contamination D1, D2, D3 flags and D4 counts against a brute force for
  300 pairs (B6-B10);
- H12 phase 2 passes for pre_only, placebo 90 and exclusion 500.
It also prints reviewer context: nearest-distance percentiles behind D1
and the S-3 check that no control has a complaint within 350 m during W.
"""

import dataclasses

import h3
import numpy as np
import pandas as pd

from _pipeline import check, finish, m, run


def pipeline(params):
    Q = run(params, until="balance")
    return dict(c=Q["c"], s=Q["s"], e=Q["e"], es=Q["es"], el=Q["el"], idx=Q["idx"],
                pairs=Q["pairs"], bal=Q["bal"], con=Q["con"], crime=Q["crime_raw"])


base = m.parse_args([])
P = pipeline(base)
pairs, c, e, crime = P["pairs"], P["c"], P["e"], P["crime"]
rng = np.random.default_rng(20260929)
sample = rng.choice(len(pairs), 300, replace=False)

# Independent inputs.
cx, cy = c.x_m.to_numpy(), c.y_m.to_numpy()
ep = c.episode_id.to_numpy()
ep_start = e.start_s.to_numpy()
ds, de = c.dark_start_s.to_numpy(), c.dark_end_s.to_numpy()
kx, ky, kt = crime.x_m.to_numpy(), crime.y_m.to_numpy(), crime.t_s.to_numpy()
kcell = np.array(m._h3_cells(crime.latitude.to_numpy(), crime.longitude.to_numpy(), 9), dtype=object)

bad = {k: 0 for k in ("prior", "density", "pre")}
for i in sample:
    p = pairs.iloc[i]
    bs, be = m._to_seconds(pd.Series([p.baseline_start, p.baseline_end]))
    cs = m._to_seconds(pd.Series([p.created_date]))[0]
    for role in ("treatment", "control"):
        x, y = p[f"{role}_x_m"], p[f"{role}_y_m"]
        d = np.sqrt((cx - x) ** 2 + (cy - y) ** 2)
        eps = np.unique(ep[d <= 250])
        prior = int(((ep_start[eps] >= bs) & (ep_start[eps] <= be - 1)).sum())
        bad["prior"] += prior != p[f"bal_{role}_prior_episodes_250m"]
        cell = p[f"{role}_h3_res9"]
        dens = ((kcell == cell) & (kt >= bs) & (kt < be)).sum() / h3.cell_area(cell, unit="km^2")
        bad["density"] += not np.isclose(dens, p[f"bal_{role}_h3r9_density"], rtol=0, atol=1e-9)
        dk = np.sqrt((kx - x) ** 2 + (ky - y) ** 2)
        inw = (kt >= cs - 14 * 86400) & (kt <= cs)
        bad["pre"] += (int((inw & (dk <= 100)).sum()) != p[f"bal_{role}_pre_100m"]
                       or int((inw & (dk > 100) & (dk <= 250)).sum()) != p[f"bal_{role}_pre_250m"])
for k, v in bad.items():
    check(f"brute-force bal_* {k} (300 pairs x 2 roles)", v == 0, f"{v} mismatches")

# Control pre counts via the site crime index equal the ball-query counts.
sid = pd.Index(P["s"].site_id.to_numpy(dtype=object)).get_indexer(pairs.control_site_id.to_numpy(dtype=object))
created = m._to_seconds(pairs.created_date)
via_index = m._count_in_window(P["idx"].crime_direct_key, sid, created - 14 * 86400, created, end_inclusive=True)
via_ring = m._count_in_window(P["idx"].crime_ring_key, sid, created - 14 * 86400, created, end_inclusive=True)
check("control pre counts: ball query == site crime index (all pairs)",
      bool((via_index == pairs.bal_control_pre_100m.to_numpy()).all() and (via_ring == pairs.bal_control_pre_250m.to_numpy()).all()))

# SMD / VR against an independent formula.
def smd(t, cc):
    return (t.mean() - cc.mean()) / np.sqrt((t.var(ddof=1) + cc.var(ddof=1)) / 2), t.var(ddof=1) / cc.var(ddof=1)
ok = True
for name, entry in P["bal"]["matched"].items():
    r = name.split("_")[-1]
    s_, v_ = smd(np.log1p(pairs[f"treatment_base_{r}"].astype(float)), np.log1p(pairs[f"control_base_{r}"].astype(float)))
    ok &= np.isclose(s_, entry["smd"]) and np.isclose(v_, entry["variance_ratio"])
for name, entry in P["bal"]["balance_only"].items():
    raw = name.removeprefix("log_")
    f = np.log1p if name.startswith("log_") else (lambda a: a)
    s_, v_ = smd(f(pairs[f"bal_treatment_{raw}"].astype(float)), f(pairs[f"bal_control_{raw}"].astype(float)))
    ok &= np.isclose(s_, entry["smd"]) and np.isclose(v_, entry["variance_ratio"])
check("SMD/VR independent recomputation", bool(ok))
check("zero-variance rules", m._smd_vr([1, 1], [1, 1])["smd"] == 0 and m._smd_vr([1, 1], [1, 1])["variance_ratio"] == 1
      and m._smd_vr([2, 2], [1, 1])["smd"] == np.inf and m._smd_vr([1, 3], [1, 1])["variance_ratio"] == np.inf)

# Contamination brute force on the sample.
tx, ty = pairs.treatment_x_m.to_numpy(), pairs.treatment_y_m.to_numpy()
qx, qy = pairs.control_x_m.to_numpy(), pairs.control_y_m.to_numpy()
ws, we = m._to_seconds(pairs.window_start), m._to_seconds(pairs.window_end)
closed = m._to_seconds(pairs.closed_date)
own = pd.Index(c.unique_key.to_numpy()).get_indexer(pairs.treatment_key.to_numpy())
f1 = f2 = f3 = 0
bad = 0
for i in sample:
    ov = (ws <= we[i]) & (we >= ws[i]); ov[i] = False
    d1 = (np.sqrt((tx - qx[i]) ** 2 + (ty - qy[i]) ** 2) <= 500) & ov
    d3 = (np.sqrt((qx - qx[i]) ** 2 + (qy - qy[i]) ** 2) <= 500) & ov
    dc = np.sqrt((cx - qx[i]) ** 2 + (cy - qy[i]) ** 2)
    d2 = ((dc > 350) & (dc <= 500) & (ds <= we[i]) & (de >= ws[i])).any()
    dt = np.sqrt((cx - tx[i]) ** 2 + (cy - ty[i]) ** 2)
    d4 = (dt <= 100) & (ds <= closed[i]) & (de >= created[i]); d4[own[i]] = False
    f1 += d1.any(); f2 += d2; f3 += d3.any()
    bad += int(d4.sum()) != int(m._dark_near(P["idx"], c, np.array([[tx[i], ty[i]]]), -1.0, 100.0,
                                            created[i:i + 1], closed[i:i + 1], exclude=own[i:i + 1])[0])
flags = {}
for key, fn in (("D1", lambda: m._pairs_near_other_pairs(np.c_[qx, qy], np.c_[tx, ty], 500, ws, we)),
                ("D3", lambda: m._pairs_near_other_pairs(np.c_[qx, qy], np.c_[qx, qy], 500, ws, we)),
                ("D2", lambda: m._dark_near(P["idx"], c, np.c_[qx, qy], 350, 500, ws, we) > 0)):
    flags[key] = fn()
check("D1 brute force (300)", int(flags["D1"][sample].sum()) == f1, f"{f1} flagged")
check("D2 brute force (300)", int(flags["D2"][sample].sum()) == f2, f"{f2} flagged")
check("D3 brute force (300)", int(flags["D3"][sample].sum()) == f3, f"{f3} flagged")
check("D4 per-pair counts brute force (300)", bad == 0, f"{bad} mismatches")

# Context for the reviewer (not in the pipeline): nearest distances behind D1/D3.
tree_t = m.cKDTree(np.c_[tx, ty])
near_other = []
for i in range(len(pairs)):
    idxs = tree_t.query_ball_point([qx[i], qy[i]], 500)
    ds_ = [np.hypot(tx[j] - qx[i], ty[j] - qy[i]) for j in idxs
           if j != i and ws[j] <= we[i] and we[j] >= ws[i]]
    if ds_:
        near_other.append(min(ds_))
near_other = np.array(near_other)
print(f"  D1 nearest other-treatment distance among flagged pairs: p10 {np.percentile(near_other, 10):.0f}, "
      f"p50 {np.percentile(near_other, 50):.0f}, p90 {np.percentile(near_other, 90):.0f}; "
      f"share of all pairs with one <= 350 m: {(near_other <= 350).sum() / len(pairs):.4f}, <= 250 m: {(near_other <= 250).sum() / len(pairs):.4f}")
dark_near_treat = m._dark_near(P["idx"], c, np.c_[qx, qy], -1.0, 350, ws, we) > 0
print(f"  controls with any complaint within 350 m during W (must be 0 under full_window): {int(dark_near_treat.sum())}")

# Variants.
for label, params in (("pre_only", dataclasses.replace(base, control_selection="pre_only")),
                      ("placebo 90", dataclasses.replace(base, placebo_shift_days=90)),
                      ("exclusion 500", dataclasses.replace(base, exclusion_radius_m=500.0))):
    Q = pipeline(params)
    r = m._check_h12(Q["pairs"], include_balance=True)
    con = Q["con"]
    check(f"{label}: H12 phase 2", r.passed, f"{len(Q['pairs']):,} pairs; matched_pass {Q['bal']['matched_pass']}; "
          + ", ".join(f"{k} {con[k]['share']:.3f}" for k in ("D1", "D2", "D2b", "D3") if k in con))



finish()
