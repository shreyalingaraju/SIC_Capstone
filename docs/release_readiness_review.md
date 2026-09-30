# LightSafe Issue 4: Phase 10 release-readiness review

Written for: the Issue 4 reviewer and capstone reviewers deciding whether the `issue4-step1` work can be released publicly. It gives the single conclusion the approved plan asks for (A or B) and the evidence behind it.

**This is a self-review (G11).** The agent that implemented Phases 1–9 wrote it. It is not an independent review. The independent reviews on record are the Commit 11 review, the Phase 3–5 review ([issue4_decisions.md §4.4, §9](issue4_decisions.md)) and the user's review of Phase 9.

**Scope.** This review follows the Phase 10 specification of the approved phase plan (T3 L672, approved at T3 L678, 2026-09-28T17:31:28Z):

> Perform one final holistic review. Evaluate: **Methodology** (leakage, post-treatment conditioning, contamination, estimand changes); **Implementation** (determinism, reproducibility, migration risks); **Documentation** (consistency, completeness); **Release Readiness** (public reproducibility, auditability, transparency). Output `release_readiness_review.md` with one conclusion: A. Not ready for release, or B. Ready for release, with detailed justification.

The plan adds that the conclusion must be clearly marked as a self-review (G11), and that the report goes in `docs/` (Q11).

**How this review was done:**
- It uses only the evidence already produced in Phases 1–9. No Stage 7–10 pipeline or validator was re-run for it.
- The only checks run were read-only repository checks (git state and sha256 of existing files; §1).
- It changes no code, validator, output, decision or acceptance criterion.
- It does not change any Phase 9 result, and it resolves no open item.

**Conclusion: A. Not ready for release** (§8).

---

## 1. Repository state (checked 2026-09-30, before this commit)

| Item | State |
|---|---|
| Branch | `issue4-step1`, HEAD `9d44ce3` (Phase 9), 27 commits ahead of `main`, working tree clean |
| `main` | `daaf1f2`, equal to tag `baseline-pre-issue4` and to `origin/main` |
| Push / merge | `issue4-step1` has no upstream. Nothing has been pushed or merged |
| `outputs/` (tracked, pre-Issue-4) | The four files have the same sha256 as in [after_phase9.sha256](evidence/phase9/after_phase9.sha256). They are unchanged since Phase 9 |
| `data/processed/` (git-ignored) | `control_area_pairs.parquet` `abb9869b…` and `causal_panel.parquet` `eaa0cd74…` (the Issue 4 canonical outputs). The backups `control_area_pairs.pre_issue4.parquet` `59f74253…` and `causal_panel.pre_issue4.parquet` `d4daa7de…` are also there. All four equal their Phase 9 hashes |
| `scripts/validation/commit12_validate.py` | Last changed in `a167ab6` (Commit 12). Unchanged |
| Notebooks 05–08 | No stale banner (Q7 not yet scheduled) |

---

## 2. Phase 9 acceptance status

- **Phase 9 (`9d44ce3`) was reviewed and approved by the user on 2026-09-30.** That approval was given in the Phase 10 instruction. This review does not re-open or change it.
- The Phase 9 results are those in [acceptance_report.md](acceptance_report.md):
  - **Stage 7:** H0–H18 and `H16_post` pass in all six runs (20 of 20 recorded checks each).
  - **Checklist:** 18 of the 20 Step 1 items pass, or pass under an approved amendment (A1 for item 5, A2 for item 8, A3 for item 20, and the item 11 amendment). **Item 9 is partly met**, because the hand check of the top artifact sites was not repeated. **Item 19 is met**, with one behaviour not exercised in Phase 9 (§3.2).
  - **Stages 8, 9 and 10:** run end to end. `stage8_validate.py` 11/11, `stage9_validate.py` 12/12, `stage10_validate.py` 9/9.
  - **`commit12_validate.py`:** 38/40, with **two fixture-dependent FAILs** (§3.3).
  - **No Stage 9 or 10 estimate was reported** (M8).
- **What the repository still records.** README, [issue4_design.md](issue4_design.md) and [issue4_acceptance.md](issue4_acceptance.md) still describe the acceptance decision as "pending review", and [acceptance_report.md §8](acceptance_report.md) says the decision on Step 1 and Stages 8–10 is "for the reviewer". The repository does not yet record the approval, or whether it is the formal acceptance decision on Step 1 and Stages 8–10. Phase 10 does not edit those files (§6.1).

---

## 3. Evidence: what is verified and what was not re-exercised

### 3.1 Directly evidenced in Phase 9

| Area | Evidence ([evidence/phase9/](evidence/phase9/)) |
|---|---|
| Stage 7 canonical run into `data/processed`, with 0 hard-check failures; 20,051 pairs; 87,680 unmatched; 107,731 total | `stage7_canonical.log`, `match_diagnostics.canonical.json` |
| Determinism (item 14): two separate processes give identical pair-list and order hashes and byte-identical parquet files; the `--check-determinism` second pass is identical | `stage7_canonical.log`, `stage7_canonical2.log`, `acceptance_summary.json` |
| Seed stability with per-seed balance (item 15): seed 101 −0.199%, seed 202 −0.274% | `match_diagnostics.seed101.json`, `match_diagnostics.seed202.json` |
| Caliper 0.2 smoke run (item 16) and placebo 90 with H16 and `H16_post` (item 17) | `stage7_smoke_caliper02.log`, `stage7_placebo90.log` |
| pyarrow schemas in all six runs (item 18); strict diagnostics JSON | acceptance report §3 |
| Item 11 "alone" condition: 0 treatments removed by `W_OUTSIDE_DARKNESS` alone, canonical and placebo | `acceptance_summary.json` `item11` |
| **X3 backup created, byte-identical:** `control_area_pairs.pre_issue4.parquet` sha256 = the original `59f74253…` | `baseline_before_phase9.sha256`, `after_phase9.sha256`, `stage7_canonical.log` |
| **Q9 backup created, byte-identical:** `causal_panel.pre_issue4.parquet` sha256 = the original `d4daa7de…` | same files, `stage8_canonical.log` |
| Stage 8 canonical panel: 120,306 rows, 40,102 units; the D3 guard drops 0 | `stage8_canonical.log`, `stage8_validate.out` |
| Stage 9 and 10 end-to-end runs (scratch `--out`), with validators | `stage9_validate.out`, `stage10_validate.out`, `runs.jsonl` |
| Cluster-structure figures stored as evidence (49.42% cross-cell pairs; 2,541 of 4,167 reused control sites span several clusters, 6,270 pairs; 5,171 dual-role sites) | `acceptance_summary.json` |
| Legacy `outputs/` unchanged | `after_phase9.sha256` |

### 3.2 Not re-exercised in Phase 9 (relies on earlier evidence)

| Item | What was not re-exercised | Earlier evidence relied on |
|---|---|---|
| **X3 / Q9 "existing backup kept, never overwritten"** | No second canonical write into `data/processed/` took place, so no run met an existing backup. Phase 9 shows only that each backup was written once, and that its hash at the end still equals the original | Pairs: the Phase 2 scratch validation ([evidence/commit12/commit12_validate.out](evidence/commit12/commit12_validate.out), "existing backup never overwritten": PASS). Panel: Phase 9 run 7, `stage8_validate.py`, "legacy panel backed up once, then kept": PASS, run before the canonical panel write |
| **Item 9 hand check** of the top artifact sites' coordinates | Not repeated. The top-10 table was re-attached ([acceptance_report.md §3](acceptance_report.md)) | The implementer's Commit 4 report, approved with Commit 4 (transcript only) |
| Item 10, first-of-episode re-derivation (88,388) | Not re-run in Phase 9 | Phase 8 `commit05_validate.py` ([evidence/stage7_commits/](evidence/stage7_commits/)) |
| Item 8, contamination D1–D5 | The values were re-reported. They are identical to Commit 11. `compute_contamination` is byte-identical since `c2bb341`, but its helpers were not re-audited | Commit 11 review package (A2) |
| Stage 9 and 10 run logs | Not kept (M8). Only exit codes, output hashes and structural facts are recorded | `runs.jsonl`, `acceptance_summary.json` |

### 3.3 The two `commit12_validate.py` fixture-dependent failures (gap V-10)

- **Failed checks:** `legacy backup created with equal sha256` and `existing backup never overwritten` ([evidence/phase9/commit12_validate.out](evidence/phase9/commit12_validate.out)).
- **Cause:** the validator builds its legacy fixture by copying `data/processed/control_area_pairs.parquet`, which since Phase 9 holds the Issue 4 pairs file. `preserve_legacy_pairs` correctly refuses to back up a new-schema file, and the check "new-schema file not backed up" passes.
- **What this means:** the two failures do not show an X3 defect. The real X3 backup is byte-identical (§3.1).
- **Status:** the frozen script is unchanged (the user chose Option A in Phase 9). The failures **will recur on every future run** of the script while `data/processed/` holds Issue 4 outputs. V-10 also records that the `stage8_validate.py` legacy-backup check fails once an Issue 4 panel is in `data/processed`. That validator was not run after the Phase 9 panel write.

---

## 4. Methodology

These findings restate the documented record ([issue4_design.md §3, §9](issue4_design.md)). No new analysis was done for this review.

### 4.1 Leakage

- **What is documented:**
  - The matching variables are night-crime counts during B = [c − 400 d, c − 35 d). B does not overlap the analysis window W, so the Stage 10 pre-trend test uses data the matching never saw (design §3.4).
  - The Stage 8 pre-window counts are balance-only, not matching variables. Their balance flags pass (item 5).
  - Under A1, the reviewer concluded that the `prior_episodes_250m` imbalance does not show leakage, a matching defect, an implementation error or a methodological violation ([issue4_acceptance.md §2.2](issue4_acceptance.md)). It stays listed as M2.
- **Not documented:** a dedicated leakage audit of Stages 8–10 beyond these points. This review did not do one.

### 4.2 Post-treatment conditioning

- **M4, selection on the future, on both sides.** Controls must stay clean through the whole of W, which extends past closure. Treatments must stay clean after closure (S-4 post). The `pre_only` variant covers the control side only (D2b: 59% of those controls go dark later). No variant covers the treatment-side S-4 post rule, and adding one needs a design decision. **Open.**
- **M8 / L1.** `baseline_crime_intensity` is the unit's pre-window outcome count, and it is a covariate in the Stage 9 basic, separate, Poisson and NB models. It is documented as a bad control. **Open.**

### 4.3 Contamination

- **M3, interference.** Contamination D1 0.743, D2 0.868 and D3 0.908 are all above the 5% review share. 19.4% of pairs have another pair's treatment within 250 m, dark only outside the control's W.
- **A2.** The shares are accepted for the canonical `c2bb341` definitions, with the explanation that they are a scale effect.
- **Sensitivity.** The only documented check is the exclusion-500 run from the Commit 11 review (7,419 pairs, D2 = 0.000, D1 = 0.320). The D19 grid runner (Step 5) is not implemented, so there is no systematic sensitivity evidence. **Open.**

### 4.4 Estimand changes

- **M1.** Results describe first-reported, isolated outages in quieter areas. The matched mean `base_100m` is 5.77 against 8.40 for all eligible treatments (log1p SMD −0.185), and about 70% of Stage 3 treatments are removed by the rules. The limitation must be stated in any write-up. **Open.**
- **M5.** The placebo population differs from the canonical one (39,882 vs 33,152 eligible; 25,720 placebo pairs). It is a design-level placebo.
- **Comparability.** Legacy and Issue 4 results, including standard errors, are not comparable (design §5.4).

**Methodology finding:**
- The approved design has been implemented as frozen. The Phase 3–5 and Commit 11 reviews found no blocking methodology issue.
- M1–M7 are disclosed limitations of the approved method.
- M8 is the item that keeps every Stage 9 and 10 result provisional: exposure is not normalised, `baseline_crime_intensity` is a bad control, and the event study has the −7..0 day gap.

---

## 5. Implementation

### 5.1 Determinism

| Stage | Evidence | Level |
|---|---|---|
| S7 | Item 14 pass: separate processes, identical hashes, byte-identical parquet files. A mismatch under `--check-determinism` is a hard failure with exit 1 | Directly evidenced (Phase 9) |
| S8 | The canonical panel sha256 `eaa0cd74…` equals the Phase 3–5 scratch panel | Two matching hashes; **not asserted by the validator** (V-6) |
| S9 | `stage9_validate.py`: "two identical runs" | Directly evidenced |
| S10 | `stage10_validate.py`: byte-identical CSV, JSON and PNG across two runs | Directly evidenced |

### 5.2 Reproducibility

- **Recorded:** Stage 7 diagnostics record the git SHA, dirty flag, platform, packages, input sizes and sha256, and all parameters (item 13).
- **Provenance chain (L8):** the Stage 8 panel does not record the pairs-file sha256, the pre-window mode or `placebo_shift_days`. The Stage 10 summary has no panel sha256. No Stage 8–10 output records the git commit. **Open.**
- **Untested:** the FE standard-error convention (K = 3; V-1), two-way clustering against an independent computation (V-2), and the Stage 10 SEs and pre-trend F-test (V-7). An independent check of the Stage 9 SEs is listed as "not yet available" ([issue4_acceptance.md §4.2](issue4_acceptance.md)).

### 5.3 Migration risks

- **Mitigated:** the legacy pairs and panel are preserved with verified hashes (§3.1). Stages 8–10 reject old-schema inputs.
- **Not re-exercised:** the never-overwrite path (§3.2).
- **Default output paths (L9):** Stages 9 and 10 default to `--out outputs/`. Run with the default, they overwrite the tracked legacy results without a backup. The documented mitigation is to use a scratch `--out` until the legacy outputs are moved, which is not yet scheduled. **Open.**
- **Notebooks 05–08** read the old schemas and show stale outputs. They have no banner (Q7, not yet scheduled). **Open.**
- **Cross-stage constants:** radii, windows and the Stage 3 validity rule (A5) are repeated in several stages. No automated cross-stage test enforces them ([issue4_acceptance.md §4.2](issue4_acceptance.md)). **Open.**
- **Validator fixtures (V-10):** on the current `data/processed/` state, `commit12_validate.py` reports 2 FAILs (§3.3). **Open.**

**Implementation finding:**
- Stage 7 determinism, the Stage 7 hard checks and the Stage 8–10 structural validators pass on the canonical data.
- The open implementation items are the validator gaps V-1 to V-11, the provenance chain (L8), the default output paths (L9) and the unbannered notebooks. None of them was found to be a defect in the Stage 7–10 computations. They limit how far the outputs can be independently verified and used safely.

---

## 6. Documentation

### 6.1 Consistency

These inconsistencies exist in the repository at `9d44ce3`. Phase 10 records them and does not edit the files:

| # | Where | What it says | Current state |
|---|---|---|---|
| C-1 | README "Project status"; [issue4_design.md](issue4_design.md) status; [issue4_acceptance.md](issue4_acceptance.md) status | The acceptance decision is "pending review" | Phase 9 was approved by the user on 2026-09-30 (§2). No approval is recorded in the repository |
| C-2 | [issue4_decisions.md §8](issue4_decisions.md) Q11; [issue4_design.md §10](issue4_design.md); [acceptance_report.md §8](acceptance_report.md) | `release_readiness_review.md` is "Pending (Phases 9, 10)" or "Phase 10 (not started)" | This document exists as of the Phase 10 commit. `acceptance_report.md` is the Phase 9 record and is left as it was |
| C-3 | [issue4_design.md §5.4](issue4_design.md), "Evidence status" | The cluster-structure figures are "not yet stored as repository evidence; Phase 9 is to regenerate and store them" | Stored in Phase 9 ([acceptance_report.md §6](acceptance_report.md), `acceptance_summary.json`) |
| C-4 | [issue4_acceptance.md §1](issue4_acceptance.md) status column | Pre-Phase-9 statuses ("Met (scratch)", "Pending Phase 9") | Intentional: the document says they are kept for the record and superseded by `acceptance_report.md` |
| C-5 | Commit message of `f804a78` | Stage 10 validator has "10 checks" | The script has 9. Corrected in the design doc in Phase 9; the commit message stays as it is (no history rewrite) |
| C-6 | [issue4_decisions.md §10](issue4_decisions.md) | The M1–M8 list has no explicit approval of the list itself | Recorded as limitations, not approved decisions |

None of these changes a result or a decision. C-1 to C-3 are stale status text.

### 6.2 Completeness

- **Present:** the design and implementation record, the decision log, the acceptance checklist with amendments, the migration note, the Phase 9 acceptance report with evidence, the validation README, and the README with M1–M8 and L1–L10.
- **Not yet available, and no phase assigned** ([issue4_acceptance.md §4.2](issue4_acceptance.md)):
  - a permanent check of the item 11 "alone" condition (it was evaluated in Phase 9 from the unmatched tables, but not by a permanent validator);
  - an independent check of the Stage 9 SEs;
  - a cross-stage constants test.

---

## 7. Release readiness

### 7.1 Public reproducibility

- **Input data is not in the repository.** `data/raw/` and `data/processed/` are git-ignored. The inputs are identified by size and sha256 ([acceptance_report.md §1](acceptance_report.md)). The decision log records that re-downloading the 311 data would change the outage set (Issue 1–3 D4), so a fresh download is not expected to reproduce those bytes. How to obtain the exact inputs is not documented.
- **Environment:** `requirements.txt` pins the full environment except `pyarrow`, which has no version (the installed 25.0.1 is recorded in the README and the diagnostics). The only tested platform is Windows 11 with Python 3.14.4. No other platform was tested.
- **Validators on the current state:** a third party running `commit12_validate.py` against the current `data/processed/` would see 38/40 with the two V-10 failures (§3.3). This is expected behaviour, but it is not a clean `FAILS: none`.
- **Phase 9 measurement:** the wall-time and peak-RSS wrapper is not in the repository. `runs.jsonl` records each run's command, start time, exit code, wall seconds and peak RSS. The Stage 7 diagnostics record their own runtime and peak working set.

### 7.2 Auditability

- **Strengths:**
  - Hashes of every Phase 9 input and output ([acceptance_summary.json](evidence/phase9/acceptance_summary.json)).
  - Stage 7 provenance in the diagnostics.
  - Verbatim validator outputs.
  - A dated decision log that gives each implementation location.
- **The decision log's sources are local transcripts** (T1–T4). They are not in the repository ([issue4_decisions.md](issue4_decisions.md), "Sources"), so the approvals cannot be checked from the repository alone.
- **Evidence `.out` files** do not record their command lines, input hashes or git commit (V-11). For the Phase 9 runs, `runs.jsonl` and the acceptance report supply the commands and the commit.
- **Provenance chain** gaps across Stages 8–10 (L8, §5.2).
- **Self-reviews:** the Commit 12 review ([evidence/commit12/self_review.md](evidence/commit12/self_review.md)) and this review are self-reviews.

### 7.3 Transparency

- **In place:**
  - The README states that results are not final and that every Stage 9 and 10 result is provisional (M8).
  - `did_summary.json` carries the status "provisional (M8 open …)", and the Stage 10 plot is titled "(provisional)".
  - M1–M8 and L1–L10 are disclosed in the README and the design doc.
- **Legacy results:** the tracked pre-Issue-4 results in `outputs/` are described as "not current results" in the README. The files themselves carry no marker, have not been moved to `outputs/legacy_pre_issue4/` (not yet scheduled), and sit at the Stage 9/10 default output path (L9). Notebooks 05–08 show the old design with no banner (Q7, not yet scheduled).

---

## 8. Conclusion

### **A. Not ready for release.**

**Justification.** The conclusion rests only on items that are already documented as open:

1. **M8 is open.**
   - Every Stage 9 and 10 result is provisional (design §9.1; phase plan additional instruction 3).
   - Stage 8 exposure is not normalised, `baseline_crime_intensity` is a bad control, and the event study has the −7..0 day gap.
   - A public release of the pipeline's results cannot happen while M8 keeps them provisional, and no Stage 9/10 result has been produced for publication.
2. **Legacy results are still in place.**
   - The pre-Issue-4 files in `outputs/` have not been moved (not yet scheduled).
   - They are at the default output path of Stages 9 and 10, which overwrite them without a backup (L9).
   - Notebooks 05–08 show the old design without the approved banners (Q7, not yet scheduled).
   - The README explains all this, but the files and notebooks do not.
3. **Validation gaps V-1 to V-11 are open and not yet scheduled.**
   - The Stage 9 and 10 standard errors, two-way clustering and the pre-trend test are not independently checked.
   - On the current data state, `commit12_validate.py` does not return `FAILS: none` (V-10, §3.3).
4. **Public reproducibility and auditability are incomplete.**
   - The exact inputs are not obtainable from the repository (§7.1).
   - The decision record depends on transcripts that are not in the repository (§7.2).
   - Stages 8–10 do not record a full provenance chain (L8).
   - `pyarrow` is unpinned.
5. **Step 5 (`robustness.py`, the D18/D19 grid) is out of scope (Q6).** Apart from the single runs in the record, there is no systematic sensitivity or placebo analysis through Stages 7 → 8 → 9. It stays open as a release item.
6. **The acceptance record is not updated in the repository.** The Phase 9 approval is not recorded, and the status text in three documents still reads "pending review" (§6.1 C-1).

**What this conclusion does not say:**
- It does not change any Phase 9 result, and it does not say that the Stage 7–10 implementation is defective.
- Phase 9 found every Stage 7 hard check passing, 18 of 20 checklist items passing (item 9 partly met; item 19 met with the non-overwrite path relying on earlier evidence), and Stages 8–10 validated structurally.
- The not-ready conclusion comes from the open limitations (M8 above all), the deferred work, and the reproducibility and auditability gaps. It does not come from the acceptance results.

---

## 9. Open and deferred items (register)

| Item | Status | Source |
|---|---|---|
| M1 estimand change; M2 `prior_episodes_250m` imbalance; M3 interference; M4 selection on the future; M5 placebo population; M6 year-dependent closure quality; M7 "not reported dark" | Open; disclosed limitations | [issue4_design.md §9.1](issue4_design.md) |
| **M8** defects outside Issue 4; every Stage 9/10 result provisional | **Open** | same |
| L1–L10 (NB α = 1.0, centre definitions, site cells, endpoint sharing, shifted pre-window, −7..0 gap, provenance chain, default output paths, cluster structure) | Open; recorded | [issue4_design.md §9.2](issue4_design.md) |
| V-1 to V-11 validation gaps (V-10 includes the `commit12_validate.py` fixture dependency) | Open; not yet scheduled | [issue4_acceptance.md §5](issue4_acceptance.md) |
| Two `commit12_validate.py` fixture-dependent FAILs | Known validator limitation (V-10); the script is frozen and unchanged | [acceptance_report.md §5](acceptance_report.md) |
| Item 9 hand check of the top artifact sites | Done in Commit 4; not repeated; item 9 partly met | [acceptance_report.md §3](acceptance_report.md) |
| X3 / Q9 never-overwrite path | Not re-exercised in Phase 9; earlier evidence PASS | §3.2 |
| Q7 notebook banners; D16 notebook realignment | Not yet scheduled; realignment deferred | [issue4_decisions.md §2, §8](issue4_decisions.md) |
| Moving legacy results to `outputs/legacy_pre_issue4/` | Not yet scheduled | [issue4_design.md §10](issue4_design.md) |
| Step 5 `robustness.py` (D18/D19 grid) | Out of scope (Q6); open release item | same |
| Permanent item 11 "alone" check; independent Stage 9 SE check; cross-stage constants test | Not yet available; no phase assigned | [issue4_acceptance.md §4.2](issue4_acceptance.md) |
| Commit 6 fix-up P3 | Deferred by decision | [issue4_decisions.md §4](issue4_decisions.md) |
| Recording the Phase 9 approval and the acceptance decision in the repository documents | Not done; outside the Phase 10 scope | §2, §6.1 |
| Push / merge of `issue4-step1` | Not done (Q12) | [issue4_decisions.md §8](issue4_decisions.md) |
