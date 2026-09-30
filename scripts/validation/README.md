# Issue 4 validation scripts

Written for: anyone re-running the Issue 4 checks on Stages 7–10.

These scripts are the validation code used during the Issue 4 review, kept in the repository and rewritten to run against the current modules. They are independent cross-checks: brute-force recomputations, negative tests and variant runs.

They do **not** replace the hard checks H0–H18 that Stage 7 runs on every execution. The canonical Stage 7 run stops on any hard-check failure regardless of these scripts.

## How to run

- Run from the **repository root**, with the project virtual environment:

  ```
  .venv/Scripts/python.exe scripts/validation/<script>.py [arguments]
  ```

  On Linux or macOS, use `.venv/bin/python`.
- Every script prints one `PASS:` or `FAIL:` line per check, and ends with **`FAILS: none`** when everything passes.
- A failing script ends with `FAILS: [...]` and exits with a non-zero code (the Stage 7 commit scripts exit 1).

**Dependencies:** the project virtual environment (`requirements.txt`). The tested versions are in the top-level [README](../../README.md#environment). No package beyond the pipeline's own is needed. The scripts use numpy, pandas, pyarrow, pyproj, h3, scipy and statsmodels.

**Inputs:**
- `data/raw/streetlight_complaints.csv`
- `data/processed/clean_streetlights.parquet`
- `data/processed/clean_crime.parquet`
- The Stage 8–10 scripts also need a Stage 7 or Stage 8 output passed on the command line.

**Nothing is written to `data/processed/` or `outputs/`:**
- The Stage 7 commit scripts work in memory.
- `commit12_validate.py` and the Stage 8–10 scripts write only to fresh temporary directories.
- The Stage 9 and 10 scripts always pass a temporary `--out`, so the legacy files in `outputs/` are never overwritten.

## Scripts

Runtimes were measured on the development machine (Windows 11, Python 3.14.4) on 2026-09-30, one script at a time. The Commit 12 and Stage 8–10 figures are the estimates in each script's docstring.

| Script | What it checks | Arguments | Runtime | Status |
|---|---|---|---|---|
| `commit04_validate.py` | Sites, the artifact rule, H18 and its negative tests, `_modal_label` | none | about 10 s | Core |
| `commit05_validate.py` | Episodes, H3/H4 and their negative tests, the F6 first-of-episode count (88,388) | none | about 15 s | Core |
| `commit06_validate.py` | Spatial indexes and A13: dirty flags and crime counts against brute force, boundary cases, guards, determinism | none | about 1 min | Core |
| `commit07_validate.py` | Treatment windows and rules 1–8: a 2,000-treatment brute force (canonical and placebo), placebo S-4 (C4), synthetic S-4 boundaries | none | about 1 min | Core |
| `commit08_validate.py` | Baselines against a 2,000-treatment brute force (canonical and placebo), sample SD, z without centring, H15 negatives | none | about 2.5 min | Optional (long) |
| `commit09_validate.py` | Matching: pair invariants, an H9 sweep, reason codes, a 150-treatment brute-force candidate set, an independent greedy replay, determinism, variants (seeds 101/202, never, pre_only, caliper 0.2, placebo 90) | none | about 6 min | Optional (long) |
| `commit10_validate.py` | Pair assembly against independent sources, H5–H9/H12/H14 and 16 one-mutation negative tests, variants, H9 endpoints, the `d91f1f8` unresolved-key behaviour | none | about 2 min | Optional (long) |
| `commit11_validate.py` | Balance `bal_*` and contamination D1–D4 against a 300-pair brute force, SMD/VR formulas, H12 phase 2 for pre_only, placebo 90 and exclusion 500 | none | about 4 min | Optional (long) |
| `commit12_validate.py` | H10/H11 plus negative tests, H13, H16_post, strict JSON, determinism across processes, schemas, failure diagnostics, legacy pairs backup | none | about 6–8 min | Optional (long) |
| `stage8_validate.py` | Stage 8 panel: pre counts equal Stage 7 `bal_*_pre_*`, brute-force recount, structure, shifted pre-window, placebo, backup, old-schema rejection | `PAIRS_DIR [PLACEBO_PAIRS_DIR]` | about 1–2 min | Needs Stage 7 output |
| `stage9_validate.py` | Stage 9: FE = LSDV coefficients, paired mean = FE, CR1 SE of the paired mean, dataset statistics, reproducibility | `PANEL [PAIRS_FILE] [EXTRA_PANEL ...]` | about 2–3 min | Needs a Stage 8 panel |
| `stage10_validate.py` | Stage 10: units and geometry, brute-force weekly counts, FE = LSDV coefficients, clusters and weeks, byte reproducibility, old-schema rejection | `PANEL [EXTRA_PANEL ...]` | about 1–2 min | Needs a Stage 8 panel |

**What "Status" means:**
- **Core:** quick; run after any change to Stage 7.
- **Optional (long):** brute-force and variant runs that take several minutes each. Run them for acceptance or after changing the step they cover.
- **Needs … output:** run against the files a Stage 7 or Stage 8 run produced.

**Shared helper:** [_pipeline.py](_pipeline.py) runs the Stage 7 steps in `main()`'s order (up to a chosen step) with their output suppressed, and returns every intermediate table. It also provides `check()` and `finish()`. It is used by the `commit04`–`commit11` scripts. `commit12_validate.py` and the Stage 8–10 scripts predate it and are self-contained.

## Expected outputs

Each script's last line must be `FAILS: none`. The outputs of the runs recorded so far are in:
- [docs/evidence/stage7_commits/](../../docs/evidence/stage7_commits/): `commit04`–`commit11`, run at `57f4cb4` with the Phase 8 scripts.
- [docs/evidence/commit12/](../../docs/evidence/commit12/): `commit12_validate.out`.
- [docs/evidence/stage8/](../../docs/evidence/stage8/), [stage9/](../../docs/evidence/stage9/) and [stage10/](../../docs/evidence/stage10/).

**Canonical reference values** that the Stage 7 scripts assert or print (the inputs in `data/` as of 2026-09-30):

| Value | Script |
|---|---|
| 46,034 sites; artifact threshold T = 44 with 44 artifact sites | commit04 |
| 701,062 episode links; 88,388 first-of-episode Stage 3 treatments | commit05 |
| sd_100 = 1.104199, sd_250 = 1.197278 | commit08 |
| 20,051 pairs; seeds 101 / 202 give 20,011 / 19,996 pairs | commit09 |
| 935 treatment–treatment overlaps at 876 sites | commit10 |
| exclusion 500 gives 7,419 pairs, contamination D2 = 0.000 and D1 = 0.320 | commit11 |

New input data would change these values. A script that asserts one of them (commit04, commit05, commit10) would then fail, which is intended.

## Not included

- **The drafting files used while writing Stage 7** (`c4_build_sites.py`, `c5_build_episodes.py`, `c6_builder.py`, `c6_helpers.py`, `c7_steps.py`, `c8_steps.py`, `c8_h15.py`, `commit2_block.py`, `new_block.py`, `c11_wire.py`, `fixup_hashes.py`, `c9_selfsite.py`). They are drafts of module code or one-off explorations, not validators.
- **Commits 1–3** (parameters and H0, loaders and H1, darkness and H2/H17). Their validation was done with command-line runs and inline checks, and was not part of the Phase 8 list.
