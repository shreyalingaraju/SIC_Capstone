# Stages 12–13: operational dispatch (redesign) and score audit

Written for: reviewers of the redesigned Stages 12–13 and whoever builds Stage 14. It replaces the retired causal-score design in [stage12_priority_scoring.md](stage12_priority_scoring.md) and [stage13_prioritization_engine.md](stage13_prioritization_engine.md), which are kept for history only.

**Framing.** Descriptive simulation of FIFO dispatch of unresolved reported streetlight complaints, with an audit (and rejection) of candidate priority scores. The `framing` string stored in the Stage 12/13 outputs predates the audit decision; this sentence supersedes it. Nothing here estimates or claims crime reduction, crimes prevented or a causal repair benefit. The frozen Stage 11 result is null and is not used. "Open" means reported-open in 311, not physically dark.

**Status.** Uncommitted. Validation: 9 of 9 checks pass ([scripts/validation/stage12_13_validate.py](../scripts/validation/stage12_13_validate.py)).

## Decision

**No operational priority score is frozen. FIFO (earliest report first) is the frozen operational baseline.** This is score-audit option C.

Retention criterion: keep a score only if every component has a clear decision-time operational meaning, makes no causal crime-benefit claim, creates no unjustified fairness trade-off, and gives a policy that can be defended independently of the metric used to evaluate it.

| Component | Verdict | Reason |
|---|---|---|
| A: age, min(age / 30 d, 1), with a 30-day guard | Not needed | Ranking by A, with ties broken by arrival, gives exactly the FIFO schedule (Check I) |
| C: local night activity (250 m, [created − 395 d, created − 30 d), 2023 ECDF) | Removed | It encodes a normative preference for higher-activity locations that no demonstrated crime effect supports (Stage 11 is null). In the stable regime it moves waiting from Staten Island and Queens to Manhattan, lowers 7-day service from 100% to 95.3%, and raises the maximum wait from 6.4 to 17.0 days. No replacement location weight was added. |
| R: repeat complaints, current form [arrival − 30 d, t) | Removed | Every complaint is already its own job. 35% of R counts are later complaints that are themselves queued jobs, so a multi-reported site gets several boosts; same-site same-day dispatches rise from 1,783 to 2,085 site-days. |
| R: prior-only form [arrival − 30 d, arrival) | Removed | At decision time it cannot tell a duplicate of a pending complaint from a recurrence after a repair. That would need simulation (policy) state. Re-reporting is also suppressed while a complaint is open (Stage 11 blocker study). |

## Job definition (common evaluation population)

- **Jobs:** one per evaluation-period complaint (2024-01-01 to 2026-06-28) at a non-artifact site. `job_id = unique_key`, arrival = `created_date`, never merged.
- **Built before simulation:** the job set is written by Stage 12 (`data/processed/stage12/jobs.parquet`, 57,444 jobs).
- **Identical for every policy:** Check H is a hard failure on any difference.
- **Artifact sites:** the Stage 7 rule, computed from pre-2024 complaints only.

## Simulation assumptions (Stage 13)

None of these is inferred from 311 closure dates, which are never loaded.
- **Decisions:** daily at 08:00, using only jobs that arrived before that time.
- **Capacity:** K = 65 jobs per day, from ⌈2023 daily complaint arrivals 58.48 / 0.9⌉.
- **Service:** a selected job is resolved 1 day later and does not affect any other job.
- **Start and end:** the queue starts empty; after the last arrival, decisions continue until the queue drains.
- **Regimes:** overload starts at the beginning of the first calendar quarter whose mean daily arrivals exceed K (2025-07-01), computed from arrivals only.

**Policy invariance.** Every waiting job is always eligible, each decision serves min(K, queue) jobs, and resolution has no effect on other jobs. Under these conditions the backlog path, throughput and *mean* wait are identical for every ordering rule. A policy can only redistribute waiting. This follows from the design, not from the data; the stored backlog series of all five policies are bit-identical.

## Audit comparison (same jobs, K, service time and horizon; waits in days)

| Policy | Stable: median / p90 / max | Stable: served within 7 d | Overload: within 7 d / 30 d | Guard selections |
|---|---|---|---|---|
| **FIFO (frozen)** | 0.91 / 3.67 / 6.38 | 100% | 18.0% / 38.9% | n/a |
| Age only | identical to FIFO | 100% | 18.0% / 38.9% | 16,091 (all in overload) |
| (A + C) / 2 | 0.69 / 3.66 / 16.98 | 95.3% | 19.2% / 33.1% | 17,617 |
| (A + R_prior) / 2 | 0.85 / 3.76 / 7.55 | 99.8% | 19.1% / 35.5% | 17,004 |
| (A + C + R) / 3 | 0.68 / 3.38 / 20.30 | 95.3% | 20.5% / 31.6% | 18,020 |

- **Mean wait** is 1.46 days (stable regime) and 27.64 days (overload) for every policy.
- **Stable-regime mean wait by borough:**

| Borough | FIFO | (A + C + R) / 3 |
|---|---|---|
| Staten Island | 1.51 | 3.28 |
| Queens | 1.41 | 1.78 |
| Manhattan | 1.45 | 0.69 |

- **Overload regime:** arrivals are 74–79 per day in 2025 Q3–Q4 against K = 65. The guard decides every scored selection from 2026 Q1, and p90 and max waits are identical across policies. This is a capacity shortfall, and no ordering rule fixes it.

Full tables: `outputs/stage13_dispatch/dispatch_metrics.csv`, `dispatch_by_borough.csv` (regimes all / stable / overload) and `dispatch_summary.json`.

## Stage 14 handoff

- **Frozen inputs:**
  - The Stage 12 job set: `jobs.parquet` (job_id, arrival_s, site_id, borough; `activity_pct` is kept only for the audit).
  - The FIFO baseline and the simulation assumptions above.
- **No score is available as a benefit or objective coefficient.**
- **Forbidden:**
  - The focal `closed_date`, realised duration, status and resolution fields.
  - Future crime or arrivals.
  - Stage 7 pair artefacts.
  - `tau_net`, the old `priority_score`, `displacement_estimates.csv` and any Stage 11 coefficient.
  - The audited, rejected scores.
- **Stage 14:** the old `src/optimization/ilp_solver.py` (reads the retired `outages_scored.parquet`) is retired. Stage 14 is now the FIFO capacity / service-level analysis in [stage14_capacity_analysis.md](stage14_capacity_analysis.md).
