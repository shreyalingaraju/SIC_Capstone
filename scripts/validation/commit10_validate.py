"""
Stage 7 Commit 10 validation: pair assembly, the pair hard checks and
their negative tests.

Usage, from the repository root with the project venv:
    python scripts/validation/commit10_validate.py

Source: the Commit 10 (e273ffb) scratchpad validator, its H9 endpoint
check and the d91f1f8 fix-up test, rewritten to use the current module
through _pipeline.py; the checks are unchanged. Nothing is written to
disk. Expected last line: "FAILS: none".

Checks:
- assembled values against independent sources: Stage 3 coordinates,
  dates and duration (U8); control site geometry; site H3 cells for both
  roles (U1); site modal borough and precinct; pair_id; reuse counts;
  windows and baselines; row order; schema;
- H5, H6, H7, H8, H9, H12 (phase 1) and H14 pass on the canonical pairs;
- one-mutation negative tests: each makes exactly its target check fail;
- all checks pass for never, pre_only and placebo 90;
- H9 endpoints and the d91f1f8 unresolved-key behaviour.
"""

import contextlib
import dataclasses
import io

import numpy as np
import pandas as pd

from _pipeline import check, finish, m, run


def pipeline(params):
    Q = run(params, until="pairs")
    return dict(c=Q["c"], s=Q["s"], el=Q["el"], cov=Q["cov"], sc=Q["sc"],
                match=Q["match"], pairs=Q["pairs"])


def run_all(params, P, pairs=None, **override):
    pairs = P["pairs"] if pairs is None else pairs
    c, s, el, cov, sc = (override.get(k, P[k]) for k in ("c", "s", "el", "cov", "sc"))
    h9, tt = m._check_h9(params, pairs, el, c, s)
    return {
        "H5": m._check_h5(pairs, c, s), "H6": m._check_h6(params, pairs, cov),
        "H7": m._check_h7(params, pairs), "H8": m._check_h8(params, pairs, sc),
        "H9": h9, "H12": m._check_h12(pairs, include_balance=False),
        "H14": m._check_h14(pairs),
    }, tt


base = m.parse_args([])
P = pipeline(base)
pairs, s, el = P["pairs"], P["s"], P["el"].set_index("treatment_key")

# ---- Assembly values against independent sources.
s3 = pd.read_parquet(m.TREATMENT_FILE, columns=["unique_key", "latitude", "longitude", "created_date",
                                                "closed_date", "outage_duration_hours"]).set_index("unique_key")
s3 = s3.loc[pairs.treatment_key]
check("treatment lat/lon = Stage 3 within 1e-6 deg",
      bool((np.abs(pairs.treatment_latitude.to_numpy() - s3.latitude.to_numpy()) <= 1e-6).all()
           and (np.abs(pairs.treatment_longitude.to_numpy() - s3.longitude.to_numpy()) <= 1e-6).all()))
check("created/closed = Stage 3 (canonical)",
      bool((pairs.created_date.to_numpy() == s3.created_date.to_numpy().astype("datetime64[us]")).all()
           and (pairs.closed_date.to_numpy() == s3.closed_date.to_numpy().astype("datetime64[us]")).all()))
dur_diff = np.abs(pairs.outage_duration_hours.to_numpy() - s3.outage_duration_hours.to_numpy())
check("outage_duration_hours vs Stage 3 column", bool(dur_diff.max() < 1e-6), f"max |diff| {dur_diff.max():.2e} h")
sid = s.set_index("site_id")
cs, ts = sid.loc[pairs.control_site_id], sid.loc[pairs.treatment_site_id]
check("control lat/lon/x/y = site", bool(
    (pairs.control_latitude.to_numpy() == cs.latitude.to_numpy()).all()
    and (pairs.control_x_m.to_numpy() == cs.x_m.to_numpy().astype(float)).all()
    and (pairs.control_y_m.to_numpy() == cs.y_m.to_numpy().astype(float)).all()))
check("H3 cells = site cells (both roles, U1)", all(
    (pairs[f"{r}_h3_res{k}"].to_numpy(dtype=object) == frame[f"h3_res{k}"].to_numpy(dtype=object)).all()
    for r, frame in (("treatment", ts), ("control", cs)) for k in (7, 9, 10)))
check("borough/precinct = site modal labels", bool(
    (pairs.control_borough.to_numpy(dtype=object) == cs.borough.to_numpy(dtype=object)).all()
    and (pairs.treatment_borough.to_numpy(dtype=object) == ts.borough.to_numpy(dtype=object)).all()
    and pairs.treatment_police_precinct.fillna("<NA>").to_numpy(dtype=object).tolist()
    == ts.police_precinct.fillna("<NA>").to_numpy(dtype=object).tolist()))
check("pair_id format", bool((pairs.pair_id == pairs.treatment_key.astype(str) + "_" + pairs.control_site_id).all()))
check("control_reuse_count = value_counts",
      bool((pairs.control_reuse_count.to_numpy()
            == pairs.control_site_id.map(pairs.control_site_id.value_counts()).to_numpy()).all()))
e = el.loc[pairs.treatment_key]
check("windows and treatment baselines copied", bool(
    (pairs.window_start.to_numpy() == e.window_start.to_numpy()).all()
    and (pairs.baseline_end.to_numpy() == e.baseline_end.to_numpy()).all()
    and (pairs.treatment_base_100m.to_numpy() == e.base_100m.to_numpy()).all()))
check("row order = match_order", bool(pairs.match_order.is_monotonic_increasing))
check("schema = PAIRS_DTYPES minus bal_*", list(pairs.columns) == [c for c in m.PAIRS_DTYPES if not c.startswith("bal_")]
      and all(str(pairs[c].dtype) == m.PAIRS_DTYPES[c] for c in pairs.columns))

res, tt = run_all(base, P)
check("all checks pass (canonical)", all(r.passed for r in res.values()), str({k: r.n_violations for k, r in res.items()}))
print("  H9 treatment-treatment:", tt)

# ---- Negative tests: one mutation each; expect exactly the target check to fail.
def negative(name, target, expect_n, pairs=None, **override):
    r, _ = run_all(base, P, pairs=pairs, **override)
    failed = sorted(k for k, v in r.items() if not v.passed)
    ok = failed == [target] and r[target].n_violations == expect_n
    check(f"negative {name}", ok, f"failed {failed}, {target} violations {r[target].n_violations} (expected {expect_n}); "
          f"{r[target].examples[0][:110] if r[target].examples else ''}")

# H5: the first pair's complaint is no longer first of its episode.
c2 = P["c"].copy()
row = int(np.flatnonzero(c2.unique_key.to_numpy() == pairs.treatment_key.iloc[0])[0])
c2.loc[row, "is_first_of_episode"] = False
negative("H5 not first-of-episode", "H5", 1, c=c2)
# H5: treatment site flagged artifact (2 pairs at most share it, count them).
s2 = P["s"].copy()
tsite = int(c2.site_idx.iloc[row])
s2.loc[tsite, "is_artifact"] = True
n_at = int((pairs.treatment_site_id == s2.site_id.iloc[tsite]).sum())
negative("H5 artifact site", "H5", n_at, s=s2)
# H6: crime coverage ends one second before the latest W end.
cov2 = dataclasses.replace(P["cov"], crime_max_s=int(m._to_seconds(pairs.window_end).max()) - 1)
n_w = int((m._to_seconds(pairs.window_end) > cov2.crime_max_s).sum())
negative("H6 crime coverage cut", "H6", n_w, cov=cov2)
# H7: move one control 1 cm (distance_m no longer equals the formula).
p2 = pairs.copy(); p2.loc[0, "control_x_m"] += 0.01
negative("H7 corrupted control x", "H7", 1, pairs=p2)
# H7: borough mismatch.
p2 = pairs.copy(); p2.loc[1, "control_borough"] = "NOT_A_BOROUGH"
negative("H7 borough mismatch", "H7", 1, pairs=p2)
# H8: control 250 m baseline pushed outside the caliper.
p2 = pairs.copy(); p2.loc[2, "control_base_250m"] = int(p2.loc[2, "treatment_base_250m"]) * 20 + 50
negative("H8 caliper", "H8", 1, pairs=p2)
# H9: second pair forced onto the first pair's control site with the same W.
p2 = pairs.copy()
p2.loc[1, ["control_site_id", "window_start", "window_end"]] = p2.loc[0, ["control_site_id", "window_start", "window_end"]].to_numpy()
p2.loc[1, "pair_id"] = f"{p2.loc[1, 'treatment_key']}_{p2.loc[1, 'control_site_id']}"
negative("H9 overlapping control uses", "H9", 2, pairs=p2)
# H9: control placed at an eligible treatment's site during that treatment's W.
p2 = pairs.copy()
other = el.reset_index().iloc[0]
p2.loc[5, "control_site_id"] = s.site_id.iloc[int(other.site_idx)]
p2.loc[5, "window_start"], p2.loc[5, "window_end"] = other.window_start, other.window_end
p2.loc[5, "pair_id"] = f"{p2.loc[5, 'treatment_key']}_{p2.loc[5, 'control_site_id']}"
negative("H9 control over treatment W", "H9", 1, pairs=p2)
# H9 never: a control site used twice without overlapping windows.
never = dataclasses.replace(base, reuse_policy="never")
dup = pairs[pairs.control_reuse_count >= 2].control_site_id.iloc[0]
rows = pairs.index[pairs.control_site_id == dup]
r, _ = run_all(never, P)
check("negative H9 never reuse", not r["H9"].passed and r["H9"].n_violations == int((pairs.control_reuse_count >= 2).sum()),
      f"violations {r['H9'].n_violations}")
# H12: duplicated pair_id / null in required column / wrong dtype / self site.
p2 = pairs.copy(); p2.loc[1, "pair_id"] = p2.loc[0, "pair_id"]
negative("H12 duplicate pair_id", "H12", 2, pairs=p2)
p2 = pairs.copy(); p2.loc[3, "control_h3_res9"] = pd.NA
negative("H12 null required column", "H12", 1, pairs=p2)
p2 = pairs.copy(); p2["match_order"] = p2["match_order"].astype("int64")
negative("H12 wrong dtype", "H12", 1, pairs=p2)
p2 = pairs.copy(); p2["extra"] = 1
negative("H12 extra column", "H12", 1, pairs=p2)
# H14: clean > band; reuse_blocked = caliper.
p2 = pairs.copy(); p2.loc[0, "n_candidates_clean"] = p2.loc[0, "n_candidates_band"] + 1
negative("H14 clean > band", "H14", 1, pairs=p2)
p2 = pairs.copy(); p2.loc[0, "n_candidates_reuse_blocked"] = p2.loc[0, "n_candidates_caliper"]
negative("H14 reuse_blocked = caliper", "H14", 1, pairs=p2)

# ---- Variants: all checks pass.
for label, params in (("never", never), ("pre_only", dataclasses.replace(base, control_selection="pre_only")),
                      ("placebo 90", dataclasses.replace(base, placebo_shift_days=90))):
    Q = pipeline(params)
    r, tt = run_all(params, Q)
    check(f"all checks pass ({label})", all(v.passed for v in r.values()),
          f"{len(Q['pairs']):,} pairs; TT overlaps {tt}")


# ---- H9 endpoints (Commit 10 review): each control interval is the closed
# [w_start_s, w_end_s] of its treatment, with no - 1 applied.
ws_, we_ = m._to_seconds(pairs.window_start), m._to_seconds(pairs.window_end)
check("H9 endpoints: control interval == [w_start_s, w_end_s] of its treatment",
      bool((ws_ == e.w_start_s.to_numpy()).all() and (we_ == e.w_end_s.to_numpy()).all()))


# ---- d91f1f8 fix-up: unresolved keys are recorded H9 violations, the other
# pair checks still run and are recorded, and the run still fails.
def pair_stage(pairs_, el_):
    checks, summary = {}, {}
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            m.run_hard_checks(base, "pairs", checks, pairs=pairs_, treatments_eligible=el_,
                              complaints=P["c"], sites=P["s"], coverage=P["cov"],
                              scales=P["sc"], summary=summary)
        raised = None
    except m.HardCheckError as error:
        raised = str(error)
    return checks, summary, raised


checks_, summary_, raised_ = pair_stage(P["pairs"], P["el"])
check("pairs stage passes on valid data; 935 treatment-treatment overlaps reported",
      raised_ is None and all(r.passed for r in checks_.values())
      and summary_["n_treatment_pairs_overlapping"] == 935, str(summary_))
p2 = P["pairs"].copy(); p2.loc[0, "control_site_id"] = "E0_N0"
checks_, _, raised_ = pair_stage(p2, P["el"])
check("fix-up: unresolved control_site_id is 1 recorded H9 violation; all 7 checks recorded; run fails",
      list(checks_) == ["H5", "H6", "H7", "H8", "H9", "H12", "H14"]
      and not checks_["H9"].passed and checks_["H9"].n_violations == 1 and raised_ is not None,
      (checks_["H9"].examples or [""])[0][:90])
el2 = P["el"].copy(); el2.loc[0, "treatment_key"] = -1
checks_, _, raised_ = pair_stage(P["pairs"], el2)
check("fix-up: unresolved eligible treatment_key is 1 recorded H9 violation; run fails",
      len(checks_) == 7 and not checks_["H9"].passed and checks_["H9"].n_violations == 1
      and raised_ is not None, (checks_["H9"].examples or [""])[0][:90])


finish()
