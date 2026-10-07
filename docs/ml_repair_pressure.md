# Repair-pressure context (ML)

Written for: technical reviewers and judges who want to know exactly what the machine-learning component does, and does not, do in LightSafe.

## In one paragraph

LightSafe ranks outages with a transparent priority score and a constrained dispatch plan. Alongside that, a frozen XGBoost model reads recent 311 street-light complaint and closure patterns and reports, per borough, how slow repairs currently are relative to that borough's own 2024-2025 history ("repair pressure"). It is context for dispatchers. It is **not** a crime predictor, it does **not** rank individual outages, and it does **not** change any priority score, queue, FIFO benchmark or dispatch plan.

## Where ML sits

```
Raw 311 + crime data ──> causal / spatial pipeline ──> priority score ──> queue, FIFO comparison, constrained plan
        │                                                                            (unchanged by ML)
        └──> frozen repair-pressure model ──> borough repair-pressure context ──────> dashboard (Overview), shown beside
```

| Part | Location |
|---|---|
| Research: data audit, features, model comparison, evaluation, synthetic validation, model card | `ml_model/` |
| Runtime: verified frozen copy, integrity check, regime computation, API | `backend/services/ml/` |
| Snapshot builder (offline, reproducible) | `scripts/ml/build_regime_snapshot.py` → `outputs/ml/regime_snapshot.json` |
| Endpoint | `GET /api/ml/regime` |

There is one runtime implementation. `ml_model/` is research and provenance only; the backend serves a byte-identical copy of `ml_model/models/frozen_model.joblib` (`scripts/ml/freeze_runtime_artifacts.py` records both SHA-256 values in `backend/services/ml/artifacts/MANIFEST.json`). Nothing was retrained, tuned or re-thresholded for the integration.

## The model (from `ml_model/README.md` and `model_card.json`)

- **Task:** at the time a complaint is filed, will it still be unresolved after 7 days (`slow_repair`)?
- **Data:** NYC 311 "Street Light Out" complaints, modelling window from 2024-01-01 (earlier years have a zero-duration closure artefact).
- **Features (24):** location and calendar fields, repeat-complaint history at the location, and borough state before the complaint day (backlog, new complaints, recent closure speed). No post-outcome fields are used.
- **Split (chronological):** train 2024-01 to 2025-06 (32,622 rows), validation 2025-07 to 2025-12 (14,624), test 2026-01 to 2026-09 (19,722).
- **Models compared:** logistic regression, random forest, XGBoost (12 configurations). Selected on **validation ROC-AUC only**: XGBoost 0.804 vs logistic regression 0.799, which is within noise. Threshold 0.6153 (Youden J on validation) is stored but not used by the repair-pressure context.
- **NYC test:** ROC-AUC 0.744, PR-AUC 0.903 against a prevalence of 0.797 (a no-skill PR-AUC), balanced accuracy 0.689. AUC fell from 0.804 on validation as repair pace kept drifting.

## What the model has actually learned

Most of its skill is the borough's *recent repair pace* (`boro_share_gt168_14d` is the dominant feature; static features alone reach only 0.553 test AUC). Within a borough it ranks individual complaints weakly (test AUC 0.46 to 0.65 by borough). That is why it is used for borough-level operational context and not for ranking outages.

## How repair pressure is computed

1. Every complaint from 2024-01-01 is scored with the frozen model.
2. For each borough, the mean score over the trailing 28 days is computed. The current window is the 28 days ending at the latest complaint in the data.
3. The same statistic is computed for weekly-spaced 28-day windows across 2024-01 to 2025-12 (the model's development period).
4. The current value is ranked against that history: percentile at or above 67 is **High**, 33 to 67 **Moderate**, below 33 **Low**. `above_reference_range` marks a value higher than every historical window.

High means the model's repair-pressure score is high relative to its reference history. It does not mean high crime, a dangerous borough or a high crime probability. Because repair pace has slowed since 2024, several or all boroughs can read High at once; the dashboard uses neutral styling for that reason. At the current snapshot (window ending 2026-09-18) all five boroughs are High; Brooklyn and Manhattan are above the whole 2024-2025 range.

## Synthetic external validation

The frozen model was run once, unchanged, on a simulated dataset (`ml_model/outputs/synthetic/SYNTHETIC_EXTERNAL_VALIDATION.md`). Result: **partial generalisation in a simulated environment only**: ROC-AUC 0.80 pooled, but near chance within a borough-month (0.52), poorly calibrated, and wrong in Manhattan and Staten Island. The repair process in that dataset is an authored queue simulation with backlog causing delay by construction, so it does not show real-world generalisation to another city.

## Integrity and failure behaviour

At startup the backend verifies each artifact's SHA-256 against the manifest, checks that scikit-learn and xgboost match the versions the model was saved with (scikit-learn 1.8.0 and xgboost 3.4.1, recorded in `model_card.json`, embedded in the pickle, and pinned in `requirements.txt`), loads the pipeline once, and confirms it reproduces 60 stored predictions to 1e-5. The snapshot also records the model hash and is ignored if it does not match the loaded model. Any failure, or `LIGHTSAFE_ML_ENABLED=0`, makes `/api/ml/regime` return `available: false` with a plain-language note; every other endpoint is unaffected (tested: analytical responses are identical with ML on, off and broken). No model is downloaded and no external model API is used.

## Limitations

- Context only. No crime data entered the model, so it cannot speak to crime.
- Probabilities are not calibrated across periods; only the within-history ranking is used.
- The reference period is the model's own development data, and repair pace has drifted, so categories saturate at High.
- The snapshot is static between runs of `scripts/ml/build_regime_snapshot.py`, which needs the raw 311 file.
- Not shown to improve dispatch outcomes; it has not been tested for that and does not alter dispatch.

## How the rest of LightSafe fits (for orientation)

- **Causal evidence.** A matched difference-in-differences design (20,051 pairs, 120,306 observations) gives a post-period combined net effect of +0.0195 (SE 0.0217, 95% CI -0.0230 to +0.0620). The interval includes zero, so the data do not show that outages change nearby crime; the dashboard's evidence page says so. The frozen Stage 11 exposure analysis is a null association.
- **Spatial analysis and priority score.** Local night-crime rate within 250 m and outage duration drive a 0-100 ranking index. It is not a probability of crime.
- **Dispatch and fairness.** A FIFO simulation is the benchmark; an integer program selects the daily repairs under a budget with per-borough minimums (the "price of fairness" is reported). These outputs are descriptive ranking conventions, not proof of crimes prevented.
- See the README's "Final methodology" and `docs/stage11_exposure_analysis.md`, `docs/stage12_13_operational_dispatch.md`, `docs/stage14_*.md`.
