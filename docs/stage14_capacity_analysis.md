# Stage 14: capacity and service-level analysis (frozen FIFO)

Written for: reviewers of the redesigned pipeline. It replaces the ILP design in [stage14_optimization.md](stage14_optimization.md), which is kept for history only.

**Framing.** This is a descriptive capacity analysis of the frozen Stage 13 FIFO dispatch simulation. It does not optimise anything. It does not estimate or claim crime reduction, crimes prevented, a causal repair benefit, physical repair times or an optimal crew allocation.

**Status.** Uncommitted. Validation: 10 of 10 checks pass ([scripts/validation/stage14_capacity_validate.py](../scripts/validation/stage14_capacity_validate.py)). The Stage 12–13 suite still passes 9 of 9.

## 1. Why the old formulation was rejected

The old Stage 14 ([src/optimization/ilp_solver.py](../src/optimization/ilp_solver.py), untouched) picked one day's repairs from a backlog as an ILP. It maximised the sum of `priority_score` over the selected outages, subject to a budget of 20 unit-cost repairs and borough minimum quotas. Several of its assumptions are no longer valid:

- **Input file.** It reads `data/processed/outages_scored.parquet`. That is the retired Stage 12 output.
- **Benefit coefficient.** It uses `priority_score` (`tau_net` × local crime rate × duration factor) as the benefit. `tau_net` is retired: Stage 11 is a frozen null result. The Stage 12–13 score audit then froze no score at all. So no benefit coefficient exists, and the objective is undefined.
- **Duration factor.** It used retrospective outage duration, which comes from 311 closure dates. Closure dates are not repair time and are not available at decision time.
- **Capacity.** It assumed K = 20 repairs/day. The frozen capacity rule gives K = 65.
- **Borough quotas.** These were an arbitrary 50%-of-capacity convention with no agency basis.
- **Static backlog.** It made one decision on the whole backlog, with no arrivals. That is inconsistent with the frozen chronological simulation.

With uniform costs and no valid benefit, the ILP reduces to "pick any K jobs". An ILP would add only the appearance of optimisation.

**What is retained conceptually:**
- one job uses one unit of daily capacity;
- the daily capacity is the input of interest (varied, not optimised);
- deterministic, reproducible runs.

## 2. Question

> Given the observed chronological streetlight-report arrivals and a finite repair capacity, how does the dispatch system behave under the frozen FIFO policy, and which tested capacity, if any, meets the stated service-level targets?

## 3. Assumptions (all inherited unchanged from frozen Stage 13)

- **Jobs:** the immutable Stage 12 set of 57,444 jobs, one per evaluation-period complaint (2024-01-01 to 2026-06-28) at a non-artifact site. The arrival is the complaint's `created_date`.
- **Decisions:** daily at 08:00, using only jobs that arrived before that time. FIFO order is earliest arrival first, ties broken by job id.
- **Service:** a selected job resolves exactly 1 day later. This is a simulation assumption, not inferred from 311 closure dates, which are never read.
- **Start and end:** the queue starts empty. After the last arrival, decisions continue until the queue drains.
- **Regimes:**
  - The split date comes from the frozen Stage 13 rule (first calendar quarter whose mean daily arrivals exceed K = 65), applied to arrivals only.
  - Stable means arrivals before 2025-07-01; overload means arrivals from 2025-07-01.
  - The same split is used for every tested K.

Only K varies. Code: [src/models/capacity_analysis.py](../src/models/capacity_analysis.py). It calls `dispatch_simulation.simulate(..., "fifo", K)` unchanged. It reads only `jobs.parquet` (job_id, arrival_s, site_id, borough) and `arrivals.parquet` (created_date, period, is_artifact_pre). It does not read the rejected `activity_pct`, and it does not read or import any Stage 11 output, `tau_net`, `priority_score` or the ILP.

## 4. Tested capacities

K ∈ {55, 60, 65, 70, 75, 80} jobs/day. The grid was pre-specified and not tuned. K = 65 is the frozen baseline: ⌈2023 arrivals 58.48/day ÷ 0.9⌉.

**What K means.** K is the number of modelled complaint-jobs the simulation can dispatch per day, each taking one simulated day. It is not a number of crews, staff or physical repairs:
- one physical outage can generate several complaint-jobs (22.8% of jobs have a same-site job less than 30 days earlier);
- real repair effort, travel and crew size are not modelled;
- the job set excludes 2,618 evaluation-period complaints (4.3%) with no usable coordinates and 1,444 at artifact sites. The 2023 capacity rule uses the same population definition.

## 5. Service-level targets (descriptive, not an objective)

| Target | Requirement |
|---|---|
| T7_95 | ≥ 95% of jobs served (selected) within 7 days of arrival |
| T14_99 | ≥ 99% within 14 days |
| T30_99 | ≥ 99% within 30 days |

The targets are evaluated after all simulations have run. There is no combined or weighted score.

## 6. Results

Waits are in days. Wait = selection time − arrival.

**Pooled (all 57,444 jobs):**

| K | Mean | Median | p90 | Max | ≤7 d | ≤14 d | ≤30 d | Backlog mean / max | Backlog at end of arrivals | Throughput/day | Utilisation | Last dispatch |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 55 | 49.42 | 29.55 | 122.48 | 147.40 | 25.1% | 28.1% | 52.4% | 2,439 / 8,041 | 8,021 | 54.28 | 0.99 | 2026-11-22 |
| 60 | 22.72 | 8.91 | 63.71 | 79.82 | 43.0% | 62.8% | 68.2% | 1,194 / 4,731 | 4,706 | 57.92 | 0.97 | 2026-09-15 |
| **65** | 13.47 | 2.64 | 38.70 | 46.82 | 62.4% | 65.4% | 72.0% | 739 / 2,970 | 2,940 | 59.86 | 0.92 | 2026-08-13 |
| 70 | 7.06 | 1.42 | 20.50 | 24.81 | 64.7% | 69.1% | 100% | 396 / 1,661 | 1,233 | 61.74 | 0.88 | 2026-07-17 |
| 75 | 2.76 | 0.90 | 10.58 | 15.95 | 85.5% | 96.7% | 100% | 136 / 1,119 | 81 | 63.00 | 0.84 | 2026-06-30 |
| 80 | 1.60 | 0.79 | 4.75 | 10.52 | 93.7% | 100% | 100% | 63 / 749 | 0 | 63.09 | 0.79 | 2026-06-29 |

All 57,444 jobs are served at every K. Backlog, throughput and utilisation are measured over the 910 decision days up to the end of arrivals (2026-06-29). The drain period is excluded, as in Stage 13.

**By regime:**

| K | Stable: mean / p90 / max | Stable: ≤7 / ≤14 / ≤30 d | Overload: mean / p90 / max | Overload: ≤7 / ≤14 / ≤30 d | Overload: backlog max |
|---|---|---|---|---|---|
| 55 | 13.77 / 29.39 / 31.71 | 46.3 / 52.0 / 95.0% | 91.52 / 134.44 / 147.40 | 0.0 / 0.0 / 2.2% | 8,041 |
| 60 | 4.68 / 10.81 / 13.59 | 70.6 / 100 / 100% | 44.02 / 70.87 / 79.82 | 10.5 / 18.9 / 30.7% | 4,731 |
| **65** | 1.46 / 3.67 / 6.38 | 100 / 100 / 100% | 27.64 / 40.93 / 46.82 | 18.0 / 24.6 / 38.9% | 2,970 |
| 70 | 0.82 / 1.48 / 2.86 | 100 / 100 / 100% | 14.42 / 22.98 / 24.81 | 23.1 / 32.5 / 100% | 1,661 |
| 75 | 0.71 / 1.07 / 2.29 | 100 / 100 / 100% | 5.17 / 13.58 / 15.95 | 68.3 / 92.9 / 100% | 1,119 |
| 80 | 0.66 / 0.95 / 1.82 | 100 / 100 / 100% | 2.71 / 7.76 / 10.52 | 86.2 / 100 / 100% | 749 |

**Arrival rate (calendar days, job set):**

| Scope | Days | Mean | Median | p90 | Max | % days > 55 / 60 / 65 / 70 / 75 / 80 |
|---|---|---|---|---|---|---|
| Stable | 547 | 56.86 | 58 | 86 | 118 | 53.0 / 45.3 / 37.7 / 28.7 / 19.9 / 15.2 |
| Overload | 363 | 72.57 | 73 | 109 | 185 | 69.7 / 64.2 / 58.4 / 55.1 / 46.3 / 39.9 |
| All | 910 | 63.13 | 62 | 96 | 185 | 59.7 / 52.9 / 45.9 / 39.2 / 30.4 / 25.1 |

Quarterly mean arrivals per day:

| Quarter | 2024 Q1 | 2024 Q2 | 2024 Q3 | 2024 Q4 | 2025 Q1 | 2025 Q2 | 2025 Q3 | 2025 Q4 | 2026 Q1 | 2026 Q2 |
|---|---|---|---|---|---|---|---|---|---|---|
| Mean arrivals/day | 56.8 | 49.7 | 56.9 | 62.0 | 58.6 | 57.0 | 74.2 | 78.8 | 66.4 | 70.7 |

Daily arrivals exceed K on many days even in the stable regime. What matters is the mean over weeks, because the queue absorbs day-to-day variation.

**Minimum tested simulated capacity meeting each target**, under the stated assumptions. This is descriptive: "none" means no tested K, and it is not a required capacity or crew count.

| Regime | T7_95 | T14_99 | T30_99 |
|---|---|---|---|
| Stable | 65 | 60 | 60 |
| Overload | none (K = 80: 86.2%) | 80 | 70 |
| Pooled | none (K = 80: 93.7%) | 80 | 70 |

## 7. Limitations

- **Simulation assumptions.** Everything is conditional on the 1-day service time, daily 08:00 batch decisions, unit work per job, no travel and no heterogeneity in repair resources. A longer or variable service time would raise every wait.
- **Finite horizon.** The overload regime runs for 363 days, and arrivals then stop and the queue drains.
  - For K ≤ 65 the backlog is still near its maximum when arrivals end (2,940 at K = 65), so the overload waits are not steady-state and would keep rising with a longer overload period.
  - K = 70 and K = 75 are below the mean arrival rate in 2025 Q4 (78.8/day). Their 30-day compliance holds over this horizon only.
  - K = 80 is the only tested capacity whose quarterly mean load ratio stays ≤ 1 (2025 Q4: 0.99), and only just. A mean arrival rate below K is necessary for the simulated queue to stay bounded. It is not sufficient for real-world capacity planning, which would need repair-effort, travel, crew and duplicate-report data that this project does not have.
- **Arrival data.** Arrivals are 311 complaints at non-artifact sites. They are reports, not outages. Reporting behaviour, duplicates of one outage and the 2025 Q3 increase are taken as given; their cause is not analysed.
- **Unit of capacity.** K counts simulated complaint-jobs per day (see §4). It is not a crew count. There is no agency data on crews or repair effort.
- **Service levels are about queue waiting only.** They say nothing about crime, safety or physical darkness.

## 8. Interpretation

Under the stated simulation assumptions:

- **Stable regime (before 2025-07-01).** K = 65 meets all three service-level targets: 100% of jobs are served within 7 days, and the maximum wait is 6.4 days.
- **Overload regime (from 2025-07-01).** K = 65 meets none of them. Mean arrivals rise to 72.6/day, above 65 on 58% of days, and the backlog is still growing (2,940) when arrivals end.
- **Higher capacities** improve waiting-time service levels monotonically (validated):
  - among the tested capacities, the 30-day target is met from K = 70 and the 14-day target only at K = 80;
  - no tested capacity reaches 95% within 7 days in the overload regime or pooled.

The pooled 13.47-day mean wait at K = 65 mixes a stable system (1.46 days) with a structurally overloaded one (27.64 days).

This is a simulated capacity / service-level relationship. It is not an optimal or required crew allocation, and it does not establish any crime reduction.

## 9. Validation (10/10 pass)

| Check | What it verifies |
|---|---|
| V1 | Every K receives exactly the 57,444 Stage 12 jobs (ids, arrivals, sites, boroughs); the job file hash equals the one Stage 13 used |
| V2 | In (arrival, job id) order selection times never decrease; each decision serves min(K, queue) |
| V3 | No decision serves more than K jobs; every job is served once |
| V4 | Selection always happens at an 08:00 decision after arrival; decisions up to two cutoffs are identical with all later arrivals removed, for every K |
| V5 | Resolution = selection + exactly 1 day for every job |
| V6 | A traced run reads only the two declared files and columns; no retired or forbidden names in the module code (the scanner flags the old ILP as a negative control); importing the module loads no retired module |
| V7 | For K < K′, the queue after every decision and every job's selection time are ≤ under K′ |
| V8 | The simulation takes only (jobs, capacity); each K is identical whether run alone or in a reversed sweep; the grid is the pre-specified one; stored metrics recompute from the stored logs |
| V9 | Two fresh runs produce byte-identical tables and equal log hashes; the summary differs only in its timestamp |
| V10 | K = 65 reproduces the frozen Stage 13 FIFO schedule, backlog series and job-log hash exactly; 30 shared metrics differ by 0; same regime split |

## Outputs and reproducibility

`outputs/stage14_capacity/`:
- `capacity_metrics.csv` (pooled, per K)
- `capacity_regime_metrics.csv` (per K × all/stable/overload)
- `arrival_rate_diagnostics.csv` (all/stable/overload/quarterly)
- `service_level_targets.csv`
- `stage14_summary.json`
- `validation_results.json`

Per-K schedules and backlog series are in `data/processed/stage14/` (gitignored).

```
python src/models/capacity_analysis.py
python scripts/validation/stage14_capacity_validate.py
```
