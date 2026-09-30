# LightSafe Issue 4: design and implementation record

Written for: capstone reviewers and anyone who will run, check or extend the Issue 4 pipeline. It assumes you know what a difference-in-differences panel is. It does not assume you followed the design discussion.

**Status (2026-09-30):**
- Stages 7–10 are implemented on branch `issue4-step1`.
- The Phase 3–5 independent review reported **no blocking issues**.
- The Phase 9 acceptance runs are done ([acceptance_report.md](acceptance_report.md)); the acceptance decision is pending review. Nothing here is a final result.
- Every Stage 9 and 10 estimate is provisional while M8 (§9) is open.

**How to read this document.** Each statement carries one of these labels:

| Label | Meaning |
|---|---|
| **[FROZEN]** | An approved design decision. It changes only through a new, explicit approval. |
| **[IMPL]** | An implementation detail that follows from frozen decisions, or that was approved as an implementation choice. |
| **[DEVIATION]** | Differs from the written design. The authorisation is stated, or it is marked as needing sign-off. |
| **[SIGN-OFF]** | Implemented, but never explicitly approved. It is not a frozen decision until someone approves it. (The S1–S7 items were approved on 2026-09-30; none remain.) |
| **[LIMITATION]** | A known weakness that is recorded, not fixed. |
| **[DEFERRED]** | Approved or identified work that has not been done. |

**Companion documents:**
- [issue4_decisions.md](issue4_decisions.md): dated decision log.
- [issue4_acceptance.md](issue4_acceptance.md): the 20-item Step 1 checklist and its evidence.
- [issue4_migration_s8_s10.md](issue4_migration_s8_s10.md): how Stages 8–10 moved from the old pair schema.
- [evidence/](evidence/): outputs of the validation scripts.

**ID collisions.** Several ID families are reused across the project:
- D1–D6 are the Issue 1–3 decisions. D1–D5 are also the Stage 7 contamination diagnostics, and D1–D3 are also the Phase 6 documentation requests.
- A1–A13 are Stage 7 conventions; A1–A3 are also acceptance amendments.
- M1–M4 are Commit 6 modifications; M1–M8 are known limitations.

This document always qualifies them, for example "contamination D2" or "convention A13".

---

## 1. Problem and goal

**The problem.** The pre-Issue-4 Stage 7 drew each control from *other outage records* 100–250 m away (design §0). So:
- every control was itself a treated unit;
- the outcome zones of the treatment and its control overlapped;
- controls could be dark during the pre/post windows;
- 80% of treatments had another outage within 250 m in the 35 days before them.

**[FROZEN] The goal** (design §0): each treatment outage gets one control location that is *provably not dark* during every window used downstream. "Not dark" means not reported dark; see M7. The two locations are geographically disjoint and matched on pre-period crime. The Stage 8 treatment windows and outcome definitions stay as they were.

---

## 2. Architecture

### 2.1 Stage map and data flow

| Stage | Module | Reads | Writes | Role after Issue 4 |
|---|---|---|---|---|
| S1–S2 | [src/data/download_data.py](../src/data/download_data.py) | NYC Open Data (Socrata) | `data/raw/nypd_crime.csv`, `data/raw/streetlight_complaints.csv` | Downloads. `--dataset {crime, streetlights, all}`, default `crime` (Issue 1–3 D4). The Issue 1–3 stage map lists this module as "S1–2" and does not split it further. |
| S3 | [src/data/clean_streetlights.py](../src/data/clean_streetlights.py) | raw 311 CSV | `data/processed/clean_streetlights.parquet` | Treatment outages: "Street Light Out" complaints with created and closed dates, 0.5 h ≤ duration ≤ 8,760 h, inside the NYC bounding box. 107,731 rows locally. |
| S4 | none | | | No S4 module, notebook or role exists in the repository or the approved record. **Needs verification** if the numbering is ever used. |
| S5 | [src/data/clean_crime.py](../src/data/clean_crime.py) | raw crime CSV | `data/processed/clean_crime.parquet` | Night crimes (hour ≥ 18 or ≤ 6) in the project offence categories (by `ky_cd`, motor-vehicle larceny excluded per Issue 1–3 D2) inside the NYC box. 921,427 rows. Coverage 2019-11-01 to 2026-06-30 23:40. |
| S6 | [src/features/spatial_linking.py](../src/features/spatial_linking.py) | S3, S5 | `data/processed/outage_crime_linked.parquet` | **Descriptive only.** Stage 7 no longer reads it (design §8). |
| **S7** | [src/features/match_controls.py](../src/features/match_controls.py) | raw 311 CSV, S3, S5 | `control_area_pairs.parquet`, `outage_sites.parquet`, `unmatched_treatments.parquet`, `match_diagnostics.json` (in `--out-dir`) | Control matching (§3). Rewritten in Issue 4. |
| **S8** | [src/features/build_causal_panel.py](../src/features/build_causal_panel.py) | S7 pairs, S5 | `data/processed/causal_panel.parquet` | Three-period panel (§4) |
| **S9** | [src/models/did_model.py](../src/models/did_model.py) | S8 panel | `did_summary.json`, `did_regression_results.txt` (in `--out`) | DiD models (§5) |
| **S10** | [src/models/event_study.py](../src/models/event_study.py) | S8 panel, S5 | `event_study_coefficients.csv`, `event_study_plot.png`, `event_study_summary.json` (in `--out`) | Event study (§6) |

```
raw 311 CSV ──► S3 clean_streetlights ──┐
      │                                 │
      └──────────────┐                  ▼
raw crime CSV ─► S5 clean_crime ─► S7 match_controls ─► S8 build_causal_panel ─► S9 did_model
                     │                                         │
                     └─────────────────────────────────────────┴──────────────► S10 event_study
(S6 spatial_linking reads S3 + S5 but feeds nothing downstream.)
```

**[FROZEN] Pipeline order** (design §8): Stage 7 depends on the raw 311 file, S3 and S5. The order S5 → S6 → S7 still works.

### 2.2 Stage 7's role

Stage 7 is the only place where the control universe, darkness, eligibility and matching are defined. Stages 8–10 trust its pair columns and do not re-derive geometry (Q5, §7.1).

**What Stage 7 guarantees** by the time it exits 0:
- Coverage: W and B lie inside the crime coverage, and W inside the darkness coverage (H6).
- Distance band and borough (H7).
- Caliper (H8).
- Reuse (H9).
- Clean windows and baselines, rechecked independently on a sample (H10, H11).

### 2.3 Downstream dependencies

| Consumer | Depends on | Failure if missing |
|---|---|---|
| S8 | The 29 pair columns in `PAIR_COLUMNS` ([build_causal_panel.py:78](../src/features/build_causal_panel.py#L78)) | `ValueError`: "is not an Issue 4 Stage 7 pairs file" |
| S9 | Panel columns in `REQUIRED_COLUMNS` ([did_model.py:69](../src/models/did_model.py#L69)); exactly 3 rows per `unit_id` | `ValueError` |
| S10 | Panel columns in `UNIT_COLUMNS` ([event_study.py:100](../src/models/event_study.py#L100)); unit geometry present | `ValueError` |
| Notebooks 05–08 | Old pair and panel schemas | Stale. Banners (Q7) are approved but not yet scheduled; full realignment is deferred (D16). |
| `dashboard/`, `paper/` | No references to the S7–S10 outputs were found (`git grep`) | none |

**[IMPL] Cross-stage contracts** are locked constants in Stage 7 and checked by H0: `DIRECT_RADIUS_M = 100`, `OUTCOME_RADIUS_M = 250`, `S8_PRE_WINDOW_DAYS = 14`, `POST_WINDOW_DAYS = 14`, `EVENT_WINDOW_DAYS = 35`, and the Stage 3 validity rule 0.5–8,760 h. Stages 8 and 10 repeat the same values as their own constants.
- If any of them changes in one stage, it must change in all of them.
- No automated cross-stage test enforces this; see Validation gaps in [issue4_acceptance.md](issue4_acceptance.md).

---

## 3. Stage 7 methodology

Source text: the approved design §1–§6, the Step 1 blueprint §1–§5, and the later decisions (see [issue4_decisions.md](issue4_decisions.md)). Where a later decision changed the design text, the final rule is given and the decision is named.

### 3.1 Control universe [FROZEN: D7 (modified), D17]

- **Site.** A distinct geocoded location in the raw 311 "Street Light Out" complaints, with projected coordinates rounded to 1 m in EPSG:32118 (`np.rint`, half to even).
  - `site_id = "E{x}_N{y}"` in whole metres.
  - Site latitude/longitude are converted back from the rounded coordinates.
  - Canonical: 46,034 sites.
- **Complaint universe** (E2): raw rows with non-null latitude and longitude inside `NYC_BBOX` = (40.49, 40.92, −74.26, −73.69), bounds inclusive. Canonical: 218,465 raw rows → 182,277 in the universe.
  - An unparseable `created_date` or a duplicate `unique_key` is an H1 violation, not an exclusion.
- **Darkness record from the raw 311 file**, not the Stage 3 output. It includes the 74,546 complaints Stage 3 discards.
- **Control observation** = (site, treatment window W). A site qualifies only if it is clean for all of W (S-3).
- **Site borough and precinct** (A4): the modal non-missing value across the site's complaints, ties broken alphabetically. Empty or "Unspecified" becomes null.
- **Geocode artifacts** (D17; approved threshold rule):
  - T = `quantile(n_complaints, 0.999, method="higher")` over all sites.
  - A site is an artifact when `n_complaints > T`, so ties at T are *not* artifacts.
  - Artifact sites are excluded from both roles but still count as darkness (A7).
  - Canonical: T = 44, with 44 artifact sites holding 4,735 complaints.
- **Eligible control** = not an artifact and borough present. Canonical: 45,987.

### 3.2 Darkness intervals and episodes [FROZEN]

- **Valid closure** (0.5 h ≤ duration ≤ 8,760 h, the Stage 3 rule, A5): [created − 7 d, closed].
- **Otherwise:** [created − 7 d, created + 160 h], with the imputed duration rounded to whole seconds.
  - The 7 days is `REPORT_LAG_DAYS`; the 160 h is `IMPUTED_DURATION_HOURS`. Both are D19 sensitivity parameters.
- **Episodes** (D17):
  - Two complaints are linked when both are non-artifact, lie within 25 m of each other (explicit formula, A13) and have overlapping darkness intervals (A2).
  - Episodes are the connected components of those links.
  - Artifact complaints form single-complaint episodes (A7).
- **First complaint of an episode:** the smallest (`created_date`, `unique_key`) in it (H3).
- **Canonical:** 152,656 episodes; largest footprint 44.2 m; 0 episodes spanning more than 100 m; 222 spanning more than 365 days (contamination D5).

### 3.3 Spatial rules [FROZEN: design §2, D8, D9 overlap fix, C2, C5]

| Rule | Final definition |
|---|---|
| **S-1 distance band** | 500 m ≤ d(treatment point, control site point) ≤ `match_band_max_m` (1,500 m). The 500 m minimum is locked (C5): it equals 2 × 250, so the 250 m outcome zones are disjoint. |
| **S-2 same borough** | Exact. The treatment's borough is its **site's modal borough**, the same definition controls use (Commit 4 decision). |
| **S-3 clean control** | No complaint within `exclusion_radius_m` (350 m) of the control point whose darkness interval overlaps the S-3 clean window (E3). `full_window`: [w_start, w_end]. `pre_only`: [w_start, created_shifted − 1 s]. All complaints count (artifact and treatment complaints included), with real dates and no exemptions. |
| **S-4 pre** | Dirty if any complaint within `treatment_clean_radius_m` (100 m) has start ≤ c − 1 s and end ≥ c − 35 d (R1). Canonical exemption: the treatment's own episode (C2). Placebo: no exemption. |
| **S-4 post** | Dirty if any complaint within 100 m has start ≤ W_end and end ≥ closed + 1 s (R1; D9 "overlaps", not "starts"). Canonical exemption: **the treatment complaint only**; own-episode duplicates count. Placebo: no exemption, including the real treatment complaint. |

**Placebo S-4 [FROZEN, with the approved documentation requirement].**
- The absence of a placebo S-4 post exemption follows from C4, which makes the real treatment complaint ordinary darkness.
- The absence of a placebo S-4 pre episode exemption is an implementation choice.
  - It is consistent with C4 and C2.
  - It gives the same output as the alternative under the approved regime, shift (90 d) > lag (7 d).
  - A placebo shift in (0, lag] would make the choice matter; only 90 days is approved.

**Distances** (A8, A13) are measured to each complaint's point, not to episode centres. Membership is decided only by the explicit formula.

### 3.4 Temporal rules [FROZEN: design §3, C6, D11 = No]

- **Analysis window:** W = [c − 35 d, max(closed + 14 d, c + 35 d)], closed (A2).
  - It covers the S8 pre/during/post windows and the S10 event-study weeks −4…+4. The design text said −5…+5; F5 corrected it.
- **Baseline window:** B = [c − 400 d, c − 35 d), half-open, evaluated as [b_start, b_end − 1 s] (A11, A13).
  - B does not overlap W, so the event-study pre-trend test uses data the matching never saw.
- **S8 pre-window used for balance only:** [c − 14 d, c], closed (A11).
- **Coverage:**
  - W and B inside the crime coverage.
  - W inside the darkness (311) coverage, with the lag applied at both ends (C6, U4).
  - C6 removes treatments here because rule 2 comes before rule 3; see the item 11 amendment in [issue4_acceptance.md](issue4_acceptance.md).
- **D11 = No:** `ANALYSIS_START` is unchanged (2019-11-01). The earliest eligible treatment is created 2020-12-05.

### 3.5 Treatment eligibility rules and reason codes [FROZEN order]

Rules are applied in this order. Each rejected treatment gets the code of the **first** rule it fails (`REASON_CODES`, [match_controls.py:273](../src/features/match_controls.py#L273)).

| Step | Code | Canonical dropped | Remaining |
|---|---|---|---|
| 0 | Stage 3 valid closures (H17) | 0 | 107,731 |
| 1 | `W_OUTSIDE_CRIME` | 6,836 | 100,895 |
| 2 | `W_OUTSIDE_DARKNESS` | 1,589 | 99,306 |
| 3 | `B_OUTSIDE_CRIME` | 8,421 | 90,885 |
| 4 | `ARTIFACT_SITE` | 1,767 | 89,118 |
| 5 | `NO_BOROUGH` | 3 | 89,115 |
| 6 | `NOT_FIRST_OF_EPISODE` | 16,972 | 72,143 |
| 7 | `S4_PRE_DIRTY` | 25,522 | 46,621 |
| 8 | `S4_POST_DIRTY` | 13,469 | **33,152 eligible** |
| 9 | `NO_CANDIDATE_IN_BAND` ("no usable control candidate in the band", O2) | 4 | 33,148 |
| 10 | `NO_CLEAN_CANDIDATE` | 2,796 | 30,352 |
| 11 | `NO_CANDIDATE_IN_CALIPER` | 8,556 | 21,796 |
| 12 | `REUSE_CONFLICT` | 1,745 | **20,051 pairs** |

- **Source:** [evidence/commit12/canonical_scratch_run.log](evidence/commit12/canonical_scratch_run.log). That was a scratch run at `0fd113d`; the authoritative numbers come from Phase 9.
- **First-of-episode figure (F6):** the design's "First complaint of its episode = 74,546" was a copy error. The re-derived count of first-of-episode Stage 3 treatments is 88,388 (Commit 5). The attrition row is smaller (16,972 dropped at step 6) because steps 1–5 run first.

### 3.6 Matching [FROZEN: design §4–§5, D10, Commit 8 definitions, O2–O7]

**Matching variables:**
- log1p(`base_100m`) and log1p(`base_250m`): night crimes during B.
- `base_100m` counts d ≤ 100 m. `base_250m` counts **100 < d ≤ 250 m, the ring only**, not a cumulative count.
- The treatment side is centred on the complaint's exact point; the control side on the site point.

**Scale:**
- The sample SD (ddof = 1) of each log1p variable across the run's own eligible treatments. Placebo runs use the placebo-eligible population.
- Controls use the treatment-derived SDs from the same run.
- z = log1p(count) / SD, **no centring**: the caliper and the distance use only differences, so a common mean would cancel.

**Candidates, as a nested chain:**
- band = {500 ≤ d ≤ `match_band_max_m`} ∩ same borough ∩ `eligible_control` (O2);
- clean = band ∩ S-3 clean;
- caliper = clean ∩ |Δlog1p| ≤ `caliper_sd` × SD for **each** variable. This is exactly the H8 form (O5), with `caliper_sd` = 0.5 (D10).

**Ranking:** by standardised Euclidean distance (`match_distance`), then geographic distance, then lexicographic `site_id` (O7). `band_dist` is float64 (O4).

**Order:**
- Treatments are sorted by `treatment_key`.
- Then `rng.permutation(n)` with `numpy.random.default_rng(match_seed)`, seed 42 (O6).
- The chosen control is the first ranked candidate the reuse registry accepts.

**`n_candidates_reuse_blocked`** counts only the candidates actually examined before selection (O3). So 0 ≤ blocked ≤ `n_candidates_caliper` − 1 for matched pairs (H14, U3).

### 3.7 Reuse [FROZEN: design §6, D12, C3; H9 interpretation]

- A site may serve as a control for several treatments only if their closed W intervals don't overlap. There is no cap on total reuse.
- Every eligible treatment's W is pre-registered as occupied at its own site (C3). A site therefore cannot be a control while it is inside an eligible treatment's W.
- **H9 as approved:**
  - Control–control and control–treatment overlap at a site fail.
  - Treatment–treatment overlap is allowed and reported.
    - Canonical: 935 overlapping treatment pairs at 876 sites, involving 1,831 treatments.
  - Intervals are closed [w_start_s, w_end_s]. Touching endpoints overlap. There is no tolerance.
  - H9 rebuilds the intervals from the pairs, not from `ReuseRegistry`.
- **Canonical:** 14,393 control sites; reuse mean 1.393, max 12; 643 candidates blocked by pre-registration.

### 3.8 Placebo, sensitivity and locked parameters [FROZEN: D18, D19, C4, C5]

**Placebo (D18, C4):**
- Treatment dates move back by `placebo_shift_days` (approved value 90). So do W, B, the S-4 windows and the S-3 windows.
- Complaint darkness intervals always use the real dates.
- The real treatment complaint becomes ordinary darkness.
- First-of-episode is judged on the real episode.
- The run re-matches on the shifted dates.

**Command-line parameters.** Only the D19 sensitivity parameters can be changed from the command line:
- `exclusion_radius_m` (250 / 500)
- `match_band_max_m` (2,000)
- `caliper_sd` (0.2)
- `reuse_policy` (`never`)
- `report_lag_days` (0)
- `imputed_duration_hours` (50 / 720)
- `control_selection` (`pre_only`)
- `match_seed` (42, 101, 202, 303, 404)
- `placebo_shift_days` (90)

Every other methodology value is a locked constant that H0 checks for equality (Commit 1 fix-up).

**Non-default runs** must use an `--out-dir` other than `data/processed`. `--skip-recheck` is refused for the canonical directory (H0).

**The D19 grid runner** (Step 5, `robustness.py`) is out of scope: **[DEFERRED]**, Q6.

### 3.9 Conventions A1–A13 [FROZEN]

These are recorded verbatim in `CONVENTIONS` ([match_controls.py:292](../src/features/match_controls.py#L292)) and written to `match_diagnostics.json` under `parameters.conventions`.

| ID | Convention |
|---|---|
| A1 | 311 and NYPD timestamps are naive NYC local time; the repeated DST hour is ignored. |
| A2 | Overlap is closed: [s, e] overlaps [a, b] when s ≤ b and e ≥ a. S-4 pre [c−35d, c): s < c and e ≥ c−35d. S-4 post (closed, W_end]: e > closed and s ≤ W_end. |
| A3 | Times are stored as int64 seconds. |
| A4 | Site borough and precinct are the modal value, ties broken alphabetically; empty or "Unspecified" becomes null. |
| A5 | The Stage 3 validity rule (0.5–8,760 h) is duplicated in Stage 7 and must stay in sync with `src/data/clean_streetlights.py`. |
| A6 | Darkness coverage is the min/max raw `created_date`; crime coverage is the min/max `crime_datetime`. |
| A7 | Artifact-site complaints form single-complaint episodes and still count as darkness. |
| A8 | S-3 and S-4 distances are measured to each complaint's point (A13 formula). |
| A9 | Prior episodes: episode start in B and at least one complaint within 250 m. |
| A10 | Res-9 density: crimes during B in the unit's res-9 cell divided by the cell area (km²). |
| A11 | S8 pre-window [c−14d, c] includes both ends; B is half-open (evaluated as in A13). |
| A12 | Distances follow Stage 8: ≤ 100 m direct, (100, 250] m ring, using the A13 formula. |
| A13 | Index times are whole-second int64 timestamps. A half-open window [a, b) is evaluated as the closed lookup [a, b − 1]; a left-open window (a, b] is evaluated as the closed lookup [a + 1, b]; a query with a > b is an empty window (clean / count 0). Radius membership is decided only by the explicit float64 formula sqrt(dx² + dy²) ≤ r in EPSG:32118 metres. KD-tree radius queries only generate candidates and use a positive tolerance (`KD_QUERY_TOLERANCE_M = 1e-6` m); final inclusion is decided by the explicit formula. |

The (a, b] clause of A13 was added in Commit 7, as approved.

**Scope of the A13 claim.** A13 is the convention for new radius and window logic. No complete audit of every radius decision in Stages 7–10 has been documented, so this document does not claim that every radius decision follows A13. What is established:
- Commit 6 fix-up P2 aligned the episode links and H4 with A13.
- The Stage 7 recheck follows A13 (X1, R3).
- Stages 8 and 10 generate KD candidates at r + 1e-6 m and decide membership by the explicit formula (S8-14, S10-3).

### 3.10 Balance and contamination diagnostics [FROZEN: B1–B14, contamination D1–D5]

**Balance** is report-only (B12) and never stops the run.
- SMD = (mean_T − mean_C) / sqrt((s_T² + s_C²)/2), with ddof = 1 (B1). It must be strictly < 0.1 in absolute value.
- VR = s_T² / s_C², inclusive [0.5, 2] (B2).
- **Matched variables:** log_base_100m and log_base_250m.
- **Balance-only variables:**
  - `prior_episodes_250m` and `h3r9_density`, raw only (B3; B4 uses the treatment site cell);
  - the S8 pre-window counts, raw and log1p.
- Prior episodes (B5 and its clarification) count an episode once if its `start_s` falls in [b_start_s, b_end_s − 1] and at least one of its complaints is within 250 m of the unit point. Artifact episodes are included.
- **Canonical:**
  - Matched: SMD +0.005 (VR 1.021) and +0.017 (VR 1.040). Both pass.
  - `prior_episodes_250m`: SMD +0.348 (VR 1.492), **fails its flag**. This is handled by amendment A1; see M2 and [issue4_acceptance.md](issue4_acceptance.md).
  - All other balance-only flags pass.
- **Representativeness** (B13): matched treatment mean `base_100m` is 5.77 against 8.40 for all eligible treatments (log1p SMD −0.19).

**Contamination diagnostics** (report-only; review share 5%; the denominator is pairs, B10):

| ID | Definition (final) | Canonical |
|---|---|---|
| D1 | Pairs whose control is within ≤ 500 m (B6) of a *different pair's* matched treatment (B7) with overlapping W | 0.743 |
| D2 | Pairs whose control has any complaint at `exclusion_radius_m` < d ≤ 500 whose darkness overlaps the full W (B8) | 0.868 |
| D2b | `pre_only` runs only: controls with a complaint within `exclusion_radius_m` overlapping [created, w_end] | n/a in the canonical run |
| D3 | Pairs whose control is within ≤ 500 m of a different pair's control with overlapping W | 0.908 |
| D4 | Per pair: other complaints within 100 m of the treatment overlapping [created, closed], own-episode duplicates included (B9) | mean 0.057; 95.8% of pairs have 0 |
| D5 | Episodes with span > 100 m or > 365 days (B11 reuses the Commit 5 summary) | 0 > 100 m; 222 > 365 d |

The shares above 5% are explained by the Commit 11 review package, which amendment A2 accepts for the canonical `c2bb341` definitions:
- They are a scale effect of 500 m discs combined with W of about 70 days or more.
- Contamination D1 measures overlap of the analysis windows, not overlap with the other treatment's darkness.
- D2 is the residual risk named in design §2. The exclusion-500 sensitivity run gives D2 = 0.000 and D1 = 0.320 with 7,419 pairs.

`compute_contamination` is byte-identical between `c2bb341` and the current HEAD. Its helper functions were not separately audited in Phase 6.

### 3.11 Hard checks H0–H18 [FROZEN definitions, with the approved amendments]

Every hard check stops the stage with exit 1 and writes `match_diagnostics.failed.json`.

| ID | Checks | Source |
|---|---|---|
| H0 | Parameters: metre CRS; band minimum ≥ 2 × outcome radius; exclusion radius ≥ outcome + direct radius or an approved sensitivity value (250); caliper > 0; valid enums; every float finite; every locked parameter equals its constant; non-default parameters need a non-canonical `--out-dir`; no `--skip-recheck` in the canonical directory | Blueprint §4; C-1 and C-2 fix-up (`a740307`) |
| H1 | Input integrity: raw `unique_key` unique; every Stage 3 key present, with the same times and coordinates within 1e-6°; unparseable created dates and invalid crime rows counted as violations | Blueprint; Commit 2 decision 1 |
| H2 | Darkness intervals valid and built exactly by the rule | Blueprint |
| H3 | Episodes partition the complaints; first-of-episode = smallest (created, key) | Blueprint |
| H4 | Every pair of non-artifact complaints within 25 m in *different* episodes has non-overlapping intervals. Not fully independent: it uses the same scipy KD-tree implementation as step 6 (Commit 5 review note) | Blueprint; P2 |
| H5 | Every matched treatment is first-of-episode, not an artifact, and has a borough. Path: `treatment_key` → complaints → `site_idx` → sites | Blueprint; Commit 10 H5 clarification |
| H6 | Coverage per pair, mirroring rule 2 with the lag at both ends | Blueprint; U4 |
| H7 | 500 ≤ `distance_m` ≤ max (± 1e-6); equal boroughs; recomputed d == `distance_m` exactly | Blueprint; U5 |
| H8 | Caliper in log form for both variables | Blueprint; O5 |
| H9 | Reuse and role overlap (§3.7) | Commit 1 interpretation; `d91f1f8` |
| H10 | Independent clean-window recheck (§3.12) | Blueprint; X1, X2, R1–R4, E1–E3 |
| H11 | Independent exact baseline recount (§3.12) | Blueprint; X1, R3, E1 |
| H12 | Pair validity: unique `pair_id` and `treatment_key`; treatment site ≠ control site; no nulls in required columns; dtypes. Two phases (without and then with `bal_*`); one diagnostics entry | Blueprint; U2; Commit 12 difference 3 |
| H13 | Accounting: pairs + unmatched = the Stage 3 row count (from step 2, N3); keys disjoint; the 12-step attrition telescopes (N4) | Blueprint; N3, N4 |
| H14 | band ≥ clean ≥ caliper ≥ 1 per pair; 0 ≤ reuse_blocked ≤ caliper − 1 | Blueprint; U3 |
| H15 | Both SDs finite and > 0; at least 1 eligible treatment | Blueprint |
| H16 | Placebo dates consistent (step 2). Post-matching H16 is recorded as **`H16_post`** (N5, Q1): created/closed = real − shift; darkness built from unshifted dates; no S-4 hit without exemptions. Not applicable when shift = 0 | Blueprint; N5; difference 2 |
| H17 | Stage 3 → Stage 7 valid-closure contract: every Stage 3 treatment has `is_imputed == False` (after step 4) | Commit 2 decision 4 |
| H18 | Site and artifact integrity, rebuilt independently: one site per complaint; per-site counts; T by the "higher" method; `is_artifact == (n > T)`; `eligible_control` | Commit 4 decision |

**Canonical scratch run:** all 20 recorded entries (H0–H18 plus `H16_post`) pass ([evidence/commit12/](evidence/commit12/)).

### 3.12 Independent recheck H10/H11 [FROZEN: X1, X2, R1–R4, E1–E3, N9]

**Sample** (N9): min(2,000, n_pairs) pairs, drawn with `RECHECK_SEED = 20260927` from `pair_id` order. The full-population recheck (V1) was **not adopted** (Commit 12 difference 9).

**X1 and R3: rebuilt from raw.**
- Complaints and crimes are re-projected from the raw latitude/longitude with `pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32118", always_xy=True)`.
- The spatial index only **generates candidates**, with `dwithin` at radius + 1.0 m (`RECHECK_CANDIDATE_MARGIN_M`).
- Membership is decided only by sqrt(dx² + dy²) ≤ r in float64.
- Treatment coordinates must equal `treatment_x_m/y_m` exactly.
- The control must be a rebuilt whole-metre site with a matching `site_id` and must not be an artifact.

**X2 and R2: the own episode is rebuilt from raw.**
- It is the full breadth-first transitive closure over links between non-artifact complaints within `episode_merge_radius_m`, with overlapping darkness. There is no hop, radius or size cap.
- The artifact threshold is rebuilt by the "higher" method.
- No production `episode_id`, `is_first_of_episode`, `is_artifact`, darkness interval, site table or index is read.

**Windows** (R3): W, B, S-4 pre = [c − 35 d, c − 1 s] and S-4 post = [closed + 1 s, W_end]. They are rebuilt from the raw dates plus the shift and compared in seconds.

**Terminology** (R4): "S-3 clean window", not "control contamination window".

**Radii** (E1): taken from the run's `Params`: S-3 `exclusion_radius_m`, S-4 `treatment_clean_radius_m`, links `episode_merge_radius_m`, direct `direct_radius_m`, ring (`direct_radius_m`, `outcome_radius_m`].

**Universe** (E2) as in §3.1. **S-3** (E3) as in §3.3.

**H11:** the four baseline counts must match exactly, with no tolerance.

**Evidence that R1 is exercised** (Phase 6 request D3): 36 of the 2,000 sampled canonical treatments needed the own-episode S-4 pre exemption (`recheck.n_s4_pre_own_episode_exempted = 36`). Placebo runs have 0.

### 3.13 Determinism [FROZEN: N6; the Commit 12 decision on difference 1]

**What is recorded:**
- `determinism.pair_list_sha256`: the sha256 of the sorted `pair_id` list.
- `determinism.order_hash`: the hash of the random treatment order.
- Canonical scratch values: `ab63c377…` and `b36524dc…`.

**`--check-determinism`:**
- Rebuilds the reuse registry, the band lists and the matching (steps 12–13) from scratch on the same inputs.
- Requires all four to be identical: the pair-list hash, the order hash, `pairs_raw` and the rejected table.

**Failure behaviour** (Phase 6 request D1). This is approved behaviour, not an implementation detail:
- A **pair-list hash mismatch**, an **order hash mismatch**, or any difference in `pairs_raw` or the rejected-treatment table is a hard failure.
- The run stops before the recheck (step 18) and before any output is written.
- It writes `match_diagnostics.failed.json` and exits with **code 1**.

Without `--check-determinism`, no second pass runs and `determinism.second_pass` is null.

### 3.14 Outputs, safe writing, backups and exit codes [FROZEN: N7, N8, N10, N15, X3, Q9]

- **Safe writing:**
  - All four files are written to temporary names and schema-checked with pyarrow against `ARROW_TYPES` (N10).
  - They are then renamed with `os.replace`, the diagnostics file last (N7).
  - A `PermissionError` reports which files were already replaced.
- **Failure file:** every stop writes `match_diagnostics.failed.json` into the requested `--out-dir` (N8; difference 6). A successful run removes a stale one.
- **Pairs backup** (X3; automated per difference 4):
  - An old-schema `control_area_pairs.parquet` (one without `control_site_id`) in the output directory is copied to `control_area_pairs.pre_issue4.parquet` before writing.
  - The copy's sha256 is verified. An existing backup is never overwritten.
- **Exit codes:**
  - **0:** completed and all files written (N15).
  - **1:** H0 failure, hard-check failure, determinism failure, or unhandled exception (check stderr for a traceback).
  - **2:** argparse usage error.
  - **3:** only during the Issue 4 rollout ("Stage 7 incomplete"). It is **no longer returned**.
- **Diagnostics** (§3.4 of the blueprint):
  - Written as strict JSON (`allow_nan=False`); non-finite values become `null` and are listed in `non_finite` (N1, N2).
  - `provenance` records the UTC time, platform, git SHA and dirty flag, package versions and input fingerprints.
  - The dirty flag ignores untracked files (`git status --porcelain --untracked-files=no`).

### 3.15 Output schemas [IMPL]

The implemented dtypes are `PAIRS_DTYPES`, `SITES_DTYPES` and `UNMATCHED_DTYPES` ([match_controls.py:324](../src/features/match_controls.py#L324)). They follow blueprint §3 with these additions:
- `n_candidates_reuse_blocked` (O3);
- `control_police_precinct`;
- `treatment_episode_id`.

The site table fills `n_times_treatment` and `n_times_control` in Commit 12 (U7). `outage_duration_hours` is recomputed from the pair dates (U8). Treatment H3 cells are the treatment **site's** cells (U1).

### 3.16 Implemented signatures (N12; Commit 12 difference 8)

Approved; recorded here as required. The implemented step functions differ from the engineering plan as follows:

| Function | Implemented signature | Note |
|---|---|---|
| `independent_recheck` | `(params, pairs)` | No `coverage` argument: it rebuilds everything from raw |
| `build_unmatched` | `(treatments_all, treatments_eligible, match, pairs, sites)` | |
| `compute_contamination` | `(params, pairs, complaints, episodes, idx, episode_summary)` | Takes the Commit 5 `episode_summary` (B11) |
| `load_crime` | `(params, raw_summary)` | |
| `build_darkness_intervals` | `(params, complaints, coverage)` | |
| `apply_treatment_rules` | `(params, treatments_all, complaints, sites, idx, coverage)` | |
| `compute_treatment_baselines` | `(params, treatments_eligible, idx, crime)` | |
| `match_treatments` | `(params, treatments_eligible, sites, idx, scales, registry)` | Band candidates come from a separate `build_band_candidates(params, treatments_eligible, sites, idx)` |
| `assemble_pairs` | `(params, match, treatments_eligible, sites, registry)` | |
| `compute_balance` | `(params, pairs, treatments_eligible, episodes, complaints, crime, idx)` | |
| `check_determinism` | `(params, treatments_eligible, sites, idx, scales, match)` | |
| `write_outputs` | `(params, pairs, sites_out, unmatched, state)` | |
| `run_hard_checks` | `(params, stage, checks, **tables)` | Checks run per pipeline stage |

The authoritative list is the source file; this table was taken from it at `f804a78`.

---

## 4. Stage 8: causal panel

**Module:** [src/features/build_causal_panel.py](../src/features/build_causal_panel.py). **Migration record:** [issue4_migration_s8_s10.md](issue4_migration_s8_s10.md) (S8-1 to S8-15). **Commit:** `9624b45`.

### 4.1 Inputs [IMPL]

- **Pairs:** the 29 `PAIR_COLUMNS` of a Stage 7 Issue 4 pairs file.
  - A file missing any of them is rejected with "is not an Issue 4 Stage 7 pairs file".
  - `pair_id` must be unique.
  - No `control_key`, integer key or `*_right` column is read.
- **Crime:**
  - `crime_datetime`, `latitude` and `longitude` from `clean_crime.parquet`.
  - Rows with a null in any of them are dropped (the Stage 7 rule).
  - Points are projected with `pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32118", always_xy=True)`, the same path as Stage 7.
- **No outage table is read** (Q5; §7.1).

### 4.2 Units, identifiers and geometry

| Column | Definition | Label |
|---|---|---|
| `pair_id` | Read from Stage 7: `"{treatment_key}_{control_site_id}"` (string) | [FROZEN] §7 |
| `role` | `T` or `C` | [IMPL] |
| `unit_id` | `f"{pair_id}_{role}"`. A reused control site gives one unit per pair | [FROZEN] §7, D15 |
| `location_key` | `treatment_site_id` or `control_site_id` (string) | [FROZEN] §7 |
| `location_latitude`, `location_longitude` | `treatment_latitude/longitude` (the complaint's exact point) or `control_latitude/longitude` (the site point) | [FROZEN] §7 |
| `location_x_m`, `location_y_m` | `treatment_x_m/y_m` or `control_x_m/y_m` as written by Stage 7 (EPSG:32118); not re-projected | [IMPL], additive (§7.1 item 9) |
| `base_100m`, `base_250m` | The role's Stage 7 matching baselines during B (`*_base_100m`, `*_base_250m`; 250 = ring only) | [FROZEN] §7 |
| `borough` | The role's pair column (site modal borough for both roles) | [IMPL] Q5 |
| `h3_res9`, `h3_res10` | The role's `*_h3_res9/10`: **site** cells for both roles (U1) | [FROZEN] §9, U1 |
| `treatment_h3_res7`, `control_h3_res7` | Pair-level, identical on all six rows of a pair | [IMPL] for D13 clustering, additive |
| `treatment_police_precinct` | Pair-level (the treatment site's modal precinct) | [IMPL] for D13 precinct clustering, additive |
| `outage_duration_hours` | The treatment's duration, on both roles' rows | Unchanged |
| `created_date`, `closed_date` | The treatment's dates, on both roles' rows (placebo-shifted in placebo pairs files) | Unchanged |

### 4.3 Three-period panel [FROZEN: windows and outcomes unchanged]

| Period | Window (all inclusive unless stated) | `period_order` | `post` |
|---|---|---|---|
| pre (canonical) | [c − 14 d, c] | 0 | 0 |
| pre (shifted, `--pre-window shifted`, D19) | [c − 21 d, c − 7 d), end exclusive | 0 | 0 |
| during | [c, closed] | 1 | 1 |
| post | [closed, closed + 14 d] | 2 | 1 |

**Outcomes:**
- `crime_100m`: d ≤ 100 m.
- `crime_250m`: 100 < d ≤ 250 m (the ring, not cumulative).
- KD candidates at 250 + 1e-6 m; membership by the explicit formula (S8-14).

**Other columns:**
- `baseline_crime_intensity` = the unit's pre-window 100 m count, on all three rows. The definition is unchanged; it is a known bad control (M8).
- `treatment` = 1 for T and 0 for C; `treatment_x_post` = `treatment` × `post`.

**Row count** = 6 × pairs (3 periods × 2 roles). Rows are sorted by (`pair_id`, `period_order`, role T before C), which gives deterministic output. Canonical: 120,306 rows, 27 columns, 40,102 units and 24,133 distinct locations ([evidence/stage8/canonical_scratch_run.log](evidence/stage8/canonical_scratch_run.log)).

### 4.4 Guards and assertions

| Guard | Behaviour | Label |
|---|---|---|
| D3 coverage guard (Issue 1–3 D3) | pre start ≥ the crime minimum and post end ≤ the crime maximum. **Must drop 0**: any uncovered pair raises `AssertionError`, because Stage 7 already enforces coverage | [FROZEN] §9 |
| 500 m separation | The explicit distance between `treatment_x_m/y_m` and `control_x_m/y_m` ≥ 500 − 1e-6 for every pair | [FROZEN] Step 2 |
| `unit_id` structure | Exactly 2 × pairs units; each has exactly 3 rows, one per period | [FROZEN] Step 2 |
| Constant treatment within a unit | Asserted | [IMPL] |
| Role and period balance | 3 × pairs rows per treatment value; 2 × pairs rows per period | [IMPL] |
| No nulls | In `pair_id`, `unit_id`, `location_key`, x/y, both outcomes, `baseline_crime_intensity`, both baselines, `borough` and both res-7 cells | [IMPL] |

`treatment_police_precinct` may be null. It is not in the null check; Stage 9 treats a missing value as its own cluster (§7.2 item 3).

### 4.5 CLI, output and backup [IMPL; Q9]

```
python src/features/build_causal_panel.py [--pairs PATH] [--crime PATH] [--out PATH] [--pre-window {canonical,shifted}]
```

- **Defaults:**
  - `--pairs data/processed/control_area_pairs.parquet`
  - `--crime data/processed/clean_crime.parquet`
  - `--out data/processed/causal_panel.parquet`
  - `--pre-window canonical`
- **Atomic write:** the panel is written to `<out>.tmp`, then `os.replace`. The panel's sha256 is printed.
- **Legacy backup** (Q9): before the first write, an existing panel **without** `unit_id` in the output directory is copied to `causal_panel.pre_issue4.parquet`.
  - The copy's sha256 is verified; if it differs, the copy is deleted and an error raised.
  - An existing backup is never overwritten. A panel that already has the Issue 4 schema is not backed up.
- **Shifted mode:**
  - Only the pre rows' outcomes change, and `baseline_crime_intensity` follows them on every row.
  - The during and post outcome rows are identical to the canonical panel ([evidence/stage8/stage8_validate.out](evidence/stage8/stage8_validate.out)).

---

## 5. Stage 9: difference-in-differences

**Module:** [src/models/did_model.py](../src/models/did_model.py). **Commit:** `25cc596`. **Migration record:** S9-1 to S9-6.

### 5.1 Models [FROZEN: specifications unchanged]

- **Basic DiD (OLS):** `y ~ treatment + post + treatment_x_post + baseline_crime_intensity`.
- **Separate during/post (OLS):** `y ~ treatment + during + post_period + treatment_x_during + treatment_x_post_period + baseline_crime_intensity`.
- **Two-way FE DiD:** the within transformation; details in §5.2.
- **Poisson and negative binomial (NB2) GLMs:** the separate formula.
  - NB uses `sm.families.NegativeBinomial()` with the statsmodels default dispersion α = 1.0, not estimated (M8-adjacent limitation, §9).
- **Outcomes:** `crime_100m` and `crime_250m`.
- `baseline_crime_intensity` stays a covariate where it was (M8).

### 5.2 Fixed effects [FROZEN: D15]

- **FE unit:** `unit_id` (pair × role). Every unit is checked to have exactly 3 periods.
- **Two-way within transformation:** x_it − x̄_i. − x̄_.t + x̄_.. with i = `unit_id` and t = `period`.
- This is then fitted with OLS of the demeaned outcome on a constant plus the demeaned `treatment_x_during` and `treatment_x_post_period`.

**Coefficient equivalence** (checked by `stage9_validate.py`): the within-transformation coefficients equal an LSDV regression with `C(unit_id) + C(period)` to 1e-9, on a 400-pair subsample.

### 5.3 Standard-error convention [IMPL, statistical record]

All Stage 9 standard errors use statsmodels' cluster-robust estimator (`cov_type="cluster"`, statsmodels 0.15.0 defaults), which is CR1:

  V = [G / (G − 1)] · [(N − 1) / (N − K)] · (X′X)⁻¹ (Σ_g X_g′ e_g e_g′ X_g) (X′X)⁻¹

- **FE model:** K = 3 (the constant and the two interaction terms).
  - The absorbed unit and period fixed effects are **not counted in K**.
  - **Rationale:** each `unit_id` is nested within every clustering scheme used. A unit belongs to one pair, and its pair has one treatment cell, one control cell and one treatment precinct. When fixed effects are nested within clusters, the absorbed effects are conventionally left out of the small-sample correction.
- **This differs from an LSDV-style correction.** An OLS with explicit `C(unit_id) + C(period)` dummies clustered the same way would count every dummy in K.
  - On the canonical panel (N = 120,306; 40,102 units) that makes (N − 1)/(N − K) about 1.5, so its standard errors would be about 22% larger.
  - This figure is derived analytically from the formula. It was not measured.
  - Neither convention is claimed to be superior. Coefficients are identical under both.
- **Paired-difference SE (D20):** the same CR1 formula on a one-regressor model (N = pairs, K = 1). The factor is G/(G − 1) (validated by `stage9_validate.py`).
- **Inference defaults** (t or normal reference distribution, degrees of freedom): statsmodels' defaults for each model class are used unchanged. They are not separately documented or validated in Phase 6.
- **Event study (§6):** the same convention, with K = 8 interaction terms and no constant.

### 5.4 Clustering [FROZEN: D13 as approved by the review verdict]

| Scheme | Definition | Used for | Canonical clusters |
|---|---|---|---|
| **Primary** | `treatment_h3_res7`: the H3 res-7 cell of the pair's **treatment site** (U1), on all six rows of the pair | Every model | **193** |
| Pair | `pair_id` | Robustness: basic OLS and FE DiD | 20,051 |
| Two-way | (`treatment_h3_res7`, `control_h3_res7`), Cameron–Gelbach–Miller via statsmodels 2-column groups | Robustness: basic OLS and FE DiD | 193 × 195 |
| Precinct | `treatment_police_precinct`; a missing precinct becomes one `<missing>` cluster | Robustness: basic OLS and FE DiD | 78 = 77 observed precinct values + 1 `<missing>` cluster (101 pairs) |

- **Minimum:** the primary scheme must have at least 50 clusters, **otherwise the run stops** (§7.2 item 4). Cluster counts for every scheme are written to `did_summary.json` (`dataset.n_clusters`) and to the results text.
- **Two-way form:** approved as S1; see §7.2 item 2.

**Observed cluster structure** (canonical scratch panel). These are properties of the implemented, approved clustering, not recommendations:
- **49.4%** of pairs have their treatment and control in different primary H3 res-7 cells.
- **Reused control sites:** 2,541 of the 4,167 control sites used by more than one pair appear in pairs belonging to more than one primary cluster. That involves 6,270 pairs.
- **5,171 sites** are a treatment site in at least one pair and a control site in at least one other pair.
- **Primary cluster sizes:** median 99 pairs, max 298, min 1. The five largest clusters hold 6.6% of pairs.
- **Two-way scheme:** it groups a reused control site's pairs through the control-cell dimension. No scheme groups a site's treatment-role and control-role appearances together.
- **Standard errors:**
  - No claim is made that res-7 clustering produces larger standard errors than the alternatives.
  - On the canonical scratch run the relative sizes of the primary, pair, two-way and precinct SEs differ by outcome and term.
  - Legacy (`location_key`) and Issue 4 standard errors are not comparable.

Evidence status: the cluster counts are in [evidence/stage9/stage9_validate.out](evidence/stage9/stage9_validate.out). The share and reuse figures above were computed during the Phase 3–5 independent review on the canonical scratch panel (sha256 `eaa0cd74…`). They are **not yet stored as repository evidence**; Phase 9 is to regenerate and store them.

### 5.5 Paired-difference estimate [FROZEN: D20; subgroup details approved as S4, S5]

- **Per pair:**
  - dd_during = (T_during − T_pre) − (C_during − C_pre)
  - dd_post = (T_post − T_pre) − (C_post − C_pre)
- Computed for both outcomes. Each estimate is the mean over pairs, with the CR1 SE clustered on `treatment_h3_res7`.
- **Equivalence** [IMPL, asserted]: in the balanced 1:1 panel the overall mean equals the two-way FE coefficient. The run stops if they differ by more than 1e-9.
  - This assertion does not detect a NaN mean; see [issue4_acceptance.md](issue4_acceptance.md), Validation gaps.
- **Subgroups** [FROZEN: S4, S5; §7.2 item 5]:
  - **By baseline tercile:** tercile of the treatment's log1p(`base_100m`), with edges at the 1/3 and 2/3 quantiles (`numpy.quantile` defaults) and ties at an edge going to the lower tercile. Canonical edges are log 2 and log 6 (`base_100m` = 1 and 5). The group sizes are unequal: 6,845 / 6,911 / 6,295 pairs.
  - **By year:** the calendar year of the pair's `created_date`.
  - Each subgroup gets its own cluster count. Subgroups with fewer than 2 clusters report the mean without an SE.

### 5.6 CLI and outputs [IMPL]

```
python src/models/did_model.py [--panel PATH] [--out DIR]
```

- **Defaults:** `--panel data/processed/causal_panel.parquet`; `--out outputs/`.
- **Writes** `did_summary.json` (strict JSON) and `did_regression_results.txt`.
- **The summary contains:**
  - the `status` ("provisional (M8 open …)");
  - the panel path and sha256;
  - dataset statistics: rows, pairs, units, locations, treatment and control sites, and control reuse mean and max;
  - the cluster variable, the FE unit and the cluster counts per scheme;
  - every model's interaction terms;
  - `robustness_clustering` and `paired_difference`.
- **[LIMITATION] The default `--out` overwrites the tracked legacy files in `outputs/`,** which have no backup. Until the legacy outputs are moved (not yet scheduled), run Stage 9 only with a scratch `--out`.

---

## 6. Stage 10: event study

**Module:** [src/models/event_study.py](../src/models/event_study.py). **Commits:** `5c4fca1` (D14) and `f804a78`. **Migration record:** S10-1 to S10-7.

### 6.1 Duplicate removal [FROZEN: D14; §7.2 item 8]

The pre-Issue-4 module held two byte-identical copies of the body:
- L1–17: a stray header with `parents[1]`;
- L18–35: the real header;
- L36–524: the body;
- L526–1014: identical to L36–524, **including a second `__main__` block**. So the script ran the event study twice.

`5c4fca1` deleted L1–17 and L525–1014 as a pure deletion. It was verified during the Phase 3–5 review:
- The new file is byte-identical to old L18–524.
- The deleted copy is byte-identical to old L36–524.
- Run on the legacy panel, the post-deletion module reproduces the committed `outputs/event_study_coefficients.csv` exactly (max abs diff 0.0).

### 6.2 Units and geometry [FROZEN: §11; §7.2 item 6]

- **Event units** are the panel's `unit_id`s: one row per unit, with `pair_id`, `location_key`, `location_x_m/y_m`, dates, `treatment`, `borough`, H3 cells and `treatment_h3_res7`.
- **Geometry:** `location_x_m/y_m`, which are the Stage 7 pair columns. The Stage 3 outage table is not read.
- **Crime:** projected from latitude/longitude with pyproj (`always_xy`), as in Stages 7 and 8. KD candidates at 100 + 1e-6 m; membership by the explicit formula. The outcome is crimes within 100 m.
- **Coverage:** a pair is kept only if [c − 35 d, c + 35 d] lies inside the crime coverage for both units. Canonical: 20,051 pairs and 40,102 units kept.

### 6.3 Weeks, estimation and pre-trend test [FROZEN; F5]

| Week | Days relative to created (start inclusive, end exclusive) |
|---|---|
| −4 | [−35, −28) |
| −3 | [−28, −21) |
| −2 | [−21, −14) |
| **−1 (reference, omitted)** | [−14, −7) |
| 0 | [0, 7) |
| +1 … +4 | [7, 14) … [28, 35) |

- **[LIMITATION] The −7..0 day gap.** Days [−7, 0) belong to no week. This is a known limitation. It was flagged, not changed, as approved: design §11 "the −7 to 0 day gap is flagged, not fixed", and M8. The run's summary `status` field states it.
- **Estimation:**
  - Two-way within transformation by `unit_id` (D15) and relative week.
  - OLS of the demeaned count on the 8 demeaned `treatment × week` indicators, with no constant.
  - SEs are CR1, clustered on `treatment_h3_res7` (D13), with the same convention as §5.3.
  - Stage 10 has no robustness clusterings.
- **Pre-trend test:** a joint Wald F-test that weeks −4, −3 and −2 are zero.
- **Validation** (`stage10_validate.py`): coefficients equal an LSDV with `C(unit_id) + C(rel_week)` on a 300-pair subsample.

### 6.4 CLI and outputs [IMPL; §7.2 item 7]

```
python src/models/event_study.py [--panel PATH] [--crime PATH] [--out DIR]
```

- **Defaults:** `--panel data/processed/causal_panel.parquet`; `--crime data/processed/clean_crime.parquet`; `--out outputs/`.
- **Writes:**
  - `event_study_coefficients.csv`;
  - `event_study_plot.png` (headless `Agg` backend, title "… (provisional)", no Software metadata tag, byte-reproducible);
  - `event_study_summary.json` (strict JSON).
- **The summary contains:** status, panel path, observations, units, pairs, FE, cluster variable and count, week windows, the pre-trend test and the coefficients.
  - It records the panel **path only**, with no sha256, unlike Stage 9.
- **[LIMITATION]** As with Stage 9, the default `--out` overwrites the tracked legacy files in `outputs/`. Use a scratch `--out` until the legacy outputs are moved.

---

## 7. Deviations and implementation details

### 7.1 Authorised deviations

| # | Deviation | Authorisation |
|---|---|---|
| 1 | **Stage 8 geometry source (Q5).** Stage 8 uses the Stage 7 pair columns as the source for treatment geometry, borough, precinct and H3 cells, rather than performing a treatment lookup against the Stage 3 outage table. Design §9 said "The outage-table lookup stays for treatments only". **Rationale:** a single source of truth, and Stage 7 H1 already guarantees the treatment point agrees with Stage 3 within 1e-6°. | Q5, approved 2026-09-28 |
| 6 | **Stage 10 uses `location_x_m/y_m` directly.** Design §11 names `location_latitude/longitude`. The points are the same (the Stage 7 projected coordinates of the same locations). This follows the carry-forward note that Stages 8–10 should read the projected pair columns instead of re-projecting, so geometry stays bit-identical from Stage 7 on. | Consistent with the carry-forward and with Q5. Not a separate approval |
| 8 | **D14 deletion boundaries.** The design said "lines 526–1014". The verified deletion is L1–17 plus L525–1014 (§6.1). The design's statement that the second copy "never runs" was wrong: it ran. | D14; the boundary correction is documented in migration note S10-1 |
| 9 | **Additional Stage 8 panel columns** beyond design §7: `role`, `location_x_m/y_m`, `treatment_h3_res7`, `control_h3_res7`, `treatment_police_precinct`. They are additive and support D13 and §11. The design §7 columns are all present. | Additive implementation detail |
| 10 | **The shifted pre-window changes `baseline_crime_intensity`.** It is defined as the unit's pre-window 100 m count, so with `--pre-window shifted` it follows the shifted window on every row. This is a consequence of the unchanged definition (M8), not a new rule. | Follows the frozen definition |

### 7.2 Deviations approved by sign-off (S1–S6, 2026-09-30)

These items were implemented in Phases 3–5 without an explicit approval. All of them were approved on **2026-09-30** (decision log §11, S1–S6) exactly as implemented, and are now **[FROZEN]**. No code changed.

| # | Item | Earlier approved text | Implemented and approved | Sign-off |
|---|---|---|---|---|
| 2 | **Two-way clustering form** | Design D13 row and §10: "(treatment site, control site)"; review verdict: "two-way (T cell, C cell)" | (`treatment_h3_res7`, `control_h3_res7`) for basic OLS and FE DiD. The sign-off states that this is the "(treatment site, control site)" form, read as the sites' res-7 cells, which resolves the discrepancy. | S1 |
| 3 | **Missing precinct** | "police precinct as robustness"; nothing on missing values | Every missing `treatment_police_precinct` goes into one `<missing>` cluster (101 canonical pairs); there are never several | S2 |
| 4 | **Minimum 50 primary clusters** | "it must be at least 50" | A hard stop: fewer than 50 raises `ValueError`, and the analysis fails rather than proceeding | S3 |
| 5 | **D20 subgroup definitions** | "by baseline tercile and by year" | Year = the calendar year of `created_date`. Terciles = the treatment observations' log1p(`base_100m`), quantile edges at 1/3 and 2/3, a value on an edge going to the lower tercile | S4, S5 |
| 7 | **Stage 10 extra outputs** | §11: "Unchanged: … the outputs" | `event_study_summary.json` and the "(provisional)" plot title are accepted; the PNG stays byte-reproducible. Output and diagnostic behaviour only; the estimand and methodology are unchanged | S6 |

The Phases 3–5 commit structure (S7) is recorded under Q12 in the decision log §8.

---

## 8. Validation scripts and evidence (current state)

Usage, runtimes and optional scripts are in [scripts/validation/README.md](../scripts/validation/README.md).

| Script | Checks | Evidence |
|---|---|---|
| `commit04_validate.py` … `commit11_validate.py` (Phase 8), with the shared [_pipeline.py](../scripts/validation/_pipeline.py) | Stage 7 Commits 4–11: sites/H18, episodes/H3/H4, indexes/A13, rules, baselines/H15, matching, pairs and negative tests, balance and contamination | [evidence/stage7_commits/](evidence/stage7_commits/) |
| [scripts/validation/commit12_validate.py](../scripts/validation/commit12_validate.py) | Stage 7: 40 checks (H10/H11 plus negative tests, H13, H16_post, determinism, schemas, backups) | [evidence/commit12/](evidence/commit12/): `commit12_validate.out`, the canonical scratch log and diagnostics, and `self_review.md` |
| [scripts/validation/stage8_validate.py](../scripts/validation/stage8_validate.py) | Stage 8: 11 checks | [evidence/stage8/](evidence/stage8/) |
| [scripts/validation/stage9_validate.py](../scripts/validation/stage9_validate.py) | Stage 9: 12 checks | [evidence/stage9/](evidence/stage9/) |
| [scripts/validation/stage10_validate.py](../scripts/validation/stage10_validate.py) | Stage 10: 9 checks (corrected from "10" in Phase 9) | [evidence/stage10/](evidence/stage10/) |

Up to Phase 8, all the evidence came from **scratch runs** (`--out-dir` / `--out` in a temporary folder). The Phase 9 acceptance runs wrote the canonical Stage 7 and Stage 8 outputs into `data/processed` and ran Stages 9 and 10 into `outputs/robustness/` only. `outputs/` is unchanged. Evidence: [evidence/phase9/](evidence/phase9/) and [acceptance_report.md](acceptance_report.md).

Phase 8 moved the Commit 4–11 review validators into the repository and re-ran them against the current modules. Phase 9 (acceptance runs) produced the canonical evidence; see [acceptance_report.md](acceptance_report.md).

---

## 9. Known limitations [LIMITATION]

### 9.1 Methodological limitations M1–M8

| ID | Limitation | Evidence | Mitigation or status |
|---|---|---|---|
| M1 | **The estimand changes.** Results describe first-reported, isolated outages in quieter areas. | Matched baseline mean 5.77 vs 8.40 for all eligible treatments (SMD −0.19); about 70% of Stage 3 treatments are removed by the rules. | Representativeness is reported; it must be stated in the write-up. |
| M2 | **Pre-period confounding by outage-proneness.** | `prior_episodes_250m` SMD +0.35 | Report only (A1). A candidate robustness covariate or stratifier in S9, not implemented. |
| M3 | **Interference between units.** | Contamination D1 74%, D2 87%, D3 91%. 19.4% of pairs have another pair's treatment within 250 m, dark only outside the control's W. | The exclusion-500 sensitivity run (7,419 pairs, D2 = 0) |
| M4 | **Selection on the future, on both sides.** | Controls must stay clean through the whole of W; treatments must stay clean after closure (S-4 post). | The `pre_only` variant covers controls only (D2b: 59% of those controls go dark later). No variant covers the treatment-side S-4 post rule; adding one needs a design decision. |
| M5 | **The placebo population differs from the canonical one.** | 39,882 placebo-eligible vs 33,152 canonical; 25,720 placebo pairs; long outages can be post-dirty in the placebo by design. | Interpret it as a design-level placebo, not a same-units placebo. |
| M6 | **Closure data quality depends on the year.** | In 2020–22 darkness relies heavily on the 160 h imputation (F2). | Imputed-duration sensitivity runs at 50 and 720 h (D19; runner deferred); year shares reported |
| M7 | **"Provably not dark" really means "not reported dark".** | 36,188 complaints have no coordinates; unreported outages are invisible. | Stated as a caveat |
| M8 | **Known defects outside Issue 4 must be disclosed before any results are published.** | Stage 8 exposure is not normalised; `baseline_crime_intensity` is the pre-period outcome (a bad control); the event-study weeks skip days −7..0; `event_study.py` was duplicated (now fixed by D14). | Open. **Every Stage 9 and 10 result is provisional.** |

The M1–M8 list is taken from the final release review. No message explicitly approves the list itself; see [issue4_decisions.md](issue4_decisions.md).

### 9.2 Additional limitations recorded after Phases 3–5

| # | Limitation |
|---|---|
| L1 | `baseline_crime_intensity` keeps its existing definition (the unit's pre-window 100 m outcome count) and stays a covariate in the Stage 9 basic, separate, Poisson and NB models (M8). |
| L2 | The NB2 model uses the statsmodels default dispersion **α = 1.0**. α is not estimated, and statsmodels emits a `ValueWarning`. This behaviour predates Issue 4 and was not changed because the model specifications are frozen. |
| L3 | **Treatment outcome counts are centred on the complaint's exact point**; the control's are centred on the rounded site point. So the two roles use slightly different centre definitions (≤ 0.71 m offset from a site point). This is consistent with the Stage 7 baselines. |
| L4 | **The treatment's `location_key` and H3 cells are its site's, not its complaint point's** (U1). On the canonical panel, treatment `treatment_h3_res7` differs from the res-7 cell of `location_latitude/longitude` for 5 of 20,051 pairs, and `h3_res9` for 30. |
| L5 | **Endpoint sharing:** the canonical windows are inclusive and share endpoints. A crime exactly at `created` counts in both pre and during; one exactly at `closed` counts in both during and post. This predates Issue 4. |
| L6 | **The shifted pre-window changes `baseline_crime_intensity`** (§7.1 item 10). A shifted panel is indistinguishable from a canonical one in the Stage 9/10 outputs; the panel does not record its pre-window mode. |
| L7 | **The −7..0 day event-study gap** (§6.3). Flagged, not fixed. |
| L8 | **Provenance chain.** The Stage 8 panel records neither the sha256 of the pairs file it was built from, nor the pre-window mode, nor `placebo_shift_days`. The Stage 10 summary has no panel sha256, and no Stage 8–10 output records the git commit. |
| L9 | **Default output paths.** Stages 9 and 10 default to `outputs/`, which holds tracked legacy results, without a backup (§5.6, §6.4). |
| L10 | **The clustering structure in §5.4:** reused and dual-role sites span several primary clusters. |

---

## 10. Deferred work [DEFERRED]

| Item | Source | Status |
|---|---|---|
| Step 5 `src/models/robustness.py`: the D18/D19 grid through S7 → S8 → S9 into `outputs/robustness/<run_id>/` | Q6 | Out of scope for this cycle; open item for the release documents |
| Notebooks 05–08 | D16, Q7 | Stale banners approved but not yet scheduled; full realignment deferred |
| `outputs/robustness/` in `.gitignore` | Q8 | **Done** in Phase 7 |
| Delete the four empty legacy source files (`src/causal_analysis.py`, `src/preprocess.py`, `src/prioritization.py`, `src/spatial_linking.py`) | Q10 | **Done** in Phase 7 |
| Move the legacy results to `outputs/legacy_pre_issue4/` | Original Phase 7 plan | Not part of the Phase 7 scope given on 2026-09-30; not yet scheduled |
| Validation scripts in the repository (Commit 4–11 validators, shared helper, README) | Phase 8 plan | **Done** in Phase 8 |
| Hardening the Stage 8–10 validators (acceptance doc §5, gaps V-1 to V-11) | Phase 3–5 review | Not in the original Phase 8 scope; not yet scheduled |
| Canonical acceptance runs into `data/processed` | Phase 9 plan | **Done** in Phase 9 ([acceptance_report.md](acceptance_report.md)) |
| `docs/acceptance_report.md`, `docs/release_readiness_review.md` | Q11 | Phases 9 and 10 |
| M8 fixes (exposure normalisation, `baseline_crime_intensity`, the −7..0 gap) | M8 | Deferred; results stay provisional |
| Commit 6 fix-up P3 (group-id guard in `_build_sorted_index`) | P3 | Deferred by decision |
