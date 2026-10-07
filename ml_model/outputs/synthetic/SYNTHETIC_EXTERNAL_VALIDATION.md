# External validation of the frozen NYC model on the synthetic dataset

**Conclusion in one line: partial generalization, in a simulated environment only.** The model ranks complaints better than chance and its borough-level "slow regime" signal carries over, but it does not rank individual complaints within a regime, its probabilities are too high, and the frozen threshold misses most slow repairs.

## 1. Objective
Test, once, how the frozen NYC-trained slow-repair classifier behaves on data from a different (simulated) environment. Nothing was retrained, refit, recalibrated, re-thresholded or tuned; the synthetic generator and the model files were not touched.

## 2. Frozen model
XGBoost (depth 3, 200 trees, lr 0.05) inside its preprocessing pipeline, trained on NYC 2024-01..2025-06, selected on NYC validation, decision threshold 0.6153 (Youden J on NYC validation). Files in `models/`. The NYC test numbers below come from `models/model_card.json`.

## 3. Synthetic dataset
`synthetic_data/` (seed 42): 49,796 rows (244 exact duplicates), 2024-01-01 to 2026-09-30. Repair durations come from an **authored queue simulation** (crew capacity vs workload, regimes, quick lane, complex repairs). Slow-repair prevalence ~35%. See `synthetic_data/README.md`.

## 4. Methodology
* Command run unchanged: `python -m evaluation.evaluate_synthetic --data synthetic_data/data/synthetic_streetlight_complaints.csv`. Label = `closed_date - created_date > 168 h` via the existing pipeline code (open complaints older than 7 days count as slow; complaints younger than 7 days and unresolved have no label and are excluded).
* **Eligible observations: 49,489** of 49,796 rows (all, including duplicates). **History-ready: 45,952** (created on/after 2024-03-01, i.e. 60 days of in-file history). Warm-up only: 3,537. Not silently dropped: all three groups are reported (`metrics_all_groups.json`). A de-duplicated history-ready run gives the same results to 3 decimals (ROC-AUC 0.799).
* `evaluation/analyze_synthetic.py` (new, read-only diagnostics) produced the subgroup, calibration, shift and behaviour analyses. Generator ground truth confirmed the pipeline's labels match on every eligible row. The saved predictions reproduce exactly.
* Everything labelled "info only" below was not used to change anything.

## 5. All-observation results (n = 49,489, prevalence 0.353, threshold 0.6153)
ROC-AUC 0.791; PR-AUC 0.680 (prevalence 0.353, so +0.327 / 1.93x over a no-skill model); accuracy 0.760 (always predicting "fast" gives 0.647); precision 0.756; recall 0.470; F1 0.580; Brier 0.193 (prevalence-only baseline 0.228).
Confusion matrix (TN / FP / FN / TP): 29,383 / 2,649 / 9,252 / 8,205.

## 6. History-ready results (n = 45,952, prevalence 0.348)
ROC-AUC 0.800; PR-AUC 0.688 (+0.339, 1.97x over prevalence); accuracy 0.769 (majority baseline 0.652); precision 0.763; recall 0.487; F1 0.595; Brier 0.191.
Confusion matrix: TN 27,526 / FP 2,418 / FN 8,211 / TP 7,797.
Warm-up rows alone (n = 3,537) are clearly worse: ROC-AUC 0.675, recall 0.28. So **history truncation matters**, as expected for a model driven by history features, but it only moves the pooled all-rows result by about +0.008 AUC because warm-up is 7% of rows.

## 7. NYC vs synthetic

| Metric | NYC test | Synthetic all | Synthetic history-ready | Δ all vs NYC | Δ history-ready vs NYC |
|---|---|---|---|---|---|
| n | 19,722 | 49,489 | 45,952 | | |
| Positive prevalence | 0.797 | 0.353 | 0.348 | -0.444 | -0.448 |
| ROC-AUC | 0.744 | 0.791 | 0.800 | +0.048 | +0.056 |
| PR-AUC | 0.903 | 0.680 | 0.688 | -0.223 | -0.215 |
| PR-AUC − prevalence | +0.106 | +0.327 | +0.339 | +0.221 | +0.233 |
| PR-AUC / prevalence | 1.13x | 1.93x | 1.97x | | |
| Accuracy | 0.739 | 0.760 | 0.769 | +0.020 | +0.029 |
| Accuracy of always-majority | 0.797 | 0.647 | 0.652 | | |
| Precision | 0.884 | 0.756 | 0.763 | -0.128 | -0.121 |
| Recall | 0.774 | 0.470 | 0.487 | -0.304 | -0.287 |
| F1 | 0.826 | 0.580 | 0.595 | -0.246 | -0.231 |
| Balanced accuracy | 0.689 | 0.694 | 0.703 | +0.005 | +0.015 |

Reading it correctly: the headline PR-AUC, precision, recall and F1 all fell, but they are prevalence-driven. On NYC test the positive class was 80% so PR-AUC 0.90 was only 1.13x better than guessing and the model was *less accurate than predicting "slow" for everything*. On synthetic the positive class is 35%, PR-AUC 0.69 is ~2x the no-skill level. ROC-AUC and balanced accuracy, which do not depend on prevalence, are similar or slightly higher on synthetic. The **recall drop is a threshold effect** (see 9): the frozen threshold was chosen for an environment where most complaints were slow. Higher synthetic AUC does not mean the model "works better" here; see section 9 on why.

## 8. Domain-shift analysis (PSI vs NYC training set; >0.25 = major; `domain_shift_psi*.csv`)
* **Category/location features: major shift** — zip_code 4.1, police_precinct 3.8, community_board 1.8, location_type 0.56, address_type 0.36 (the generator uses NYC-like codes assigned at random: 9/41 precincts and 20/80 zips are unseen by the model; unseen levels are pooled). Their permutation importance is ~0, so they cost little.
* **Regime/history features: major shift** — boro_backlog_60d 2.2 (mean 212 → 130), boro_share_gt168_14d 2.0 (0.44 → 0.33), city_median_dur_14d 1.9, boro_median_dur_14d 1.5, boro_closed_14d 0.7, boro_created_7d 0.33. These are the model's key inputs.
* **Repeat-location features:** loc_days_since_prev 0.78 (mean 328 → 130 days, 25% missing vs 15%), loc_prior_90d/365d ≈ 0.02/0.09 (ok). The generator's repeat structure is denser but the model barely uses these features.
* **Temporal/time features:** hour 0.13 (evening vs morning peak), month 0.05, dow 0.04, borough 0.04 (ok); weekend share 24% vs 10%.
* **Workload:** boro_created_7d and backlog are lower than NYC train.
* Context: the model already met large shifts inside NYC (validation/test vs train: city_median_dur_14d PSI 4.8 and 7.1, boro_share_gt168_14d 1.4 and 1.8), so shift magnitude alone does not predict failure; what matters is whether the *relationship* between the shifted features and the outcome holds.
Shift explains the calibration/threshold changes (section 9), and the change in which features carry the signal; it does not by itself explain the higher AUC, which comes from how the simulation was built.

## 9. Model behaviour (does it still rely on borough-level recent regime?)
Evidence on the history-ready set:
* **Yes, the model is a regime tracker here too.** Across 155 borough-months, mean predicted probability vs observed slow rate: Pearson 0.78, Spearman 0.46. Within Bronx, Brooklyn, Queens the borough-month correlation is 0.84-0.87.
* **But it ranks almost nothing within a regime.** Within borough-month the weighted ROC-AUC is **0.524** (near chance), versus 0.80 pooled. The pooled AUC is mostly between-regime variation (which month/borough is slow), exactly the pattern found on NYC.
* **It fails where its regime proxy does not apply.** Manhattan: borough-month correlation 0.02, within-borough AUC 0.57, mean prediction 0.38 vs observed 0.10. Staten Island: correlation 0.29, AUC 0.57, predicted 0.47 vs observed 0.12.
* **Compressed predictions.** Per-borough-month observed rates span 0.01-0.86 but predictions only 0.25-0.76. Bottom six deciles of the score all have observed rates of 0.12-0.19 (flat, no ranking), real separation appears only in the top four deciles (0.41 → 0.78).
* **Different features drive it than in NYC.** Permutation importance on synthetic: boro_median_dur_14d 0.113, boro_backlog_60d 0.081, boro_created_7d 0.026, boro_share_gt168_14d 0.023; on NYC the top feature was boro_share_gt168_14d at 0.143. Single raw-feature AUCs on synthetic: backlog 0.884, boro_median_dur 0.884, boro_share_gt168 0.876; the model (0.80) is *below* a plain backlog feature on its own. This difference is largely a property of the authored simulation (queue backlog is the delay mechanism) and not evidence the model learned a general backlog law.
* Repeat-location features carry no usable signal here (single AUC loc_prior_90d 0.54, days-since 0.49), though the generator builds in a repeat effect, so the model/NYC does not exploit it.

**Discrimination vs calibration.**
* *Discrimination* (ranking): good at the pooled level (AUC 0.80), weak within regimes.
* *Calibration*: poor. Mean predicted probability 0.474 vs observed 0.348 (over-prediction by 0.13); decile calibration error 0.155; the lowest six deciles predict 0.25-0.46 against observed 0.12-0.19; Brier skill vs prevalence-only is 16% (NYC: 4%). No recalibration was applied. Probabilities are not usable as risk estimates outside NYC.
* *Threshold*: at the frozen 0.6153, 22% of rows are flagged and recall is 0.49. For information only: a Youden threshold re-derived on synthetic (0.51) would give recall 0.71, F1 0.70, balanced accuracy 0.77, which shows much of the recall loss is threshold/calibration shift, not lost ranking. This number was not used and the threshold was not changed; selecting a threshold on synthetic results would be tuning.

**Verdict on the three categories:** it does not *fail* (it is clearly above chance and the borough-level regime signal transfers in 3 of 5 boroughs), it does not *generalize reasonably* (no within-regime ranking, broken in Manhattan/Staten Island, miscalibrated, threshold mismatched). **Partial generalization.**

## 10. Limitations
* **The synthetic repair process is an authored queue simulation.** This result shows how the frozen model behaves in *this simulated environment*, **not** real-world generalization to another city. Real repair processes may differ in kind, not just in parameters.
* The generator was designed with backlog→delay as a mechanism, so the model's reliance on backlog features is partly built into the test (not neutral); high AUC can be a consequence of that design.
* Synthetic prevalence 0.35 vs NYC 0.80 and differing regime timing make PR-AUC, precision, recall and F1 non-comparable across datasets; only AUC, balanced accuracy and lift over prevalence are roughly comparable.
* One synthetic dataset, one seed: no variance estimate across alternative synthetic environments. Differences of ~0.01 AUC (e.g. history-ready vs all) should not be over-read.
* Duplicates (0.5%) and injected quality issues are included in the "all" counts; de-duplication changes nothing at 3 decimals.
* The NYC test period was itself non-stationary (prevalence 0.80), so "NYC test performance" is a modest baseline (AUC 0.74).

## 11. Generalization conclusion
**Partial generalization.** Evidence: pooled ROC-AUC 0.79-0.80 and balanced accuracy 0.69-0.70 (vs 0.74 / 0.69 on NYC), PR-AUC ~2x the base rate, and strong tracking of borough-level regimes in Bronx/Brooklyn/Queens (r 0.84-0.87); against: within borough-month AUC 0.52, no skill in Manhattan, weak in Staten Island, over-prediction (0.47 vs 0.35), 51% of slow repairs missed at the frozen threshold, and a different feature mix doing the work. Useful outside NYC only as a coarse "is this borough/period in a slow regime" indicator after local recalibration and threshold selection (which were deliberately not done here), not as a per-complaint predictor.

Files: `metrics_all.json`, `metrics_history_ready.json`, `metrics_all_groups.json`, `synthetic_metrics.json`, `synthetic_predictions.csv`, `nyc_vs_synthetic_comparison.csv`, `synthetic_confusion_matrix.png`, `synthetic_history_ready_confusion_matrix.png`, `roc_*.png`, `pr_*.png`, `calibration_curve.png`, `calibration_deciles_*.csv`, `by_borough_history_ready.csv`, `borough_month_tracking.csv`, `feature_importance_synthetic_vs_nyc.csv`, `domain_shift_psi*.csv`, `behaviour_and_calibration.json`.
