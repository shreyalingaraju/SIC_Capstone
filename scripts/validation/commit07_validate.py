"""
Stage 7 Commit 7 validation: treatment windows and eligibility rules.

Usage, from the repository root with the project venv:
    python scripts/validation/commit07_validate.py

Source: the Commit 7 (6cae561) scratchpad validator, rewritten to use the
current module through _pipeline.py; the checks are unchanged. Nothing is
written to disk. Runtime about 2-4 minutes (brute force on 2 x 2,000
treatments). Expected last line: "FAILS: none".

Checks:
- canonical and placebo: a 2,000-treatment brute force (raw rereads,
  Timedelta arithmetic) reproduces every reason code of rules 1-8;
- rules 4-6 inputs identical in both runs; placebo windows = canonical
  windows moved back exactly 90 days;
- placebo S-4 (C4): the pre exemption on/off makes no difference for any
  treatment reaching rule 7 (shift > lag); the post exemption changes
  only real outages of 69 days or more, and only adds dirt;
- synthetic boundary suite for the S-4 pre/post predicates (A2, A13),
  the 100 m radius, the C2 exemptions and the placebo 69-day edge.
"""

import contextlib
import dataclasses
import io

import numpy as np
import pandas as pd

from _pipeline import check, finish, m, run

DAY = pd.Timedelta(days=1)

canonical = m.parse_args([])
placebo = dataclasses.replace(canonical, placebo_shift_days=90)
P = run(canonical, until="rules")
c, s, t, att = P["c"], P["s"], P["t"], P["attrition"]
Pp = run(placebo, until="rules")
tp, attp = Pp["t"], Pp["attrition"]

# ---------------------------------------------------------------
# Independent inputs for the brute force (raw rereads, Timedelta).
# ---------------------------------------------------------------
raw_crime = pd.read_parquet(m.CRIME_FILE, columns=["crime_datetime"])["crime_datetime"]
raw_311 = pd.to_datetime(
    pd.read_csv(m.RAW_COMPLAINTS_FILE, usecols=["created_date"])["created_date"],
    format="ISO8601",
)
crime_min, crime_max = raw_crime.min(), raw_crime.max()
dark_min, dark_max = raw_311.min(), raw_311.max()

created, closed = c["created_date"], c["closed_date"]
duration = closed - created
valid = closed.notna() & (duration >= pd.Timedelta(hours=0.5)) & (duration <= pd.Timedelta(hours=8760))
d_start = (created - 7 * DAY).to_numpy()
d_end = (created + pd.Timedelta(hours=160)).where(~valid, closed).to_numpy()
cx, cy = c["x_m"].to_numpy(), c["y_m"].to_numpy()
episode = c["episode_id"].to_numpy()

# First-of-episode recomputed by sorting on (episode, created, unique_key).
order = c.sort_values(["episode_id", "created_date", "unique_key"], kind="mergesort")
first = np.zeros(len(c), dtype=bool)
first[order.index[~order["episode_id"].duplicated()].to_numpy()] = True

codes = [code for code, step in m.REASON_CODES if step <= 8]


def brute_reason(row, shift_days):
    shift = shift_days * DAY
    ci = int(row.complaint_idx)
    cc = created.iloc[ci] - shift
    cl = closed.iloc[ci] - shift
    w_start = cc - 35 * DAY
    w_end = max(cl + 14 * DAY, cc + 35 * DAY)
    b_start, b_end = cc - 400 * DAY, cc - 35 * DAY
    site = int(c["site_idx"].iloc[ci])

    if w_start < crime_min or w_end > crime_max:
        return 1
    if w_start - 7 * DAY < dark_min or w_end + 7 * DAY > dark_max:
        return 2
    if b_start < crime_min or b_end - pd.Timedelta(seconds=1) > crime_max:  # half-open B
        return 3
    if bool(s["is_artifact"].iloc[site]):
        return 4
    if pd.isna(s["borough"].iloc[site]):
        return 5
    if not first[ci]:
        return 6

    near = np.sqrt((cx - cx[ci]) ** 2 + (cy - cy[ci]) ** 2) <= 100
    placebo_run = shift_days > 0
    cc64, cl64 = np.datetime64(cc), np.datetime64(cl)
    we64, ws64 = np.datetime64(w_end), np.datetime64(w_start)

    pre = near & (d_start < cc64) & (d_end >= ws64)
    if not placebo_run:
        pre &= episode != episode[ci]
    if pre.any():
        return 7

    post = near & (d_start <= we64) & (d_end > cl64)
    if not placebo_run:
        post[ci] = False
    if post.any():
        return 8
    return 0


rng = np.random.default_rng(20260927)
sample = rng.choice(len(t), 2000, replace=False)

for label, frame, shift in (("canonical", t, 0), ("placebo", tp, 90)):
    got = frame["reason_step"].fillna(0).astype(int).to_numpy()[sample]
    want = np.array([brute_reason(frame.iloc[i], shift) for i in sample])
    mismatch = int((got != want).sum())
    counts = np.bincount(want, minlength=9)
    check(
        f"{label}: 2,000-treatment brute force matches every reason code",
        mismatch == 0,
        f"{mismatch} mismatches; sample reasons 0-8: {counts.tolist()}",
    )

# ---------------------------------------------------------------
# Canonical vs placebo: only shifted windows and C4 explain differences.
# ---------------------------------------------------------------
art_c = s["is_artifact"].to_numpy()[t.site_idx.to_numpy()]
art_p = s["is_artifact"].to_numpy()[tp.site_idx.to_numpy()]
check("rules 4-6 inputs identical in both runs (site, borough, real first-of-episode)",
      bool((art_c == art_p).all())
      and bool((t.treatment_borough.isna() == tp.treatment_borough.isna()).all())
      and bool((t.is_first_of_episode == tp.is_first_of_episode).all()))
shift_s = 90 * 86400
check("placebo windows are the canonical windows moved back exactly 90 days",
      all(bool((t[col] - tp[col] == shift_s).all()) for col in
          ("created_s", "closed_s", "w_start_s", "w_end_s", "b_start_s", "b_end_s")))

# Counterfactual: placebo windows with the canonical exemptions.
# Reuse _s4_dirty with a canonical-exemption copy of the params but the
# placebo windows already stored in tp.
cc_, idx_ = Pp["c"], Pp["idx"]
pre_p, post_p = m._s4_dirty(placebo, tp, cc_, idx_)
pre_x, post_x = m._s4_dirty(canonical, tp, cc_, idx_)  # canonical exemptions, placebo windows

reach7 = tp["reason_step"].fillna(0).astype(int).to_numpy()
reach7 = (reach7 == 0) | (reach7 >= 7)
check("placebo S-4 pre: exemption on/off identical for every treatment reaching rule 7 (shift > lag proof)",
      bool((pre_p[reach7] == pre_x[reach7]).all()),
      f"{int(reach7.sum()):,} treatments reach rule 7; raw flags differ only for "
      f"{int((pre_p != pre_x).sum()):,} non-first-of-episode rows removed by rule 6")
differs = post_p != post_x
dur_days = (tp.closed_s - tp.created_s).to_numpy() / 86400
check("placebo S-4 post: the exemption changes only treatments whose real outage >= 69 days",
      bool((dur_days[differs] >= 69).all()),
      f"{int(differs.sum()):,} treatments differ; min duration {dur_days[differs].min() if differs.any() else float('nan'):.2f} d")
check("placebo S-4 post: removing the exemption can only add dirt, never remove it",
      bool((post_p >= post_x).all()))

# ---------------------------------------------------------------
# Expanded synthetic boundary suite.
# ---------------------------------------------------------------
D = 86400
C = 1_700_000_000
CLOSED = C + 5 * D
W_END = max(CLOSED + 14 * D, C + 35 * D)
params_s = dataclasses.replace(canonical)


def world(test=None, shift=0, treat_closed=CLOSED, test_episode="other"):
    """Treatment complaint 0 at (0, 0); optional test complaint 1."""

    rows = [dict(x=0.0, y=0.0, s=C - 7 * D, e=treat_closed, ep=0)]
    if test is not None:
        dist, s_, e_ = test
        rows.append(dict(x=dist, y=0.0, s=s_, e=e_, ep=0 if test_episode == "own" else 1))
    comp = pd.DataFrame({
        "x_m": [r["x"] for r in rows], "y_m": [r["y"] for r in rows],
        "dark_start_s": [r["s"] for r in rows], "dark_end_s": [r["e"] for r in rows],
        "episode_id": [r["ep"] for r in rows], "site_idx": np.arange(len(rows), dtype=np.int32),
        "is_first_of_episode": [True] + [test_episode != "own"] * (len(rows) - 1),
    })
    sites = pd.DataFrame({"is_artifact": [False] * len(rows), "borough": pd.array(["QUEENS"] * len(rows), dtype="string"),
                          "police_precinct": pd.array(["Precinct 1"] * len(rows), dtype="string")})
    tr = pd.DataFrame({
        "treatment_key": [1], "complaint_idx": [0], "x_m": [0.0], "y_m": [0.0],
        "borough": pd.array([pd.NA], dtype="string"), "police_precinct": pd.array([pd.NA], dtype="string"),
        "created_date": m._from_seconds([C - shift * D]), "closed_date": m._from_seconds([treat_closed - shift * D]),
    })
    idx = m.SpatialIndexes(m.cKDTree(comp[["x_m", "y_m"]].to_numpy()), None, None,
                           *(np.array([], np.int64),) * 3, *(np.array([], np.int64),) * 3, {})
    cov = m.Coverage(crime_min_s=0, crime_max_s=m.KEY_TIME_LIMIT - 1, dark_min_s=0, dark_max_s=m.KEY_TIME_LIMIT - 1)
    p = dataclasses.replace(params_s, placebo_shift_days=shift)
    with contextlib.redirect_stdout(io.StringIO()):
        tr = m.compute_windows(p, tr)
        tr, *_ = m.apply_treatment_rules(p, tr, comp, sites, idx, cov)
    step = tr["reason_step"].iloc[0]
    return 0 if pd.isna(step) else int(step)


PRE_HIT = (C - 10 * D, C - 1)
cases = [
    ("complaint at exactly 100 m is considered", world((100.0, *PRE_HIT)), 7),
    ("complaint at 100 m + 1e-7 is ignored", world((100.0000001, *PRE_HIT)), 0),
    ("pre: e = c - 35 d is dirty", world((10.0, C - 50 * D, C - 35 * D)), 7),
    ("pre: e = c - 35 d - 1 s is clean", world((10.0, C - 50 * D, C - 35 * D - 1)), 0),
    ("pre: s = c - 1 s is dirty", world((10.0, C - 1, C + 1 * D)), 7),
    ("pre: s = c is clean (during only)", world((10.0, C, C + 1 * D)), 0),
    ("post: e = closed + 1 s is dirty", world((10.0, C + 1 * D, CLOSED + 1)), 8),
    ("post: e = closed is clean (during only)", world((10.0, C + 1 * D, CLOSED)), 0),
    ("post: s = W_end is dirty", world((10.0, W_END, W_END + 5 * D)), 8),
    ("post: s = W_end + 1 s is clean", world((10.0, W_END + 1, W_END + 5 * D)), 0),
    ("canonical pre: own-episode duplicate is exempt", world((10.0, C - 6 * D, CLOSED), test_episode="own"), 0),
    ("canonical post: own-episode duplicate is dirty (C2)", world((10.0, C - 6 * D, CLOSED + 2 * D), test_episode="own"), 8),
    ("canonical: the treatment complaint alone is never counted", world(), 0),
    ("placebo post: real outage of exactly 69 d is dirty", world(shift=90, treat_closed=C + 69 * D), 8),
    ("placebo post: real outage of 69 d - 1 s is clean", world(shift=90, treat_closed=C + 69 * D - 1), 0),
]
for name, got, want in cases:
    check(f"synthetic: {name}", got == want, f"reason {got}, expected {want}")


finish()
