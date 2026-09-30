"""
Stage 7 Commit 9 validation: reuse registry, band candidates and matching.

Usage, from the repository root with the project venv:
    python scripts/validation/commit09_validate.py

Source: the Commit 9 (3b0dd40) scratchpad validator, rewritten to use the
current module through _pipeline.py; the checks are unchanged. Nothing is
written to disk. Slow (see scripts/validation/README.md): the brute-force
candidate sets and the seven variant pipelines (seed 42 again, seeds 101
and 202, never, pre_only, caliper 0.2, placebo 90). Expected last line:
"FAILS: none".

Checks:
- pair invariants on every pair: explicit distance, 500-1,500 m band,
  same borough, eligible control, caliper in H8 form (O5), candidate
  monotonicity and the reuse_blocked bound (O3), key accounting, control
  site != treatment site;
- an independent interval sweep for the approved H9 interpretation;
- reason codes 9-12 agree with the candidate counts (O2);
- a brute-force band/clean/caliper/ranking for 150 treatments (O2, O4,
  O7) and an independent greedy replay of the whole match (O3, O6);
- determinism (seed 42 twice) and the variants (replay for never,
  pre_only and placebo; brute force for pre_only; never reuses no site).
The variant lines print the pair counts used by the seed-stability rule.
"""

import dataclasses
import time

import numpy as np
import pandas as pd

from _pipeline import check, finish, m, run


def pipeline(params):
    Q = run(params, until="matching")
    return dict(c=Q["c"], s=Q["s"], cr=Q["crime"], el=Q["el"], idx=Q["idx"],
                scales=Q["sc"], match=Q["match"], summary=Q["match_summary"],
                band=Q["band"])


def brute_force(params, P, rows):
    """Independent candidate sets: raw tables, explicit formula, no indexes."""
    c, s, cr, el, scales = P["c"], P["s"], P["cr"], P["el"], P["scales"]
    sx, sy = s.x_m.to_numpy(float), s.y_m.to_numpy(float)
    sb = s.borough.fillna("<NA>").astype(object).to_numpy()
    se = s.eligible_control.to_numpy()
    cx, cy = c.x_m.to_numpy(), c.y_m.to_numpy()
    ds, de = c.dark_start_s.to_numpy(), c.dark_end_s.to_numpy()
    kx, ky, kt = cr.x_m.to_numpy(), cr.y_m.to_numpy(), cr.t_s.to_numpy()
    bad = 0
    for r in rows:
        row = el.iloc[r]
        d = np.sqrt((sx - row.x_m) ** 2 + (sy - row.y_m) ** 2)
        band = np.flatnonzero((d >= 500) & (d <= params.match_band_max_m) & se
                              & (sb == row.treatment_borough))
        ws = row.w_start_s
        we = row.w_end_s if params.control_selection == "full_window" else row.created_s - 1
        live = np.flatnonzero((ds <= we) & (de >= ws))
        dd = np.sqrt((sx[band, None] - cx[None, live]) ** 2
                     + (sy[band, None] - cy[None, live]) ** 2)
        clean = band[~(dd <= params.exclusion_radius_m).any(axis=1)]
        inb = np.flatnonzero((kt >= row.b_start_s) & (kt <= row.b_end_s - 1))
        dk = np.sqrt((sx[clean, None] - kx[None, inb]) ** 2
                     + (sy[clean, None] - ky[None, inb]) ** 2)
        b100 = (dk <= 100).sum(axis=1)
        b250 = ((dk > 100) & (dk <= 250)).sum(axis=1)
        l100, l250 = np.log1p(b100.astype(float)), np.log1p(b250.astype(float))
        cal = ((np.abs(row.log_base_100m - l100) <= params.caliper_sd * scales.sd_100)
               & (np.abs(row.log_base_250m - l250) <= params.caliper_sd * scales.sd_250))
        md = np.sqrt((row.z100 - l100 / scales.sd_100) ** 2 + (row.z250 - l250 / scales.sd_250) ** 2)
        sid = s.site_id.astype(object).to_numpy()[clean]
        ranked = sorted(zip(md[cal], d[clean][cal], sid[cal], clean[cal]))
        got = m._caliper_candidates(params, el, P["idx"], scales, np.array([r]))
        n_band = int(np.diff(P["idx"].band_offsets)[r])
        base_of = {int(k): (int(a), int(b)) for k, a, b in zip(clean, b100, b250)}
        ok = (n_band == len(band) and got["n_clean"][0] == len(clean)
              and got["n_caliper"][0] == int(cal.sum())
              and got["site"].tolist() == [int(x[3]) for x in ranked]
              and [(int(a), int(b)) for a, b in zip(got["base_100"], got["base_250"])]
              == [base_of[int(x)] for x in got["site"]])
        bad += not ok
    return bad


def replay(params, P):
    """Independent greedy replay: own order, own interval registry."""
    el, match = P["el"], P["match"]
    keys = el.treatment_key.to_numpy()
    seq = np.argsort(keys, kind="stable")[np.random.default_rng(params.match_seed).permutation(len(el))]
    ws, we, site = el.w_start_s.to_numpy(), el.w_end_s.to_numpy(), el.site_idx.to_numpy()
    prereg, used = {}, {}
    for i in range(len(el)):
        prereg.setdefault(int(site[i]), []).append((ws[i], we[i]))
    ov = lambda L, a, b: any(s0 <= b and e0 >= a for s0, e0 in L)
    pr = match.pairs_raw.set_index("treatment_key")
    rj = match.rejected.set_index("treatment_key")
    idx = P["idx"]
    bad = 0
    n_pairs = 0
    for pos, r in enumerate(seq):
        got = m._caliper_candidates(params, el, idx, P["scales"], np.array([r]))
        chosen, blocked = None, 0
        for st in got["site"].tolist():
            L = used.get(st, [])
            if (params.reuse_policy == "never" and L) or ov(L, ws[r], we[r]) or ov(prereg.get(st, []), ws[r], we[r]):
                blocked += 1
                continue
            chosen = st
            break
        k = keys[r]
        if chosen is None:
            bad += k not in rj.index
        else:
            used.setdefault(chosen, []).append((ws[r], we[r]))
            n_pairs += 1
            p = pr.loc[k] if k in pr.index else None
            bad += p is None or p.control_site_idx != chosen or p.n_candidates_reuse_blocked != blocked or p.match_order != pos
    return bad, n_pairs


base = m.parse_args([])
t0 = time.perf_counter()
P = pipeline(base)
pr, rj, s = P["match"].pairs_raw, P["match"].rejected, P["s"]
el = P["el"].set_index("treatment_key")
print(f"pipeline {time.perf_counter() - t0:.1f} s, pairs {len(pr)}")

# Pair invariants on every pair.
t = el.loc[pr.treatment_key]
d = np.sqrt((s.x_m.to_numpy(float)[pr.control_site_idx] - t.x_m.to_numpy()) ** 2
            + (s.y_m.to_numpy(float)[pr.control_site_idx] - t.y_m.to_numpy()) ** 2)
check("distance_m equals explicit formula", np.array_equal(d, pr.distance_m.to_numpy()))
check("500 <= d <= 1500", bool(((d >= 500) & (d <= 1500)).all()))
check("same borough", bool((s.borough.to_numpy()[pr.control_site_idx] == t.treatment_borough.to_numpy()).all()))
check("control eligible", bool(s.eligible_control.to_numpy()[pr.control_site_idx].all()))
sc = P["scales"]
check("caliper (H8 form)", bool(
    ((np.abs(t.log_base_100m.to_numpy() - np.log1p(pr.control_base_100m.to_numpy(float))) <= 0.5 * sc.sd_100)
     & (np.abs(t.log_base_250m.to_numpy() - np.log1p(pr.control_base_250m.to_numpy(float))) <= 0.5 * sc.sd_250)).all()))
check("band >= clean >= caliper >= 1", bool(
    ((pr.n_candidates_band >= pr.n_candidates_clean) & (pr.n_candidates_clean >= pr.n_candidates_caliper)
     & (pr.n_candidates_caliper >= 1)).all()))
check("0 <= reuse_blocked <= caliper - 1", bool(
    ((pr.n_candidates_reuse_blocked >= 0) & (pr.n_candidates_reuse_blocked <= pr.n_candidates_caliper - 1)).all()))
check("treatment_key unique, disjoint", pr.treatment_key.is_unique and rj.treatment_key.is_unique
      and not set(pr.treatment_key) & set(rj.treatment_key)
      and len(pr) + len(rj) == len(el))
check("control site != treatment site", bool((pr.control_site_idx.to_numpy() != t.site_idx.to_numpy()).all()))

# Independent interval sweep (H9 interpretation).
iv = pd.concat([
    pd.DataFrame({"site": t.site_idx.to_numpy(), "a": t.w_start_s.to_numpy(), "b": t.w_end_s.to_numpy(), "role": "C"})
    .assign(site=pr.control_site_idx.to_numpy()),
    pd.DataFrame({"site": el.site_idx.to_numpy(), "a": el.w_start_s.to_numpy(), "b": el.w_end_s.to_numpy(), "role": "T"}),
]).sort_values(["site", "a", "b"], kind="mergesort")
viol = 0
for _, g in iv.groupby("site"):
    if (g.role == "C").sum() == 0:
        continue
    A, B, R = g.a.to_numpy(), g.b.to_numpy(), g.role.to_numpy()
    for i in np.flatnonzero(R == "C"):
        others = np.arange(len(g)) != i
        viol += int(((A[others] <= B[i]) & (B[others] >= A[i])).any())
check("no control interval overlaps another interval at its site", viol == 0, f"{viol} violations")

# Reason codes.
check("reason codes 9-12 only", set(rj.reason_step.unique()) <= {9, 10, 11, 12})
nb = rj.n_candidates_band.to_numpy(int); nc = rj.n_candidates_clean.to_numpy(int); nk = rj.n_candidates_caliper.to_numpy(int)
st = rj.reason_step.to_numpy()
check("reason matches counts", bool(np.array_equal(
    st, np.select([nb == 0, nc == 0, nk == 0], [9, 10, 11], 12))))

# Brute-force candidate sets.
rng = np.random.default_rng(20260928)
sample = rng.choice(len(P["el"]), 150, replace=False)
t0 = time.perf_counter()
bad = brute_force(base, P, sample)
check("brute-force band/clean/caliper/ranking (150 treatments)", bad == 0, f"{bad} mismatches, {time.perf_counter() - t0:.0f} s")

# Greedy replay.
t0 = time.perf_counter()
bad, n = replay(base, P)
check("independent greedy replay", bad == 0 and n == len(pr), f"{bad} mismatches, {n} pairs, {time.perf_counter() - t0:.0f} s")

# Determinism and variants.
hash42 = P["summary"]["order_hash"]
pairs42 = pr.copy()
for label, params in (
    ("seed 42 again", base),
    ("seed 101", dataclasses.replace(base, match_seed=101)),
    ("seed 202", dataclasses.replace(base, match_seed=202)),
    ("never", dataclasses.replace(base, reuse_policy="never")),
    ("pre_only", dataclasses.replace(base, control_selection="pre_only")),
    ("caliper 0.2", dataclasses.replace(base, caliper_sd=0.2)),
    ("placebo 90", dataclasses.replace(base, placebo_shift_days=90)),
):
    Q = pipeline(params)
    q = Q["match"].pairs_raw
    extra = ""
    if label == "seed 42 again":
        check("determinism (seed 42 x2)", Q["summary"]["order_hash"] == hash42 and q.equals(pairs42))
    if label in ("never", "pre_only", "placebo 90"):
        b, n = replay(params, Q)
        check(f"replay {label}", b == 0 and n == len(q), f"{b} mismatches")
        if label == "pre_only":
            bb = brute_force(params, Q, rng.choice(len(Q["el"]), 40, replace=False))
            check("brute force pre_only (40)", bb == 0, f"{bb} mismatches")
    if label == "never":
        check("never: each control site once", q.control_site_idx.is_unique)
    sm = Q["summary"]
    print(f"  {label:<14} eligible {sm['n_treatments']:,} pairs {sm['n_pairs']:,} reasons {sm['unmatched_reasons']} "
          f"sites {sm['n_control_sites']:,} max reuse {sm['control_reuse_max']} prereg {sm['blocked_by_preregistration']}")



finish()
