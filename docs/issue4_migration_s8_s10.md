# Issue 4: Stage 8–10 migration note

Written for: reviewers of the Stage 8–10 changes. It lists every assumption the old code made about the Stage 7 output and what replaces it.

- **Baseline:** code at `a167ab6` (Stage 7 Commit 12 complete; Stages 8–10 unchanged since Issue 1–3).
- **Decision sources:** approved design §7 and §9–§11 (Steps 2–4), decisions D13, D14, D15 and D20, and the Q5 approval that Stage 7 pair columns are the authoritative geometry source.

## Why the migration is needed

The new Stage 7 writes `control_area_pairs.parquet` with a different unit of analysis:

- **Old pairs file:** a pair was (treatment outage, control outage).
  - `control_key` was another 311 `unique_key` (an integer).
  - Geometry and H3 cells came from joining both keys to the Stage 3 outage table.
- **New pairs file:** a pair is (treatment outage, control **site**).
  - The control is a rounded 1 m location (`control_site_id = "E{x}_N{y}"`, a string). It is not an outage, so it has no Stage 3 row.
  - Both units carry their geometry, labels and H3 cells as explicit columns.
  - `pair_id = "{treatment_key}_{control_site_id}"` is built by Stage 7.
  - A control site can serve several pairs at non-overlapping times (D12).

## Stage 8: `src/features/build_causal_panel.py`

| # | Old assumption (location in the old file) | Replacement | Source |
|---|---|---|---|
| S8-1 | Controls are outages: `control_lookup` renames `unique_key` to `control_key` and merges on it (L119, L136). | No outage-table lookup for either role. Unit geometry, borough, precinct and H3 cells come from the pair columns (`treatment_*`, `control_*`). | §9 "control lookup"; Q5 (treatment side too, a documented deviation from §9) |
| S8-2 | Treatment geometry comes from the Stage 3 `geometry`, projected with geopandas `to_crs` (L77). | `treatment_x_m/y_m`, the complaint's exact point in EPSG:32118 as written by Stage 7. H1 guarantees it agrees with Stage 3 within 1e-6°. | Q5 |
| S8-3 | Crime points come from `geometry.to_crs("EPSG:32118")`. | `latitude`/`longitude`, projected with `pyproj.Transformer(..., always_xy=True)`, the same path as Stage 7. Rows with a null datetime, latitude or longitude are dropped (the Stage 7 rule). | consistency with A13/R3 |
| S8-4 | `pair_id = f"{int(treatment_key)}_{int(control_key)}"` (L400–402). | `pair_id` is read from the pairs file (a string). No `int()` casts. | §7, §9 |
| S8-5 | `location_key = int(treatment_key)` or `int(control_key)` (L423, L447). | `location_key = treatment_site_id` or `control_site_id` (strings). | §7, §9 |
| S8-6 | A location identifies one panel unit. | New `unit_id = f"{pair_id}_{role}"` with role `T` or `C`. A reused control site gives one unit per pair. | §7; D15 |
| S8-7 | H3 from `h3_res9`/`h3_res10` (treatment) and `h3_res9_right`/`h3_res10_right` (control) left over from the Stage 6 spatial join (L438–439, L464–465). | Per row: the role's `*_h3_res9`/`*_h3_res10` site cells (U1). New pair-level columns `treatment_h3_res7` and `control_h3_res7` support clustering (D13). | §9; U1; D13 |
| S8-8 | No unit coordinates in the panel. | `location_latitude`/`location_longitude` (§7), plus `location_x_m`/`location_y_m`, so Stage 10 needs no reprojection. | §7, §11 |
| S8-9 | No matching baselines in the panel. | `base_100m`, `base_250m` per unit, from `treatment_base_*`/`control_base_*`. | §7 |
| S8-10 | Precinct not carried. | `treatment_police_precinct` (pair level) for the precinct clustering robustness check. | D13 |
| S8-11 | The D3 coverage guard may drop pairs. | Kept, and it must now drop 0 (Stage 7 enforces coverage). Any drop stops the run. | §9; Step 2 asserts |
| S8-12 | Asserts only check period counts. | Added: every pair's explicit distance is ≥ `MATCH_BAND_MIN_M` − 1e-6; each `unit_id` has exactly 3 rows; `pair_id` is unique in the pairs file. | Step 2 asserts |
| S8-13 | Script-level code with fixed paths; overwrites `causal_panel.parquet` in place. | Functions, `main()` and argparse: `--pairs`, `--out`, `--pre-window {canonical, shifted}` (shifted = [c − 21 d, c − 7 d), D19). Atomic write (temporary file, then `os.replace`). An existing old-schema panel (no `unit_id`) is first copied to `causal_panel.pre_issue4.parquet`, sha256-verified and never overwritten. | Part 3 Step 2; Q9 |
| S8-14 | KD query at exactly r = 250, then the explicit formula. | KD query at r + 1e-6 m generates candidates; the explicit formula decides (A13 convention). Only a point within 1e-6 m of the 250 m boundary could change. | A13 |

| S8-15 | (new) | With `--pre-window shifted`, `baseline_crime_intensity` follows the shifted pre-window on every row, because it is defined as the unit's pre-window 100 m count. The during and post rows are otherwise identical to the canonical panel (checked by `stage8_validate.py`). | definition kept (M8) |

**Unchanged:**
- **Windows:** pre `[c − 14 d, c]`, during `[c, closed]`, post `[closed, closed + 14 d]`, all inclusive.
- **Outcomes:** `crime_100m` (d ≤ 100) and `crime_250m` (100 < d ≤ 250, the ring).
- **Other columns:** `baseline_crime_intensity` (the unit's pre-window 100 m count, a known bad control, deferred as M8), and `outage_duration_hours` (the treatment's duration on both rows).

## Stage 9: `src/models/did_model.py`

| # | Old assumption | Replacement | Source |
|---|---|---|---|
| S9-1 | Cluster on `location_key` (L94), whose numeric values the old panel treated as outage keys. | Primary: `treatment_h3_res7`, the H3 res-7 cell of the treatment site, on both rows of a pair. | D13 |
| S9-2 | No robustness clustering. | For basic OLS and FE DiD: `pair_id`; two-way (`treatment_h3_res7`, `control_h3_res7`); treatment police precinct. Cluster counts reported for every scheme. The primary scheme must have ≥ 50 clusters, otherwise the run stops. | D13; design review C |
| S9-3 | The within-transformation groups by `location_key` (L207). A reused control site shared one FE across unrelated pairs. | Group by `unit_id`. Every unit has exactly 3 periods and constant treatment status. | D15 |
| S9-4 | Dataset stats: rows, pairs, `location_key` count, cluster label. | Adds units, distinct treatment and control sites, control reuse (mean, max), and clusters per scheme. | §10 |
| S9-5 | No design-based estimate. | D20: paired-difference estimate. Per pair, (T_during − T_pre) − (C_during − C_pre), and the same with post, for both outcomes. It is the mean with cluster-robust SE (primary clusters). Also by tercile of the treatment's log1p(`base_100m`) and by created year. In a balanced 1:1 panel it equals the two-way FE DiD coefficient, which the run checks as an internal consistency test. | D20 |
| S9-6 | Fixed paths; writes into `outputs/`. | `--panel`, `--out` (directory). Default output is `outputs/`; verification runs use a scratch directory, so no provisional result is published (M8 is open). | Part 3 Step 3; user instruction 3 |

**Unchanged:** the model specifications (basic OLS, separate during/post, FE, Poisson, NB2), the outcomes, and `baseline_crime_intensity` as a covariate (M8, deferred).

**Statistical implications (observed on the canonical scratch run, 20,051 pairs):**
- **Clustering is much coarser.** Primary clustering moves from `location_key`, about 100k clusters in the legacy run, where a reused control site counted as a cluster, to 193 treatment H3 res-7 cells.
  - Robustness schemes: 20,051 pairs; two-way 193 × 195 cells; 78 precincts.
  - Standard errors are expected to be larger, and to account for spatial correlation between nearby pairs that the old clustering ignored.
  - Legacy and Issue 4 standard errors are not comparable.
- **FE on `unit_id`.** A reused control site no longer shares one fixed effect across unrelated pairs. Every FE unit is balanced (3 periods), as the within-transformation assumes. The FE coefficients now equal the paired-difference means exactly (the run asserts this).
- **NB2 uses a fixed α = 1.0.** statsmodels emits a `ValueWarning`. This is pre-existing (the old code made the same call). It is recorded as a limitation, not changed, because the model specifications are frozen.
- **Tercile edges.** The D20 terciles use quantile edges of log1p(treatment `base_100m`), and ties go to the lower tercile. Group sizes are therefore unequal (canonical: 6,845 / 6,911 / 6,295).

## Stage 10: `src/models/event_study.py`

| # | Old assumption | Replacement | Source |
|---|---|---|---|
| S10-1 | Two copies of the module: L1–35 is a stray header with the wrong project root (`parents[1]`); L36–524 is the working copy; L526–1014 is byte-identical to L36–524 and never runs. | Delete L1–35 and L526–1014 in a commit of their own (no behaviour change). | D14 |
| S10-2 | Event units get geometry by merging `location_key` with the Stage 3 outage table (L108–121). This raises `ValueError` for controls, which are not outages. | Units come from the panel's `unit_id` and `location_x_m/y_m` (plus lat/lon); no outage table. | §11; S8-8 |
| S10-3 | Crime projected with geopandas `to_crs`; KD query at exactly 100 m. | pyproj Transformer from lat/lon (as Stage 7 and 8). KD query at 100 + 1e-6 m, then the explicit formula. | A13 |
| S10-4 | FE and clustering on `location_key`. | FE on `unit_id`; clustering on `treatment_h3_res7`. | §11; D13, D15 |
| S10-5 | Documentation says weeks −5…+5. | Weeks −4…+4, reference week −1 = [−14, −7) days. The −7…0 day gap is **flagged, not fixed**, as approved. | review F5; §11 |
| S10-6 | Fixed paths. | `--panel`, `--out` (directory); verification runs use a scratch directory. | as S9-6 |

**Unchanged:** week windows, the complete-coverage filter (c ± 35 d), outcome radius 100 m, reference week, and the pre-trend F-test.

## Out of scope (recorded as open)

- Step 5 `robustness.py` (the D18/D19 grid through S7→S8→S9).
- Full realignment of notebooks 05–08 (only stale banners, Phase 7).
- M8 defects: S8 exposure normalisation, `baseline_crime_intensity`, the −7…0 gap.
