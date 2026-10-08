# LightSafe API contract and UI integration

Written for: developers running or extending the LightSafe dashboard.

The dashboard (`frontend/`) talks only to the FastAPI backend (`backend/`). The backend reads the generated Stage 11-14 artifacts once at startup and serves them read-only. It never recomputes the analysis and never writes to `data/` or `outputs/`. Interactive API docs are at `http://127.0.0.1:8000/docs`.

> **Status of the design shown.** The README's "Final methodology (frozen 2026-10-06)" marks the Stage 12 priority score, Stage 13 FIFO-vs-LightSafe comparison and Stage 14 ILP as superseded by a FIFO baseline plus capacity analysis, because the Stage 11 net effect behind the score is not statistically distinguishable from zero. The dashboard presents the Stage 12-14 design as a decision-support ranking convention and says so on screen (`GET /api/summary` -> `notice`). It does not claim crimes prevented.

## Run locally

First-time setup (and after any change to `requirements.txt`), from the repository root, with Python 3.14:

```
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

This installs the pinned versions, including scikit-learn 1.8.0 and xgboost 3.4.1, which the repair-pressure model requires (`GET /api/ml/regime` returns `available: false` otherwise).

Two terminals, from the repository root.

```
# Terminal 1: backend (http://127.0.0.1:8000)
.venv\Scripts\python.exe -m uvicorn backend.main:app --port 8000

# Terminal 2: frontend (http://localhost:5173, proxies /api to :8000)
cd frontend
npm install        # first time only
npm run dev
```

Tests: `.venv\Scripts\python.exe -m unittest backend.tests.test_api -v` and `cd frontend && npx tsc --noEmit && npm run build`.

Environment variables: see `.env.example` (`LIGHTSAFE_ML_ENABLED`, `LIGHTSAFE_HOST`, `LIGHTSAFE_PORT`, `LIGHTSAFE_CORS_ORIGINS`, `VITE_API_BASE_URL`). No secrets are used.

## Data sources

| Artifact | Stage | Used by |
|---|---|---|
| `data/processed/outages_scored.parquet` | 12 | outages, priority, map, overview |
| `outputs/prioritized_queue.csv` | 13 | `/api/queue`, outage ranks |
| `outputs/fifo_vs_lightsafe_comparison.csv` | 13 | `/api/comparison`, overview |
| `outputs/optimal_dispatch_plan.csv` | 14 | dispatch status and rank on every outage |
| `outputs/optimal_dispatch_summary.json` | 14 | `/api/optimization` |
| `outputs/displacement_estimates.csv` | 11 | `/api/causal/overview` |
| `outputs/event_study_coefficients.csv`, `outputs/did_summary.json` | 9-10 | `/api/causal/*` |
| `data/processed/clean_crime.parquet` | 5 | `/api/map/crimes` (sample, context layer) |

If an artifact is missing the API still starts: `/api/health` reports `degraded`, list endpoints return empty pages, and summary endpoints return `{"available": false}`.

## Conventions

- JSON, UTF-8. Errors: `404 {"detail": "..."}` for unknown IDs, `422` (FastAPI validation) for bad parameters. Unknown artifacts never produce `500`.
- Pagination: `page` (>=1, default 1), `page_size` (1-500, default 50). Responses carry `total_count`, `page`, `page_size`, `total_pages`.
- `borough`: any of Manhattan, Brooklyn, Queens, Bronx, Staten Island (case-insensitive); `All` or omitted = no filter.
- `priority_tier`: `High`, `Medium`, `Low` or `All`. Tiers come straight from Stage 12 (High >= 80, Medium >= 40).
- `dispatch_status` values: `recommended` (selected by the Stage 14 plan), `deferred` (scored, not selected), `not_scored`.
- "Impact" and `impact_index` are Stage 12 priority-score index points, not crimes.

## Endpoints

| Method and path | Parameters | Response |
|---|---|---|
| `GET /api/health` | - | `status` (`healthy`/`degraded`), `artifacts_loaded`, `missing_artifacts[]` |
| `GET /api/summary` | - | pipeline status: `notice`, `impact_note`, `stages[]`, `artifacts[]` (file, available, modified_utc) |
| `GET /api/overview` | `borough`, `priority_tier` | `kpis`, `plan_vs_fifo` (nullable), `priority_mix`, `recommended_repairs[]` (top 10), `optimization` (nullable), `impact_note`, `data_status` |
| `GET /api/outages` | `borough`, `priority_tier`, `search` (ID or street, <=100 chars), `dispatch_status`, `min_score` (0-100), `scope` (`scored`/`excluded`/`all`), paging | page of **Outage item**, sorted by score desc then report date |
| `GET /api/outages/{id}` | - | **Outage detail**; 404 if unknown. Not-scored outages return 200 with `decomposition: null` |
| `POST /api/outages/{id}/{approve\|defer\|flag\|reset}` | - | `{outage_id, operator_note, success}`. Session-local note held in memory; does not alter the plan. 404 unknown ID, 422 unknown action |
| `GET /api/priority` | as `/api/outages` (scored only) | same page plus `summary` |
| `GET /api/priority/summary` | - | counts of total/scored/excluded, `exclusion_reasons`, `tier_counts`, `borough_counts`, `score_stats` |
| `GET /api/queue` | `method` (`FIFO`/`LightSafe`), `borough`, `priority_tier`, paging | page of **Queue item** from the Stage 13 simulation |
| `GET /api/comparison` | `points` (2-1000, default 150) | `repair_capacity_per_day`, `total_days`, `total_impact_index`, `day_one`, `milestones` (days to 50%/90% of total index points and to clear, per method), downsampled `curve[]` |
| `GET /api/optimization` | - | Stage 14 summary: `solver_status`, `n_selected`, `n_deferred`, `daily_budget`, `budget_used`, `budget_remaining`, `objective_value`, `unconstrained_objective`, `price_of_fairness`, `price_of_fairness_pct`, `all_quotas_satisfied`, `borough_table[]` |
| `GET /api/optimization/plan` | `decision` (`recommended`/`deferred`), `borough`, paging | page of **Outage item** with `impact_index`; recommended are ordered by `optimization_rank` |
| `GET /api/map/outages` | `borough`, `priority_tier`, `dispatch_status`, `limit` (1-5000, default 500) | GeoJSON FeatureCollection plus `total_matching`, `returned`. All recommended repairs are always included; the rest are highest score first. Outages without valid coordinates are skipped |
| `GET /api/map/crimes` | `night_only`, `limit` | GeoJSON sample of recent night crimes (context layer) |
| `GET /api/map/buffers/{id}` | - | GeoJSON polygons for the 100 m and 250 m analysis rings; 404 unknown ID |
| `GET /api/causal/overview` | - | Stage 11 estimates by period (`post`, `during`) with CI, p-value, `significant_at_5pct`, `ci_includes_zero`; event study; dataset summary |
| `GET /api/causal/event-study` | - | array of weekly coefficients |
| `GET /api/ml/regime` | `borough` (optional) | Borough repair-pressure context from the frozen model: `available`, `model_status` (`state` = `ready`/`disabled`/`unavailable`, `integrity_verified`), `window`, `reference`, `boroughs[]` (`category` High/Moderate/Low, `relative_score` = percentile of the borough's own 2024-2025 history, `above_reference_range`, `observation_count`), `explanation`, `limitations[]`, `role: "context_only"`. When ML is off or unverified: `available: false` plus a `note`; never a 500. Context only: no per-outage values, not used by any dispatch endpoint. See [ml_repair_pressure.md](ml_repair_pressure.md) |

### Outage item

`outage_id`, `borough`, `police_precinct`, `location_desc`, `latitude`, `longitude`, `created_date`, `closed_date`, `outage_duration_hours`, `duration_days`, `scored`, `exclusion_reason`, `local_crime_rate` (crimes/day within 250 m over the 14-day look-back), `priority_score` (0-100 index, null if not scored), `priority_tier`, `dispatch_status`, `optimization_rank` (recommended only), `operator_note`, `impact_index` (plan endpoints only). Nullable fields are explicitly `null`, never invented defaults.

### Outage detail (adds to the item)

`incident_address`, `cross_street_1/2`, `intersection_street_1/2`, `fifo_queue_rank`, `lightsafe_queue_rank`, `recent_crime_count_14d` (= local_crime_rate x 14), `impact_index`, `decomposition` (`tau_net`, `tau_net_source`, `local_crime_rate`, `lookback_days`, `duration_factor`, `outage_duration_hours`, `raw_priority`, `priority_score`, `priority_tier`).

### Queue item

`queue_rank`, `repair_day`, `outage_id`, `borough`, `location_desc`, `created_date`, `priority_score`, `priority_tier`, `impact_index`.

The TypeScript types for all of these are in `frontend/src/types/`.

## Compatibility mapping from the earlier teammate API

| Earlier UI/API | Now | Why |
|---|---|---|
| `expected_crimes_prevented`, "Expected Prevention", "Est. Crime Impact" | `impact_index`, "index points" | Stage 13/14 impact is the sum of priority scores, not crimes |
| `priority_tier` recomputed in the service | Stage 12 `priority_tier` as stored | one source of truth |
| `rank`, `expected_impact` on outages | `optimization_rank` (plan rank), `impact_index` | the old fields mixed list position with plan rank |
| `assigned_crew`, `stop_number`, `/api/repair-plan` crews | removed; `dispatch_status` + `/api/optimization*` | the pipeline has no crew or route model; crews were invented round-robin |
| `POST /api/repair-plan/generate` | removed | it ignored its parameters; the plan is a fixed artifact |
| `travel_penalty_mins`, `severity_multiplier` | removed | not part of the Stage 12 formula |
| hardcoded `pre_trend_p_value`, `displacement_share`, row counts, "last pipeline run" | read from the estimate files / file timestamps | removed fabricated fallbacks |
| `closed_date` forced to null in lists | real value | |
| approve / defer / flag | kept as session-local `operator_note` | UI convenience; does not change the plan |
| default coordinates when missing | `null`; map skips the record | no fabricated locations |
| CORS `*` with credentials | allow-list from `LIGHTSAFE_CORS_ORIGINS`, GET/POST only | |

## Known limitations

- Operator notes are lost on backend restart.
- `/api/map/crimes` is a sample of recent night crimes (default 600), a context layer rather than full crime data.
- The queue and plan are fixed artifacts (K = 20 repairs/day, quota fraction 0.5). Changing them means re-running the pipeline.
- The Stage 12 score, FIFO-vs-LightSafe gain and ILP objective are index quantities. The score is dominated by recent local crime and outage duration because `tau_net` is a single constant with a confidence interval that includes zero.
- The final-methodology Stages 11-14 (exposure model, FIFO simulation, capacity analysis) write to `outputs/stage11_exposure`, `stage13_dispatch`, `stage14_capacity`; the dashboard does not read them.
