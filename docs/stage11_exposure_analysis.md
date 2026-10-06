# Stage 11: reported-open exposure and night crime (frozen)

Written for: reviewers of the final pipeline. It records the frozen Stage 11 estimand, result and limitations. Until now these were only in code docstrings and output JSON. It replaces the retired pair/ring/net design (`src/models/displacement_model.py`, `outputs/displacement_estimates.csv`, `tau_net`), which is kept for history only.

**Status.** Frozen. Validation: 7 of 7 checks pass ([scripts/validation/stage11_exposure_validate.py](../scripts/validation/stage11_exposure_validate.py)). Re-estimation reproduces `stage11_estimates.csv` byte for byte.

## Estimand

This is an association between **reported-open** streetlight exposure and night crime, by distance band:
- The coefficient is the change in log expected night crime in an H3 res-10 cell per additional reported-open light-night in a distance band.
- A light-night is a night on which a 311 "Street Light Out" complaint at a site was open in 311.

It is **not**:
- the effect of physical darkness;
- the effect of a repair;
- a causal crime-reduction estimate;
- an expected benefit of dispatch.

The 311 `closed_date` is not a verified repair time. That is why the exposure is reported-open status.

An earlier reading treated the coefficients as an attenuated (conservative) signal of darkness under non-differential timing error. It is **withdrawn**: the timing sensitivity below changes the sign of the 0–100 m estimate in 3 of 6 scenarios. The `estimand` string stored in `stage11_estimates.csv` and `stage11_summary.json` still carries that clause. It is left unchanged so the frozen outputs reproduce, and this document supersedes it.

## Design

| | |
|---|---|
| Code | [build_exposure_panel.py](../src/features/build_exposure_panel.py) (11a), [exposure_model.py](../src/models/exposure_model.py) (11b), [stage11_sensitivity.py](../src/models/stage11_sensitivity.py) (11c) |
| Inputs | Raw 311 file and `clean_crime.parquet` only; no Stage 7 pair outputs |
| Panel | H3 res-10 cell × ISO week, 2024-W01 to 2026-W26 (130 weeks); 54,467 cells within 500 m of a non-artifact site |
| Exposure | Reported-open interval per complaint: [created, closed), or to the 311 snapshot time if there is no closure, capped at 8,760 h. Closures under 0.5 h and closed < created are excluded. Site-nights are merged so a site counts at most once per night. Exposure is summed over sites in bands 0–100 / 100–250 / 250–500 m (500–750 m as a cutoff sensitivity) |
| Outcome | Night crimes (18:00–07:00) in the cell-week |
| Model | Poisson PPML with cell FE and borough × week FE; SEs clustered on H3 res-7 (200 clusters, G/(G−1)) |
| Estimation sample | 3,921,970 cell-weeks in 30,169 cells. Cells and borough-weeks with zero crime are dropped, as the FE require. All 349,692 crimes are retained |

## Result (primary specification)

| Band | β per light-night | SE | p |
|---|---|---|---|
| 0–100 m | −0.00022 | 0.00103 | 0.83 |
| 100–250 m | −0.000002 | 0.00061 | 1.00 |
| 250–500 m | 0.00015 | 0.00026 | 0.58 |

All bands are null. The 500–750 m cutoff and fortnight specifications are also null.

**Timing sensitivity (11c):**
- The six scenarios are: strict validity rule, start 7 days earlier, early end, gap merging at 7 and 14 nights, and a deadline-closure split.
- All are null (every p ≥ 0.23).
- The 0–100 m sign differs from the primary estimate in 3 of 6 scenarios.
- Deadline-closure and other exposure do not differ detectably.

## Use downstream

None. No Stage 11 coefficient enters Stages 12–14, and no Stage 12–14 module reads a Stage 11 output.

## Limitations

- **Reporting, not darkness.** Exposure is reported-open status. 39% of complaint-nights come from intervals capped at 8,760 h (closed after more than a year, or never closed). A further 7% come from uncapped intervals with no closure.
- **Contemporaneous measurement.** Exposure and crime are measured in the same week, so reverse-direction (crime-related reporting) and common shocks below the borough-week level are not excluded.
- **Null is not "no effect".** A null association is not evidence of no darkness effect. The confidence intervals exclude only large per-night associations.
- **Retrospective sample definition.** The cell universe and artifact sites use the full-sample complaint counts. This is acceptable for a retrospective association. It differs from the pre-2024 artifact rule used by the Stage 12 job set.
