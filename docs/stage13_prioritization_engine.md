# Stage 13: repair prioritization engine

Written for: reviewers and the Stage 14 implementer. It describes how the dispatch benchmark is built and what it can and cannot show.

**Status.** Implemented on `issue4-step1` after Stage 12. Provisional while M8 is open. The comparison benchmarks the Stage 12 priority index against FIFO under a stated simulation. **It does not show that the LightSafe queue prevents crime.**

## Objective

Build the dispatch engine: order outages by the Stage 12 priority score and compare it with the traditional First-In-First-Out (FIFO) queue under a repair-crew capacity limit. Code: [src/models/prioritization_engine.py](../src/models/prioritization_engine.py). Notebook: [notebooks/11_prioritization_engine.ipynb](../notebooks/11_prioritization_engine.ipynb). Validator: [scripts/validation/stage13_validate.py](../scripts/validation/stage13_validate.py).

## Input

`data/processed/outages_scored.parquet` (Stage 12, read only). Columns used: `unique_key` (outage id), `created_date`, `priority_score`, `priority_tier`, `raw_priority`, `local_crime_rate`, `duration_factor`, `scored`, `exclusion_reason` (and `borough` when present).

## Queues

- **FIFO:** `created_date` ascending; ties broken by `unique_key` ascending.
- **LightSafe Causal:** `priority_score` descending; ties broken by `created_date` ascending, then `unique_key` ascending. The Stage 12 score is used as is.
- Ordering never depends on the input row order (stable sort on fully specified keys). 60,819 of the scored outages share a score with another, so tie-breaking matters (mostly scores of exactly 0).

## Excluded Stage 12 records

3,901 outages have `scored == False` (`lookback_not_covered_by_crime_data`: created after the crime data ends) and no score. The Guide text given for this stage does not say how to treat them. The smallest defensible choice: **exclude them from both queues** and report the count. They are not placed at the end and are not assigned any priority. Both queues hold the same 103,830 outages, so the comparison is like for like.

## Repair capacity and simulation

K repairs per day (default 20, `--capacity`). All scored outages are in the backlog at day 0, and the crew repairs K per day in queue order: the outage at rank r is repaired on day ceil(r / K). `created_date` defines the FIFO order but does not delay availability, because the data are retrospective. 103,830 repairs take 5,192 days at K = 20.

## Expected impact

Each repair credits the outage's Stage 12 `priority_score` (0–100), unchanged, with no extra factor (`expected_impact` column). The cumulative "expected crimes prevented" is therefore the cumulative sum of **index points, not a count of crimes**. Stage 12's score is a decision-support index built from a constant `tau_net` that is not statistically distinguishable from zero and from observed post-closure durations; it is not a probability.

## Comparison metric

For each day, cumulative impact under each method, `absolute_difference = LightSafe − FIFO`, and `improvement_pct = (LightSafe − FIFO) / FIFO × 100`. If the FIFO cumulative impact is 0, `improvement_pct` is empty (NaN), never infinite.

## Results (K = 20; 103,830 repairs simulated for each method)

| Day | Repairs | FIFO cumulative | LightSafe cumulative | Difference | Improvement |
|---|---|---|---|---|---|
| 1 | 20 | 3.22 | 1,208.63 | 1,205.41 | 37,427% |
| 7 | 140 | 30.65 | 5,026.46 | 4,995.80 | 16,299% |
| 30 | 600 | 254.64 | 13,354.37 | 13,099.73 | 5,145% |
| 90 | 1,800 | 665.72 | 24,582.97 | 23,917.24 | 3,593% |
| 365 | 7,300 | 2,713.67 | 41,609.66 | 38,895.99 | 1,433% |
| 1,000 | 20,000 | 8,363.84 | 50,112.64 | 41,748.80 | 499% |
| 5,192 (end) | 103,830 | 54,346.99 | 54,346.99 | 0 | 0% |

- 50% of the total impact is reached on day 2,495 under FIFO and day 112 under LightSafe; 90% on day 4,511 and day 836.
- Both queues repair the same outages, so the final totals are equal by construction and the improvement tends to 0%. The difference is in how quickly impact accumulates.
- The very large percentages come from the extreme right skew of the score (a few outages hold most of the index) and from the queue maximising the same index it is measured on. They should not be read as a crime reduction.

## Limitations

- Impact is the Stage 12 index, not a crime count or probability. Stage 12's `tau_net` is a constant that is not statistically distinguishable from zero, so the ranking is essentially recent local crime × duration.
- Duration is observed after closure (retrospective); a live dispatcher would only know elapsed time.
- Static backlog with a constant K; no arrivals over time, travel, crew skills or repair-time differences.
- Circularity: LightSafe sorts by the metric used to score it, so it dominates FIFO by construction (a greedy order maximises every cumulative sum).
- 3,901 recent outages are not simulated.
- Provisional (M1, M3, M8 apply).

## Outputs and reproducibility

```
python src/models/prioritization_engine.py [--scored P] [--capacity K] [--out DIR]
python scripts/validation/stage13_validate.py
```

- `outputs/prioritized_queue.csv`: 207,660 rows (both methods): `method`, `queue_rank`, `repair_day`, `outage_id`, `created_date`, `priority_score`, `priority_tier`, `expected_impact`, `raw_priority`, `local_crime_rate`, `duration_factor`, `borough`. Floats are written to 10 significant digits (about 23 MB).
- `outputs/fifo_vs_lightsafe_comparison.csv`: 5,192 rows, one per simulated day: `repair_capacity_k`, `day`, `cumulative_repairs`, both cumulative impacts, `absolute_difference`, `improvement_pct`, `impact_unit`.
- Runs are deterministic: two runs give byte-identical CSVs (checked by the validator). The Stage 12 parquet, `causal_panel.parquet` and `displacement_estimates.csv` are only read and are hash-checked.
