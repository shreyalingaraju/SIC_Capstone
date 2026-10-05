# D18 / D19 robustness, sensitivity and placebo grid (Step 5)

Written for: reviewers deciding how far the Stage 9 and Stage 11 estimates can be trusted. It does not change any earlier stage or any acceptance record.

**Status.** This implements the work deferred in Q6 ([issue4_design.md §3.8, §10](issue4_design.md); [issue4_decisions.md](issue4_decisions.md) D18, D19, Q6; [release_readiness_review.md](release_readiness_review.md), open item 5). It was added after the Stage 8–11 commits and is not part of the accepted Stage 8–11 work. All results stay **provisional** (M8 open).

## What it is

`src/models/robustness.py` runs the accepted pipeline once per specification: Stage 7 (`match_controls.py`), then Stage 8 (`build_causal_panel.py`), then the Stage 9 estimator via `src/models/displacement_model.run` (paired-difference DiD, equal to the two-way FE coefficient; CR1 SEs clustered on `treatment_h3_res7`) with the Stage 11 definitions (direct 0–100 m, displacement 100–250 m ring, `net = direct + displacement`). Each run changes exactly one D19 parameter, or applies the D18 placebo shift. Nothing is written to `data/processed/` or to the Stage 8–11 outputs.

Output goes to `outputs/robustness/d19_grid/` (git-ignored, Q8):
- `robustness_estimates.csv`: long format with spec, parameter, value, period, effect, estimate, SE, CI, n, estimator, clustering, classification, difference from primary, sign agreement and placebo expectation.
- `robustness_summary.json`: hashes and per-spec metadata.

```
python src/models/robustness.py [--run-id ID] [--only SPEC ...] [--keep-intermediates]
python scripts/validation/robustness_validate.py
```

## Specification grid (16 runs)

| Class | Spec id | Change from the primary |
|---|---|---|
| primary | `primary_rerun` | none; reproduces the canonical pairs file byte for byte and the Stage 11 estimates exactly |
| sensitivity | `exclusion_250`, `exclusion_500` | `--exclusion-radius-m` |
| sensitivity | `band_max_2000` | `--match-band-max-m 2000` |
| sensitivity | `caliper_0.2` | `--caliper-sd 0.2` |
| sensitivity | `reuse_never` | `--reuse-policy never` |
| sensitivity | `report_lag_0` | `--report-lag-days 0` (D19 "pre-buffer 0") |
| sensitivity | `duration_50`, `duration_720` | `--imputed-duration-hours` |
| sensitivity | `control_pre_only` | `--control-selection pre_only` (D19 "ITT controls", mapped through design §3.8 and M4) |
| sensitivity | `seed_101`, `_202`, `_303`, `_404` | `--match-seed` (42 is the primary) |
| sensitivity | `pre_window_shifted` | Stage 8 `--pre-window shifted` on the canonical pairs |
| placebo | `placebo_90` | `--placebo-shift-days 90` (D18): dates, windows and clean rules shifted, re-matched |

Two D19 items needed interpretation: "ITT controls" is read as `pre_only` (the only matching option the design ties to it), and "5 seeds" as the default 42 plus 101/202/303/404. The full 96-run grid is not run; D19 says one change at a time.

## Results (during / post; change in crime counts; SE in parentheses)

| Spec | n pairs | direct | displacement | net |
|---|---|---|---|---|
| **primary** | 20,051 | −0.0008 (.0072) / **+0.0158 (.0076)** | −0.0239 (.0184) / +0.0037 (.0194) | −0.0247 (.0202) / +0.0195 (.0217) |
| exclusion_250 | 28,909 | −0.0082 / −0.0010 | +0.0002 / −0.0237 | −0.0080 / −0.0247 |
| exclusion_500 | 7,419 | −0.0090 / −0.0044 | +0.0027 / −0.0100 | −0.0063 / −0.0144 |
| band_max_2000 | 23,074 | −0.0055 / +0.0042 | −0.0164 / +0.0057 | −0.0219 / +0.0099 |
| caliper_0.2 | 12,154 | +0.0016 / **+0.0197 (.0092)** | −0.0063 / −0.0012 | −0.0047 / +0.0185 |
| reuse_never | 17,623 | −0.0064 / +0.0159 | −0.0066 / +0.0208 | −0.0129 / +0.0367 |
| report_lag_0 | 22,267 | +0.0017 / +0.0049 | **−0.0374 (.0185)** / +0.0064 | −0.0357 / +0.0113 |
| duration_50 | 20,273 | −0.0045 / +0.0073 | −0.0265 / +0.0154 | −0.0311 / +0.0227 |
| duration_720 | 18,803 | −0.0072 / +0.0041 | −0.0257 / +0.0072 | −0.0329 / +0.0113 |
| control_pre_only | 27,341 | −0.0121 / +0.0079 | −0.0023 / −0.0244 | −0.0143 / −0.0165 |
| seeds 101–404 | about 20,000 | −0.0021 to −0.0038 / +0.0099 to +0.0131 | −0.0092 to −0.0220 / +0.0047 to +0.0143 | −0.0130 to −0.0247 / +0.0164 to +0.0243 |
| pre_window_shifted | 20,051 | +0.0058 / **+0.0224 (.0077)** | −0.0062 / +0.0214 | −0.0003 / **+0.0439 (.0218)** |
| *placebo_90 (not causal)* | 25,720 | −0.0093 / −0.0044 | −0.0168 / −0.0158 | −0.0261 / −0.0201 |

Bold marks 95% intervals that exclude zero. The complete table, with CIs and the displacement proportion (a point estimate only), is in the CSV.

## Reading the results

- **Net effect:** the interval contains zero in every specification and both periods except `pre_window_shifted` post (+0.044, z about 2.0). The primary's "no detectable net effect" holds. The one exception sits in the specification that changes the baseline window, so it is a flag and not a finding.
- **Direct post effect (primary +0.0158, significant):** positive in 12 of 14 sensitivity runs, but significant in only three (`caliper_0.2`, `reuse_never` at z = 1.96, `pre_window_shifted`), and negative under both exclusion radii. Treat it as suggestive, not robust.
- **Direct during effect:** near zero or slightly negative everywhere; never significant.
- **Displacement:** significant only in `report_lag_0` during (−0.037). The sign changes across specifications, so the data neither show nor exclude displacement.
- **Placebo (expected null; not an effect):** all six inferential placebo intervals contain zero, consistent with the expectation. The estimates lean negative (direct during z about −1.7), and none reaches significance.
- **Overall:** the sensitivity analysis does not overturn the primary reading (no reliable net effect or displacement), and it does not make the direct post effect robust. The spread across seeds (net post +0.016 to +0.024) gives a rough size of matching noise.

## Limitations

- Provisional: M8 applies to every run.
- Runs reuse the same data and matching logic; they are not independent replications.
- No multiplicity adjustment across 16 specifications and 8 estimates.
- `pre_window_shifted` also changes `baseline_crime_intensity` (L6).
- Sample sizes range from 7,419 to 28,909 pairs, so some specifications change the estimand (M1) as well as the specification.
- The displacement proportion has no interval and is very unstable across specifications.
- Not done: the full 96+ run grid, the Stage 10 event study per specification, Poisson/NB variants, other placebo shifts.
