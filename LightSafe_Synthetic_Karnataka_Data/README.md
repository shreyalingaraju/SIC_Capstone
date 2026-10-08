# LightSafe: Synthetic Karnataka Streetlight & Crime Data

> **⚠️ THIS IS SYNTHETIC DATA.**
> It is **NOT real Karnataka crime data** and **NOT real municipal streetlight data**.
> Every record was produced by a random simulation (`generate_synthetic_data.py`).
> It must **not** be interpreted, quoted or published as real crime statistics for
> Bengaluru, Mysuru, Mangaluru, Hubballi, Belagavi, Shivamogga, Tumakuru or anywhere else.

## Why this exists

LightSafe (*A Causal Decision-Support System for Prioritizing Streetlight Repairs*) was built
on NYC 311 streetlight complaints and NYPD complaint data, which run to hundreds of thousands of
rows. That is too heavy for student laptops. This package provides two **small, shareable**
datasets with the **same column structure** as the NYC raw extracts. They also contain a
**known, planted outage → night-crime effect**, so the team can check whether the causal
pipeline recovers an effect whose true size we control.

## Contents

```
LightSafe_Synthetic_Karnataka_Data/
├── README.md                     this file
├── generate_synthetic_data.py    generator + validation (fixed seed)
├── data/
│   ├── synthetic_streetlights.csv   6,856 rows
│   └── synthetic_crime.csv          31,603 rows
└── metadata/
    └── dataset_summary.txt       counts, distributions, SHA-256 hashes, validation report
```

## At a glance

| | |
|---|---|
| Date range | **2025-01-01 00:00 to 2025-12-31 23:59** (365 days) |
| Streetlight records | **6,856** (5,959 are `Street Light Out`) |
| Crime records | **31,603** |
| Cities | Bengaluru, Mysuru, Mangaluru, Hubballi, Belagavi, Shivamogga, Tumakuru |
| Random seed | **20250101** |
| Requirements | Python 3.9+, numpy, pandas, scipy (scipy only for validation) |

### Per-city counts

| City | Streetlight records | of which Street Light Out | Crime records |
|---|---:|---:|---:|
| Bengaluru  | 2,025 | 1,763 | 9,263 |
| Mysuru     | 1,120 |   979 | 5,212 |
| Mangaluru  |   830 |   728 | 3,862 |
| Hubballi   |   860 |   748 | 3,725 |
| Belagavi   |   702 |   601 | 3,394 |
| Shivamogga |   559 |   487 | 2,720 |
| Tumakuru   |   760 |   653 | 3,427 |

Crime cities are assigned by nearest city centre. The crime file has no city column (see below).

## Columns

Column names deliberately match the raw NYC files the LightSafe pipeline reads
(`data/raw/streetlight_complaints.csv` and `data/raw/nypd_crime.csv`).

### `synthetic_streetlights.csv`

| Column | Meaning | Example |
|---|---|---|
| `unique_key` | Complaint ID (unique, as in 311) | `61000008` |
| `created_date` | Complaint creation = outage start, `YYYY-MM-DDTHH:MM:SS.000` | `2025-01-01T02:10:00.000` |
| `closed_date` | Repair/closure time; **empty** if still open at period end | `2025-01-09T14:30:22.000` |
| `descriptor` | `Street Light Out` (87%), `Street Light Cycling`, `Street Light Dayburning`, `Street Light Dim`, `Lamppost Damaged` | `Street Light Out` |
| `status` | `Closed`, `Open`, `In Progress` | `Closed` |
| `borough` | **City name.** Named `borough` because the pipeline groups by that column | `Mangaluru` |
| `latitude`, `longitude` | WGS84, 6 decimals | `12.903024, 74.856697` |

`unique_key` and `borough` are the only additions to the six requested columns. The existing
pipeline reads both (complaint de-duplication and per-borough grouping).

### `synthetic_crime.csv`

| Column | Requested field | Meaning | Example |
|---|---|---|---|
| `cmplnt_num` | – | Complaint ID (unique) | `427912914` |
| `cmplnt_fr_dt` | crime date | `YYYY-MM-DD` | `2025-01-01` |
| `cmplnt_fr_tm` | crime time | `HH:MM:SS` | `00:07:00` |
| `ky_cd` | – | NYPD-style offence key code | `344` |
| `ofns_desc` | offense description | NYPD-style offence label | `ASSAULT 3 & RELATED OFFENSES` |
| `law_cat_cd` | – | `FELONY` / `MISDEMEANOR` | `MISDEMEANOR` |
| `crime_category` | crime category | Grouped category | `ASSAULT` |
| `latitude`, `longitude` | – | WGS84, 6 decimals | `12.315417, 76.660093` |

`cmplnt_num`, `ky_cd` and `law_cat_cd` are kept because the existing `clean_crime.py`
de-duplicates on `cmplnt_num` and filters/maps categories via `ky_cd`. The codes and labels match
its `KY_CD_CATEGORIES` table exactly:

| ky_cd | ofns_desc | crime_category | Records |
|---|---|---|---:|
| 105 | ROBBERY | ROBBERY | 3,263 |
| 106 | FELONY ASSAULT | ASSAULT | 2,257 |
| 344 | ASSAULT 3 & RELATED OFFENSES | ASSAULT | 4,726 |
| 107 | BURGLARY | BURGLARY | 3,882 |
| 109 | GRAND LARCENY | GRAND LARCENY | 4,619 |
| 341 | PETIT LARCENY | PETIT LARCENY | 9,126 |
| 121 / 351 | CRIMINAL MISCHIEF & RELATED OF | MISCHIEF | 932 / 2,798 |

## How the data was generated

### Coordinates
* Each city has a real approximate centre and an urban radius (Bengaluru 11 km; Mysuru 5.5 km;
  Mangaluru, Hubballi and Belagavi 5 km; Shivamogga and Tumakuru 4 km).
* Inside each city, 12–40 **neighbourhood hotspots** are placed around the centre, each with its
  own spread and weight. Points are drawn from this hotspot mixture plus a diffuse urban
  component. The result is clustered, uneven density like a real city, not a uniform blob.
* Points further than 1.6 × radius from the centre are rejected and re-drawn. Mangaluru points
  west of longitude 74.838 (the Arabian Sea) are also rejected.
* There are **no** random points across the state. Every record sits in one of the 7 clusters.

### Streetlight outages
* 4,000 light **sites** (poles) are placed in the cities, weighted by city activity.
* Each site has its own failure propensity (gamma-distributed). Most sites fail once or twice and
  some fail repeatedly; repeat complaints at a site never overlap in time and carry ~6 m GPS
  jitter.
* Outage start dates are spread over the year, with ~30% more failures in the monsoon
  (Jun–Sep). Creation times peak in the evening (~20:00, when outages are noticed) and the
  morning (~09:00).
* **Duration** is log-normal around a city-specific median (Bengaluru 4 d → Shivamogga/Tumakuru
  6 d). 4% of jobs get a 15–60-day backlog, clipped to 2 h–90 d. Closure times are moved into
  crew working hours (08:00–18:59). Realised durations: median ≈ 5.3 days, IQR ≈ 3–9.7 days,
  95th percentile ≈ 29 days.
* Outages not repaired by 31 Dec 2025 have status `Open`/`In Progress` and an empty
  `closed_date` (168 records).

### Crime events
Three components are generated and then shuffled together. The CSV does **not** say which
component a record came from.
1. **Background crime** (15,500): city-wide, drawn from the hotspot mixture plus a wider diffuse
   component. It is not tied to any light.
2. **Local baseline crime** (~16,000): crime around each light site at a site-specific rate
   (busier neighbourhoods get more, plus random site-level noise). It occurs **all year**,
   whether or not the light is out. ~85% falls within ~100 m of the pole (half-normal, σ = 55 m)
   and 15% falls 100–300 m away.
3. **Outage-induced crime** (~130): the planted effect, described next.

Timing: each offence type has its own hour-of-day profile. Robbery and assault peak at night,
larceny in the afternoon, and burglary has day and night peaks. Day-to-day volume has mild
seasonality (festival-season peak around late October, slight monsoon dip) and a
weekday/weekend pattern. Minutes show mild heaping at :00 and :30, as real logs do. Overall,
~49% of crime falls at night (19:00–05:59).

Distance from each crime to the nearest `Street Light Out` location: 0–100 m 46.5%,
100–250 m 30.8%, 250–1000 m 19.3%, >1 km 3.5%. So some crimes are close to outages, some are
moderately far, and some are unrelated.

### The planted outage → night-crime effect
For each **`Street Light Out`** complaint (other descriptors have no effect):
* With probability **0.65** the outage is "active". Its uplift factor is drawn from
  Gamma(2, 0.275), mean **+55%** on that site's **night-time** local crime rate. The other 35%
  have **zero** effect, so the effect is heterogeneous.
* Extra crimes are Poisson(local night rate × uplift × outage length). They fall only at **night
  (19:00–05:59)**, within ~100 m of the pole (half-normal σ = 45 m), and mostly in
  visibility-sensitive offences (robbery 28%, assault 25%, burglary 14%, grand larceny 15%,
  petit larceny 10%, mischief 8%).
* **After repair** the uplift decays exponentially (2-day time constant, cut off at 7 days), so
  crime returns toward baseline.
* **Daytime crime is not affected.**

**Expected true effect:** about +36% on *local-site* night crime during an outage (0.65 × 0.55).
Background crime also falls within 100 m, so the naive measured ratio is diluted. The validation
measures (per 1,000 site-days, within 100 m, 5,294 closed outages):

| Window | Night crime | Day crime (placebo) |
|---|---:|---:|
| 14 days before outage | 9.8 | 11.0 |
| During outage | **12.8** | 11.6 |
| Days 7–21 after repair | 11.5 | 10.9 |
| During ÷ mean(before, after) | **1.20** | 1.06 |

The effect is moderate and noisy: individual outages show no visible jump (≈0.02 extra crimes
per outage), but it is detectable in aggregate. Pre/post differ by sampling noise alone.

## Reproducing

```bash
cd LightSafe_Synthetic_Karnataka_Data
python generate_synthetic_data.py              # seed 20250101 -> identical files
python generate_synthetic_data.py --seed 7     # a different realisation, same design
```

The script overwrites `data/*.csv` and `metadata/dataset_summary.txt`, then runs all validation
checks; it exits non-zero if any check fails. With the default seed, the SHA-256 hashes in
`metadata/dataset_summary.txt` should match (tested with numpy 2.x / pandas on Windows). Very
different numpy versions may change the random stream.

## Notes for integrating into LightSafe

These are hints only. Nothing in the main project was changed.
* Rename or copy the files to the paths the pipeline expects (`data/raw/streetlight_complaints.csv`,
  `data/raw/nypd_crime.csv`).
* `clean_crime.py` has an `ANALYSIS_START` of 2019-11-01 and **NYC coordinate bounds**
  (lat 40.49–40.92, lon −74.26 to −73.69). These must be changed for Karnataka, or every row will
  be dropped. Karnataka clusters span roughly lat 12.2–16.0, lon 74.4–77.8.
* Any projected CRS used for metre distances (NYC State Plane) must be swapped for a suitable
  one, e.g. UTM zone 43N (EPSG:32643), which covers all seven cities.
* Many NYC raw columns (precinct, community board, address fields, etc.) are intentionally absent.

## Limitations

* **Synthetic and not representative.** Crime levels, mixes, city ratios and repair times are
  plausible *assumptions*, not estimates for any real Karnataka city.
* City geometry is simplified: there are no real roads, wards, rivers or lakes (apart from the
  Mangaluru coastline cut-off), so points may fall in places with no streets.
* The causal effect is built in by construction, with a simple form (multiplicative,
  night-only, decaying). Recovering it validates the *mechanics* of the pipeline, not the real
  world. Results from this data say nothing about whether streetlight outages cause crime in
  Karnataka or anywhere else.
* There is no reporting delay, under-reporting, geocoding error or duplicate complaints beyond
  repeat outages at the same pole.
* Sites with more crime do not fail more often, so there is no confounding between outage
  propensity and crime level. Real data is messier.
* There is one year of data, so long-term trends and multi-year seasonality are not represented.
