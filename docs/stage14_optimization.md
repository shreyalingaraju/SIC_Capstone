# Stage 14: ILP optimization layer

> **Retired (2026-10-06).** This design reads the retired `outages_scored.parquet` and maximises a `priority_score` that no longer exists (Stage 11 is null, and the Stage 12–13 audit froze no score). It is replaced by the capacity / service-level analysis in [stage14_capacity_analysis.md](stage14_capacity_analysis.md). The code and outputs below are kept unchanged for audit history only.

Written for: reviewers and the Stage 15 implementer. It states the optimization model, the parameter conventions the Guide left open, and what the result does and does not mean.

**Status.** Implemented on `issue4-step1` after Stage 13. Provisional while M8 is open. The plan maximizes the Stage 12 priority index under stated constraints. **It is not a guaranteed or estimated number of crimes prevented.**

## Objective

Choose which outages to repair in one day under a crew budget and borough minimum quotas, using integer linear programming (PuLP with the standard CBC solver). Code: [src/optimization/ilp_solver.py](../src/optimization/ilp_solver.py). Notebook: [notebooks/12_optimization_solver.ipynb](../notebooks/12_optimization_solver.ipynb). Validator: [scripts/validation/stage14_validate.py](../scripts/validation/stage14_validate.py).

## Model

```
prob = pulp.LpProblem("LightSafe_Dispatch", pulp.LpMaximize)
x_i in {0,1}                                  1 = repair, 0 = defer
maximize    sum_i benefit_i * x_i
subject to  sum_i repair_cost_i * x_i <= daily_budget
            sum_{i in borough b} x_i  >= minimum_quota_b     for each borough b
```

- **Candidates:** the 103,830 outages with `scored == True` in `data/processed/outages_scored.parquet` (the Stage 13 backlog). The 3,901 unscored outages are not candidates. The 17 outages with borough "Unspecified" form their own group.
- **Benefit.** The Guide's `sum(x_i * tau_net_i)` cannot be used literally: Stage 11's `tau_net` is one global estimate, so it would only count repairs. The outage-specific quantity the project derived from it is the Stage 12 `priority_score` (`tau_net × local_crime_rate × duration_factor`, clipped at 0, min-max scaled to 0–100), which Stage 13 also credits per repair. So `benefit_i = priority_score_i`, in index points. The optimizer does not estimate outage-level causal effects.

## Parameters (not set by the Guide)

The Guide names `Daily_Budget`, `repair_cost[i]` and `Minimum_Quota_b` without values. A repository search found none, except the Stage 13 capacity K = 20 repairs per day. Stage 14-only conventions, all configurable (function arguments and CLI), none a real-world claim:

| Parameter | Value used | Basis |
|---|---|---|
| `repair_cost_i` | 1.0 for every outage | No cost field exists. Outage duration is not repair effort. |
| `daily_budget` | 20.0 cost units | Equals the Stage 13 K = 20 repairs/day at cost 1. |
| `minimum_quota_b` | `floor(0.5 × 20 × share_b)`, capped at the borough's outage count | `share_b` = the borough's share of the candidates. Half of the day's capacity is guaranteed in proportion to backlog. `--quota BOROUGH=N` overrides a borough; `--quota-fraction` changes the 0.5. |

Resulting quotas: Bronx 1, Brooklyn 3, Manhattan 1, Queens 2, Staten Island 1, Unspecified 0 (total 8 of 20).

Feasibility is checked before solving: a quota above a borough's candidate count, or quotas whose cheapest satisfaction exceeds the budget, raise `InfeasibleQuotaError` with a diagnostic. Zero budget and budgets below one repair are valid and select nothing.

## Solver and determinism

CBC (`PULP_CBC_CMD`, single thread) via `pulp==3.3.2`. Version 4.0.0 of PuLP no longer bundles CBC, so `requirements.txt` pins 3.3.2. If CBC is unavailable the run fails with `SolverUnavailableError`; no other solver is substituted. Candidates are put in a canonical order (benefit descending, `created_date`, `unique_key`), and ties are broken toward that order by a perturbation of at most 1e-6 index points per outage on the solver coefficients. The reported objective uses the unperturbed benefits. Re-runs are byte-identical.

## Input and outputs

- Input: `data/processed/outages_scored.parquet` (read only).
- `outputs/optimal_dispatch_plan.csv`: one row per candidate (`outage_id`, `borough`, `created_date`, `priority_score`, `benefit`, `repair_cost`, `selected_for_repair`, `decision` = repair/defer, `optimization_rank` = 1..n for selected rows and 0 for deferred). About 7.6 MB.
- `outputs/optimal_dispatch_summary.json`: status, parameters, quotas, objective, budget used and remaining, borough allocation, quota compliance, and the unconstrained optimum.

## Results

| | |
|---|---|
| Solver status | Optimal |
| Candidates / selected / deferred | 103,830 / 20 / 103,810 |
| Objective (total index of repaired outages) | 1,152.83 |
| Budget used / remaining | 20 / 0 |
| Borough allocation | Manhattan 13, Brooklyn 3, Queens 2, Bronx 1, Staten Island 1 |
| Quotas met | All (Brooklyn 3 ≥ 3, Queens 2 ≥ 2, Bronx 1 ≥ 1, Manhattan 13 ≥ 1, Staten Island 1 ≥ 1) |
| Unconstrained optimum | 1,208.63 (Manhattan 15, Queens 4, Brooklyn 1) |
| Price of fairness | 55.80 index points (4.6%) |

The unconstrained optimum equals the Stage 13 LightSafe day-1 cumulative impact (1,208.63), which cross-checks the two stages. The constrained plan differs from the Stage 13 queue: to meet the quotas it swaps in outages from Brooklyn, Bronx and Staten Island.

## Limitations

- The objective is the Stage 12 index, not a probability or a crime count. `tau_net` is a constant that is not statistically distinguishable from zero, so the optimizer in effect picks high recent-crime, long-duration outages, subject to the quotas.
- Costs, budget and quotas are conventions, not agency values. With uniform costs the problem is a cardinality-constrained selection; a richer cost model would change the plan.
- One day's dispatch from the whole backlog. No arrivals, travel, crew skills or multi-day scheduling. Durations are retrospective (Stage 12).
- Borough quotas are a fairness proxy at borough scale, not a measure of spatial equity within boroughs.
- Provisional (M1, M3, M8 apply).

## Reproducibility

```
python src/optimization/ilp_solver.py [--scored P] [--out DIR] [--daily-budget B] [--repair-cost C] [--quota-fraction F] [--quota BOROUGH=N ...] [--time-limit S]
python scripts/validation/stage14_validate.py
```

A full run takes about 75 seconds. The validator checks synthetic cases (empty input, one outage, zero budget, ample budget, multiple boroughs, exact quotas, infeasible quotas, ties and input-order independence, a brute-force optimum), the committed outputs, a byte-identical re-run and the integrity of the Stage 8–13 artifacts.
