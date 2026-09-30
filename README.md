# LightSafe

Streetlight outages and night-time crime in New York City: a causal pipeline that matches each reported streetlight outage to a nearby location that was not dark, and compares crime before, during and after the outage.

## Project status (2026-09-30)

> **Results are not final. Do not treat any Stage 9 or Stage 10 estimate as a result until Phase 9 acceptance is complete, and not before the M8 limitations are resolved or disclosed.**

- **Issue 4** (control redesign) has migrated the Stage 7–10 implementation, on branch `issue4-step1`:
  - Stage 7 is rewritten.
  - Stages 8–10 are migrated to the new pair schema.
  - An independent review of the Stage 8–10 migration found no blocking issues.
- **Phase 6 (documentation)** is complete: this README and [docs/issue4_design.md](docs/issue4_design.md), [docs/issue4_decisions.md](docs/issue4_decisions.md) and [docs/issue4_acceptance.md](docs/issue4_acceptance.md).
- **The Phase 9 acceptance runs are done; the acceptance decision is pending review.** Results are in [docs/acceptance_report.md](docs/acceptance_report.md).
  - The canonical Stage 7 and Stage 8 outputs are now in `data/processed/`.
  - Stages 9 and 10 were run only into the git-ignored `outputs/robustness/`, so `outputs/` itself is unchanged.
- **Legacy results** in `outputs/` are pre-Issue-4 and are **not** current results (see [Stale and legacy results](#stale-and-legacy-results)).
- Nothing on `issue4-step1` has been pushed or merged to `main`.

## Pipeline

Run every stage from the repository root: Stages 1–7 use paths relative to it.

| Stage | Command | What it does |
|---|---|---|
| S1–S2 | `python src/data/download_data.py [--dataset {crime,streetlights,all}]` | Downloads NYPD complaints (from 2019-11-01) and/or the 311 "Street Light Out" complaints from NYC Open Data into `data/raw/`. The default is `crime`: streetlights are refreshed only when asked. Optional env var `SODA_APP_TOKEN`. |
| S3 | `python src/data/clean_streetlights.py` | Treatment outages: "Street Light Out" complaints with created and closed dates, a duration of 0.5–8,760 h, inside the NYC bounding box → `data/processed/clean_streetlights.parquet` |
| S4 | none | No S4 module exists in this repository. The stage map lists S1–S2 as one module. |
| S5 | `python src/data/clean_crime.py` | Night crimes (hour ≥ 18 or ≤ 6) in the project offence categories (by NYPD `ky_cd`; motor-vehicle larceny excluded) inside NYC → `data/processed/clean_crime.parquet` |
| S6 | `python src/features/spatial_linking.py` | Crimes around each outage during the outage → `outage_crime_linked.parquet`. **Descriptive only**: Stage 7 no longer uses it. |
| S7 | `python src/features/match_controls.py` | Control matching (below) |
| S8 | `python src/features/build_causal_panel.py` | Three-period causal panel (below) |
| S9 | `python src/models/did_model.py --out <dir>` | Difference-in-differences models (below) |
| S10 | `python src/models/event_study.py --out <dir>` | Event study (below) |

**Dependencies:** S7 reads the raw 311 file, S3 and S5. S8 reads S7 and S5. S9 reads S8. S10 reads S8 and S5.

The design, every definition and every decision are in [docs/issue4_design.md](docs/issue4_design.md).

### Stage 7: control matching

Each eligible treatment outage is matched 1:1 to a control *site*: a distinct geocoded 311 streetlight location that is not reported dark during the treatment's analysis window. The site lies 500–1,500 m away in the same borough and has similar baseline night crime.

**Inputs:**
- `data/raw/streetlight_complaints.csv` (darkness universe)
- `data/processed/clean_streetlights.parquet` (treatments)
- `data/processed/clean_crime.parquet` (baselines)

**Outputs** (in `--out-dir`, default `data/processed/`):
- `control_area_pairs.parquet`: one row per pair
- `outage_sites.parquet`: site table
- `unmatched_treatments.parquet`: one row per unmatched treatment, with a reason code
- `match_diagnostics.json`: provenance, parameters, attrition, balance, contamination, hard checks, recheck, determinism, runtime

**CLI.** Only the approved D19 sensitivity parameters can be changed; every other methodology value is locked.

```
python src/features/match_controls.py
    [--report-lag-days N] [--imputed-duration-hours H]
    [--exclusion-radius-m M] [--match-band-max-m M]
    [--caliper-sd X] [--reuse-policy {non_overlapping,never}]
    [--control-selection {full_window,pre_only}] [--match-seed N | --seed N]
    [--placebo-shift-days N]
    [--out-dir DIR] [--skip-recheck] [--check-determinism]
```

**Output directory rules:**
- Any non-default parameter requires an `--out-dir` other than `data/processed`, so sensitivity and placebo runs never overwrite the canonical outputs.
- `--skip-recheck` is refused for the canonical directory.
- Files are written to temporary names, schema-checked, then renamed, with the diagnostics file last.
- A pre-Issue-4 `control_area_pairs.parquet` in the output directory is first copied to `control_area_pairs.pre_issue4.parquet` (sha256 verified; never overwritten).

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | Completed; all four files written |
| 1 | H0 parameter failure, hard-check failure, determinism failure, or unhandled exception (check stderr). `match_diagnostics.failed.json` is written for every stop the stage handles itself. |
| 2 | Command-line usage error (argparse) |
| 3 | Used only during the Issue 4 rollout ("Stage 7 incomplete; no outputs written"). **No longer returned.** |

**Hard checks H0–H18.** Every one stops the stage with exit 1:

| ID | Checks |
|---|---|
| H0 | Parameters |
| H1 | Input integrity |
| H2 | Darkness intervals |
| H3–H4 | Episodes |
| H5 | Treatment eligibility |
| H6 | Coverage |
| H7 | Distance band and borough |
| H8 | Caliper |
| H9 | Reuse |
| H10–H11 | Independent recheck |
| H12 | Pair validity |
| H13 | Accounting |
| H14 | Candidate counts |
| H15 | Standardisation |
| H16 | Placebo dates; recorded after matching as `H16_post` |
| H17 | Stage 3 valid-closure contract |
| H18 | Site and artifact integrity |

Definitions: [docs/issue4_design.md §3.11](docs/issue4_design.md).

**Independent recheck (H10/H11).**
- Covers min(2,000, pairs) pairs drawn with a fixed seed.
- Darkness, sites, artifact flags, own episodes, windows and baseline counts are rebuilt from the raw files, reusing no production structure.
- Spatial indexes only generate candidates; membership is decided by the explicit distance formula.
- `--skip-recheck` records H10/H11 as `skipped`; it is allowed only outside `data/processed`.

**Determinism.**
- Every run records the sha256 of the sorted `pair_id` list and an order hash.
- `--check-determinism` re-runs the matching from scratch. Any difference stops the run with exit 1 before anything is written.

**Sensitivity and placebo.**
- The D19 values are exclusion radius 250/500, band max 2,000, caliper 0.2, `never` reuse, lag 0, imputed duration 50/720 h, `pre_only` controls, and seeds 101/202/303/404.
- `--placebo-shift-days 90` moves the treatment dates, windows and clean-rule windows back 90 days and re-matches. Darkness always uses the real dates.
- Sensitivity and placebo results are report-only.
- The grid runner (`robustness.py`) is not implemented (out of scope).

### Stage 8: causal panel

```
python src/features/build_causal_panel.py [--pairs PATH] [--crime PATH] [--out PATH] [--pre-window {canonical,shifted}]
```

- **Defaults:** `--pairs data/processed/control_area_pairs.parquet`; `--crime data/processed/clean_crime.parquet`; `--out data/processed/causal_panel.parquet`.
- **Rows:** 6 per pair (T and C × pre, during, post).
- **Identifiers:** `unit_id = pair_id + "_T"` or `"_C"`. Geometry, borough, precinct and H3 cells come from the Stage 7 pair columns; no outage-table lookup.
- **Windows** (inclusive): pre [c − 14 d, c], during [c, closed], post [closed, closed + 14 d]. Outcomes are crimes within 100 m and in the 100–250 m ring.
- **`--pre-window`:**
  - `canonical` (default): the pre-window above.
  - `shifted`: pre = [c − 21 d, c − 7 d) (D19 robustness). Only the pre rows change, and `baseline_crime_intensity` follows them.
- **Guards:** the crime-coverage guard must drop 0 pairs; every control must be ≥ 500 m from its treatment.
- **Writing:** the panel is written atomically. An existing pre-Issue-4 panel (no `unit_id`) is first backed up as `causal_panel.pre_issue4.parquet` (sha256 verified; never overwritten).

### Stage 9: difference-in-differences

```
python src/models/did_model.py [--panel PATH] [--out DIR]
```

- **Defaults:** `--panel data/processed/causal_panel.parquet`; `--out outputs/`.
- **Writes:** `did_summary.json` (strict JSON, marked provisional) and `did_regression_results.txt`.
- **Models** (specifications unchanged): basic OLS, separate during/post, two-way fixed effects by `unit_id`, Poisson and negative binomial.
- **Clustering:**
  - Standard errors are clustered on the treatment site's H3 res-7 cell.
  - Robustness clusterings for the OLS and FE models: pair, two-way treatment/control cell, and precinct.
  - The run stops if there are fewer than 50 primary clusters.
- **Paired differences:** also reported overall, by baseline tercile and by year.
- **Use a scratch `--out` for now.** The default `outputs/` holds the tracked legacy results, and Stage 9 overwrites them without a backup.

### Stage 10: event study

```
python src/models/event_study.py [--panel PATH] [--crime PATH] [--out DIR]
```

- **Defaults:** `--panel data/processed/causal_panel.parquet`; `--crime data/processed/clean_crime.parquet`; `--out outputs/`.
- **Writes:** `event_study_coefficients.csv`, `event_study_plot.png` (headless, byte-reproducible, titled "provisional") and `event_study_summary.json`.
- **Estimation:**
  - Units are the panel's `unit_id`s, with the panel's projected coordinates.
  - Weekly counts within 100 m for relative weeks −4…+4. Week −1 = days [−14, −7) is the reference.
  - FE by `unit_id` and week; SEs clustered on the treatment H3 res-7 cell; joint pre-trend F-test on weeks −4…−2.
- **Coverage:** only pairs whose ±35-day window lies inside the crime coverage are used.
- **The −7…0 day gap:** days [−7, 0) belong to no week. This is a known limitation, flagged and not fixed.
- **Use a scratch `--out` for now**, for the same reason as Stage 9.

### Validation scripts

In [scripts/validation/](scripts/validation/): `commit12_validate.py` (Stage 7), `stage8_validate.py`, `stage9_validate.py` and `stage10_validate.py`.
- Each script's docstring gives its usage and checks.
- They write to temporary directories, and the expected last line is `FAILS: none`.
- Their outputs are in [docs/evidence/](docs/evidence/).

## Environment

Tested on Windows 11 (`Windows-11-10.0.26200-SP0`), in the project virtual environment (`.venv`).

| Package | Version |
|---|---|
| Python | 3.14.4 |
| pandas | 3.0.6 |
| numpy | 2.5.3 |
| pyarrow | 25.0.1 |
| geopandas | 1.1.4 |
| shapely | 2.1.2 |
| pyproj | 3.8.0 |
| statsmodels | 0.15.0 |
| scipy | 1.18.1 |
| h3 | 4.5.0 |
| matplotlib | 3.11.2 |
| psutil | 7.2.2 |

- **Sources:** the installed versions in `.venv` (2026-09-30), and the Stage 7 diagnostics provenance of the Commit 12 scratch run (Python, numpy, pandas, scipy, geopandas, shapely, h3, pyproj, pyarrow, psutil: identical).
- **Pinning:** `requirements.txt` pins the full environment. It lists `requests` and `pyarrow` without a version.

## Limitations

Full descriptions and evidence are in [docs/issue4_design.md §9](docs/issue4_design.md).

**Methodological (M1–M8):**

| ID | Limitation |
|---|---|
| M1 | **The estimand changes.** Results describe first-reported, isolated outages in quieter areas: matched baseline 5.77 crimes vs 8.40 for all eligible treatments, and about 70% of Stage 3 treatments removed by the rules. |
| M2 | **Pre-period confounding by outage-proneness.** `prior_episodes_250m` is imbalanced (SMD +0.35; balance-only, reported). |
| M3 | **Interference between units.** 74–91% of controls are within 500 m of other concurrent pairs or residual darkness (contamination D1–D3). |
| M4 | **Selection on the future.** Controls must stay clean through the whole window and treatments after closure. Only the control side has a variant (`pre_only`). |
| M5 | **The placebo population differs** from the canonical one. It is a design-level placebo. |
| M6 | **Closure data quality depends on the year.** 2020–22 darkness relies heavily on imputed durations. |
| M7 | **"Provably not dark" means "not reported dark".** 36,188 complaints have no coordinates. |
| M8 | **Known defects outside Issue 4, disclosed before any publication:** Stage 8 exposure is not normalised; `baseline_crime_intensity` is the pre-period outcome (a bad control); the event-study weeks skip days −7…0. The `event_study.py` duplication is now fixed (D14). **Every Stage 9 and 10 result is provisional.** |

**Added after the Stage 8–10 migration:**
- The negative binomial model uses a fixed dispersion α = 1.0 (not estimated).
- Treatment outcome counts are centred on the complaint's exact point, and controls on the rounded site point. The treatment's site ID and H3 cells are its site's, so they can differ slightly from the complaint point.
- The windows share endpoints: a crime exactly at the created or closed time counts in two periods.
- The shifted pre-window also changes `baseline_crime_intensity`, and panels don't record which mode built them.
- Reused and dual-role sites span several primary clusters. 49.4% of pairs have the treatment and control in different res-7 cells.
- Stages 9 and 10 default to writing into `outputs/`, over the tracked legacy results.
- Outputs don't record a full provenance chain (pairs hash → panel hash → git commit).

## Stale and legacy results

- **`outputs/`** (`did_summary.json`, `did_regression_results.txt`, `event_study_coefficients.csv`, `event_study_plot.png`) holds **pre-Issue-4 results**.
  - They were produced with the old control design, in which every control was itself a treated outage.
  - They are **not current results**. They must not be cited or compared with Issue 4 standard errors.
  - They stay in place for now. Moving them to `outputs/legacy_pre_issue4/` is planned but not yet scheduled; it was not part of the Phase 7 scope.
- **`data/processed/`** (git-ignored) holds the Issue 4 canonical outputs, written in Phase 9. The pre-Issue-4 `control_area_pairs.parquet` and `causal_panel.parquet` are preserved there as `control_area_pairs.pre_issue4.parquet` and `causal_panel.pre_issue4.parquet` (sha256 verified; see [docs/acceptance_report.md §4](docs/acceptance_report.md)). Stages 9 and 10 reject the legacy panel because it has no `unit_id`.
- **Notebooks 05–08** show the old schema and stale outputs. Stale banners (Q7) are approved but not yet scheduled, and full realignment is deferred. The pipeline runs from `src/`, not from the notebooks.
