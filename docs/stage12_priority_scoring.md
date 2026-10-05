# Stage 12: priority score generation

Written for: reviewers and later-stage implementers who need to know exactly how the outage priority index is built and what it can and cannot say.

**Status.** Implemented on `issue4-step1` after Stage 11 and the D18/D19 robustness work. Provisional while M8 is open. The score is a **decision-support priority index derived from the Stage 11 causal estimate. It is not a probability.**

## Objective

Convert the Stage 11 net treatment effect into an outage-level priority score on 0–100, with High / Medium / Low tiers. Code: [src/features/priority_score.py](../src/features/priority_score.py). Notebook: [notebooks/10_priority_scoring.ipynb](../notebooks/10_priority_scoring.ipynb). Validator: [scripts/validation/stage12_validate.py](../scripts/validation/stage12_validate.py).

```
python src/features/priority_score.py [--outages P] [--crime P] [--stage11 P] [--out P]
python scripts/validation/stage12_validate.py
```

## Inputs

| File | Columns used |
|---|---|
| `data/processed/clean_streetlights.parquet` (107,731 rows) | `unique_key` (outage id, unique), `created_date`, `closed_date`, `latitude`, `longitude`, `outage_duration_hours`; every other column is carried through to the output |
| `data/processed/clean_crime.parquet` (921,427 rows, 2019-11-01 to 2026-06-30) | `crime_datetime`, `latitude`, `longitude` |
| `outputs/displacement_estimates.csv` (Stage 11, read only) | `effect_name == "net_post"`: `estimate`, `standard_error` |

`causal_panel.parquet` is not read. It is only hash-checked by the validator.

## Decisions made with the project owner

The repository does not specify these, and the Capstone Guide is not in the repository. Two choices were asked and answered before implementation:
- **τ_net = Stage 11 `net_post`** (+0.019500, SE 0.021663). The alternative `net_during` is negative (−0.024737) and would clip every raw priority to 0.
- **Outages scored = all cleaned outage records.** The repository has no "currently open" definition: the cleaned file holds only records with a `closed_date`, so no subset is open. The only "active" notion in the code is the interval `created ≤ t ≤ closed` (`spatial_linking.py`), which is not a live-open filter.

## Components

- **τ_net:** read unchanged from Stage 11. Sign convention preserved: positive = more crime; net = direct + displacement. One global constant, stored in `tau_net` with provenance in `tau_net_source`.
- **local_crime_rate:** crimes per day within 250 m of the outage location (the Stage 8 outcome radius, EPSG:32118, KD candidates at r + 1e-6 m, membership by the explicit distance formula) during the 14 days (the Stage 8 canonical pre-window length) ending **strictly before** `created_date`. Missing coordinates or crime times are excluded, not imputed. Crime coverage: an outage is excluded if its look-back window starts before the crime data or it was created after the last crime record.
- **duration_factor:** `outage_duration_hours / 24` (days). Valid only for 0 < hours ≤ 8,760 (the Stage 3 validity rule is 0.5–8,760 h). NaN, zero, negative or longer values are excluded, never scored.
- **raw_priority:** `max(0, tau_net × local_crime_rate × duration_factor)`. No other multiplier. If `tau_net` were non-positive, every raw priority would be 0.
- **Normalisation:** min-max over scored outages, `100 × (raw − min) / (max − min)`. If `max == min` (including all zero) every score is 0.
- **Tiers:** `score ≥ 80` High; `score ≥ 40` Medium; otherwise Low.

## Output

`data/processed/outages_scored.parquet`: all 107,731 input rows and original columns, plus `scored`, `exclusion_reason`, `tau_net`, `tau_net_source`, `local_crime_rate`, `duration_factor`, `raw_priority`, `priority_score`, `priority_tier`. Excluded rows keep their fields with empty score and tier. The file is git-ignored with the rest of `data/processed/`.

## Results

| | |
|---|---|
| Total outage records | 107,731 |
| Scored | 103,830 |
| Excluded | 3,901 (all `lookback_not_covered_by_crime_data`: created 2026-07-01 to 2026-09-12, after the crime data ends) |
| Score min / max / mean / median | 0 / 100 / 0.523 / 0.032 |
| High / Medium / Low | 3 / 28 / 103,799 |
| Raw priority exactly 0 | 37,555 (no recorded crime within 250 m in the 14 days before) |

Top examples (by score): `57807769` (Manhattan, score 100), `65567892` (Queens, 88.5), `57943263` (Manhattan, 82.4), `56570130` (Manhattan, 79.2), `62152298` (Brooklyn, 71.3). The notebook lists the top 15 with all components.

## Leakage audit

- **Used at decision time:** location, `created_date`, and crime in the 14 days before `created_date` (end exclusive). The brute-force check in the validator confirms that no crime at or after `created_date` enters the rate.
- **τ_net** is estimated on the matched Stage 8 panel, which includes periods after many of these outages; it is a population-level estimate, not outage-specific information.
- **Not available at decision time:** `outage_duration_hours` is the total duration, known only at closure. It stands in for elapsed or expected duration. In live use it should be replaced by the elapsed time of an open outage.

## Limitations

- τ_net is not statistically distinguishable from zero and its choice (post, not during) drives whether any score exists. Because τ_net is a constant, the ranking is simply recent local crime × duration; the causal estimate only gates the sign.
- The min-max scale is set by the single maximum, and the distribution is very skewed: 99.97% of scored outages are Low. Tiers are relative to the most extreme outage, not absolute risk. This follows the specified formula and was not adjusted.
- Applying the matched-population average (M1) to every outage is an extrapolation. M3 (interference) and M8 apply.
- All outages are historical and closed; the score is retrospective until it is applied to live open outages.
- The 14-day, 250 m and per-day choices follow Stage 8 conventions; other choices would change the ranking.
