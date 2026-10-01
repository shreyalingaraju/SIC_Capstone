# LightSafe Issue 4: Step 1 acceptance checklist

Written for: the Issue 4 reviewer and whoever runs Phase 9. It is the authoritative 20-item Step 1 checklist, with its amendments, the evidence so far and what is still missing.

**Status (2026-10-01): Step 1 and the Stage 8–10 migration are formally accepted (project-owner decision, 2026-10-01T14:07:40Z), with item 9 accepted as partly met. Not release-ready; no push or merge is authorised by this acceptance.**
- The 20 items were approved with the Step 1 blueprint (decision log §3).
- Commit 12 was approved on 2026-09-28 on the basis of scratch-run evidence.
- The Phase 9 acceptance runs (canonical into `data/processed`) are done. The per-item results, evidence and hashes are in [acceptance_report.md](acceptance_report.md) (Q11), which supersedes the "Pending Phase 9" statuses below.
  - Phase 9 result: 18 of 20 items pass, or pass under an approved amendment. Item 9 is partly met (the hand check was not repeated). Item 19 is met, but its never-overwrite path relied on earlier evidence.
- Phase 9 was "reviewed and **APPROVED**" by the user (2026-09-30T17:14:01Z; decision log §9). That statement did not itself declare formal acceptance.
- **Formal acceptance (2026-10-01T14:07:40Z, project-owner decision; decision log §9):**
  - **Scope:** Step 1 (Stage 7) and the Stage 8–10 migration are formally accepted.
  - **Basis:** the completed acceptance review and [acceptance_report.md](acceptance_report.md) at `9d44ce3`. The report itself left the decision to the reviewer and is unchanged.
  - **Item 9** stays **partly met** and is accepted as a documented limitation.
  - **Q12:** this satisfies Q12's requirement that acceptance be complete.
  - **What it is not:** a release-readiness approval, or by itself an authorisation to push or merge to `main`. M8, V-1 to V-11 and the other open items below remain open.
- The Phase 10 self-review concludes **A. Not ready for release** ([release_readiness_review.md](release_readiness_review.md)). That conclusion concerns release, not the checklist results.
- The status column in §1 is kept as it was before Phase 9, for the record.

## Evidence status

| Status | Meaning |
|---|---|
| **Met (scratch)** | Met by evidence in [evidence/](evidence/) from a scratch run (`--out-dir` in a temporary folder) at `0fd113d`. Phase 9 must re-confirm it on the canonical run. |
| **Met** | Met, and needs no rerun (for example a git tag). |
| **Partly met** | Some of the required evidence exists; the rest is listed. |
| **Pending Phase 9** | Will be produced by the Phase 9 acceptance runs. |
| **Not yet available** | No evidence exists, and no phase has been assigned to produce it. |

**Main evidence files:**
- [evidence/commit12/canonical_scratch_run.log](evidence/commit12/canonical_scratch_run.log): the canonical Stage 7 run (default parameters, scratch `--out-dir`, exit 0).
- [evidence/commit12/match_diagnostics.canonical_scratch.json](evidence/commit12/match_diagnostics.canonical_scratch.json): its diagnostics (git `0fd113d`, dirty = false).
- [evidence/commit12/commit12_validate.out](evidence/commit12/commit12_validate.out): 40 checks, `FAILS: none`. Validator: [scripts/validation/commit12_validate.py](../scripts/validation/commit12_validate.py).
- [evidence/commit12/self_review.md](evidence/commit12/self_review.md): the implementer's self-review (not an independent review).

**Reviews so far:**
- Commit 12 was approved (decision log §5.1) with the status "X1–X3, R1–R4, E1–E3, H10, H11, H13, H16_post, determinism, diagnostics, safe output writing, backup handling, validation package: PASS".
- Commit 11 was reviewed with no blocking defects, but there is no standalone approval message.
- Phases 3–5 were independently reviewed with no blocking issues (decision log §9).
- The S1–S7 sign-off items were approved on 2026-09-30T16:01:39Z (decision log §11). This was after the Phase 6 commit `932b374`, which had recorded them as needing sign-off.
- Phase 7 (Q8 `.gitignore`, Q10 stub deletion) is done.
- Phase 8 (`6d25204`) moved the Commit 4–11 validators into the repository; all report `FAILS: none` ([evidence/stage7_commits/](evidence/stage7_commits/)).
- Phase 9 (`9d44ce3`) was approved on 2026-09-30T17:14:01Z.
- Phase 10 (`f79af97`) is a self-review (G11), not an independent review.

---

## 1. The 20 items

| # | Item | Pass criterion (as approved, with amendments) | Status | Evidence | Commit | Validator / check |
|---|---|---|---|---|---|---|
| **Pair counts** | | | | | | |
| 1 | Canonical run completes | 0 hard-check failures (H0–H16, plus the later H17, H18 and `H16_post`) | **Met (scratch)**: all 20 recorded checks pass; exit 0 | diagnostics `hard_checks`; commit12_validate "hard checks all pass" | `0fd113d` | H0–H18, H16_post |
| 2 | Pair count | Reported. Expected 18k–25k; anything outside needs a written explanation before Step 2 | **Met (scratch)**: **20,051 pairs** (Phase 6 request D2) | canonical log "Outputs"; diagnostics `outputs` | `0fd113d` | none |
| 3 | Complete accounting | pairs + unmatched = 107,731 | **Met (scratch)**: 20,051 + **87,680** unmatched = **107,731**; **46,034** sites (Phase 6 request D2) | canonical log; H13 pass plus 3 negative tests | `0fd113d` | H13 |
| **Balance metrics** | | | | | | |
| 4 | Matched variables | \|SMD\| < 0.1 and 0.5 ≤ VR ≤ 2, both | **Met (scratch)**: log_base_100m SMD +0.005, VR 1.021; log_base_250m SMD +0.017, VR 1.040 | diagnostics `balance.matched` | `0fd113d` (definitions `c2bb341`) | B1, B2 |
| 5 | Balance-only variables | *As amended by acceptance amendment A1* (§2.2): reported and reviewed; each failed flag needs a written explanation; Step 1 fails only if the reviewer concludes it shows a matching defect, leakage, an implementation error or a methodological violation. The S8 pre-window counts are reported explicitly | **Met under A1 (scratch)**: `prior_episodes_250m` **fails its flag** (SMD +0.348, VR 1.492); every other flag passes (h3r9_density +0.040; pre_100m +0.010; pre_250m +0.041; log_pre_100m +0.007; log_pre_250m +0.040). The written explanation and reviewer conclusion are in §2.2 | diagnostics `balance.balance_only` | `0fd113d` | B12 (report only) |
| 6 | Representativeness | Matched vs eligible treatments, by baseline, borough and year, reported | **Met (scratch)**: matched mean `base_100m` 5.77 vs eligible 8.40 (log1p SMD −0.19); borough and year shares reported (M1) | diagnostics `balance.representativeness` | `0fd113d` | B13 |
| **Contamination diagnostics** | | | | | | |
| 7 | H10 recheck | 0 violations on 2,000 pairs | **Met (scratch)**: all 16 H10 conditions 0; H11 all 4 counts exact. **36 of 2,000 sampled treatments needed the own-episode exemption** (Phase 6 request D3), showing that R1 is exercised. H10/H11 also pass in the placebo 90 and `pre_only` runs | commit12_validate "H10 passes", "canonical own-episode exemption exercised – 36 sampled treatments" | `0fd113d` | H10, H11 |
| 8 | Contamination D1–D5 | *As amended by acceptance amendment A2* (§2.3): report, verify, explain | **Met under A2**, for the canonical `c2bb341` definitions: D1 0.743, D2 0.868, D3 0.908 (all above 5%), D4 mean 0.057, D5 0 > 100 m / 222 > 365 d. Explanation and verification in the Commit 11 review package. `compute_contamination` is byte-identical at HEAD; its helpers were not re-audited | diagnostics `contamination`; decision log §4.4 | `c2bb341`, `0fd113d` | none (report only) |
| 9 | Artifacts | Threshold and number of artifact sites reported; top artifact sites checked by hand (coordinates checked) | **Partly met.** T = 44, 44 artifact sites (4,735 complaints) are reported. The top 10 sites and their locations (Central Park, Flushing Meadows, Prospect Park, Forest Park, Queensboro Bridge, Jersey City, …) were identified by the implementer in the Commit 4 report and approved with Commit 4. That table is **not stored in repository evidence**. Re-attach it in Phase 9 | diagnostics `universe`; H18 pass; Commit 4 report (transcript) | `4007672`, `0fd113d` | H18 |
| **Attrition reporting** | | | | | | |
| 10 | Attrition table | Printed and in the JSON; each step telescopes; the "first of episode" figure re-derived | **Met (scratch)**: 13-row table (steps 0–12) printed and in `attrition`; H13 telescoping pass. First-of-episode re-derived as 88,388 Stage 3 treatments (Commit 5), replacing the 74,546 copy error | canonical log "Attrition"; diagnostics `attrition` | `7fc8065`, `0fd113d` | H13 |
| 11 | Reason codes | All 12 present (count 0 allowed). *As amended* (§2.1): "No treatment is removed by W_OUTSIDE_DARKNESS alone." | **Met (scratch)** for the codes: all 12 present in `unmatched_reasons`. The "alone" condition was shown in Commit 7 (0 in both the canonical and placebo runs) with a scratchpad-only brute-force validator; there is **no permanent check** of it. **Pending Phase 9** for re-confirmation | diagnostics `unmatched_reasons`; Commit 7 validation (transcript) | `6cae561`, `0fd113d` | internal attrition guard |
| **Runtime reporting** | | | | | | |
| 12 | Timings and peak RSS | In the JSON; total < 5 min and peak < 4 GB | **Met (scratch)**: diagnostics `runtime.total_seconds` = 29.7 s, `peak_mb` = 1,524.8 MB. The self-review quotes "27.4 s summed over steps" and a peak working set of 1,531 MB; these are different measurements of the same run, and both are within limits | diagnostics `runtime` | `0fd113d` | none |
| **Reproducibility reporting** | | | | | | |
| 13 | Provenance | Git SHA, input hashes, package versions and parameters in the JSON | **Met (scratch)**: `provenance` has the git sha `0fd113d` (dirty false), platform, the Python and package versions, input fingerprints with sha256, and all parameters | diagnostics `provenance`, `parameters` | `0fd113d` | none |
| 14 | Determinism | Two runs with seed 42 give identical pair-list hashes | **Met (scratch)**: two separate processes give pair-list sha256 `ab63c377…` and order hash `b36524dc…`, with byte-identical parquet files; the `--check-determinism` second pass is identical. **Failure behaviour** (Phase 6 request D1): see §2.4 | commit12_validate "pair-list hash equal across processes", "parquet files byte-identical", "determinism second pass identical" | `0fd113d` | N6 |
| 15 | Seed stability | Per the approved rule: \|N_s − N_42\| / N_42 ≤ 0.02 for s = 101 and 202, each; balance must also pass for each seed | **Partly met.** Pair counts at Commit 9 (`3b0dd40`, in-process scratch runs): seed 101 20,011 (−0.20%), seed 202 19,996 (−0.27%). Both pass the count rule. The balance code did not exist yet (added in `c2bb341`), so **per-seed balance has not been evaluated**, and the counts are not stored as repository evidence. **Pending Phase 9** | Commit 9 report (transcript T3 L286) | `3b0dd40` | seed rule |
| 16 | Sensitivity smoke test | One non-default run (`--caliper-sd 0.2 --out-dir outputs/robustness/smoke`) completes without touching `data/processed` | **Pending Phase 9.** Caliper 0.2 was run in-process at Commit 9 (12,154 pairs), and H0 refuses non-default parameters in `data/processed`, which was tested in the Commit 1 fix-up. The end-to-end CLI smoke run is **not recorded** | none in repo | `a740307`, `3b0dd40` | H0 |
| 17 | Placebo smoke test | `--placebo-shift-days 90` completes and H16 passes | **Met (scratch)**: H16_post passes on placebo 90, and 2 negative tests fail it; H10/H11 pass on placebo. 25,720 placebo pairs (M5). The placebo pairs build a Stage 8 panel | commit12_validate "H16_post passes (placebo)"; stage8_validate "placebo pairs build a panel" | `0fd113d`, `9624b45` | H16, H16_post |
| 18 | Schemas | All four files match blueprint §3 exactly, checked with pyarrow | **Met (scratch)**: pyarrow schemas match `ARROW_TYPES` (N10). The implemented schema adds `n_candidates_reuse_blocked`, `control_police_precinct` and `treatment_episode_id` (see [issue4_design.md §3.15](issue4_design.md)) | commit12_validate "pyarrow schemas match" | `0fd113d` | N10 |
| 19 | Old outputs | The old `control_area_pairs.parquet` is kept as `control_area_pairs.pre_issue4.parquet` until Step 2 is accepted | **Partly met.** The backup mechanism (X3, automated) is verified in scratch: created with an equal sha256, never overwritten, a new-schema file not backed up. The legacy pairs file in `data/processed` is still untouched, because no Issue 4 run has written there. The actual backup happens on the first canonical write. **Pending Phase 9** | commit12_validate "legacy backup created with equal sha256", "existing backup never overwritten" | `9ce8542` | X3 |
| 20 | Git | *As amended by acceptance amendment A3* (§2.3): the approved multi-commit plan replaces "Step 1 in one commit"; the `baseline-pre-issue4` tag requirement remains | **Met**: tag `baseline-pre-issue4` = `daaf1f2` = current `main`; Step 1 on `issue4-step1` in the approved 12 commits plus fix-ups (`a740307`, `b7767cf`, `3d32977`, `d91f1f8`, `0fd113d`) | `git tag`, `git log` | `daaf1f2` … `0fd113d` | none |

---

## 2. Amendments and required statements

### 2.1 Item 11 amendment (approved 2026-09-27T09:53:26Z)

**Replaced criterion:** "`W_OUTSIDE_DARKNESS` = 0".

**New criterion:** "**No treatment is removed by W_OUTSIDE_DARKNESS alone.**" The rules are not reordered.

**Explanation required by the approval:**
- `W_OUTSIDE_DARKNESS` (rule 2) is evaluated **before** `B_OUTSIDE_CRIME` (rule 3), in the approved first-failure order.
- **1,589 canonical** and **1,242 placebo** treatments are attributed to `W_OUTSIDE_DARKNESS` because of first-failure accounting.
- Every one of those treatments also fails `B_OUTSIDE_CRIME`.
- So the eligible population is unchanged relative to a hypothetical ordering that put `B_OUTSIDE_CRIME` first. Canonical eligible = 33,152; placebo eligible = 39,882.
- The issue is **attribution, not eligibility**.

The amendment is recorded in `ACCEPTANCE_AMENDMENTS["item_11"]` and written to `match_diagnostics.json` under `parameters.acceptance_amendments`.

### 2.2 Acceptance amendment A1: balance-only flags (applies to item 5)

**Rule** (final text of 2026-09-28):
- Balance-only diagnostics are **reported** and **reviewed**.
- Each failed balance-only diagnostic requires a **written explanation**.
- It is a failure **only if the reviewer concludes** the result shows a **matching defect, leakage, an implementation error or a methodological violation**.
- Known example: `prior_episodes_250m`.

**Application to the canonical run:**
- **Flag failed:** `prior_episodes_250m` (the number of darkness episodes starting within 250 m during B), SMD +0.348, VR 1.492.
- **Written explanation** (Commit 11 review package, `c2bb341`): "treatment points had about 36% more outage episodes within 250 m during B than their controls. Outages cluster: treatments are outage locations, while controls are sites that were clean over W. … It doesn't break the implementation, and the approved design doesn't match on this variable." The treatment mean is 6.543 against 4.810 for controls. `prior_episodes_250m` is a balance-only diagnostic, not a matching variable, and the matching variables (item 4) are balanced.
- **Reviewer conclusion** (Commit 11 review, 2026-09-28T17:05:20Z): "The observed imbalance in `prior_episodes_250m` is not a matching failure because it is a balance-only diagnostic rather than a matching variable. The contamination metrics follow the approved definitions and were independently reproduced. No blocking methodology or implementation issues were identified."
- **Result:** not a Step 1 failure under A1. **`prior_episodes_250m` stays listed as a limitation** (M2: pre-period confounding by outage-proneness).

Phase 9 must re-report the balance-only table and confirm that no other flag fails.

### 2.3 Acceptance amendments A2 and A3

**A2: contamination (applies to item 8).**
- Contamination review shares require **reporting, verification and an explanation**.
- The Commit 11 contamination review package satisfies this **for the canonical `c2bb341` definitions**. The explanation is summarised in [issue4_design.md §3.10](issue4_design.md) and the decision log §4.4.
- **If the contamination D1–D5 definitions or code change, re-verification is required.** As of `f804a78`, the body of `compute_contamination` is unchanged since `c2bb341`; its helpers were not re-audited in Phase 6.
- **Sensitivity and placebo runs are report-only**: their shares are reported but are not acceptance criteria.

**A3: git (applies to item 20).**
- The single-commit requirement of item 20 is replaced by the approved 12-commit rollout plan.
- The **`baseline-pre-issue4` tag requirement remains**, and it is satisfied.

**Q12 commit rhythm (after Step 1).**
- Q12 asks for one commit per phase.
- Phases 3–5 were intentionally delivered as five commits, because Q4 required D14 in its own commit and the migration note had to come first.
- This interpretation was approved as S7 on 2026-09-30. Git history is left as it is (decision log §8).

### 2.4 Determinism failure behaviour (item 14; approved 2026-09-28, Phase 6 request D1)

With `--check-determinism`, Stage 7 re-runs steps 12–13 (a fresh reuse registry, band lists and matching) and compares them with the first pass. Any of the following is a **hard failure**:
- a **pair-list hash mismatch** (sha256 of the sorted `pair_id` list);
- an **order hash mismatch**;
- any difference in `pairs_raw` or in the rejected-treatment table.

On failure, the run stops before the recheck and before writing any output, writes `match_diagnostics.failed.json`, and exits with **code 1**.

This is documented behaviour, not an implementation detail. Without `--check-determinism` no second pass runs; item 14 is then shown by comparing the hashes of two separate runs.

---

## 3. Stage 8–10 acceptance criteria

The approved Step 2–4 asserts and design §12 hard check 7 ("The S8 panel invariants hold, and `unit_id` has 3 rows each") are the only approved acceptance criteria for Stages 8–10. There is no separate checklist for them.

| Criterion | Status | Evidence | Commit |
|---|---|---|---|
| Step 2: every control ≥ 500 m from its treatment | **Met (scratch)**: asserted on every run | [evidence/stage8/](evidence/stage8/) | `9624b45` |
| Step 2: `unit_id` has 3 rows | **Met (scratch)**: 40,102 units × 3 = 120,306 rows | stage8 log and validate | `9624b45` |
| Step 2: the D3 guard drops 0 pairs | **Met (scratch)**: 0 pairs outside coverage | stage8 log | `9624b45` |
| Stage 8 pre counts equal Stage 7 `bal_*_pre_*` for every pair | **Met (scratch)** | stage8_validate | `9624b45` |
| Step 3: FE on `unit_id`; D13 clustering with cluster counts; D20 | **Met (scratch)**: 193 primary clusters (≥ 50); paired mean = FE (4 terms) | stage9_validate | `25cc596` |
| Step 4: D14 as its own commit; `unit_id` FE; H3 res-7 clustering; week-label docs | **Met**: D14 verified as a pure deletion reproducing the legacy CSV (Phase 3–5 review) | stage10_validate; review | `5c4fca1`, `f804a78` |
| Phase 3–5 independent review | **No blocking issues found** (reported 2026-09-30) | decision log §9 | none |
| Sign-off items S1–S7 (decision log §11) | **Approved** 2026-09-30T16:01:39Z, as implemented; no code change | [issue4_design.md §7.2](issue4_design.md) | `25cc596`, `f804a78` |
| Phases 3–5 commit structure (Q12, S7) | **Accepted.** The five commits were intentional: Q4 required D14 in its own commit. History is not rewritten or squashed (decision log §8) | `git log` | `a4a9b89`, `9624b45`, `25cc596`, `5c4fca1`, `f804a78` |

**No Stage 9 or 10 estimate is an acceptance criterion or a result.** They stay provisional while M8 is open.

---

## 4. Evidence still pending

### 4.1 Expected from Phase 9 (acceptance runs), with the Phase 9 outcome

The list below is the pre-Phase-9 expectation. The outcome column records what [acceptance_report.md](acceptance_report.md) and [evidence/phase9/](evidence/phase9/) show. It adds no evidence of its own.

| Expected evidence | Phase 9 outcome |
|---|---|
| The canonical Stage 7 run into `data/processed`, authoritative for items 1–14 and 17–19; the first write creates `control_area_pairs.pre_issue4.parquet` (item 19) | **Produced.** Exit 0, 20 of 20 checks; backup byte-identical (report §3, §4) |
| Seeds 101 and 202 end to end: pair counts **and** per-seed balance (item 15) | **Produced.** Both pass (report §3) |
| The caliper 0.2 CLI smoke run into a non-canonical directory (item 16) | **Produced.** Pass |
| Re-confirming the item 11 "alone" condition, canonical and placebo | **Produced** from the unmatched tables (`acceptance_summary.json` `item11`). There is still no permanent check (§4.2) |
| Re-attaching the item 9 top-artifact table with coordinates | **Table re-attached** (report §3). The hand check of the coordinates was **not repeated**, so item 9 stays partly met |
| The canonical Stage 8 panel into `data/processed`; the first write creates `causal_panel.pre_issue4.parquet` (Q9) | **Produced.** Backup byte-identical |
| The never-overwrite path of both backups | **Not exercised** in Phase 9: no second canonical write into `data/processed/` took place, so no run met an existing canonical backup. The direct evidence comes from scratch fixtures only:<br>• pairs backup: the Phase 2 [evidence/commit12/commit12_validate.out](evidence/commit12/commit12_validate.out), "existing backup never overwritten": PASS;<br>• panel backup: Phase 9 run 7, `stage8_validate.py` on its scratch fixture (run before the canonical panel write), "legacy panel backed up once, then kept": PASS.<br>Neither shows how an existing canonical backup is treated (report §4) |
| The Stage 9 and 10 runs, **into a scratch or non-published location** (instruction 3) | **Produced** into `outputs/robustness/` only. Structural results only; no estimate is reported (M8) |
| The Phase 3–5 cluster-structure figures stored as repository evidence | **Stored** in `acceptance_summary.json` `stage8_panel` ([issue4_design.md §5.4](issue4_design.md)). The cluster-size figures (median, max, min, top-five share) are not stored |
| `docs/acceptance_report.md` (Q11) | **Produced** (`9d44ce3`) |

### 4.2 Not yet available (no phase assigned yet)

- A permanent check of the item 11 "alone" condition. `scripts/validation/commit07_validate.py` (Phase 8) brute-forces every rule-1–8 reason code on 2,000 treatments, but it does not test the "alone" condition directly.
- An independent check of the Stage 9 standard errors against LSDV or an external implementation. Only the coefficients and the paired-mean SE are checked.
- A cross-stage test that the Stage 7 locked constants (radii, windows) equal the Stage 8 and 10 constants.

---

## 5. Validation gaps (known; open)

These gaps come from the Phase 3–5 review. They are not part of the original Phase 8 scope, which moved the review validators into the repository ([scripts/validation/README.md](../scripts/validation/README.md)). They remain open and are not yet scheduled. None of them blocks the documentation.

| # | Gap |
|---|---|
| V-1 | The FE standard-error convention (K = 3, [issue4_design.md §5.3](issue4_design.md)) is not tested. The LSDV comparison covers coefficients only. |
| V-2 | Two-way clustering is checked only for SE > 0, not against an independent CGM computation. |
| V-3 | No Stage 8–10 check recomputes the H3 cells from coordinates; they are trusted from Stage 7. |
| V-4 | The Stage 8 and 10 brute-force checks share the geometry path (pyproj, pair x/y) with the code. An error in the Stage 7 x/y would pass them; only Stage 7 H1 and H10 guard it. |
| V-5 | Placebo panels are only checked to build and run, not for content. |
| V-6 | Stage 8 determinism is not asserted by the validator. The evidence is two matching sha256 values (`eaa0cd74…`). |
| V-7 | The Stage 10 SEs and pre-trend F-test are not independently checked. |
| V-8 | Stage 9 does not check that each pair has exactly one T and one C unit. Its paired-mean = FE assertion would pass on a NaN mean. |
| V-9 | The Stage 8 validator compares only `location_x_m`, `base_100m` and `location_key` with the pair columns (not y, lat/lon, `base_250m`, borough or res-9/10 cells). |
| V-10 | The Stage 8 validator's legacy-backup check depends on the state of `data/processed`. It is skipped silently when `causal_panel.parquet` is missing, and it will fail once Phase 9 writes an Issue 4 panel there. The old-schema rejection checks in the Stage 8 and 10 validators also fall back to whatever is in `data/processed`. **Also affects `commit12_validate.py`** (observed in Phase 9): its legacy-backup test copies `data/processed/control_area_pairs.parquet` as the legacy fixture. Once that path holds the Issue 4 pairs, "legacy backup created with equal sha256" and "existing backup never overwritten" fail. This is not an X3 defect: `preserve_legacy_pairs` correctly refuses a new-schema file. Recorded in [acceptance_report.md §5](acceptance_report.md); the frozen script is unchanged. |
| V-11 | The evidence `.out` files don't record their command lines, input hashes or the git commit. |

---

## 6. Known limitations (acceptance context)

Accepting Step 1 does not remove these. They are listed in full in [issue4_design.md §9](issue4_design.md).

| Limitation | Summary |
|---|---|
| M1 | The estimand changes (matched baseline 5.77 vs 8.40) |
| M2 | `prior_episodes_250m` imbalance (A1) |
| M3 | Interference (contamination D1–D3) |
| M4 | Selection on the future |
| M5 | The placebo population differs |
| M6 | Year-dependent closure quality |
| M7 | "Not reported dark" |
| M8 | Defects outside Issue 4: every Stage 9 and 10 result is provisional |
| L1–L10 | The Phase 3–5 additions, for example NB α = 1.0, the −7..0 gap, and default output paths over the tracked legacy results |
