"""
Stage 7 Commit 5 validation: episodes, H3 and H4.

Usage, from the repository root with the project venv:
    python scripts/validation/commit05_validate.py

Source: the in-memory tests run for Commit 5 (7fc8065) and the F6 count,
rewritten to use the current module through _pipeline.py. Nothing is
written to disk. Runtime about 1 minute. Expected last line:
"FAILS: none".

Checks:
- the site table has n_episodes (int32); complaint order unchanged;
- determinism (two builds) and placebo invariance (episodes use real dates);
- artifact complaints are single-complaint episodes; one first per episode;
- H3 and H4 pass; negative tests fail the expected check (first flag
  moved; member moved; episode end_s + 1; site n_episodes + 1; episode
  split; one link dropped);
- F6: 88,388 Stage 3 treatments are first of their episode.
"""

import dataclasses
import hashlib

import numpy as np
import pandas as pd

from _pipeline import check, finish, m, run


def digest(frame):
    return hashlib.sha256(
        pd.util.hash_pandas_object(frame, index=True).values.tobytes()
    ).hexdigest()[:16]


p = m.parse_args([])
P = run(p, until="episodes")
c, s, e, links = P["c"], P["s"], P["e"], P["links"]

check("complaint order unchanged (still unique_key order)",
      bool(c["unique_key"].is_monotonic_increasing))
check("site columns = SITES_DTYPES without the two later columns; n_episodes int32",
      list(s.columns) == [k for k in m.SITES_DTYPES
                          if k not in ("n_times_treatment", "n_times_control")]
      and str(s.n_episodes.dtype) == "int32")

P2 = run(p, until="episodes")
check("determinism: episodes, episode_id and sites identical in two builds",
      digest(e) == digest(P2["e"]) and bool((c.episode_id == P2["c"].episode_id).all())
      and digest(s) == digest(P2["s"]))

Pp = run(dataclasses.replace(p, placebo_shift_days=90), until="episodes")
check("placebo: episodes identical (real dates, C4)",
      digest(Pp["e"]) == digest(e) and bool((Pp["c"].episode_id == c.episode_id).all()))

artifact = s.is_artifact.to_numpy()[c.site_idx.to_numpy()]
check("artifact complaints are single-complaint episodes (A7)",
      bool((e.n_complaints.to_numpy()[c.episode_id.to_numpy()[artifact]] == 1).all()),
      f"{int(artifact.sum()):,} artifact complaints")
check("exactly one first complaint per episode", int(c.is_first_of_episode.sum()) == len(e))


def results(complaints, episodes, sites, episode_links=links):
    return (m._check_h3(complaints, episodes, sites),
            m._check_h4(p, complaints, sites, episode_links))


r3, r4 = results(c, e, s)
check("H3 and H4 pass on the canonical episodes", r3.passed and r4.passed,
      f"H3 {r3.n_violations}, H4 {r4.n_violations}; {len(links):,} links")


def negative(label, expect_h3_fail, expect_h4_fail, **kw):
    args = dict(complaints=c, episodes=e, sites=s)
    args.update(kw)
    r3, r4 = results(**args)
    ok = ((not r3.passed) == expect_h3_fail) and ((not r4.passed) == expect_h4_fail)
    detail = (f"H3 {'fail' if not r3.passed else 'pass'}, H4 {'fail' if not r4.passed else 'pass'}; "
              f"{(r3.examples or r4.examples or [''])[0][:80]}")
    check(f"negative: {label}", ok, detail)


multi = int(e.index[e.n_complaints >= 2][0])
members = np.flatnonzero(c.episode_id.to_numpy() == multi)

cc = c.copy()
cc.loc[members[0], "is_first_of_episode"] = False
cc.loc[members[1], "is_first_of_episode"] = True
negative("first flag moved to another member (H3)", True, False, complaints=cc)

cc = c.copy()
cc.loc[members[-1], "episode_id"] = len(e) - 1
negative("member moved to another episode (H3 and H4)", True, True, complaints=cc)

ee = e.copy()
ee.loc[5, "end_s"] += 1
negative("episode end_s + 1 (H3)", True, False, episodes=ee)

ss = s.copy()
ss.loc[0, "n_episodes"] += 1
negative("site n_episodes + 1 (H3)", True, False, sites=ss)

cc = c.copy()
cc.loc[members[-1], "episode_id"] = len(e)
r4 = m._check_h4(p, cc, s, links)
check("negative: episode split, new id for one member (H4)", not r4.passed,
      (r4.examples or [""])[0][:80])

r4 = m._check_h4(p, c, s, links[:-1])
check("negative: one link dropped (H4)", not r4.passed, (r4.examples or [""])[0][:80])

# F6: first-of-episode count among the Stage 3 treatments (before the
# coverage, artifact and S-4 rules).
first = c["is_first_of_episode"].to_numpy()[P["t"]["complaint_idx"].to_numpy()]
check("F6: Stage 3 treatments that are first of their episode = 88,388",
      int(first.sum()) == 88_388, f"{int(first.sum()):,} of {len(P['t']):,}")

finish()
