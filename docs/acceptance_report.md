# LightSafe Issue 4: Phase 9 acceptance report

Written for: the Issue 4 reviewer, who decides whether Step 1 and the Stage 8–10 migration are accepted. It reports what the Phase 9 acceptance runs did and found. It does not make the acceptance decision.

- **Run date:** 2026-09-30, from 16:46:47 to 17:00:16 UTC.
- **Code:** branch `issue4-step1` at `6d25204`, clean working tree (git dirty flag false in every Stage 7 diagnostics file).
- **Scope:** the Phase 9 plan (T3 L672), checked against the 20-item Step 1 checklist in [issue4_acceptance.md](issue4_acceptance.md) and the Stage 8–10 criteria:
  - Stage 7 canonical run twice as separate processes, compared by hash (item 14);
  - seeds 101 and 202 (item 15);
  - the caliper 0.2 smoke run into `outputs/robustness/smoke` (item 16);
  - placebo 90 (item 17);
  - the pyarrow schema check and H0–H18 (H16 in the placebo run);
  - Stages 8, 9 and 10 end to end.
- **Evidence:** everything cited here is in [evidence/phase9/](evidence/phase9/). [acceptance_summary.json](evidence/phase9/acceptance_summary.json) collects the values used below.

## Summary

| Area | Result |
|---|---|
| Stage 7 hard checks H0–H18 and `H16_post`, all six runs | **Pass**: 20 of 20 recorded checks pass in every run |
| Commit 12 outputs (four files, accounting, diagnostics) | **Pass** |
| Determinism (item 14) | **Pass**: two separate processes give identical pair-list and order hashes and byte-identical parquet files; the in-process second pass is identical |
| Schema checks (item 18) | **Pass**: every run's three parquet files match `PAIRS_DTYPES`, `SITES_DTYPES` and `UNMATCHED_DTYPES` in pyarrow |
| Diagnostics | **Pass**: strict JSON, every section present, `non_finite` empty, provenance recorded |
| Migration success | **Pass**: first canonical writes into `data/processed`. Both legacy files were backed up byte-identically (X3, Q9) |
| Stage 8 | **Pass**: canonical panel built; `stage8_validate.py` 11/11 |
| Stage 9 | **Pass**: end-to-end run exit 0 (scratch `--out`); `stage9_validate.py` 12/12 |
| Stage 10 | **Pass**: end-to-end run exit 0 (scratch `--out`); `stage10_validate.py` 9/9 |
| `commit12_validate.py` | **38 of 40 pass; 2 FAIL** because of the post-migration state of their fixture, not because X3 failed (§5) |
| Checklist items 1–20 | 18 pass or pass under an approved amendment. Item 9 is **partly met** (hand check not repeated). Item 19 is **met**, with one behaviour not exercised in Phase 9 (§4) |

**No Stage 9 or Stage 10 estimate is reported here.** M8 is open. Those runs wrote only to `outputs/robustness/` (git-ignored). Their stdout logs are not in the repository, and only their exit codes, output hashes and structural facts are recorded. The legacy files in `outputs/` are unchanged (§7).

The validator outputs are kept verbatim, as in the Phase 5 evidence. `stage10_validate.out` includes the validator's own detail strings: the pre-trend F statistic, and the last coefficient line of each extra-panel run. These are validator diagnostics, not reported results.

---

## 1. Platform and environment

| Item | Value |
|---|---|
| Platform | `Windows-11-10.0.26200-SP0` |
| Python | 3.14.4 (`.venv`) |
| Packages (Stage 7 provenance) | numpy 2.5.3, pandas 3.0.6, scipy 1.18.1, geopandas 1.1.4, shapely 2.1.2, h3 4.5.0, pyproj 3.8.0, pyarrow 25.0.1, psutil 7.2.2 |
| Also used by Stages 9–10 | statsmodels 0.15.0, matplotlib 3.11.2 |
| Git | `6d252048bb9c4b3ebc8cca3b1793dfd01fc3c5f4`, branch `issue4-step1`, dirty = false |

**Inputs** (unchanged by Phase 9; the baseline and after-run hashes are equal):

| File | Size (bytes) | sha256 |
|---|---|---|
| `data/raw/streetlight_complaints.csv` | 136,324,946 | `be0cb278dc9a7cc01f5caccc0f0e69925681e059367771d5e14fcbd463b4d7de` |
| `data/raw/nypd_crime.csv` | 398,588,072 | `5e4ad83039310b3843261cef8739e4f3e5acd70480d0fcb742d466417d4cddbd` |
| `data/processed/clean_streetlights.parquet` | 10,331,206 | `502a906f2733ef629a9877cc8fa4a86b8d60f94a923ef9d0ef1ad5329d29c948` |
| `data/processed/clean_crime.parquet` | 30,075,992 | `51f1e8d53934a3f0ae6ab9bdb3f8a3b544743dbc6ee398a89dfa2975488c073d` |

---

## 2. Runs

All commands were run from the repository root with `.venv/Scripts/python.exe`, one at a time, in this order.

**How they were measured:** wall time and peak RSS (process tree, polled every 0.2 s) came from a local wrapper script that is not in the repository. The Stage 7 diagnostics record their own runtime and peak working set as well. Full records: [runs.jsonl](evidence/phase9/runs.jsonl).

| # | Run | Command arguments | Exit | Wall (s) | Peak RSS (MB) | Log |
|---|---|---|---|---|---|---|
| 1 | Stage 7 canonical | `src/features/match_controls.py --check-determinism` | 0 | 49.8 | 1,396 | [stage7_canonical.log](evidence/phase9/stage7_canonical.log) |
| 2 | Stage 7 canonical, second process | `… --out-dir outputs/robustness/canonical2` | 0 | 41.5 | 1,316 | [stage7_canonical2.log](evidence/phase9/stage7_canonical2.log) |
| 3 | Stage 7 seed 101 | `… --match-seed 101 --out-dir outputs/robustness/seed101` | 0 | 39.3 | 1,329 | [stage7_seed101.log](evidence/phase9/stage7_seed101.log) |
| 4 | Stage 7 seed 202 | `… --match-seed 202 --out-dir outputs/robustness/seed202` | 0 | 81.8 | 1,415 | [stage7_seed202.log](evidence/phase9/stage7_seed202.log) |
| 5 | Stage 7 smoke run | `… --caliper-sd 0.2 --out-dir outputs/robustness/smoke` | 0 | 37.5 | 1,333 | [stage7_smoke_caliper02.log](evidence/phase9/stage7_smoke_caliper02.log) |
| 6 | Stage 7 placebo | `… --placebo-shift-days 90 --out-dir outputs/robustness/placebo90` | 0 | 42.9 | 1,385 | [stage7_placebo90.log](evidence/phase9/stage7_placebo90.log) |
| 7 | Stage 8 validator (before the canonical panel write) | `scripts/validation/stage8_validate.py data/processed outputs/robustness/placebo90` | 0 | 40.1 | 912 | [stage8_validate.out](evidence/phase9/stage8_validate.out) |
| 8 | Stage 8 canonical | `src/features/build_causal_panel.py` | 0 | 6.5 | 466 | [stage8_canonical.log](evidence/phase9/stage8_canonical.log) |
| 9 | Stage 8 placebo panel | `… --pairs outputs/robustness/placebo90/control_area_pairs.parquet --out outputs/robustness/placebo90/causal_panel.parquet` | 0 | 8.2 | 537 | [stage8_placebo90.log](evidence/phase9/stage8_placebo90.log) |
| 10 | Stage 8 shifted panel | `… --pre-window shifted --out outputs/robustness/shifted/causal_panel.parquet` | 0 | 6.6 | 470 | [stage8_shifted.log](evidence/phase9/stage8_shifted.log) |
| 11 | Stage 9 end to end | `src/models/did_model.py --out outputs/robustness/phase9_stage9` | 0 | 10.2 | 1,720 | not kept (M8) |
| 12 | Stage 10 end to end | `src/models/event_study.py --out outputs/robustness/phase9_stage10` | 0 | 8.0 | 578 | not kept (M8) |
| 13 | Stage 9 validator | `scripts/validation/stage9_validate.py data/processed/causal_panel.parquet data/processed/control_area_pairs.parquet outputs/robustness/placebo90/causal_panel.parquet outputs/robustness/shifted/causal_panel.parquet` | 0 | 40.0 | 2,076 | [stage9_validate.out](evidence/phase9/stage9_validate.out) |
| 14 | Stage 10 validator | `scripts/validation/stage10_validate.py data/processed/causal_panel.parquet outputs/robustness/placebo90/causal_panel.parquet outputs/robustness/shifted/causal_panel.parquet` | 0 | 46.8 | 1,260 | [stage10_validate.out](evidence/phase9/stage10_validate.out) |
| 15 | Commit 12 validator | `scripts/validation/commit12_validate.py` | 0* | 257.1 | 2,359 | [commit12_validate.out](evidence/phase9/commit12_validate.out) |

\* `commit12_validate.py` prints its `FAILS:` line but does not set a non-zero exit code. Its result is judged by that line (§5), not by the exit code.

**Ordering note.** The Stage 8 validator (run 7) was run before the canonical panel write (run 8). Its legacy-backup check copies `data/processed/causal_panel.parquet` as the legacy fixture, so it can only pass while that file is still the pre-Issue-4 panel. This is the same fixture dependency as gap V-10 (§5). No validator was changed.

---

## 3. The 20 Step 1 items

The criteria and amendments are as approved in [issue4_acceptance.md](issue4_acceptance.md). "Canonical" means run 1, at `6d25204`.

| # | Item | Result | Evidence |
|---|---|---|---|
| 1 | Canonical run completes with 0 hard-check failures | **Pass.** Exit 0. All 20 recorded checks pass: H0–H18 plus `H16_post`. In the canonical run `H16_post` records "pass" with 0 violations; it has nothing to check when the shift is 0, and the Commit 12 evidence shows the same | `match_diagnostics.canonical.json` → `hard_checks`; stage7_canonical.log |
| 2 | Pair count reported; expected 18k–25k | **Pass.** 20,051 pairs | diagnostics `outputs`; `control_area_pairs.parquet` row count |
| 3 | pairs + unmatched = 107,731 | **Pass.** 20,051 + 87,680 = 107,731; 46,034 sites; H13 pass | row counts; H13 |
| 4 | Matched variables: \|SMD\| < 0.1 and 0.5 ≤ VR ≤ 2 | **Pass.** log_base_100m SMD +0.0050 (VR 1.021); log_base_250m SMD +0.0166 (VR 1.040) | diagnostics `balance.matched` |
| 5 | Balance-only variables (acceptance amendment A1) | **Pass under A1.** `prior_episodes_250m` fails its flag (SMD +0.348, VR 1.492); the written explanation and reviewer conclusion are in [issue4_acceptance.md §2.2](issue4_acceptance.md), and it stays listed as limitation M2. Every other flag passes: h3r9_density +0.040; pre_100m +0.010; pre_250m +0.041; log_pre_100m +0.007; log_pre_250m +0.040. The same single flag fails for seeds 101 and 202 | diagnostics `balance.balance_only` |
| 6 | Representativeness reported | **Pass.** Matched mean `base_100m` is 5.77 (median 3, p90 15) against 8.40 for all eligible (median 4, p90 21); log1p SMD −0.185 (M1). Borough and year shares are in the diagnostics | diagnostics `balance.representativeness` |
| 7 | H10 recheck: 0 violations on 2,000 pairs | **Pass.** 2,000 pairs (seed 20260927); all 16 H10 conditions 0; all 4 H11 counts exact. **36** sampled treatments needed the own-episode S-4 pre exemption (R1 is exercised) | diagnostics `recheck` |
| 8 | Contamination D1–D5 (acceptance amendment A2) | **Pass under A2.** D1 0.743, D2 0.868, D3 0.908 (all above the 5% review share); D4 mean 0.057 (95.8% zero); D5: 152,656 episodes, 0 over 100 m, 222 over 365 days, maximum footprint 44.2 m. Identical to the Commit 11 canonical values that A2 accepts for the `c2bb341` definitions | diagnostics `contamination` |
| 9 | Artifact threshold and sites reported; top artifact sites checked by hand | **Partly met.** T = 44 and 44 artifact sites are reported (H18 pass), and the top-10 table is recorded below. The hand check of their coordinates was done by the implementer in the Commit 4 report and approved with Commit 4. It was **not repeated** in Phase 9 | diagnostics `universe`; the table below |
| 10 | Attrition table telescopes | **Pass.** 13 rows (steps 0–12); H13 telescoping pass; 16,972 dropped at step 6. The re-derived first-of-episode count (88,388 Stage 3 treatments, replacing the 74,546 copy error) is asserted by the Phase 8 `commit05_validate.py` ([evidence/stage7_commits/](evidence/stage7_commits/)) and was not re-run in Phase 9 | diagnostics `attrition`; stage7_canonical.log |
| 11 | All 12 reason codes present; "No treatment is removed by W_OUTSIDE_DARKNESS alone" | **Pass.** All 12 present. Canonical: all 1,589 treatments attributed to `W_OUTSIDE_DARKNESS` also fail `B_OUTSIDE_CRIME`. Placebo: all 1,242 do. In both runs 0 treatments are removed by rule 2 alone. Checked from the unmatched tables and coverage (B = [c − 400 d, c − 35 d) against the crime coverage) | diagnostics `unmatched_reasons`; acceptance_summary.json `item11` |
| 12 | Timings and peak memory in the JSON; < 5 min and < 4 GB | **Pass.** Canonical diagnostics: 47.5 s total, 1,524 MB peak. Every Stage 7 run is under 82 s and 1,540 MB | diagnostics `runtime`; §2 |
| 13 | Provenance in the JSON | **Pass.** git SHA, dirty flag, platform, packages, input sizes and sha256, and all parameters | diagnostics `provenance`, `parameters` |
| 14 | Determinism: two runs with seed 42 give identical pair-list hashes | **Pass.** Runs 1 and 2 (separate processes): pair-list sha256 `ab63c3773819247367c0a8261d06e7f0f9639d40525e817698950e85e516ee8b` and order hash `b36524dc…` in both; the three parquet files are byte-identical. Run 1's `--check-determinism` second pass is identical. A mismatch would stop the run with exit 1 before writing ([issue4_acceptance.md §2.4](issue4_acceptance.md)); none occurred | diagnostics `determinism`; §6 hashes |
| 15 | Seeds 101 and 202 within ±2% of seed 42; balance passes per seed | **Pass.** Seed 101: 20,011 pairs, deviation 0.199%. Seed 202: 19,996 pairs, deviation 0.274%. Matched balance passes for both (seed 101: SMD +0.0055 / +0.0160; seed 202: +0.0047 / +0.0175) | `match_diagnostics.seed101.json`, `match_diagnostics.seed202.json` |
| 16 | Caliper 0.2 smoke run completes without touching `data/processed` | **Pass.** Exit 0, 12,154 pairs, all checks pass. No file in `data/processed` was modified after the run started | stage7_smoke_caliper02.log; file modification times |
| 17 | `--placebo-shift-days 90` completes and H16 passes | **Pass.** Exit 0, 25,720 pairs; H16 and `H16_post` pass | `match_diagnostics.placebo90.json` |
| 18 | All four files match the schema (pyarrow) | **Pass**, for all six runs; the diagnostics JSON is strict | pyarrow `read_schema` against the `*_DTYPES` / `ARROW_TYPES` |
| 19 | Old `control_area_pairs.parquet` kept as `control_area_pairs.pre_issue4.parquet` | **Met.** Created by run 1 and byte-identical to the original (§4) | §4 |
| 20 | Git (acceptance amendment A3) | **Pass.** Tag `baseline-pre-issue4` = `daaf1f2` = `main`; Step 1 was delivered in the approved multi-commit plan | `git tag`, `git log` |

**Item 9: the top 10 sites by complaint count** (canonical `outage_sites.parquet`; all are artifacts):

| site_id | Complaints | lat, lon | Borough (site mode) |
|---|---|---|---|
| E302934_N68372 | 940 | 40.7824, −73.9652 | MANHATTAN |
| E313332_N63942 | 467 | 40.7424, −73.8422 | QUEENS |
| E303181_N55126 | 298 | 40.6631, −73.9624 | BROOKLYN |
| E312460_N59514 | 209 | 40.7025, −73.8526 | QUEENS |
| E304161_N65357 | 208 | 40.7552, −73.9507 | QUEENS |
| E298206_N60841 | 184 | 40.7146, −74.0212 | MANHATTAN |
| E319753_N62174 | 134 | 40.7263, −73.7662 | QUEENS |
| E315295_N46144 | 121 | 40.5821, −73.8193 | QUEENS |
| E308715_N75168 | 116 | 40.8435, −73.8967 | BRONX |
| E306614_N68060 | 111 | 40.7795, −73.9216 | QUEENS |

The Commit 4 report placed the first six at Central Park, the Flushing Meadows area, Prospect Park, Forest Park, the Queensboro Bridge / Roosevelt Island, and Jersey City. That identification was by the implementer and was not repeated in Phase 9.

---

## 4. X3 and Q9 backup evidence (migration)

The Phase 9 canonical runs were the first Issue 4 writes into `data/processed/`. Before either write, each stage backed up the pre-Issue-4 file automatically.

| Backup | Created by | sha256 of the original (baseline before Phase 9) | sha256 of the backup | Equal |
|---|---|---|---|---|
| `data/processed/control_area_pairs.pre_issue4.parquet` (X3) | Stage 7, run 1 (log: "legacy pairs backup: backed_up") | `59f742531a9e7325a67e24ad8d2f87bbaf1cc075f0311ee200ca237628d3b94b` | `59f742531a9e7325a67e24ad8d2f87bbaf1cc075f0311ee200ca237628d3b94b` | **yes** |
| `data/processed/causal_panel.pre_issue4.parquet` (Q9) | Stage 8, run 8 (log: "backed up to … (sha256 d4daa7de8b601367…)") | `d4daa7de8b60136720fd911403e05600306537e1c8a699580b786cbe3ed23fe1` | `d4daa7de8b60136720fd911403e05600306537e1c8a699580b786cbe3ed23fe1` | **yes** |

- **When hashed:** the baseline hashes were taken before any Phase 9 run ([baseline_before_phase9.sha256](evidence/phase9/baseline_before_phase9.sha256)). The backup hashes were taken after all runs ([after_phase9.sha256](evidence/phase9/after_phase9.sha256)).
- **Not overwritten during Phase 9:** each backup was written once. Its hash at the end of Phase 9 still equals the original.
- **Not exercised in Phase 9:** the "existing backup is kept, never overwritten" behaviour. No second canonical write into `data/processed/` took place, so no run met an existing backup. The last direct evidence of that behaviour:
  - For the pairs backup: the Phase 2 scratch validation, [evidence/commit12/commit12_validate.out](evidence/commit12/commit12_validate.out), "existing backup never overwritten": PASS.
  - For the analogous panel backup: the Stage 8 validator in Phase 9 (run 7), "legacy panel backed up once, then kept": PASS.

**Canonical Issue 4 outputs now in `data/processed/`:**

| File | sha256 |
|---|---|
| `control_area_pairs.parquet` | `abb9869b94d517991778e55bb5b5952ad9a32a7edcf61457ead05a1fced314a4` |
| `outage_sites.parquet` | `9b349a620a3fbf9212eb98fe7f213474ef81754795318815f43dea0cea30112e` |
| `unmatched_treatments.parquet` | `b8fa10c3b83a1f87713f414e20552c28e8df0e3e144694e36708a99ea59e8947` |
| `match_diagnostics.json` | `c493c14b5cc4e05b1bc31ae3d69d47d122b92ef9b2a968dcfbaeb086f764e8d3` (copy: [match_diagnostics.canonical.json](evidence/phase9/match_diagnostics.canonical.json)) |
| `causal_panel.parquet` | `eaa0cd74b75d5f99007ff36a96a562c6fde3c02e264d61cbd25aa652c08d1e35` |

The three Stage 7 parquet hashes equal those of the Commit 12 scratch run (`abb9869b…`, `9b349a62…`, `b8fa10c3…`; [evidence/commit12/self_review.md](evidence/commit12/self_review.md)). The panel hash equals the Phase 3–5 scratch panel (`eaa0cd74…`, [evidence/stage8/](evidence/stage8/)).

---

## 5. Validator results, including the two fixture-dependent failures

| Script | Result | Output |
|---|---|---|
| `stage8_validate.py` | 11/11 PASS, `FAILS: none` | [stage8_validate.out](evidence/phase9/stage8_validate.out) |
| `stage9_validate.py` | 12/12 PASS, `FAILS: none` | [stage9_validate.out](evidence/phase9/stage9_validate.out) |
| `stage10_validate.py` | 9/9 PASS, `FAILS: none`. The script has 9 checks; the "10 checks" in the `f804a78` commit message and in the design doc was a counting error, corrected in the design doc | [stage10_validate.out](evidence/phase9/stage10_validate.out) |
| `commit12_validate.py` | **38/40 PASS; `FAILS: ['legacy backup created with equal sha256', 'existing backup never overwritten']`** | [commit12_validate.out](evidence/phase9/commit12_validate.out) |

The `commit12_validate.py` checks that pass cover:
- H10 and H11 on canonical, placebo and `pre_only`, including 9 H10 and 1 H11 negative tests;
- the 36 exercised exemptions, the artifact threshold rebuilt from raw and the rebuilt own episodes;
- the E2 universe violations;
- H13 and its three negative tests;
- `H16_post` (not applicable in the canonical run; pass on placebo; two negative tests);
- CLI runs in two separate processes, with identical hashes and byte-identical parquet files;
- strict JSON, schemas, all hard checks in the diagnostics, and U7;
- no leftover temporary files, H0 failure diagnostics, stale-failure-file removal and `--skip-recheck`;
- "new-schema file not backed up".

**The two failures: known validator limitation (gap V-10).**

- **What failed:**
  - `legacy backup created with equal sha256`
  - `existing backup never overwritten`
- **Cause:**
  - The validator ([commit12_validate.py:252–258](../scripts/validation/commit12_validate.py#L252-L258)) builds its legacy fixture by copying `data/processed/control_area_pairs.parquet` into a temporary folder, then expects `preserve_legacy_pairs` to back it up.
  - After the required Phase 9 canonical run, that path holds the **new Issue 4** pairs file.
  - `preserve_legacy_pairs` correctly refuses to treat a new-schema file as legacy: it backs up only a file without `control_site_id`. The third check in the same block ("new-schema file not backed up") confirms that behaviour and passes.
  - The first check therefore sees no backup. The second fails as a consequence of the first.
- **What this means:** these two validator checks fail because of the fixture's post-migration state. **They do not show a failure of the X3 implementation.** The real X3 backup made in this run is byte-identical to the original (§4).
- **Status:**
  - `commit12_validate.py` is a frozen Commit 12 artifact. It is **unchanged** (it is identical to the committed file) and was not modified to work around this.
  - No ad-hoc substitute run was made.
  - The limitation is recorded under gap V-10 in [issue4_acceptance.md §5](issue4_acceptance.md). It is open and not yet scheduled. It will recur on every future run of this script while `data/processed/` holds Issue 4 outputs.

---

## 6. Stage 8–10 results (structural only; no estimates)

**Stage 8** (canonical panel, run 8):
- 120,306 rows × 27 columns; 20,051 pairs; 40,102 units; 24,133 locations.
- The D3 guard dropped 0 pairs.
- `stage8_validate.py` confirms:
  - pre counts equal the Stage 7 `bal_*_pre_*` for every pair;
  - the brute-force recount, the structure and the shifted pre-window;
  - the placebo panel builds;
  - legacy-panel backup handling;
  - the old-schema pairs file is rejected.
- Scratch panels: placebo `b2662e46…`, shifted `85f0eeb4…`.

**Stage 9** (run 11, `--out outputs/robustness/phase9_stage9`):
- Exit 0. The summary status is "provisional (M8 open …)".
- Clusters: `treatment_h3_res7` 193 (≥ 50, S3); `pair_id` 20,051; two-way 193 × 195; precinct 78 = 77 observed values + 1 `<missing>` cluster (101 pairs, S2).
- The run asserted that the paired-difference means equal the FE coefficients.
- `stage9_validate.py` confirms:
  - FE = LSDV coefficients;
  - paired mean = hand mean = FE;
  - the CR1 SE formula;
  - dataset statistics;
  - the terciles and years partition the pairs (6,845 / 6,911 / 6,295);
  - two identical runs;
  - the placebo and shifted panels run.
- Output hashes: `did_summary.json` `b64d6784…`, `did_regression_results.txt` `5aab33df…`.

**Stage 10** (run 12, `--out outputs/robustness/phase9_stage10`):
- Exit 0. 360,918 observations; 40,102 units; 20,051 pairs; FE `unit_id` and `rel_week`; 193 clusters; reference week −1.
- The summary status flags the [−7, 0) day gap.
- `stage10_validate.py` confirms:
  - the units and panel geometry, with no outage table read;
  - brute-force weekly counts;
  - within = LSDV;
  - clusters and weeks;
  - byte-identical CSV, JSON and PNG across two runs;
  - the extra panels run;
  - the legacy panel (now `causal_panel.pre_issue4.parquet`) is rejected.
- Output hashes: CSV `3befc0b0…`, PNG `86c603b8…`, JSON `ff9f592d…`.

**Observed cluster structure** (canonical panel; stored as expected in [issue4_acceptance.md §4.1](issue4_acceptance.md)). These are properties of the approved clustering, not recommendations:
- 49.42% of pairs have their treatment and control in different primary H3 res-7 cells.
- 2,541 of the 4,167 reused control sites span more than one primary cluster. That involves 6,270 pairs.
- 5,171 sites are a treatment site in one pair and a control site in another.

Full hashes of every Phase 9 output are in [acceptance_summary.json](evidence/phase9/acceptance_summary.json) under `hashes`.

---

## 7. What Phase 9 did not change

- **Legacy `outputs/`:** all four files have sha256 identical to the pre-Phase-9 baseline. Stage 9 and 10 ran only with a scratch `--out` (M8).
- **Unchanged files:** inputs (§1), `data/processed/outage_crime_linked.parquet`, and all code and validators, including `commit12_validate.py`.
- **Not done:** notebooks, the dashboard, and the legacy-output move.

---

## 8. Open and not-yet-scheduled items

| Item | Status |
|---|---|
| M1–M8 limitations; M8 keeps every Stage 9/10 result provisional | Open ([issue4_design.md §9](issue4_design.md)) |
| Validator gaps V-1 to V-11, now including the `commit12_validate.py` fixture dependency (V-10) | Open; not yet scheduled |
| Item 9 hand check of the top artifact sites | Done in Commit 4; not repeated |
| Q7 notebook banners; D16 notebook realignment | Not yet scheduled; deferred |
| Moving the legacy results to `outputs/legacy_pre_issue4/` | Not yet scheduled |
| Step 5 `robustness.py` (D18/D19 grid) | Out of scope (Q6) |
| `docs/release_readiness_review.md` | Phase 10 (not started) |
| **The acceptance decision on Step 1 and Stages 8–10** | **For the reviewer.** Not made in this report |

---

## 9. Evidence index ([evidence/phase9/](evidence/phase9/))

| File | Content |
|---|---|
| `runs.jsonl` | Every run: command, UTC start, exit, wall seconds, peak RSS |
| `baseline_before_phase9.sha256`, `after_phase9.sha256` | sha256 of `data/raw`, `data/processed` and `outputs/` before and after Phase 9 |
| `stage7_*.log` | The six Stage 7 run logs |
| `match_diagnostics.{canonical,canonical2,seed101,seed202,smoke,placebo90}.json` | Stage 7 diagnostics of each run (`data/` and `outputs/robustness/` are git-ignored) |
| `stage8_canonical.log`, `stage8_placebo90.log`, `stage8_shifted.log` | Stage 8 run logs |
| `stage8_validate.out`, `stage9_validate.out`, `stage10_validate.out`, `commit12_validate.out` | Validator outputs |
| `acceptance_summary.json` | Values used in this report: hashes, backup checks, item 9 and item 11 evaluations, panel structure, Stage 9/10 structure (no estimates), canonical diagnostics extract |
