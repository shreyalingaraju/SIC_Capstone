# Commit 12 self-review (Phase 2)

Written for: the Issue-4 reviewer. This is a self-review by the implementer, not an independent review.

- **Code reviewed:** `src/features/match_controls.py` at `0fd113d` (Commit 12 `9ce8542` plus one fix-up).
- **Evidence** (all in this folder, generated from `0fd113d` with a clean working tree):
  - `canonical_scratch_run.log`: full canonical run with `--out-dir` pointing to a scratch folder (default parameters, exit 0).
  - `match_diagnostics.canonical_scratch.json`: the diagnostics file from that run.
  - `commit12_validate.out`: output of `scripts/validation/commit12_validate.py`, 40 checks, `FAILS: none`.

## 1. Specification compliance

| Item | Status | Evidence |
|---|---|---|
| **X1** candidate generation vs. membership | Implemented | `_recheck_candidates` (L3946) queries the geopandas `sindex` with `dwithin` at radius + `RECHECK_CANDIDATE_MARGIN_M` (1.0 m, L3925) and keeps a point only if `sqrt(dx²+dy²) ≤ r`. No buffer or polygon test decides membership. |
| **X2** independent rebuild | Implemented | `_recheck_complaints` (L3974) rebuilds the universe, projection, darkness intervals, 1 m sites and artifact flags from the raw CSV. `_recheck_own_episode` (L4073) rebuilds the own episode. No production `episode_id`, `is_artifact`, darkness interval, site table or index is read. Validation: the rebuilt own episode equals the production episode for 300 multi-complaint treatments; the artifact threshold is 44 with 44 artifact sites, as in production. |
| **X3** legacy output preservation | Implemented (automated) | `preserve_legacy_pairs` (L4756), called at the start of `write_outputs`, handles the pairs file. Validation: backup created with an equal sha256; an existing backup is kept; a new-schema file is not backed up. The `causal_panel.parquet` backup (Q9) belongs to Phase 3. The first run into `data/processed` is Phase 9. |
| **R1** S-4 rules | Implemented | `independent_recheck`, from L4237: pre is `s ≤ c−1 s`, `e ≥ c−event`; post is `s ≤ W_end`, `e ≥ closed+1 s`. Canonical: pre exempts the rebuilt own episode, post exempts only the treatment complaint. Placebo: no exemptions. Validation: 5 injected `S4_PRE_DIRTY` and 5 `S4_POST_DIRTY` treatments are flagged. The exemption for other complaints in the own episode is exercised for 36 of 2,000 sampled treatments (0 in placebo runs). |
| **R2** episodes and artifacts | Implemented | Breadth-first closure with no hop, radius or size cap. Links need both complaints non-artifact, `d ≤ episode_merge_radius_m`, and closed-interval overlap. T = `quantile(counts, 0.999, "higher")`; artifact if count > T. Artifact complaints stay darkness in S-3 and S-4. A treatment at an artifact site is a violation (negative test at quantile 0.5). |
| **R3** geometry, projection, windows, inputs | Implemented | `Transformer.from_crs("EPSG:4326", projected_crs, always_xy=True)` for complaints and crimes. Treatment coordinates must equal the pair columns exactly. The control must be a rebuilt whole-metre site with a matching `site_id` that is not an artifact. W and B are rebuilt from the raw dates plus the shift and compared in seconds. Crime rows with null values are dropped. Sub-second times are violations. |
| **R4** terminology | Implemented | "S-3 clean window" in the docstring (L4129); the phrase "contamination window" is absent. The `pre_only` window is `[w_start, created_shifted − 1 s]`. |
| **E1** radii from Params | Implemented | Every radius comes from `params` (`exclusion_radius_m`, `treatment_clean_radius_m`, `episode_merge_radius_m`, `direct_radius_m`, `outcome_radius_m`). Only the 1 m candidate margin is a constant. |
| **E2** complaint universe | Implemented | Latitude and longitude non-null and inside the inclusive `NYC_BBOX`. An unparseable `created_date` or a duplicate `unique_key` is a violation, not an exclusion. Negative test on a corrupted CSV copy: 2 duplicates and 1 unparseable date flagged, no crash. |
| **E3** S-3 | Implemented | `s3_end` (L4225): `w_end` for `full_window`, `c − 1` for `pre_only`. All complaints count, with real dates and no exemptions. Negative test: a control placed at the dark treatment site gives 2 hits. |
| **A1–A3** acceptance amendments | Partially implemented | Recorded verbatim in `diagnostics.parameters.acceptance_amendments` (`ACCEPTANCE_AMENDMENTS`, L4530). The checklist document is Phase 6. |
| **N5** post-matching H16 | Implemented | `_check_h16_post` (L5467): shifted dates, darkness from real dates, S-4 with no exemption. It passes on placebo 90; the negative tests fail it. Canonical runs report it as not applicable. |
| **N1–N2** JSON | Implemented | `_json_safe` / `diagnostics_json` (L4580): `allow_nan=False`, explicit numpy/timestamp/Path conversion, non-finite values → `null` plus a `non_finite` list. The file parses under strict JSON. |
| **N3–N4** H13 | Implemented | `_check_h13` (L5413): total = `n_s3_rows` from step 2; 12-step telescoping attrition; each step's dropped count equals the unmatched rows with that `reason_step`. Three negative tests fail it. |
| **N6** determinism | Implemented | `pair_list_hash` and `check_determinism` (L4474, L4481). Separate process runs give identical hashes and byte-identical parquet files. |
| **N7** rename order | Implemented | `write_outputs` (L4806): temporary files, then the schema check, then `os.replace`, with the diagnostics file last. A `PermissionError` is reported with the list of files already replaced. |
| **N8** failure file | Implemented | `_stop` (L6157) and `main`'s exception handler call `write_failed_diagnostics`. H0 failure: file written, exit 1. A successful run removes a stale failure file. |
| **N9** sample | Implemented | `_recheck_sample` (L3928): `min(RECHECK_SAMPLE_N, n)` from `pair_id` order with `RECHECK_SEED`. |
| **N10** Arrow types | Implemented | `ARROW_TYPES` and `_arrow_schema_problems` run on each temporary file before the rename. |
| **N11** parameter recording | Implemented | `parameters.values/locked/conventions/contamination_review_share/recheck_candidate_margin_m/h16_post_definition/acceptance_amendments`. |
| **N12** implemented signatures | Deferred to Phase 6 | Documentation item. |
| **N13** H9 examples | Implemented | The descriptive overlap line was removed from `examples`. It is printed from `main` and stored in `reuse.treatment_treatment_overlaps`. |
| **N14** unmatched nulls | Implemented | Steps 1–8: baselines and candidate counts are null. Steps 9–12: filled (validation "unmatched schema and reasons"). |
| **N15** exit 0 | Implemented | `_run` returns `EXIT_COMPLETED` only after `write_outputs`. |
| **U7** site role counts | Implemented | `assemble_sites` (L4440): the sums of both counts equal the pair count. |

## 2. Validation results

From `commit12_validate.out` and the canonical run.

| Area | Result |
|---|---|
| H10 on the canonical 2,000-pair sample | PASS; all 16 conditions 0 |
| H11 on the canonical 2,000-pair sample | PASS; all 4 counts exact |
| H10 negative tests | 9 tests (coordinates, window, S-3, control site ×2, S-4 pre, S-4 post, artifacts, universe), each flags exactly the intended condition |
| H11 negative test | 3 corrupted counts flagged exactly |
| H10/H11 in placebo 90 and `pre_only` runs | PASS |
| H13 | PASS; 3 negative tests fail it |
| H16_post | Not applicable (canonical); PASS (placebo 90); 2 negative tests fail it |
| Determinism | `--check-determinism` second pass identical. Two separate processes give pair-list sha256 `ab63c377…`, order hash `b36524dc…`, and byte-identical parquet files. |
| Outputs | Pairs 20,051, sites 46,034, unmatched 87,680 (20,051 + 87,680 = 107,731). pyarrow schemas match. No `.tmp` or failure files left. |
| Diagnostics | Strict JSON; every §3.4 section present; `non_finite` is empty in the canonical run; all 20 hard checks `pass` |
| `--skip-recheck` | H10 and H11 recorded as `skipped` |
| Runtime | 27.4 s summed over steps (recheck 2.7 s, writing 0.2 s); peak working set 1,531 MB |

**Canonical output hashes** (scratch run; the Phase 9 acceptance run is authoritative):
- pairs `abb9869b…`
- sites `9b349a62…`
- unmatched `b8fa10c3…`

## 3. Deviations and interpretations (not hidden)

1. **Check ID `H16_post`.** The post-matching H16 is recorded under `H16_post`, so it doesn't overwrite the step 2 `H16` in `hard_checks`. Naming only.
2. **H12 has one diagnostics entry.** Phase 2 (full schema) replaces the phase 1 record, because checks are keyed by ID. Both passed. A phase 1 failure stops the run before phase 2, so no failure can be hidden.
3. **A determinism difference stops the run with exit 1.** N6 only said "compare". Stopping is my interpretation: outputs that aren't reproducible shouldn't be written. Please confirm.
4. **X3 is automated.** `write_outputs` backs up an old-schema pairs file in any `out_dir`, instead of relying on a manual copy. It copies only a file without `control_site_id` and never overwrites an existing backup.
5. **The `write_outputs` step is missing from the diagnostics runtime**, because the diagnostics file is written inside that step. It is in the log (0.2 s).
6. **H0 failures write the failure file to the requested `out_dir`.** If H0 failed because `--out-dir` was missing for a non-default run, the failure file goes to `data/processed`. It is never a canonical output, and the next successful run removes it.
7. **Extra recheck diagnostic** `n_s4_pre_own_episode_exempted`. It is evidence that the R1 exemption is exercised and has no effect on pass or fail.
8. **Signatures differ from the engineering plan:**
   - `independent_recheck(params, pairs)` has no `coverage` argument; it rebuilds everything from raw.
   - `build_unmatched(treatments_all, treatments_eligible, match, pairs, sites)`.
   - To be recorded in Phase 6 (N12).
9. **The recheck samples 2,000 pairs as specified.** The full-population recheck suggested in the final review (V1) was not adopted, because it isn't part of the frozen specification.
10. **Evidence logs contain absolute scratch paths** (the `--out-dir` value). They are informational only.

## 4. Remaining for later phases

- `docs/` documentation (A1–A3 checklist, N12 signatures): Phase 6.
- `causal_panel.parquet` backup: Phase 3.
- First canonical write into `data/processed`, which will exercise the legacy pairs backup: Phase 9.
