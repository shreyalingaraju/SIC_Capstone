# LightSafe

**A decision-support system for NYC streetlight repairs: does a dark street cause crime, and how much repair capacity does the city need?**

LightSafe links NYC 311 "Street Light Out" complaints to NYPD night-crime records, tests whether reported outages measurably change nearby crime, and uses that evidence to decide how repairs should be dispatched. It also analyses how waiting times respond to daily repair capacity, and adds a machine-learning "repair pressure" signal for each borough.

> **Headline result.** With these data, no reliable effect of reported streetlight outages on nearby night crime was detected, under two independent designs. LightSafe therefore does **not** claim that repairing a light reduces crime. The frozen final method is a first-in-first-out (FIFO) dispatch policy plus a capacity analysis.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Key Findings](#key-findings)
3. [Architecture](#architecture)
4. [Data](#data)
5. [Methodology](#methodology)
6. [Machine-Learning Component](#machine-learning-component)
7. [Dashboard](#dashboard)
8. [Repository Structure](#repository-structure)
9. [Getting Started](#getting-started)
10. [Reproducibility and Validation](#reproducibility-and-validation)
11. [Limitations](#limitations)
12. [Claims and Non-Claims](#claims-and-non-claims)
13. [Future Work](#future-work)
14. [Contributors](#contributors)
15. [License](#license)

---

## Project Overview

Cities have more reported broken streetlights than repair crews. If darkness raises crime, fixing the right lights first could prevent crime. Answering that requires three kinds of knowledge, which LightSafe deliberately keeps apart:

| Kind of knowledge | Question | Where it lives in LightSafe |
|---|---|---|
| Causal estimation | If a light were fixed, would nearby crime fall? | Matched difference-in-differences and event study; fixed-effects Poisson (both null) |
| Prediction | What will happen next? | XGBoost classifier for 311 closure speed |
| Decision optimisation | Given limited crews, what should be repaired? | FIFO queue simulation and capacity sweep |

Because the causal benefit could not be established, and because the 311 `closed_date` turned out not to be a reliable repair time, the project narrowed its final question to:

> *Given the observed chronological streetlight-report arrivals and a finite repair capacity, how does the dispatch system behave under FIFO, and which tested capacity, if any, meets the stated service-level targets?*

## Key Findings

| Area | Result |
|---|---|
| Matched DiD (20,051 treatment-control pairs) | Net post-repair effect **+0.0195** crimes per pair-window, 95% CI −0.023 to +0.062 (null) |
| Hexagon-week Poisson (PPML) | 0-100 m exposure coefficient **−0.00022**, p = 0.83 (null) |
| FIFO at 65 jobs/day | 100% served within 7 days before July 2025; 18% within 7 days afterwards (overload period) |
| Capacity needed (overload period) | 30-day target first met at **70 jobs/day**; 14-day target at **80 jobs/day**; 7-day target never met in the tested range |
| XGBoost repair-pressure model | Test ROC-AUC **0.744** (validation 0.804); PR-AUC 0.903 against a 0.797 no-skill level |

A "job" is one 311 complaint, not one repair crew.

Pooled results of the capacity sweep:

| Jobs/day | Mean wait (days) | Within 7 days | Within 30 days | Backlog at end of arrivals |
|---:|---:|---:|---:|---:|
| 55 | 49.4 | 25.1% | 52.4% | 8,021 |
| 60 | 22.7 | 43.0% | 68.2% | 4,706 |
| 65 | 13.5 | 62.4% | 72.0% | 2,940 |
| 70 | 7.1 | 64.7% | 100% | 1,233 |
| 75 | 2.8 | 85.5% | 100% | 81 |
| 80 | 1.6 | 93.7% | 100% | 0 |

## Architecture

The repository contains three analysis tracks and a dashboard. Only Track B is the frozen final method.

```
RAW DATA (NYC Open Data, Socrata API)
  311 "Street Light Out"            NYPD complaints (Historic + YTD)
          |                                   |
   S3 clean_streetlights              S5 clean_crime
   107,731 outages                    921,427 night crimes
          |                                   |
   +------+-----------------------------------+------------------+
   |                                                             |
   TRACK A  Exploratory causal (provisional)    TRACK B  Frozen final pipeline
   S6  Spatial linking                          S11 Exposure PPML (null, feeds nothing)
   S7  Match controls  (20,051 pairs)           S12 Job definition (57,444 jobs)
   S8-S10  DiD and event study                  S13 FIFO queue simulation
                                                S14 Capacity analysis (55-80 jobs/day)

   TRACK C  ML context
   311 data -> XGBoost -> borough repair pressure (High / Moderate / Low)
          |
   Dashboard: FastAPI backend (read-only) + React frontend
```

| Track | Purpose | Status |
|---|---|---|
| **A. Exploratory causal design** | Match each outage to a "not reported dark" control site 500-1,500 m away; run DiD and an event study | Provisional; not used downstream |
| **B. Frozen final pipeline** | Association model (null), FIFO queue simulation, capacity sweep | Final method (frozen 2026-10-06); makes no crime-reduction claim |
| **C. ML repair pressure** | Predict whether a complaint will still be open after 7 days; summarise by borough | Context only; feeds no ranking or score |

## Data

| Source | Dataset | Use |
|---|---|---|
| NYC Open Data, 311 Service Requests (`fhrw-4uyv`) | Complaints filtered to `descriptor = 'Street Light Out'`, 218,465 rows, 2020-01-01 to 2026-09-18 | Outages, queue jobs, ML features and labels |
| NYPD Complaint Data Historic (`qgea-i56i`) and Current Year-To-Date (`5uac-w243`) | Filtered server-side to dates on or after 2019-11-01 | Night-crime counts |

Key points about the data:

- A 311 row is a **resident report**, not a sensor reading. Unreported outages are invisible.
- `closed_date` is **not** a verified repair time. About 17.8% of closures since 2024 land between 222 and 240 hours, which points to an administrative deadline, and Pending rows carry placeholder dates.
- 16.6% of complaints have no coordinates.
- Crime data end on 2026-06-30, so all crime-linked stages stop there while 311 data run to 2026-09-18.
- Raw and processed data are git-ignored. Download them with the provided script.

**Cleaning funnel**

| Stage | Rule highlights | Result |
|---|---|---|
| Stage 3, streetlights | Valid created/closed dates and coordinates; closed not before created (removes 21,051 Pending placeholders); duration 0.5-8,760 hours; NYC bounding box | 107,731 outages |
| Stage 5, crime | De-duplicate on `cmplnt_num`; six offence groups selected by `ky_cd` (motor-vehicle larceny excluded); night hours 18:00-06:59; NYC bounding box | 921,427 night crimes |

All distance calculations use metre-based **EPSG:32118**.

## Methodology

**Spatial and temporal design**

| Concept | Definition |
|---|---|
| Direct zone | Within 100 m of the light |
| Ring zone | 100-250 m (displacement) |
| Control distance | 500-1,500 m from the treatment, same borough |
| Pre window | 14 days before the report |
| During window | Report time to closure |
| Post window | 14 days after closure |

**Track A: matched difference-in-differences.** Eligible, isolated, first-reported outages are matched to control sites with no reported darkness nearby and a similar baseline night-crime level (caliper 0.5 standard deviations). Estimators include OLS and two-way fixed-effects DiD, Poisson and negative binomial, and a paired-difference estimator, with standard errors clustered on 193 H3 resolution-7 cells. An event study provides a pre-trend check.

| Effect | During | Post |
|---|---:|---:|
| Direct (0-100 m) | −0.0008 | +0.0158 (p = 0.038, not robust) |
| Ring (100-250 m) | −0.0239 | +0.0037 |
| Net (0-250 m) | −0.0247 | +0.0195 (CI includes zero) |

**Track B, Stage 11: exposure model.** A fixed-effects Poisson (PPML) model on H3 resolution-10 cell-weeks (3.9 million cell-weeks, 349,692 crimes) with cell and borough-by-week fixed effects. All distance bands are null, and timing sensitivities flip the sign of the 0-100 m coefficient. It is treated as an association, not a causal estimate.

**Track B, Stages 12-14: dispatch.** 57,444 complaints (2024-01-01 to 2026-06-28) become queue jobs built from only five raw columns; `closed_date` and `status` are never loaded. Every day at 08:00 the oldest K jobs are dispatched and resolved one simulated day later. K = 65 comes from 2023 mean daily arrivals (58.48) at 90% target utilisation. Candidate crime-weighted scores were audited and rejected: age alone reproduces FIFO, and adding local crime activity shifts waiting from Staten Island and Queens to Manhattan without a supporting crime effect. When every job is eligible and service is fixed, ordering cannot change mean wait, only who waits.

## Machine-Learning Component

| Item | Detail |
|---|---|
| Task | Binary classification: will a 311 complaint still be open 168 hours (7 days) after filing? |
| Model | XGBoost, depth 3, 200 trees, learning rate 0.05 (chosen from 11 candidates by validation ROC-AUC) |
| Features | 24 features known at filing time; none come from crime data or the causal stages |
| Split | Chronological: train 2024-01 to 2025-06, validation 2025-H2, test 2026 |
| Validation / test ROC-AUC | 0.804 / 0.744 |
| Most important feature | Borough share of slow closures in the prior 14 days |
| Use | Mean score per borough over the last 28 days, ranked against 2024-2025 windows and shown as High / Moderate / Low |

The model behaves as a **regime tracker**: within a single borough its AUC ranges from 0.46 to 0.65, so it is used only at borough level. A leakage audit found no feature-level leakage. Feature importance reflects predictive reliance, not causes. Logistic regression performs about as well on the test set (ROC-AUC 0.746).

## Dashboard

The dashboard is a read-only FastAPI backend with a React (Vite, Tailwind) frontend.

| Page | Content |
|---|---|
| Overview | Counts, recommended repairs, and the Repair Pressure card |
| City Map | Outage points, sampled night crimes, 100 m / 250 m rings |
| Outages | Searchable outage list and details |
| Dispatch Plan | Retired score and integer-programming plan |
| Legacy DiD Summary | Direct, ring and net estimates with event-study chart |
| City Replay | Day-by-day replay of the frozen FIFO queue at a chosen K (50-90) |

> **Note.** Several views (Overview, City Map, Outages, Dispatch Plan) still display the retired priority-score and ILP design from before the final method was frozen. The backend and page text state that this design is superseded. City Replay shows the frozen FIFO simulation. Aligning the dashboard with the frozen pipeline is listed under [Future Work](#future-work).

## Repository Structure

| Path | Contents |
|---|---|
| `src/data/` | Download and cleaning (Stages 1-5) |
| `src/features/` | Spatial linking, control matching, causal panel, exposure panel, job set |
| `src/models/` | DiD, event study, exposure PPML, dispatch simulation, capacity analysis |
| `src/optimization/` | Retired ILP solver |
| `ml_model/` | XGBoost research code, frozen model, model card, evaluation |
| `backend/` | FastAPI read-only API; `services/ml/` loads a verified copy of the frozen model |
| `frontend/` | React / TypeScript dashboard |
| `scripts/validation/` | Per-stage validators with hard checks |
| `scripts/ml/` | ML snapshot builder |
| `outputs/` | Tracked results |
| `docs/` | Design records, decision log, acceptance reports, stage documents |
| `notebooks/` | Exploratory notebooks (05-08 stale; 09-12 retired) |
| `data/` | Git-ignored raw and processed data |

## Getting Started

### Prerequisites

- Python 3 with the project's dependencies installed (GeoPandas, H3, pandas, XGBoost, scikit-learn, FastAPI, among others)
- Node.js and npm for the frontend
- Network access to NYC Open Data (Socrata) for the raw downloads

> **Version pinning.** The backend's ML integrity check refuses to run with a different scikit-learn or XGBoost version than the frozen model was built with (scikit-learn 1.8.0 is pinned). If the versions do not match, the Repair Pressure card shows a fallback note and all other pages are unaffected. Install the pinned versions from the project's environment files.

### 1. Download and clean the data

Run the Stage 1-5 scripts in order:

1. `src/data/download_data.py` (paged download with a count check against the server)
2. `src/data/clean_streetlights.py`
3. `src/data/clean_crime.py`

Cleaned outputs are written to `data/processed/` (`clean_streetlights.parquet`, `clean_crime.parquet`).

### 2. Run the analysis stages

Run the stage modules for the track you need (for example `src/features/operational_priority.py`, `src/models/dispatch_simulation.py` and `src/models/capacity_analysis.py` for the frozen FIFO and capacity results). Outputs are written under `outputs/`.

### 3. Start the dashboard

```bash
# Backend (FastAPI, read-only)
uvicorn backend.main:app --port 8000

# Frontend (React + Vite), from the frontend/ directory
npm install
npm run dev        # served on port 5173
```

## Reproducibility and Validation

- Fixed random seeds (for example 42 for control matching and model training).
- Hashes of inputs and outputs, with determinism re-runs.
- Per-stage validators in `scripts/validation/` that print PASS/FAIL hard checks, including a check that Stages 12-14 never load `closed_date` or `status`.
- Backend tests confirm that every analytical response is identical with the ML component on, off or broken.
- All time stamps are naive New York local time; the repeated daylight-saving hour is ignored.

## Limitations

- 311 complaints measure **reporting**, not darkness or repair; reporting varies by neighbourhood.
- `closed_date` behaves partly like an administrative clock, which weakens every duration-based window.
- Parallel trends is assumed. Matched treatments were more outage-prone than controls on prior outage episodes (SMD 0.35), and the estimand covers only isolated, first-reported outages in quieter areas.
- The Stage 11 model is contemporaneous, so reverse causation is not excluded.
- Known defects in the exploratory design are documented as M1-M8, including an unnormalised during-window count.
- The ML model faces strong non-stationarity (slow-closure rate 0.46 to 0.80 across splits), uncalibrated probabilities across periods, and a reference period that is its own training period.
- The dispatch simulation assumes one-day service with unit work per job: no crews, travel or job types. Duplicate reports become separate jobs, so K is not a crew count.
- The raw crime file is not stored in the repository; re-run the download to reproduce Stage 5.

## Claims and Non-Claims

**Defensible claims**

- With these data, no effect of reported outages on nearby night crime could be detected under two designs.
- FIFO is frozen as the operational baseline, and waiting times respond strongly to daily capacity.
- A borough-level ML signal tracks how slowly 311 has recently been closing complaints.

**Do not claim**

- That LightSafe reduces crime or that repairing lights prevents a given number of crimes.
- That the AI predicts crime, or that XGBoost identifies causal drivers.
- That the ILP gives an optimal crew allocation, or that K = 65 crews are needed.
- That outage duration equals darkness duration, or that the causal model is validated.

A null result does not prove that streetlights do not matter. It excludes only large effects, and only for reported, isolated outages measured through 311 timestamps.

## Future Work

- Obtain real outage and repair work-order logs, or sensor data, to replace 311 timestamps.
- Add crew, travel and job-type data to model real dispatch.
- Extend crime coverage beyond June 2026 and use a sunset-based definition of night.
- Re-run the causal tests with normalised exposure windows and fixes for the known design issues.
- Align the dashboard's main views with the frozen FIFO and capacity pipeline.
- Re-benchmark the ML model on newer data with calibration and cross-validation.

## Contributors

Commit authors in the repository history: Shreya Lingaraju, Rachana N, CodeZilla740, Abhijna-13 and pnkashyap2006.

## License

*Add your license here.*
