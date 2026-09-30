# LightSafe Issue 4: decision log

Written for: capstone reviewers checking what was decided, when, by whom and where it is implemented.

## How to read this log

**Sources.** Every entry is taken from the recorded project conversation. The transcript files are local to the implementer's machine and are not in the repository:

| Short name | Transcript | Covers |
|---|---|---|
| T1 | `60e09e94` | Issues 1–3 |
| T2 | `94c2dae9` | Design freeze, blueprint, Commits 1–8 |
| T3 | `2fbabb3f` | Commits 8–12, the phase plan, Phases 1–5 |
| T4 | `5d1ea6ff` | Phase 3–5 review and Phase 6 |

`T2 L74` means line 74 (0-based) of that transcript's `.jsonl` file.

**Dates** are the UTC timestamp of the approving user message. Commit hashes are from `git log` on `issue4-step1`; commit times are local (+05:30).

**Status values:**
- **Implemented:** in code at `f804a78`.
- **Documented:** a documentation requirement, met by this Phase 6 documentation.
- **Not implemented:** for example D11 = No.
- **Pending:** approved but not yet done.
- **Needs sign-off:** implemented but never explicitly approved.

**Rationale** is quoted or closely paraphrased from the proposal or approval. Where the record gives none, the entry says **"not recorded"**.

**Needs verification** marks any value that could not be established from the record.

**Several ID families are reused.** This log always uses the family name:

| Family | Meaning |
|---|---|
| Issue 1–3 D1–D6 | The Issue 1–3 decisions |
| Issue 4 D7–D20 | The design decisions |
| Contamination D1–D5 | The Stage 7 diagnostics (Commit 11) |
| Phase 6 requests D1–D3 | Documentation requests (Commit 12 approval) |
| Blueprint C1–C6 | Blueprint clarifications |
| Commit 1 review C-1, C-2 | Commit 1 review findings |
| Conventions A1–A13 | Stage 7 conventions |
| Acceptance amendments A1–A3 | Changes to the acceptance checklist |
| Commit 6 M1–M4 | Commit 6 design modifications |
| Limitations M1–M8 | Known limitations |
| Commit 7 Q1–Q4, Commit 8 Q1–Q4, phase plan Q1–Q12 | Three separate question sets |

Stage 7 code references are to [src/features/match_controls.py](../src/features/match_controls.py) unless stated.

---

## 1. Issue 1–3 decisions (context)

All approved together (T1 L224, **2026-09-26T17:16:51Z**). They were implemented in `daaf1f2`, which is tagged `baseline-pre-issue4`.

| ID | Decision | Rationale | Status | Location |
|---|---|---|---|---|
| D1 | "D1 = a": change only the Stage 7 CRS constant (EPSG:32118, metres) | Leaving Stage 7 on EPSG:2263 (US feet) made pairs 30–76 m apart and zones share 53–81% of their area | Superseded by the Issue 4 rewrite; the CRS is kept | `PROJECTED_CRS` |
| D2 | Exclude motor-vehicle larceny (`ky_cd` 110, 342) | Keeps the original six categories without changing the methodology | Implemented | [clean_crime.py](../src/data/clean_crime.py) `EXCLUDED_KY_CD` |
| D3 | Stage 8 coverage guard | 14-day post windows ran past the last crime date and were counted as zero | Implemented; kept in Issue 4 and must drop 0 | [build_causal_panel.py](../src/features/build_causal_panel.py) `check_coverage` |
| D4 | Download crime only | Re-downloading 311 would change the outage set | Implemented | [download_data.py](../src/data/download_data.py) `--dataset` default `crime` |
| D5 | Edit notebook code cells; leave outputs stale | The pipeline runs from `src/` | Implemented | notebooks |
| D6 | Stage 6 chunked narrow spatial join | Stage 6 could not run in memory otherwise | Implemented | [spatial_linking.py](../src/features/spatial_linking.py) |

---

## 2. Issue 4 design decisions D7–D20

**Approval:** T2 L74, **2026-09-27T07:24:25Z** ("Treat the following decisions as FINAL"). The design text is T2 L2; the review verdicts and new decisions are T2 L68.

| ID | Decision (final) | Rationale (from design / review) | Status | Commit(s) | Location |
|---|---|---|---|---|---|
| D7 | Control universe = 311 sites clean over W, **modified** with 25 m episode merging, the geocode-artifact exclusion and the Stage 3 bounding box | Same population and reporting propensity as treatments; no external data (Issue 1–3 D4) | Implemented | `7039dcf`, `682eb6a`, `4007672`, `7fc8065` | `load_raw_complaints`, `build_sites`, `build_episodes` |
| D8 | S-3 exclusion radius 350 m; 250/500 in the sensitivity runs | 350 = 250 + 100: no other outage's direct zone touches the control's outcome zone. 500 is too sparse; 250 lets direct zones reach the ring | Implemented | `24f0c2f`, `3b0dd40` | `EXCLUSION_RADIUS_M` |
| D9 | Treatment rules: first complaint of the episode, and clean 100 m pre/post, with the S-4 post rule changed from "starts" to **"overlaps"** | Closes the hole where a duplicate report stays dark after closure | Implemented | `6cae561` | `apply_treatment_rules` |
| D10 | Caliper 0.5 SD; 0.2 in the sensitivity runs | More representative; balance still well under 0.1 | Implemented | `3b0dd40` | `CALIPER_SD` |
| D11 | **No**: `ANALYSIS_START` is not moved | No 311 data before 2020; 2020 has the messiest closure data; gain about +2k pairs | Not implemented (by decision) | none | `download_data.py`, `clean_crime.py` `ANALYSIS_START` |
| D12 | Reuse only with non-overlapping W, plus the cross-pair contamination diagnostic | Prevents the same crimes being counted in two concurrent pairs | Implemented | `e2d88fa`, `3b0dd40`, `e273ffb`, `c2bb341` | `ReuseRegistry`, `_check_h9`, `compute_contamination` |
| D13 | Primary clustering on the H3 res-7 cell of the treatment site. Review verdict: robustness by pair, two-way (T cell, C cell) and precinct; report cluster counts | Both pair members and reused sites were meant to share clusters (§10). The review found that many controls fall in another cell | Implemented | `25cc596`, `f804a78`; columns `e273ffb` | [did_model.py](../src/models/did_model.py) `cluster_schemes`; [event_study.py](../src/models/event_study.py) `CLUSTER_COLUMN` |
| D14 | Remove the duplicate `event_study.py` copy, as its own commit | Otherwise every edit has to be made twice | Implemented | `5c4fca1` | [event_study.py](../src/models/event_study.py) |
| D15 | Stage 9 FE unit = `unit_id` (pair × role) | Each unit then has 3 periods and constant treatment, as the demeaning assumes | Implemented | `9624b45`, `25cc596`, `f804a78` | `two_way_demean`; `estimate` |
| D16 | Realign notebooks 05–08 after `src/` is validated | As Issue 1–3 D5 | Pending: banners in Phase 7 (phase plan Q7); full realignment deferred | none | notebooks |
| D17 | Episodes merge sites within 25 m; sites above the 0.999 quantile of complaints are artifacts, excluded from both roles | 1 m rounding splits one pole into several sites; geocoder fallback points (for example 940 complaints in Central Park) are not streetlights | Implemented | `4007672`, `7fc8065` | `build_sites`, `build_episodes` |
| D18 | Placebo re-matches on shifted dates, with W, B and the clean rules all shifted | A placebo inside B, where matching equalised crime, would be biased toward a null | Implemented (Stage 7); the runner is deferred | `e2d88fa`, `7039dcf`, `6cae561`, `9ce8542` | `--placebo-shift-days`; `H16`, `H16_post` |
| D19 | Robustness set, one change at a time: exclusion 250/500; band max 2,000; caliper 0.2; never-reuse; pre-buffer 0; imputed duration 50/720 h; ITT controls; S8 pre = [−21, −7); 5 seeds | A full grid is 96+ runs (hours) | Parameters implemented (Stage 7 CLI; Stage 8 `--pre-window shifted`); the grid runner is deferred (phase plan Q6) | `a740307`, `9624b45` | `SENSITIVITY_FIELDS`; `--pre-window` |
| D20 | Add the paired-difference estimate and effects by baseline tercile and by year to Stage 9 | Each pair shares identical windows, so a simple mean cross-checks the regression; the estimand changes | Implemented. The tercile and year **definitions need sign-off** (§11) | `25cc596` | `paired_differences`, `paired_estimates` |

---

## 3. Step 1 blueprint clarifications and rules

**Approvals:** C1–C6 at T2 L103 (**2026-09-27T07:39:35Z**), re-confirmed at T2 L110 (**2026-09-27T07:47:43Z**) together with the two rules below. Blueprint: T2 L97. Engineering plan (12 commits, conventions A1–A12): T2 L122, approved at T2 L128 (**2026-09-27T07:54:04Z**).

| ID | Decision | Rationale | Status | Commit(s) | Location |
|---|---|---|---|---|---|
| C1 | Keep the output filename `control_area_pairs.parquet` | Design §8 keeps it, and S8 reads it | Implemented | `9ce8542` | `PAIRS_FILENAME` |
| C2 | S-4 pre ignores the treatment's own episode; S-4 post ignores only the treatment complaint | Duplicates start 7 days early and would fail pre falsely; widening post would reopen the D9 hole | Implemented | `6cae561` | `apply_treatment_rules` |
| C3 | Every eligible treatment's W is pre-registered as occupied at its own site | S-3 alone doesn't enforce design §6 | Implemented | `3b0dd40` | `init_reuse_registry` |
| C4 | Placebo: treatment dates, W, B, S-4 and S-3 windows move by −90 d; the real complaint becomes ordinary darkness; first-of-episode is judged on the real episode | D18 semantics | Implemented | `7039dcf`, `6cae561` | `load_treatments`, `apply_treatment_rules` |
| C5 | The band minimum stays 500 m in the exclusion-250 run | Zone disjointness is a hard rule | Implemented | `e2d88fa` | `MATCH_BAND_MIN_M` (locked) |
| C6 | 311 coverage guard: W inside the 311 coverage, with the lag | Consistency check. Expected to remove 0, but see the item 11 amendment | Implemented | `6cae561` | rule 2 `W_OUTSIDE_DARKNESS` |
| Artifact threshold rule | T = quantile(n_complaints, 0.999, "higher") over all sites; artifact if n > T; ties not artifacts; recorded in `universe` | Deterministic integer threshold | Implemented | `4007672` | `build_sites`; H18 |
| Seed-stability rule | deviation(s) = \|N_s − N_42\| / N_42 ≤ 0.02 for s = 101 and 202, each; balance must also pass per seed | Unambiguous acceptance | Acceptance criterion (item 15); evidence partly pending (see [issue4_acceptance.md](issue4_acceptance.md)) | none | [issue4_acceptance.md](issue4_acceptance.md) |
| 12-commit plan | Step 1 in 12 commits on `issue4-step1`, with `baseline-pre-issue4` on main | Smallest reviewable steps | Implemented (`e2d88fa` … `9ce8542` plus fix-ups) | see `git log` | acceptance amendment A3 |
| Conventions A1–A12 | As in [issue4_design.md §3.9](issue4_design.md) | Pin down time, overlap and distance semantics | Implemented | `e2d88fa` (text); per-commit use | `CONVENTIONS` |
| `artifact_threshold_is_maximum` logging | Optional logging addition | No methodology change | Implemented | `4007672` | `universe` |

---

## 4. Commit-level decisions (Stage 7)

| ID | Decision | Rationale | Approved (UTC) | Status | Commit(s) | Location |
|---|---|---|---|---|---|---|
| Commit 1 review C-1 | H0 rejects any non-finite float parameter | NaN or inf passed H0 and would silently empty the band or caliper | T2 L219, 2026-09-27T08:13:00Z | Implemented | `a740307` | `_non_finite_violations` |
| Commit 1 review C-2 | Cross-stage parameters are locked constants with H0 equality checks: `valid_min/max_duration_h` (Stage 3), `event_window_days` (Stage 10), `post_window_days` (Stage 8) | W must cover the downstream windows; the validity rule must equal Stage 3's | same | Implemented | `a740307` | `_validate_params` |
| Locked non-sensitivity parameters | `site_round_m`, `episode_merge_radius_m`, `artifact_quantile`, `treatment_clean_radius_m`, `prior_episode_radius_m`, `baseline_days`, `recheck_sample_n`, `recheck_seed`, `smd_max`, `vr_min`, `vr_max` are locked. Only the D19 parameters plus run control stay on the CLI | "These are fixed methodology values and must not be user-overridable" (T2 L238) | same; spec T2 L238 08:14:22Z; fix-up approved T2 L327 08:32:53Z | Implemented | `a740307` | `_build_parser`, `SENSITIVITY_FIELDS` |
| Exit code 3 | "Stage 7 incomplete; no outputs written" during the rollout | Avoid S8 silently reading the old pairs | same | Superseded: Commit 12 returns 0; 3 is no longer returned (docstring) | `a740307`, `9ce8542` | `EXIT_INCOMPLETE` |
| H9 interpretation (Commit 1) | See §7 | | T2 L219 | Implemented | `e273ffb`, `d91f1f8` | `_check_h9` |
| Commit 2 decision 1 | Unparseable `created_date` and invalid crime rows stay inside H1 | "These are input-validity checks and fit naturally within H1's purpose" | T2 L439, 2026-09-27T08:46:31Z | Implemented | `7039dcf` | `_check_h1` |
| Commit 2 m-4 | Git dirty flag ignores untracked files | Robustness outputs must not make canonical runs report dirty = True | same | Implemented | `b7767cf` | `_git_state` |
| Commit 2 m-5 | Exit 1 documented as "H0 failure, hard-check failure, or unhandled exception" | No behaviour change | same | Implemented (later extended with "determinism failure") | `b7767cf`, `9ce8542` | module docstring |
| H17 | New check: Stage 3 → Stage 7 valid-closure contract (`is_imputed == False` for every treatment), after step 4 | Not folded into H1 | same; Commit 3 approved T2 L508, 08:51:01Z | Implemented | `682eb6a` | `_check_h17` |
| H18 | New check: site and artifact integrity (six conditions), with no renumbering | The artifact rule had no hard check | T2 L518, 2026-09-27T08:53:36Z | Implemented | `4007672` | `_check_h18` |
| Site construction | `np.rint` 1 m, `E{x}_N{y}`, lat/lon from the rounded point, modal labels with alphabetical tie-break, `pd.NA` when all missing, ordered by `site_id` | | same | Implemented | `4007672` | `build_sites` |
| Artifact T = 44 | The observed canonical threshold (44 artifact sites) | | No separate approval of the value; covered by "Commit 4 is approved" (T2 L600) and the Commit 12 approval (T3 L886) | Observed | `4007672` | `universe.artifact_threshold` |
| Universe keys | Extra validation counts stay in the internal `universe` dict; `DIAGNOSTICS_UNIVERSE_KEYS` is unchanged | Keep the approved diagnostics schema | T2 L600, 2026-09-27T08:59:48Z | Implemented (the fix-up `0fd113d` restored the rule) | `4007672`, `0fd113d` | `DIAGNOSTICS_UNIVERSE_KEYS` |
| **Treatment borough = site modal borough** (Commit 7 decision) | Treatments and controls use the same borough definition | Same definition for both roles; H7 compares them | T2 L600, 2026-09-27T08:59:48Z | Implemented | `6cae561` | `apply_treatment_rules`, `NO_BOROUGH`, S-2 |
| **Treatment precinct = site modal precinct** (Commit 7 Q1) | `treatment_police_precinct` from the site's modal precinct | Same reasoning as the borough decision; used for D13 precinct clustering | T2 L826, 2026-09-27T09:39:34Z | Implemented | `6cae561`, `e273ffb` | `assemble_pairs` |
| Commit 5 note | H4 is only partly independent (same scipy KD-tree implementation); keep the wording accurate. First-of-episode count 88,388 replaces the 74,546 copy error | | T2 L653, 2026-09-27T09:05:36Z | Documented | `7fc8065` | [issue4_design.md §3.5, §3.11](issue4_design.md) |
| Commit 6 M1–M4 | int64 keys with bound asserts and a > b = empty; explicit-formula membership with a 1e-6 m KD margin; stable `lexsort`; boundary statistics logged | Byte-identical, A13-consistent indexes | T2 L678 09:10:21Z; T2 L685 09:12:18Z | Implemented | `24f0c2f` | `build_spatial_indexes` |
| Convention A13 | Exact text of T2 L682 §1; `KD_QUERY_TOLERANCE_M = 1e-6` | Methodology-level convention for all later radius and window logic | T2 L685, 2026-09-27T09:12:18Z | Implemented | `24f0c2f` | `CONVENTIONS["A13"]` |
| A13 amendment | Adds "a left-open window (a, b] is evaluated as the closed lookup [a + 1, b]", in both locations | Needed for S-4 post; no behaviour change | T2 L836, 2026-09-27T09:41:58Z | Implemented | `6cae561` | `CONVENTIONS["A13"]`, docstring |
| Commit 6 fix-up P1 | `_to_seconds` rejects sub-second times | Robustness guard for H10/H11 reproducibility | T2 L776 09:31:54Z; accepted T2 L816 09:37:16Z | Implemented | `3d32977` | `_to_seconds` |
| Commit 6 fix-up P2 | Episode links and H4 use the KD-tree for candidates and the explicit formula for membership | Resolves a known A13 inconsistency | same | Implemented | `3d32977` | `build_episodes` |
| Commit 6 fix-up P3 | Group-id guard in `_build_sorted_index` | Protects against programmer misuse, not an approved invariant | Deferred, T2 L816 | Deferred | none | none |
| Commit 6 fix-up P4 | Explicit empty-window mask | Already guaranteed and tested | Rejected, T2 L776 | Not implemented | none | none |
| Boundary diagnostics | Always on | Single execution path and diagnostics schema | T2 L816 | Implemented | `24f0c2f` | `BOUNDARY_DIAGNOSTIC_M` |
| Wording rule | Don't claim "every radius decision follows A13" without a documented audit | | T2 L776 | Documented ([issue4_design.md §3.9](issue4_design.md)) | none | none |
| Commit 7 Q3 (placebo S-4) | Placebo: no S-4 post complaint exemption (a consequence of C4) and no S-4 pre episode exemption (an implementation choice, output-equivalent while shift > lag). Both must be documented | Traced step by step from C4 and C2 | Not approved at T2 L826; **approved T2 L836, 2026-09-27T09:41:58Z** | Implemented and documented | `6cae561` | `apply_treatment_rules` comments; [issue4_design.md §3.3](issue4_design.md) |
| S-4 predicates | pre: s ≤ c − 1 and e ≥ c − 35 d; post: s ≤ W_end and e ≥ closed + 1 | A2 and A13 | T2 L836 | Implemented | `6cae561` | same |
| Commit 7 Q4 | No new hard check; internal attrition guard only | | T2 L826 | Implemented | `6cae561` | same |
| **Item 11 amendment** | Replace "`W_OUTSIDE_DARKNESS` = 0" with "No treatment is removed by `W_OUTSIDE_DARKNESS` alone." Do not reorder the rules | First-failure attribution; see [issue4_acceptance.md](issue4_acceptance.md) | T2 L920, 2026-09-27T09:53:26Z | Documented; recorded in `ACCEPTANCE_AMENDMENTS["item_11"]` | `6cae561`, `9ce8542` | `ACCEPTANCE_AMENDMENTS` |
| Brute-force validators | Scratchpad-only for Commits 6–7; their permanent counterparts are H10/H11 (+ H5/H6) | Keeping them would need a test directory, which was not in scope | Statement at T2 L924 in reply to the T2 L920 request | Documented | none | none |
| Commit 8 Q1 | Sample SD (ddof = 1) | Part of the matching specification | T2 L927 09:55:14Z; recorded T3 L58 2026-09-28T15:53:32Z | Implemented | `0bd8d2d` | `standardise` |
| Commit 8 Q2 | z = log1p(count) / SD, no centring | Differences cancel a common mean | same | Implemented | `0bd8d2d` | `standardise` |
| Commit 8 Q3 | `TREATMENT_QUERY_CHUNK = 5,000` | | T2 L927 | Implemented | `0bd8d2d` | constant |
| Commit 8 Q4 | Placebo SDs from the placebo-eligible population | Consistent with D18 re-matching | T2 L927 | Implemented | `0bd8d2d` | `standardise` |
| Baseline predicates | `base_100m` := d ≤ 100 m; `base_250m` := 100 < d ≤ 250 m (ring only); B membership := b_start_s ≤ t ≤ b_end_s − 1; controls use treatment SDs from the same run; H15 immediately after standardisation | | Asked for at T2 L927; stated after implementation (T2 L968); approved T3 L2 2026-09-28T14:51:11Z; recorded T3 L58 15:53:32Z | Implemented | `0bd8d2d` | `compute_treatment_baselines` |
| Commit 9 baseline assumptions | Greedy 1:1 NN; exact borough; z100 and z250 only; caliper per variable; pre-registration; control baselines in treatment B; treatment SDs | | T3 L154, 2026-09-28T15:57:16Z | Implemented | `3b0dd40` | `match_treatments` |
| Own-site exclusion | Not a separate filter: it follows from S-1, S-3, C3 and H12 | | T3 L304, 2026-09-28T16:24:38Z (Commit 9 approved) | Implemented | `3b0dd40` | none |
| H5 path | `treatment_key` → `complaints.unique_key` → `site_idx` → sites, independent of `treatments_eligible`, `MatchResult` and `ReuseRegistry` | Independence | T3 L332, 2026-09-28T16:27:49Z | Implemented | `e273ffb` | `_check_h5` |
| Commit 11 prior-episode clarification | Complaint-level spatial membership, episode-level deduplication, start in B, artifact complaints included | | T3 L495, 2026-09-28T16:53:00Z | Implemented | `c2bb341` | `_prior_episodes` |

### 4.1 O2–O7 (Commit 9)

| ID | Decision (final) | Rationale | Approved (UTC) | Status | Commit | Location |
|---|---|---|---|---|---|---|
| O1 | No individual approval; covered by "Everything else in the Commit 9 specification is approved" | | T3 L154 | none | `3b0dd40` | none |
| O2 | `n_candidates_band` = count(500 ≤ d ≤ `match_band_max_m` AND same borough AND `eligible_control`); nested chain band → clean → caliper; raw spatial-band counts in diagnostics only; `NO_CANDIDATE_IN_BAND` = "no usable control candidate in the band" | Each attrition difference belongs to one filter; H14 compares successive sets | T3 L161, 2026-09-28T16:01:33Z (revised from T3 L148 via L154/L158) | Implemented | `3b0dd40` | `build_band_candidates`, `match_treatments` |
| O3 | Count only the caliper candidates examined before selection, in ranked order; stop at the first assignment; `REUSE_CONFLICT` = all examined and blocked | Consistent with the greedy matcher | T3 L161, 2026-09-28T16:01:33Z | Implemented | `3b0dd40` | `match_treatments` |
| O4 | `band_dist` is float64 | float32 can create false ties and drift at the band edges (A13) | T3 L154, 2026-09-28T15:57:16Z | Implemented | `3b0dd40` | `build_band_candidates` |
| O5 | Caliper test in the H8 form: abs(log1p_t − log1p_c) ≤ `caliper_sd` × SD | The matcher and H8 can't disagree at the boundary | same | Implemented | `3b0dd40` | `match_treatments` |
| O6 | `rng.permutation(n)` over `treatment_key`-sorted rows | Fixes reproducibility | same | Implemented | `3b0dd40` | `match_treatments` |
| O7 | Lexicographic `site_id` tie-break | As in the blueprint | same | Implemented | `3b0dd40` | `match_treatments` |

### 4.2 U1–U8 (Commit 10)

U1–U6 and U8 were approved at T3 L322 (**2026-09-28T16:26:53Z**). U7 was omitted there and approved at T3 L332 (**2026-09-28T16:27:49Z**). Commit 10 was approved after the H9 fix-up at T3 L462 (**2026-09-28T16:47:47Z**).

| ID | Decision | Rationale | Status | Commit | Location |
|---|---|---|---|---|---|
| U1 | `treatment_h3_res7/9/10` = the treatment **site's** cells | D13 says "H3 res-7 of the treatment site"; controls use site cells | Implemented | `e273ffb` | `assemble_pairs` |
| U2 | H12 in two phases (without and then with `bal_*`) | The `bal_*` columns arrive in Commit 11 | Implemented; one diagnostics entry (Commit 12 difference 3) | `e273ffb`, `c2bb341` | `_check_h12` |
| U3 | H14 adds 0 ≤ `n_candidates_reuse_blocked` ≤ `n_candidates_caliper` − 1 | A consequence of O3 | Implemented | `e273ffb` | `_check_h14` |
| U4 | H6 mirrors rule 2, including the lag at both ends | H6 and rule 2 can't disagree | Implemented | `e273ffb` | `_check_h6` |
| U5 | H7 requires exact d == `distance_m` | Same formula and inputs | Implemented | `e273ffb` | `_check_h7` |
| U6 | Treatment–treatment overlaps are reported descriptively in H9 | Not violations (H9 interpretation) | Implemented (canonical 935 pairs, 876 sites) | `e273ffb`, `9ce8542` | `reuse.treatment_treatment_overlaps` |
| U7 | `outage_sites.n_times_treatment` / `n_times_control` are filled in Commit 12 | Keeps site-level accounting in one place | Implemented | `9ce8542` | `assemble_sites` |
| U8 | `outage_duration_hours` recomputed from the pair dates | The shift cancels in placebo runs | Implemented | `e273ffb` | `assemble_pairs` |

### 4.3 B1–B14 (Commit 11)

All approved at T3 L488 (**2026-09-28T16:52:11Z**). The methodology approval followed the prior-episode clarification at T3 L495 (16:53:00Z). **There is no standalone "Commit 11 approved" message.** The review record at T3 L550 (2026-09-28T17:05:20Z) says "Commit 11 (`c2bb341`) has been reviewed … No blocking defects were identified". All items are implemented in `c2bb341` (`compute_balance`, `compute_contamination`).

| ID | Decision |
|---|---|
| B1 | SMD = (mean_T − mean_C) / sqrt((s_T² + s_C²)/2), ddof = 1, with the specified zero-variance handling (both variances 0: SMD 0 if the means are equal, otherwise ±inf, which fails) |
| B2 | VR = s_T²/s_C², inclusive [0.5, 2]; strict \|SMD\| < 0.1. Both variances 0 → VR 1; only s_C² = 0 → inf, which fails |
| B3 | Prior-episode counts and H3 density raw only |
| B4 | Treatment-side density uses `treatment_h3_res9` (the site cell) |
| B5 | Prior episodes: `episodes.start_s` in [b_start_s, b_end_s − 1], artifact episodes included |
| B6 | Contamination D1 and D3 use d ≤ 500 m |
| B7 | Contamination D1 uses matched treatments of other pairs only |
| B8 | Contamination D2 always evaluates the full W; D2b exists only for `pre_only` runs |
| B9 | Contamination D4: darkness overlap with [created_s, closed_s], treatment complaint excluded, same-episode duplicates included |
| B10 | Contamination share denominator = number of pairs |
| B11 | Contamination D5 reuses the Commit 5 `episode_summary` |
| B12 | Per-variable pass flags plus `matched_pass` and `balance_only_pass`; report only, never raises |
| B13 | Representativeness metrics as specified (including the log1p SMD, matched vs eligible) |
| B14 | Single created-year and created-month distributions |

### 4.4 Contamination diagnostics D1–D5 (Commit 11)

| Item | Record |
|---|---|
| Definitions | Proposed at T3 L482 §8; approved through B6–B11 (T3 L488) |
| Canonical results and explanation | T3 L547 (Commit 11 review package). Brute force on 300 pairs reproduced D1–D4 exactly. The shares are a scale effect; D2 is the residual risk named in design §2; the exclusion-500 run gives D2 = 0.000 and D1 = 0.320 (7,419 pairs) |
| Reviewer conclusion | T3 L550, 2026-09-28T17:05:20Z: "The contamination metrics follow the approved definitions and were independently reproduced. No blocking methodology or implementation issues were identified." |
| Acceptance rule | Acceptance amendment A2 (§6) |
| Non-blocking follow-ups from T3 L550 | `inf` serialisation → N1; `CONTAMINATION_REVIEW_SHARE` recorded in parameters → N11; the `compute_contamination` signature → N12 (documented in [issue4_design.md §3.16](issue4_design.md)) |

---

## 5. Commit 12 specification

**No single approval message.** X1–X3, R1–R4, E1–E3 and acceptance amendments A1–A3 took their final wording in the user's T3 L602 (**2026-09-28T17:16:03Z**; earlier versions T3 L565, L592). The assistant declared them ready at T3 L606 (17:16:37Z). The user froze them:
- T3 L634 (**2026-09-28T17:25:21Z**): "All have already passed review and should be treated as frozen requirements".
- T3 L678 (**2026-09-28T17:31:28Z**).

T3 L886 (**2026-09-28T18:05:47Z**) marks X1–X3, R1–R4 and E1–E3 PASS for the implementation.

| ID | Decision (final) | Rationale | Status | Commit(s) | Location |
|---|---|---|---|---|---|
| X1 | Spatial operations generate recheck candidates only; membership by the explicit formula; coordinates rebuilt from raw | Buffers are inscribed polygons (up to about 0.42 m inside at 350 m), which would give false H11 mismatches and H10 misses (A13) | Implemented | `9ce8542` | `_recheck_candidates`, `RECHECK_CANDIDATE_MARGIN_M = 1.0` |
| X2 | H10 rebuilds the own episode, artifact flags and darkness intervals from raw | Reusing the production `episode_id` would not be independent | Implemented | `9ce8542` | `_recheck_complaints`, `_recheck_own_episode` |
| X3 | Operational safeguards: back up `control_area_pairs.parquet` as `.pre_issue4.parquet`, verify the sha256, never overwrite; document that `causal_panel.parquet` stays pre-Issue-4 and that S8–S10 outputs are invalid until regeneration; scratch runs use `--out-dir` | `data/processed` is git-ignored; stale-panel hazard | Implemented as an automatic backup (Commit 12 difference 4, approved). The stale-panel hazard is documented in the README | `9ce8542` | `preserve_legacy_pairs` |
| R1 | S-4 rules as in [issue4_design.md §3.3](issue4_design.md) (pre: own-episode exemption; post: treatment complaint only; placebo: none; shifted treatment windows, real complaint dates) | The post exemption must not widen to the own episode | Implemented | `9ce8542` | `independent_recheck` |
| R2 | Episode reconstruction from raw: full BFS closure, non-artifact links ≤ `episode_merge_radius_m` with overlapping darkness, no caps; artifact rule "higher", > T; artifact complaints stay darkness; a treatment at an artifact site is an H10 violation | Independence; no reliance on the observed 44.2 m footprint | Implemented | `9ce8542` | `_recheck_own_episode` |
| R3 | Candidates at radius + 1.0 m; explicit float64 membership in EPSG:32118; pyproj `always_xy` projection with exact treatment equality; control site validation; W, B and S-4 windows rebuilt and compared; crime null rule; darkness rule with run parameters | Pins down geometry, time and inputs | Implemented | `9ce8542` | `independent_recheck` |
| R4 | Say "S-3 clean window", not "control contamination window"; the `pre_only` window is [w_start_shifted, created_shifted − 1 s] | "Contamination" refers to contamination D1–D5 | Implemented | `9ce8542` | docstring |
| E1 | Every H10/H11 distance comes from the run's `Params` | A hard-coded 350 m would break the exclusion-250/500 runs | Implemented | `9ce8542` | `independent_recheck` |
| E2 | Complaint universe = non-null lat/lon inside `NYC_BBOX`, bounds inclusive; an unparseable created date or duplicate key is an H10 violation, not an exclusion | H1 never excludes; it stops the run | Implemented | `9ce8542` | `_recheck_complaints` |
| E3 | Explicit S-3 recheck definition (full_window / pre_only windows; all complaints count; real dates; no exemptions) | S-3 was never stated | Implemented | `9ce8542` | `independent_recheck` |
| H11 exact recount | Same sample; four baseline counts exactly equal; no tolerance | | Implemented | `9ce8542` | `independent_recheck` |
| N1–N15 | Commit 12 design details (JSON safety, H13 accounting N3/N4, H16_post N5, determinism N6, rename order N7, failure file N8, sample N9, Arrow types N10, parameter recording N11, signatures N12, H9 examples N13, unmatched nulls N14, exit 0 N15) | Non-blocking recommendations made requirements | Phase plan Q2, T3 L678, 2026-09-28T17:31:28Z | Implemented, except N12, which is **documented in Phase 6** ([issue4_design.md §3.16](issue4_design.md)) | `9ce8542`, `0fd113d` | see [evidence/commit12/self_review.md](evidence/commit12/self_review.md) |
| N5 / H16_post | Formal post-matching H16 definition | The open item | Phase plan Q1, T3 L678 | Implemented | `9ce8542` | `_check_h16_post`, `H16_POST_DEFINITION` |
| V1 | Full-population recheck | Suggested as non-blocking | **Not adopted**: Commit 12 difference 9, approved T3 L886 | Not implemented | none | none |

### 5.1 Commit 12 approval

T3 L886 (**2026-09-28T18:05:47Z**) approved `9ce8542`, `0fd113d` and `a167ab6`, and "consider[s] Commit 12 approved".

| ID | Decision | Status |
|---|---|---|
| Difference 1 | **A determinism mismatch is a hard failure, exit code 1, with the failure diagnostics written.** "Continuing and writing outputs would create ambiguity about which result is authoritative." Must be recorded in `issue4_design.md` and `issue4_acceptance.md` | Implemented (`9ce8542`); **documented** ([issue4_design.md §3.13](issue4_design.md)) |
| Difference 2 | Post-matching check recorded as `H16_post` | Implemented |
| Difference 3 | One H12 diagnostics entry | Implemented |
| Difference 4 | Automatic X3 backup | Implemented |
| Difference 5 | `write_outputs` timing in the log only | Implemented |
| Difference 6 | The failure file goes to the requested `--out-dir` | Implemented |
| Difference 7 | Extra counter `n_s4_pre_own_episode_exempted` | Implemented |
| Difference 8 | Signature changes; "Still document in Phase 6" | **Documented** ([issue4_design.md §3.16](issue4_design.md)) |
| Difference 9 | 2,000-pair recheck sample | Implemented |
| Phase 6 request D1 | Document the determinism failure: pair-list hash mismatch, order hash mismatch, exit 1 | **Documented** ([issue4_design.md §3.13](issue4_design.md), [issue4_acceptance.md](issue4_acceptance.md) item 14) |
| Phase 6 request D2 | Canonical values 20,051 pairs / 46,034 sites / 87,680 unmatched / 107,731 total as acceptance evidence | **Documented** ([issue4_acceptance.md](issue4_acceptance.md) items 2, 3) |
| Phase 6 request D3 | "36 sampled treatments required the own-episode exemption" | **Documented** ([issue4_design.md §3.12](issue4_design.md), [issue4_acceptance.md](issue4_acceptance.md) item 7) |

---

## 6. Acceptance amendments A1–A3

Final text from T3 L602 (2026-09-28T17:16:03Z), frozen at T3 L634 and T3 L678. They are recorded verbatim in `ACCEPTANCE_AMENDMENTS` ([match_controls.py:4530](../src/features/match_controls.py#L4530)) and applied in [issue4_acceptance.md](issue4_acceptance.md).

| ID | Final text (T3 L602) |
|---|---|
| A1 | "Balance-only diagnostics are reported and reviewed. Each failed balance-only diagnostic requires written explanation. Failure occurs only if the reviewer concludes the result demonstrates: matching defect, leakage, implementation error, methodological violation. Known example: prior_episodes_250m." |
| A2 | "Contamination review shares require: reporting, verification, explanation. The Commit 11 contamination review package satisfies this requirement for the canonical c2bb341 definitions. Changes to contamination definitions or implementation require re-verification. Sensitivity and placebo runs are report-only." |
| A3 | "The original one-commit requirement is replaced by the approved multi-commit rollout plan. The baseline-pre-issue4 tag requirement remains valid and satisfied." |

The code's `ACCEPTANCE_AMENDMENTS` wording differs slightly from T3 L602; for example, A1 adds "in the Step 1 report". The meaning is the same. The T3 L602 text is authoritative.

---

## 7. H9 interpretation

| Step | Decision | Approved (UTC) |
|---|---|---|
| Original (Commit 1) | Treatment–treatment overlap at a site is allowed and reported. Control–control and control–treatment overlap are forbidden. H9 fails only when a control interval overlaps another interval at the same site. Under `never`, a control site appearing twice fails. H9 rebuilds its intervals independently of `ReuseRegistry` | T2 L219, 2026-09-27T08:13:00Z |
| Endpoints (Commit 10) | Closed [w_start_s, w_end_s] for control uses and for every eligible treatment's W; touching endpoints overlap; no tolerance; the same predicate as `ReuseRegistry` | T3 L426, 2026-09-28T16:41:29Z |
| Fix-up `d91f1f8` | An unresolved `control_site_id` or `treatment_key` is an H9 violation recorded through `CheckResult`; those rows are left out of the sweep; later pair checks still run | T3 L462, 2026-09-28T16:47:47Z |

Location: `_check_h9`; module docstring "H9 (approved interpretation)".

---

## 8. Phase plan decisions Q1–Q12

All approved at T3 L678 (**2026-09-28T17:31:28Z**).

| ID | Decision | Status | Commit(s) / location |
|---|---|---|---|
| Q1 | N5 is the formal H16 (post-matching) definition | Implemented | `9ce8542` |
| Q2 | N1–N15 become Commit 12 requirements | Implemented (N12 documented in Phase 6) | `9ce8542` |
| Q3 | Full Step 3 scope (D13, D15, D20, clustering variants, cluster counts, CLI) | Implemented | `25cc596` |
| Q4 | Full Step 4 scope, D14 as its own commit, `unit_id` FE, H3 res-7 clustering | Implemented | `5c4fca1`, `f804a78` |
| **Q5** | **Stage 7 pair columns are the authoritative treatment geometry source.** "Record as a documented deviation from Design §9 with rationale (single source of truth, H1 already guarantees agreement)." | Implemented; documented ([issue4_design.md §7.1](issue4_design.md)) | `9624b45` |
| Q6 | Step 5 (`robustness.py`, D18/D19 grid) out of scope; record it as open | Deferred | none |
| Q7 | Stale banners on notebooks 05–08 only | Pending (Phase 7) | none |
| Q8 | Add `outputs/robustness/` to `.gitignore` | **Pending: not yet done** | none |
| **Q9** | **Back up `causal_panel.parquet` exactly as proposed**, with hash verification and no overwrite | Implemented | `9624b45` (`preserve_legacy_panel`) |
| Q10 | Delete the four empty legacy source files and mention it in the implementation report | **Pending: not yet done** | none |
| Q11 | Reports at `docs/acceptance_report.md` and `docs/release_readiness_review.md` | Pending (Phases 9, 10) | none |
| Q12 | One commit per phase; review stops after Phases 2, 5 and 9; no push or merge to `main` until acceptance is complete | In force | none |

**Additional instructions** (T3 L678), all in force:
1. The Commit 12 specification is frozen; stop and report on any contradiction.
2. Preserve acceptance evidence in stable locations referenced from `docs/acceptance_report.md`.
3. Do not regenerate Stage 9/10 results for publication while M8 is open.
4. Write the Stage 8–10 migration note before modifying those stages. Done in `a4a9b89`.

**Q12 wording:** the proposal said "one commit per phase (5a/5b split)". The approval says "One commit per phase". Phases 3–5 were delivered as five commits (`a4a9b89`, `9624b45`, `25cc596`, `5c4fca1`, `f804a78`), because Q4 requires D14 in its own commit. Whether this satisfies Q12 **needs verification** by the reviewer.

---

## 9. Phases 3–5: Stage 8–10 migration decisions

| Item | Record | Status | Commit(s) |
|---|---|---|---|
| Migration note first | Additional instruction 4 (T3 L678) | Done | `a4a9b89` |
| Stage 8 migration | Design §9 + Q5 + Q9 + D19 `--pre-window` | Implemented | `9624b45` |
| Stage 9 migration | Design §10 + D13 (review form) + D15 + D20 | Implemented | `25cc596` |
| Stage 10 D14 deletion | Its own commit (Q4) | Implemented | `5c4fca1` |
| Stage 10 migration | Design §11 + D13 + D15; week labels corrected (F5); −7..0 gap flagged, not fixed | Implemented | `f804a78` |
| Phase 3–5 approval | **No approval in T3** (the package at T3 L1085, 2026-09-28T18:22:05Z, has no reply). In T4 (**2026-09-30T15:21:49Z**) the user reports: "The Phase 3–5 independent review has been completed. Its verdict is: No blocking issues found. Stage 8, Stage 9 and Stage 10 are considered implementation-correct." | Accepted by the user as implementation-correct; the items in §11 remain open | none |

---

## 10. Known limitations list M1–M8

Proposed in the final release review (T3 L631, 2026-09-28T17:22:24Z; text in [issue4_design.md §9.1](issue4_design.md)).

**There is no explicit approval of the list itself.** It is referenced by the approved phase plan ("known limitations M1–M8" in the README, T3 L672, approved at T3 L678), and the user refers to "M8 is still open" (T3 L678). Status: recorded as limitations, not approved design decisions.

---

## 11. Open items requiring explicit sign-off

These are implemented, but no approval exists in the record. They are **not** frozen decisions.

| # | Item | Implemented as | Source of the gap |
|---|---|---|---|
| S1 | Two-way clustering form | (`treatment_h3_res7`, `control_h3_res7`), following the D13 review verdict. The design D13 table and §10 say "(treatment site, control site)". Confirm the review form | Design/review discrepancy |
| S2 | Missing precinct | One `<missing>` cluster (101 canonical pairs) | Implementation detail, not separately approved |
| S3 | Minimum primary clusters | A hard stop below 50. The review said "it must be at least 50" (reporting) | Stricter than the original requirement |
| S4 | D20 "by year" | Calendar year of `created_date` | Not specified in the approved text |
| S5 | D20 terciles | Quantile edges (1/3, 2/3) of the treatment's log1p(`base_100m`); ties at an edge go to the lower tercile | Not specified in the approved text |
| S6 | Stage 10 additional outputs | `event_study_summary.json`, "(provisional)" plot title, byte-reproducible PNG | §11 said outputs unchanged |
| S7 | Q12 commit rhythm for Phases 3–5 | Five commits (§8) | Wording of Q12 |

---

## 12. Superseded or corrected text

| Text | Correction | Where |
|---|---|---|
| Design §3/§11 "weeks −5…+5" | Weeks −4…+4 (review F5) | [issue4_design.md §6.3](issue4_design.md) |
| Design §13 "First complaint of its episode = 74,546" | A copy of the Stage 3 discard count (F6); re-derived as 88,388 first-of-episode treatments | [issue4_design.md §3.5](issue4_design.md) |
| Design §11 "delete lines 526–1014 … the second copy never runs" | Deleted L1–17 and L525–1014; the second copy did run | [issue4_design.md §6.1](issue4_design.md) |
| Acceptance item 11 "`W_OUTSIDE_DARKNESS` = 0" | Item 11 amendment | [issue4_acceptance.md](issue4_acceptance.md) |
| Acceptance item 20 "Step 1 in one commit" | Acceptance amendment A3 | [issue4_acceptance.md](issue4_acceptance.md) |
| Migration note "Standard errors are expected to be larger" and "78 precincts" | Corrected in Phase 6: no SE direction is claimed; 77 observed precinct values + 1 `<missing>` cluster | [issue4_migration_s8_s10.md](issue4_migration_s8_s10.md) |
