"""
Stage 7 Commit 4 validation: sites, the artifact rule and H18.

Usage, from the repository root with the project venv:
    python scripts/validation/commit04_validate.py

Source: the in-memory tests run for Commit 4 (4007672), rewritten to use
the current module through _pipeline.py. Nothing is written to disk.
Runtime about 1 minute. Expected last line: "FAILS: none".

Checks:
- step 5 keeps the complaint order; the site table has the SITES_DTYPES
  columns (without the three filled by later steps);
- determinism (two builds) and placebo invariance (sites use real dates);
- H18 passes on the canonical sites;
- H18 negative tests: a site at T flagged as artifact, an artifact site
  unflagged, n_complaints + 1, a complaint moved to another site, a
  no-borough site marked eligible; each must fail H18;
- _modal_label: tie broken alphabetically, all-missing site -> <NA>.
"""

import dataclasses
import hashlib

import numpy as np
import pandas as pd

from _pipeline import check, finish, m, run

LATER_SITE_COLUMNS = ("n_episodes", "n_times_treatment", "n_times_control")


def digest(frame):
    return hashlib.sha256(
        pd.util.hash_pandas_object(frame, index=True).values.tobytes()
    ).hexdigest()[:16]


p = m.parse_args([])

# Step 1 sorts complaints by unique_key, so step 5 kept the order if it
# is still sorted.
P = run(p, until="sites")
c, s, universe = P["c"], P["s"], P["universe"]

check("complaint order unchanged by step 5 (still unique_key order)",
      bool(c["unique_key"].is_monotonic_increasing))
check("site columns = SITES_DTYPES without the later columns",
      list(s.columns) == [k for k in m.SITES_DTYPES if k not in LATER_SITE_COLUMNS])

P2 = run(p, until="sites")
check("determinism: two builds give identical sites and site_idx",
      digest(s) == digest(P2["s"]) and bool((c.site_idx == P2["c"].site_idx).all()),
      digest(s))

Pp = run(dataclasses.replace(p, placebo_shift_days=90), until="sites")
check("placebo leaves the sites unchanged (real dates, C4)", digest(Pp["s"]) == digest(s))


def h18(complaints, sites):
    result = m._check_h18(p, complaints, sites)
    return result.passed, result.n_violations, result.examples


ok, n, ex = h18(c, s)
check("H18 passes on the canonical sites", ok, f"violations {n}")
check("artifact threshold and count (canonical T = 44, 44 sites)",
      universe["artifact_threshold"] == 44 and universe["n_artifact_sites"] == 44,
      f"T {universe['artifact_threshold']}, {universe['n_artifact_sites']} artifact sites")

T = universe["artifact_threshold"]


def negative(label, complaints, sites):
    ok, n, ex = h18(complaints, sites)
    check(f"negative H18: {label}", not ok and n > 0, f"{n} violations; {ex[0][:90] if ex else ''}")


b = s.copy()
i = int(np.flatnonzero(b.n_complaints.to_numpy() == T)[0])
b.loc[i, "is_artifact"] = True
b.loc[i, "eligible_control"] = False
negative("a site at T flagged as artifact (ties are not artifacts)", c, b)

b = s.copy()
i = int(np.flatnonzero(b.is_artifact.to_numpy())[0])
b.loc[i, "is_artifact"] = False
negative("an artifact site unflagged", c, b)

b = s.copy()
b.loc[3, "n_complaints"] += 1
negative("n_complaints + 1", c, b)

cc = c.copy()
cc.loc[0, "site_idx"] = (cc.loc[0, "site_idx"] + 1) % len(s)
negative("a complaint moved to another site", cc, s)

b = s.copy()
j = int(np.flatnonzero(b.borough.isna().to_numpy())[0])
b.loc[j, "eligible_control"] = True
negative("a no-borough site marked eligible", c, b)

# Modal label: a 1-1 tie, an all-missing site, a 2-1 majority, an all-missing site.
labels = pd.Series(["QUEENS", "BROOKLYN", pd.NA, "BRONX", "BRONX", "QUEENS", pd.NA],
                   dtype="string")
mode, conflicts = m._modal_label(np.array([0, 0, 1, 2, 2, 2, 3]), labels, 4)
got = [None if pd.isna(v) else v for v in mode.tolist()]
check("_modal_label: alphabetical tie-break, all-missing -> <NA>, conflicts counted",
      got == ["BROOKLYN", None, "BRONX", None] and conflicts == 2,
      f"{got}, conflicts {conflicts}")

finish()
