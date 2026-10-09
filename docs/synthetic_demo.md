# Synthetic Karnataka demonstration

Written for: evaluators and teammates who need to run and explain the synthetic-data demo.

**All data here is simulated. Nothing is a real-world measurement.**

## Run it

```powershell
# 1. generate data + run every stage + build dashboard tables (about 15 s)
.venv\Scripts\python.exe scripts\synthetic\run_pipeline.py --regenerate
# 2. validate (data, leakage, outputs)
.venv\Scripts\python.exe scripts\synthetic\validate_synthetic.py
# 3. serve (PowerShell)
$env:LIGHTSAFE_PROFILE = "karnataka_synthetic"
.venv\Scripts\python.exe -m uvicorn backend.main:app --port 8000
cd frontend; npm run dev        # http://localhost:5173
```

Without `LIGHTSAFE_PROFILE` everything behaves exactly as before (NYC data).

## Flow

```
LightSafe_Synthetic_Karnataka_Data/generate_synthetic_data_v2.py   (v1 files untouched)
  -> v2/data/{synthetic_streetlights,synthetic_crime,synthetic_wards}.csv
  -> scripts/synthetic/prepare_raw.py          raw files in the NYC schema (+ ward as police_precinct)
  -> Stage 3 / 5 clean (src/data)               profile bbox, UTM 43N
  -> Stage 7 matched controls -> 8 panel -> 9 DiD -> 10 event study -> 11 direct/ring/net effect
  -> Stage 12 priority index -> 13 FIFO vs priority queue -> 14 ILP dispatch plan
  -> scripts/synthetic/build_context_outputs.py   ward join, ML risk model, population tables, policy/scenario simulation
  -> FastAPI (/api/synthetic/*, plus the existing endpoints)
  -> React dashboard "Synthetic Analysis"
```

`src/profile.py` (env `LIGHTSAFE_PROFILE`) switches paths, bounding box, projected CRS, analysis start, baseline length
(365 d NYC, 90 d synthetic, because there is one year of data) and the Stage 11 effect that feeds Stage 12
(`net_post` for NYC; `direct_during` for the synthetic profile, since the planted effect acts while the light is out).

## Assumptions in the data (v2)

* 131 wards (one per neighbourhood hotspot) with population, density, income, vulnerable share, rainfall, elevation, slope,
  distance to road and depot, pole age, urban/rural class. Fixed before any outage or crime is drawn (no leakage).
* Rainfall and pole age raise failure rates; density and low income raise baseline crime (confounding);
  depot/road distance lengthen repairs, density shortens them.
* Planted causal effect: active outages (65%) add night crime within ~100 m while dark, larger in dense wards, fading after repair.
  Stronger than v1 (v1's was ~0.01 crimes/outage and undetectable at 500 matched pairs).
* The generator writes `v2/ground_truth/outage_effect_truth.csv`. It is used only to evaluate the estimate and the policy benchmark.

## Components

| Kind | Component |
|---|---|
| Causal | Stage 7 matching, Stage 8-11 matched-control difference-in-differences (existing, unchanged logic) |
| Statistical | Event study / parallel-trends test, Spearman relations, group tables |
| ML | Gradient-boosting (Poisson) risk model, ward-held-out cross-fitting (new); the frozen NYC XGBoost model is NYC-specific and is not applied here |
| Rule / optimisation | Stage 12 index and tiers, Stage 13 queues, Stage 14 ILP (budget + city quotas), dispatch-policy simulation |

## Known caveats (please state them when presenting)

1. The Stage 12 score multiplies in the **realised outage duration**, which a dispatcher cannot know in advance. It is kept as defined
   and flagged "hindsight"; an ex-ante variant (causal effect × pre-outage local crime rate) is shown next to it.
2. The Stage 13 "improvement" (+453% at day 30) is measured in the priority-index's own units, so it is partly circular.
   The dashboard leads with the ground-truth benchmark instead.
3. Ring (100-250 m) "displacement" estimates are noise here: no displacement was planted and the ring baseline is imbalanced.
4. Track B (FIFO capacity explorer, City Replay) is calibrated on NYC 2023-2026 and is closed under the synthetic profile.

## Metric traceability

Served by `/api/synthetic/methodology` and shown in the dashboard's Methodology tab (source: `TRACEABILITY` in
`scripts/synthetic/build_context_outputs.py`). Example: *High-risk wards* → `/api/synthetic/overview` →
`ward_summary.csv (risk_category)` → ML risk on `outage_table.csv` ← `outages_scored.parquet` ← `synthetic_streetlights.csv`,
`synthetic_crime.csv`, `synthetic_wards.csv`.

## Decision layer (added)

`src/features/decision_priority.py` combines the pieces without conflating them:

* **XGBoost** (`count:poisson`, ward-held-out folds) predicts night crimes/day within 100 m while an outage is open. Features are all known at report time.
* **Causal AI** supplies one average effect. Effect per outage-day / mean predicted rate gives the share of dark-period crime attributable to outages (about 38%). That share is the *weight on the risk component*; it is 0 if the interval includes zero. It is never applied per ward.
* **Context** components: ward population, vulnerable share, repeat failures within 50 m in the prior 90 days, distance to depot.
* Weights are documented judgement calls; `decision_summary.json` reports sensitivity. Stage 13/14 are re-run on this score into `outputs/synthetic/decision/`.
* Repair pressure for the synthetic profile: `scripts/synthetic/build_repair_pressure.py` (share of complaints unresolved after 7 days, per city, from the simulated timestamps).
