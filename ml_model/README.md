# Streetlight slow-repair model (standalone ML experiment)

Predicts, at the moment a streetlight-out complaint is filed, whether it will **still be unresolved after 7 days**.
Trained and selected on NYC 311 data only. The synthetic dataset has **not** been used for anything yet.
Nothing here is connected to any other project.

## Reproduce

```
pip install pandas scikit-learn xgboost joblib matplotlib
python -m training.train                                   # ~1 min; writes models/ and outputs/
python -m evaluation.evaluate_synthetic --data <synthetic.csv> [--snapshot <extract time>]   # later, inference only
```

Layout: `features/build_features.py` (labels + features), `training/train.py`, `evaluation/` (metrics, external validation),
`models/` (frozen artifacts), `outputs/` (metrics, predictions, plots), `data/processed/` (feature cache).

## 1. What the data actually is

`streetlight_complaints.csv`: 218,465 rows x 44 columns, no duplicate rows or keys. Created 2020-01-01 to 2026-09-18.

* Single agency / complaint type / descriptor (all DOT "Street Light Out") — these columns are constants.
* 17 columns are 100% empty or constant (`due_date`, `location_type`, `landmark`, taxi/bridge fields, `open_data_channel_type`, ...).
* **No crime fields, no outage-duration field, no neighbouring-outage field.** Crime history and "outage duration" cannot be used;
  the only duration is complaint-to-closure, which is the outcome itself.
* Location: borough (5% missing), community board, precinct, zip (16% missing), lat/lon (16.6% missing; 92% missing for BLOCKFACE rows).
* `status`: Closed 180,221 / Pending 31,165 / Assigned 3,732 / Open 3,347. `resolution_description` is boilerplate.

Data-quality problems that shaped the design:

1. **`Pending` rows have a bogus `closed_date`** 24 h to ~12 months (median 72 h) *before* `created_date`. Treated as outcome-unknown and excluded from labels and history features.
2. **Zero-duration closures** (closed_date == created_date): ~50% of closed rows in 2020-2022, 25% in 2023, <3% from 2024. A recording artefact,
   not repair behaviour. The modelling window therefore starts **2024-01-01**.
3. **Right-censoring**: recent complaints not yet closed have unknown outcomes. Complaints younger than 7 days at the snapshot and still open are dropped.
4. **Strong non-stationarity**: share of closed complaints taking >7 days was 0.18 (2020-22) -> 0.44 (2024) -> 0.61 (2025) -> 0.75 (2026).

## 2. Problem definition

**Binary classification:** `slow_repair = 1` if a complaint is not resolved within 168 h of `created_date`.

* Built from `created_date` + `closed_date`. Open/Assigned complaints older than 7 days are positives; closed ones use their real duration.
* Why classification over regression: durations are extremely heavy-tailed (median 50 h, 99th pct >12,000 h) and partly artefactual; a
  7-day threshold is a stable, interpretable service-level question. Ranking is also supported via the predicted probability.
* The synthetic dataset can provide the same ground truth **iff** it has a report timestamp and a repair/closure timestamp per complaint
  (and ideally a borough). Without a closure time the label cannot be reproduced and the external test is impossible.

## 3. Features and leakage audit

Used (all knowable at report time): borough, location type, address type, community board, precinct, zip, council district, lat/lon,
hour/day-of-week/month, plus history features:

* `loc_prior_90d`, `loc_prior_365d`, `loc_days_since_prev` — earlier complaints at the same location (strictly earlier timestamps).
* `boro_backlog_60d`, `boro_created_7d` — unresolved / new complaints in the borough as of 00:00 of the creation day.
* `boro_closed_14d`, `boro_median_dur_14d`, `boro_share_gt168_14d`, `city_median_dur_14d` — how fast complaints closed in the 14 days *before* the creation day.

**Excluded as leaky:** `closed_date`, `resolution_action_updated_date`, `resolution_description`, `status` (all post-outcome snapshots),
`due_date` (empty), derived duration. Closure statistics use only closures that happened before the creation day. Calendar year is not a feature.

## 4. Split (chronological)

| Set | Period | Rows | Slow-repair rate |
|---|---|---|---|
| Train | 2024-01-01 .. 2025-06-30 | 32,622 | 0.458 |
| Validation | 2025-07-01 .. 2025-12-31 | 14,624 | 0.756 |
| NYC test | 2026-01-01 .. 2026-09-12 | 19,722 | 0.797 |

The prevalence moves a lot between sets (see non-stationarity above). This makes the problem harder, and the NYC test number honest about it.

## 5. Models and selection

Logistic regression, random forest, XGBoost; 12 configs total (`outputs/model_comparison_all_configs.csv`). Preprocessing lives inside the
pipeline (median imputation + missing indicators, log1p on count/duration features, one-hot with rare-category pooling, scaling for LR only);
imbalance handled with class weights / `scale_pos_weight`. Selection used **validation ROC-AUC only** (prevalence-robust); threshold = Youden J on validation.

| Model (best config) | Val ROC-AUC | Val PR-AUC | Test ROC-AUC | Test PR-AUC |
|---|---|---|---|---|
| **XGBoost** (depth 3, 200 trees, lr 0.05) | **0.804** | 0.908 | 0.744 | 0.903 |
| Logistic regression (C=0.01) | 0.799 | 0.904 | 0.746 | 0.902 |
| Random forest (depth 16, leaf 30) | 0.791 | 0.901 | 0.716 | 0.889 |
| Prior-only baseline | 0.500 | 0.756 | 0.500 | 0.796 |

Test columns are informational; they were not used to pick the model. XGBoost beat logistic regression by only 0.005 val AUC — within noise;
LR actually scores slightly higher on test. The choice follows the pre-stated rule, not a meaningful difference.

## 6. Frozen model (`models/`)

`frozen_model.joblib` (full preprocessing + XGBoost), `feature_schema.json`, `model_card.json` (config, threshold 0.6153, split dates, metrics,
library versions). Fit on the train split only (validation not merged back, to keep the threshold valid).

## 7. NYC test results (threshold 0.6153)

ROC-AUC 0.744, PR-AUC 0.903 (prevalence 0.797), balanced accuracy 0.689, precision 0.884, recall 0.774, F1 0.825, accuracy 0.739.
Confusion matrix: TN 2,421 / FP 1,593 / FN 3,548 / TP 12,160. Plots and predictions are in `outputs/`.
Validation->test AUC dropped 0.804 -> 0.744 as the repair regime kept drifting.

## 8. What the model has and hasn't learned — read before trusting it

* **Most of the skill is borough-level "recent regime"**: `boro_share_gt168_14d` is by far the top feature (permutation AUC drop 0.143; next is 0.018).
  Static features alone (borough, location, time, ...) reach only 0.732 val and **0.553 test** AUC.
* **Within a borough the model is weak**: test ROC-AUC Bronx 0.60, Manhattan 0.65, Queens 0.64, Brooklyn 0.53, Staten Island 0.46
  (Brooklyn/Staten Island are 95-97% positive, so little to separate). It ranks which *period/borough* is slow better than which *individual complaint* is slow.
* PR-AUC looks high mostly because prevalence is ~80%; compare it with the 0.796 baseline, not with 0.5.
* Month is a feature but only 1.5 seasonal cycles are in train, so seasonality is confounded with trend.
* No crime or physical-outage data exists here, so those hypotheses could not be tested.
* Probabilities are not calibrated across periods (prevalence shifts); use ranking, or recalibrate on new data before using probabilities.

## 9. Synthetic external validation (pending)

`evaluation/evaluate_synthetic.py` loads the frozen model and threshold unchanged, builds the label and features with the same code, and writes
NYC-vs-synthetic metrics, ROC/PR/confusion plots, predictions, and a PSI domain-shift table (`outputs/synthetic/`). Requirements for the synthetic file:
`created_date`, `closed_date`, `borough`; the more NYC-style columns (location type, community board, precinct, zip, lat/lon, ...) it has, the fewer
features are missing. History features are computed from the synthetic file's own complaint log, so it needs enough chronological volume
(roughly 60 days of history before the evaluated complaints). The script was smoke-tested for plumbing only, using a slice of NYC data; those numbers are not results.
Expect a large shift if synthetic repair-time dynamics or location schemes differ from NYC; PSI > 0.25 is flagged "major".
