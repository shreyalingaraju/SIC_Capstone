# Synthetic streetlight complaints — external-validation dataset

## 1. Purpose
An independent test set for the frozen NYC-trained slow-repair model. It is generated from an operational model that
differs from NYC on purpose. It must never be used to train, tune, re-threshold or select the model. The generator was
**not** adjusted by looking at model predictions; no predictions were scored against labels while building it. The only model
contact is the final compatibility check (feature engineering, plus one `predict_proba` call on 500 rows to confirm shape/finite values; output discarded).
One calibration of overall realism was done on generator statistics only (capacity ratios were set so the overall slow rate lands near 35% with wide borough/time variation).

## 2-3. Size and time
49,796 rows (49,552 unique complaints + 244 exact duplicates), created 2024-01-01 00:02 to 2026-09-30 23:35. Snapshot (extraction time) = 2026-09-30 23:59.
The queue was simulated from 2023-10-01 (burn-in, not written out), so the first 2024 complaints face a realistic backlog, **but the file has no
earlier complaints**, so the model's history features undercount for roughly the first 60 days. `ground_truth.csv` has `history_ready` (False for those days) so you can report with and without them.

## 4. Files and columns
* `data/synthetic_streetlight_complaints.csv` — model input. Columns: `complaint_id, created_date, closed_date, status, borough, descriptor_2, address_type,
  community_board, council_district, police_precinct, incident_zip, latitude, longitude, intersection_street_1/2, incident_address, street_name, cross_street_1/2`.
  This is exactly the 4 required + 14 optional raw columns read by `features/build_features.py`, plus `complaint_id`. Nothing else was added.
  Formats mirror NYC (upper-case borough, `Location Type: ...`, `05 BROOKLYN`, `Precinct 45`). No `Pending` status (a NYC-specific artefact the pipeline discards).
* `validation/ground_truth.csv` — **never give to the model**: `complaint_id, created_date, closed_date` (latent true closure, even when blank in raw),
  `repair_duration_hours, slow_repair, true_borough, site_id, followon, complex_repair, closed_in_raw, outcome_observable, history_ready, closed_date_blanked`.
* `validation/validation_report.json` — all validation output below, including monthly tables and the compatibility check.

## 5. Data-generating process (seed 42; independent `SeedSequence(42)` streams for sites / arrivals / repairs / data-quality)
1. **Sites** (15,500 synthetic locations) → 2. **arrivals** (Poisson per borough-day, sites drawn by weight, plus follow-on re-reports) →
3. **repairs** (queue simulation) → 4. closed_date = created_date + duration → 5. `slow_repair = duration > 168 h` derived afterwards → 6. missingness/quality issues injected into the raw file only.

## 6. Borough generation
Complaint share (NYC for comparison: Queens 25 / Brooklyn 23 / Bronx 22 / Manhattan 14 / SI 10 / blank 5 %): Brooklyn 30, Manhattan 22, Queens 20, Bronx 18, Staten Island 10 %
(realised 31.1 / 19.6 / 20.3 / 18.3 / 9.6, blank 1.2). Districts per borough: 10/16/10/12/4 (community board 04 in Staten Island does not exist in NYC).
Precinct, council-district and zip values come from NYC-like numeric ranges but are randomly assigned (not real mappings).

## 7. Location generation
Each site has random coordinates (district centre + N(0, 0.010°)), never copied from NYC, a location/address type, synthetic street names, and a frequency tier:
high (3% of sites, weight x12), medium (22%, x3), low (75%, x1), times a lognormal site factor. Realised: 12,107 sites complained about, 8,640 with >=2 complaints;
by realised count 888 sites have >=10, 3,391 have 4-9, 7,828 have 1-3; 72% of complaints are at sites with >=4; max 105 at one site. Repeats use identical coordinates, but because coordinates go missing at random
the pipeline sometimes keys the same site by intersection/address instead (a realistic quirk).

## 8. Arrival process
Daily rate = borough share x season (winter peak, +/-30%) x day-of-week (Sat 0.8, Sun 0.7) x per-borough linear trend (-5%..+8%/yr) x AR(1) lognormal shock x
citywide storm multipliers (1.5-2.2x, ~1.5% of days). Site choice ∝ site weight x slowly drifting district "infrastructure condition" (monthly AR(1) lognormal).
Hour of day peaks 18-22 h (NYC peaks 8-10 h). Follow-on complaints (7% of primary, 15% at high-tier sites) re-report the same site 1-10 days later. Weekend share 24%.

## 9. Repair-duration process
Per borough a FIFO-ish work queue with daily crew capacity = base capacity x regime x monthly noise x weekday factor (weekday 1.25, Sat 0.45, Sun 0.30) x storm reduction, Poisson-drawn.
* *Complex* repairs (probability rises with prior complaints at the site in 90 days, site latent severity and district condition; 16.8% overall) go to the queue and then add a lognormal delay (median 4 days).
* *Quick-lane* repairs (borough-specific 20-34% of the remainder) take lognormal(median ~16 h), slowed by current backlog pressure.
* Everything else waits in the queue (priority = creation time + N(0, 36 h) noise), then adds dispatch lag (median 2 h).
Delay therefore emerges from workload vs. capacity and is right-skewed: median 80 h, mean 256 h, p25 18 h, p75 329 h, p90 890 h, p99 1,357 h.

## 10-11. Target and ground truth
`slow_repair = 1` iff `closed_date - created_date > 168 h`, the same definition as the frozen model. Ground truth uses the latent true closure. `outcome_observable` mirrors the
pipeline's rule (closed by snapshot, or unresolved and older than 168 h); complaints still open and younger than 7 days have unknown outcomes. Slow rate over observable rows: **0.353**
(0.348 with `history_ready`). By borough: Bronx 0.56, Brooklyn 0.49, Queens 0.31, Staten Island 0.12, Manhattan 0.11. Monthly range 0.05-0.72. No borough is deterministic: monthly
borough slow rates span e.g. Brooklyn 0.02-0.85, Staten Island 0.03-0.73, Manhattan 0.01-0.61.

## 12. Domain shifts vs NYC (intentional)
| Aspect | NYC | Synthetic |
|---|---|---|
| Borough mix | Q/B/Bx/M/SI = 25/23/22/14/10 + 5% blank | 20/31/18/20/10 + 1% blank |
| Overall slow rate (2024-26) | 0.62 (0.46 -> 0.80 rising) | 0.35, falling to ~0.06 in autumn and rising in winter (seasonal capacity pressure), no monotone trend |
| Borough ordering | Brooklyn/SI slowest, Bronx fastest | Bronx/Brooklyn slowest, Manhattan/SI fastest |
| Hour of day | peak 8-10 h | peak 18-22 h |
| Weekend share | 10% | 24% |
| Location types | 86% intersection | 56% intersection, 22% residential |
| Coordinates missing | 17% | 12% (blockface 60%, others 4%) |
| Repeat locations | median 2 complaints/location | heavy tier structure, 72% of complaints at sites with >=4 |
| Crew regimes | n/a | Brooklyn slowdown 2024-06..10, Bronx 2025-01..05 and 2026-06.., Queens 2025-08..2026-01, Manhattan 2026-03..06, SI improvement 2025-06.. |
| Unseen categories | | 9 of 41 precincts, 20 of 80 zips, 1 of 52 community boards not in NYC |
The model-relevant relationship (recent backlog and slow closures predict delay) is the same *kind* of mechanism as NYC, but its strength and regime timing are different, so this is a genuine generalisation test, not a replay.

## 13. Missingness (percent of the 49,796 rows; MCAR unless noted)
`closed_date` 2.39 (1,045 truly open at snapshot + 0.8% of slow closed complaints with unrecorded closure), `status` 0.95 (1% blanked), `borough` 1.18, `community_board` 3.05,
`incident_zip` 5.88, `latitude/longitude` 12.21 (blockface 60%, others 4%; MAR on address type), `council_district` 13.94 (blank with coordinates + 2%). Street columns are blank by design depending on `address_type`
(as in NYC); `incident_address` 78% and `cross_street_*` 86% are structurally empty.

## 14. Data-quality issues (intentional)
244 exact duplicate rows (0.5%); 1,192 blank `closed_date`: 1,045 complaints truly unresolved at the snapshot plus 140 "Closed" rows with the date deliberately dropped (only on slow complaints, so labels stay correct), with duplicated rows making up the small remainder; 1,139 rows have status Open/Assigned;
~0.3% of closed rows with status "Open" although closed_date is present; blank statuses; 1,139 Open/Assigned (10% Assigned). `closed_date >= created_date` always holds (no negative durations).

## 15-16. Seed and reproducibility
Seed 42. Re-running gives byte-identical files (verified by SHA-256 across repeated runs):
raw `5dc9470e45b154d014cf6d8e4e4c0bf0e46dd143fd5a57490096b4f5e6b2c5b8`, ground truth `042c3b0c47bbd3726ddfb4609d415b9ca8b7cdd6767030d0734c58dfe0f7eb93`.
Reproduce from `ml_model/`:  `python synthetic_data/generate_synthetic_data.py`  (add `--skip-compat` to skip the frozen-pipeline check; note it rewrites `validation_report.json` without that section).
Requires numpy 2.4 / pandas 2.3 (same environment as the model).

## 17. Frozen-model compatibility
Run with the existing `features/build_features.py` (unmodified) and `models/frozen_model.joblib` (unmodified): the 24 schema features are all produced and match `feature_schema.json`;
49,489 of 49,796 rows have a known label; the pipeline's own labels match ground truth on all of them (0 mismatches); `predict_proba` accepts the features (500-row shape/finite check only).
Feature missingness: `loc_days_since_prev` 28.9% (first complaint at a site), `latitude/longitude` 12.2%, `council_district` 13.9%, `zip_code` 5.9%, `community_board` 3.1%, backlog features 0%.
No compatibility issues; nothing in the model, schema or pipeline was changed.

## 18. Limitations
* Synthetic mechanisms are authored assumptions; real repair processes are messier. Results show sensitivity to a different environment, not real-world validity.
* Coordinates are random Gaussian clusters, not real geography. Street names are generic.
* Whether delay is driven mainly by borough-level queue pressure is built into the generator; the NYC model's reliance on that is exactly what is being tested, but the design is not neutral to it.
* First ~60 days have truncated history (see `history_ready`).
* Categorical codes (precinct, zip, council) are NYC-like ranges with random assignment; they carry no real geography.
* The 2026 high-slow period and strong seasonality make prevalence shift large; compare threshold-free metrics first.
