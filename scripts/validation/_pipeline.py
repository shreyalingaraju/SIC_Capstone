"""
Shared helper for the Stage 7 validation scripts (commit04 ... commit11).

Runs the Stage 7 steps of src/features/match_controls.py in the same
order as main(), with their printing suppressed, and returns every
intermediate table so a script can check it. No hard checks are run
and nothing is written to disk: the scripts call the check functions
themselves and compare against independent recomputations.

Two differences from main(), both needed by the checks:
- the band candidate lists (indexes.band_*) are kept after matching;
- the crime table is returned both with latitude/longitude ("crime_raw",
  as load_crime returns it) and without ("crime", as main() passes it to
  the baseline and balance steps).

Run the scripts from the repository root with the project venv; the
Stage 7 input paths are relative to it.
"""

import contextlib
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

if Path.cwd().resolve() != ROOT:
    raise SystemExit(f"run the validation scripts from the repository root ({ROOT})")

sys.path.insert(0, str(ROOT / "src" / "features"))

import match_controls as m  # noqa: E402

STEPS = ("sites", "episodes", "indexes", "rules", "baselines", "matching",
         "pairs", "balance")

FAILS = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}: {name}{' - ' + detail if detail else ''}",
          flush=True)
    if not ok:
        FAILS.append(name)


def finish():
    """Print the summary line and exit 1 if any check failed."""

    print("\nFAILS:", FAILS or "none")
    sys.exit(1 if FAILS else 0)


def run(params, until="balance"):
    """
    Stage 7 steps 1-16 up to and including `until` (one of STEPS).

    Keys: c (complaints), raw_summary, t (treatments_all), n_s3_rows,
    crime_raw, crime, cov, s (sites), universe, e (episodes), links,
    es (episode summary), idx, el (treatments_eligible), rejected,
    attrition, baseline_summary, sc (scales), reg (registry), band,
    match, match_summary, pairs, bal, con.
    """

    if until not in STEPS:
        raise ValueError(f"until must be one of {STEPS}")
    last = STEPS.index(until)
    P = {}

    with contextlib.redirect_stdout(io.StringIO()):
        P["c"], P["raw_summary"] = m.load_raw_complaints(params)
        P["t"], P["n_s3_rows"] = m.load_treatments(params, P["c"])
        P["crime_raw"], P["cov"], _ = m.load_crime(params, P["raw_summary"])
        P["t"] = m._finalise_treatments(P["t"])
        P["c"], P["cov"], _ = m.build_darkness_intervals(params, P["c"], P["cov"])
        P["s"], P["c"], P["universe"] = m.build_sites(params, P["c"])
        if last < STEPS.index("episodes"):
            return P

        P["e"], P["c"], P["s"], P["links"], P["es"] = m.build_episodes(
            params, P["c"], P["s"]
        )
        if last < STEPS.index("indexes"):
            return P

        P["idx"], _ = m.build_spatial_indexes(params, P["c"], P["s"], P["crime_raw"])
        P["crime"] = P["crime_raw"].drop(columns=["latitude", "longitude"])
        if last < STEPS.index("rules"):
            return P

        P["t"] = m.compute_windows(params, P["t"])
        P["t"], P["el"], P["rejected"], P["attrition"] = m.apply_treatment_rules(
            params, P["t"], P["c"], P["s"], P["idx"], P["cov"]
        )
        if last < STEPS.index("baselines"):
            return P

        P["el"], P["baseline_summary"] = m.compute_treatment_baselines(
            params, P["el"], P["idx"], P["crime"]
        )
        P["el"], P["sc"] = m.standardise(params, P["el"])
        if last < STEPS.index("matching"):
            return P

        P["reg"] = m.init_reuse_registry(params, P["el"])
        P["idx"], P["band"] = m.build_band_candidates(params, P["el"], P["s"], P["idx"])
        P["match"], P["match_summary"] = m.match_treatments(
            params, P["el"], P["s"], P["idx"], P["sc"], P["reg"]
        )
        if last < STEPS.index("pairs"):
            return P

        P["pairs"] = m.assemble_pairs(params, P["match"], P["el"], P["s"], P["reg"])
        if last < STEPS.index("balance"):
            return P

        P["pairs"], P["bal"] = m.compute_balance(
            params, P["pairs"], P["el"], P["e"], P["c"], P["crime"], P["idx"]
        )
        P["con"] = m.compute_contamination(
            params, P["pairs"], P["c"], P["e"], P["idx"], P["es"]
        )

    return P
