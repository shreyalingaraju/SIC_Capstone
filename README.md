# LightSafe
 
**Spatial Displacement Adjusted Causal Optimization for Dynamic Municipal Streetlight Dispatch**
 
LightSafe is a decision-support system that helps municipalities understand streetlight outages, examine their relationship with nighttime crime, and plan repair dispatch under operational constraints. It combines data analysis, causal inference, machine learning and repair-capacity simulation, and presents the results in an interactive dashboard.
 
> LightSafe supports informed repair decisions. It is **not** designed to replace human dispatch judgment, and its recommendations are not automatic instructions to municipal crews.
 
---
 
## Table of Contents
 
1. [Project Status](#project-status)
2. [Real Analysis vs Synthetic Demonstration](#real-analysis-vs-synthetic-demonstration)
3. [Key Findings from the Real-Data Analysis](#key-findings-from-the-real-data-analysis)
4. [System Overview](#system-overview)
5. [Dashboard](#dashboard)
6. [Repository Structure](#repository-structure)
7. [Getting Started](#getting-started)
8. [Responsible Use and Cautions](#responsible-use-and-cautions)
9. [Limitations](#limitations)
10. [Contributing and Team Workflow](#contributing-and-team-workflow)
11. [Future Work](#future-work)
12. [Acknowledgements](#acknowledgements)
13. [License](#license)
---
 
## Project Status
 
The project has evolved from its original goal of ranking streetlight repairs by expected crime reduction. The current implementation includes:
 
- Analysis of streetlight outage and crime data.
- Causal analysis of whether outages are associated with changes in nearby nighttime crime.
- Machine-learning predictions of risk signals.
- Operational dispatch and repair-capacity simulation, including first-in-first-out (FIFO) dispatch and capacity analysis.
- A dashboard for viewing results, ward-level risk, prioritization, scenarios and methodology.
- A synthetic Karnataka-based demonstration of the dashboard pipeline.
**Important.** The causal analyses have **not** established a reliable crime-reduction effect that justifies treating causal estimates as proven repair-prioritization weights. LightSafe deliberately distinguishes predictive signals from causal evidence. Older crime-impact-based ranking and priority-scoring designs are not assumed to remain part of the active implementation, so always check the current source code before relying on them.
 
## Real Analysis vs Synthetic Demonstration
 
| | Real-world analytical data | Synthetic Karnataka demonstration |
|---|---|---|
| Purpose | Investigate the relationship between streetlight outages and crime | Demonstrate the dashboard and its pipeline |
| Nature of figures | Derived from public records | **Generated or simulated** |
| What it can show | Empirical (if limited) findings | How the system operates |
| What it cannot show | Causal proof of crime reduction | Actual measurements of crime or streetlight conditions in Karnataka |
 
Synthetic results must never be described as measured public-safety outcomes, and simulated dispatch performance is not proof of actual municipal performance. The dashboard marks synthetic content with badges and explanatory text.
 
## Key Findings from the Real-Data Analysis
 
The earlier real-data analysis used NYC 311 "Street Light Out" complaints (218,465 rows, 2020 to September 2026) and NYPD complaint data (921,427 night crimes after cleaning). Cleaning kept 107,731 valid outages.
 
| Area | Result |
|---|---|
| Matched difference-in-differences (20,051 treatment-control pairs) | Net post-repair effect **+0.0195** crimes per pair-window, 95% CI −0.023 to +0.062 (null) |
| Hexagon-week Poisson (PPML) exposure model | 0-100 m coefficient **−0.00022**, p = 0.83 (null) |
| FIFO dispatch at 65 jobs/day | 100% served within 7 days before July 2025; 18% afterwards (overload period) |
| Capacity needed in the overload period | 30-day target first met at **70 jobs/day**; 14-day target at **80 jobs/day**; 7-day target not met in the tested range |
| XGBoost repair-pressure model | Test ROC-AUC **0.744** (validation 0.804); PR-AUC 0.903 against a 0.797 no-skill level |
 
A "job" is one 311 complaint, not one repair crew. A null result does not prove that streetlights have no effect: it excludes only large effects, and only for reported, isolated outages measured through 311 timestamps.
 
## System Overview
 
```
Data sources            Clean and prepare        Analysis tracks                      Dashboard
-------------           -----------------        ------------------                   ---------
311 complaints   --->   Cleaning            ---> A. Exploratory causal               FastAPI backend
NYPD crime       --->   (outages, night          Control match -> DiD + event study   (read-only)
                         crimes, metre-based
                         coordinates)       ---> B. ML context (feeds no ranking)     React frontend
                                                 Features -> XGBoost -> borough
                                                 repair pressure
```
 
- **Track A, exploratory causal design.** Each eligible outage is matched to a control site 500-1,500 m away with no reported darkness nearby and a similar baseline crime level. Difference-in-differences and an event study compare crime before, during and after each outage. Results are provisional.
- **Track B, ML context.** An XGBoost classifier predicts whether a 311 complaint will still be open 7 days after filing, using 24 features known at filing time. Scores are summarised per borough as a "repair pressure" level. This signal feeds no ranking.
- **Operational analysis.** A FIFO dispatch simulation and a capacity sweep (55 to 80 jobs/day) show how waiting times respond to repair capacity.
## Dashboard
 
The dashboard is a FastAPI backend with a React (Vite, Tailwind) frontend. It includes views such as:
 
- Synthetic Analysis overview
- Dispatch Overview
- City Map
- Outages
- Dispatch Plan
- Legacy DiD Summary
- Geographic View (Highest Risk Wards table and a Ward profile panel, with a City filter)
- Risk & Impact
- Causal vs Prediction
- Prioritization
- Scenarios
- Methodology
> The exact availability and behaviour of individual views should be verified against the current code. Some views display earlier designs, which are labelled as superseded in the application.
 
## Repository Structure
 
| Path | Contents |
|---|---|
| `src/data/` | Download and cleaning scripts |
| `src/features/` | Spatial linking, control matching, causal panel, exposure panel, job set |
| `src/models/` | Difference-in-differences, event study, exposure model, dispatch simulation, capacity analysis |
| `ml_model/` | XGBoost research code, frozen model, model card, evaluation |
| `backend/` | FastAPI API that serves results to the dashboard |
| `frontend/` | React / TypeScript dashboard (`src/components/layout/AppShell.tsx`, `src/pages/SyntheticDashboard.tsx`) |
| `scripts/` | Validation and snapshot scripts |
| `outputs/` | Tracked results |
| `docs/` | Design records, decision log and methodology documents |
| `notebooks/` | Exploratory notebooks (some are stale or retired) |
| `data/` | Git-ignored raw and processed data |
 
> Folder contents may have changed since this table was written. Treat the current checkout as the authority.
 
## Getting Started
 
### 1. Clone the repository
 
```bash
git clone https://github.com/shreyalingaraju/SIC_Capstone.git
cd SIC_Capstone
```
 
### 2. Read before you run
 
Review this README, the methodology documentation, and the backend and data-processing code to understand how the dashboard obtains its data.
 
### 3. Set up the backend
 
Install the Python dependencies for the project (GeoPandas, H3, pandas, scikit-learn, XGBoost and FastAPI, among others). The ML component checks library versions against the frozen model, and falls back gracefully if they do not match, so install the pinned versions from the project's environment files.
 
```bash
.\.venv\Scripts\Activate.ps1
$env:LIGHTSAFE_PROFILE = "karnataka_synthetic"
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```
 
### 4. Set up the frontend
 
```bash
cd frontend
npm install
npm run dev        # development server (check the Vite config for the exact port)
npm run build      # production build check
```
 
Do not assume the API or development server is available on another teammate's machine, because local services and environment configuration may differ.
 
### Data
 
Raw data are git-ignored. The NYC 311 (`fhrw-4uyv`) and NYPD complaint (`qgea-i56i`, `5uac-w243`) datasets are downloaded from NYC Open Data with the scripts in `src/data/`.
 
## Responsible Use and Cautions
 
- A predictive model is not automatically a causal model.
- A statistically non-significant result does not prove that an effect is absent.
- Do not present synthetic Karnataka results as real-world findings.
- Do not revive retired metrics or older priority scores without checking their provenance and current implementation.
- Treat repair recommendations as decision support, not as instructions to municipal crews.
- 311 complaints record resident **reports**, not measured darkness or verified repairs, and `closed_date` is not a reliable repair time.
## Limitations
 
- Reporting bias: unreported outages are invisible, and reporting varies by neighbourhood.
- The causal designs assume parallel trends and cover only isolated, first-reported outages in quieter areas. Known design issues are documented in the project's methodology notes.
- The ML model tracks recent borough-level slowness rather than ranking individual complaints (within-borough AUC is only 0.46 to 0.65), and its probabilities are not calibrated across periods.
- The dispatch simulation assumes unit work per job with one-day service, and ignores crews, travel and job types.
- Crime data used in the real analysis end in June 2026.
## Contributing and Team Workflow
 
Before making changes:
 
1. Pull or inspect the latest repository state.
2. Run `git status` so you do not overwrite a teammate's work.
3. Read the README and the relevant code before assuming how a feature works.
4. Run the application and explore its views.
5. Verify analytical claims against their source code, data and documented outputs.
6. Discuss changes with the team before altering shared analytical logic or methodology.
Use the current source code as the authority for what is implemented. Older reports, screenshots and diagrams may describe earlier versions.
 
## Future Work
 
- Real outage and repair work-order logs, or sensor data, to replace 311 timestamps.
- Crew, travel and job-type data for realistic dispatch modelling.
- Crime data beyond June 2026 and a sunset-based definition of night.
- Normalised exposure windows and fixes for known causal-design issues.
- Aligning every dashboard view with the current methodology.
- Re-benchmarking the ML model on newer data with calibration and cross-validation.
- Validation with real municipal data in place of the synthetic Karnataka demonstration.

 
