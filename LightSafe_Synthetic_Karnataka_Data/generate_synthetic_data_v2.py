"""
LightSafe - synthetic Karnataka data, version 2 (adds ward context + confounding).

THIS DATA IS SYNTHETIC. It is not real Karnataka crime, census, weather or
municipal data and must never be read as real statistics.

The original generator (generate_synthetic_data.py) and its files are left
untouched. This script reuses its geometry, hotspot and crime-timing helpers and
changes three things:

1. It adds a WARD table (one ward per neighbourhood hotspot): population,
   density, income index, vulnerable-population share, annual rainfall,
   elevation, slope, distance to the nearest main road, distance to the repair
   depot, pole age and urban/rural class.
2. It makes those ward conditions drive the data, so outage timing is
   confounded with crime the way real data is:

       rainfall, pole age  -> how often a light fails        (outage propensity)
       population density, income -> baseline night crime    (crime level)
       density, depot distance, road access -> repair delay  (dispatch speed)

   Dense, older, rainier wards both fail more AND have more crime, so a naive
   "outages vs crime" comparison is biased. Matching on pre-outage crime
   (the existing Stage 7 design) is what removes that bias.
3. The planted causal effect is unchanged (see the original README): only
   "Street Light Out" complaints, only night hours, only within ~100 m, mean
   +55% on the local night rate for the 65% of outages that are "active".

No leakage: ward attributes are fixed before any outage or crime is drawn and
no ward attribute is computed from outages, crimes or repair times.

Outputs (relative to this file):
    v2/data/synthetic_streetlights.csv   same schema as the original
    v2/data/synthetic_crime.csv          same schema as the original
    v2/data/synthetic_wards.csv          ward context table
    v2/metadata/dataset_summary.txt      counts, SHA-256, validation report

Usage:  python generate_synthetic_data_v2.py [--seed N]
"""

import argparse
import hashlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_synthetic_data as g  # noqa: E402  (original generator: reused, not modified)

SEED = 20250102

OUT_DIR = Path(__file__).resolve().parent / "v2"
DATA_DIR = OUT_DIR / "data"
META_DIR = OUT_DIR / "metadata"

# Synthetic city-level assumptions behind the ward attributes. None of these are
# measured values; they are plausible orders of magnitude chosen for the demo.
CITY_CONTEXT = {
    #              density/km2, income idx, rain mm/yr, elevation m, slope %
    "Bengaluru":  dict(code="BLR", dens=9000, income=70, rain=970,  elev=920, slope=2.0),
    "Mysuru":     dict(code="MYS", dens=5500, income=58, rain=800,  elev=770, slope=1.5),
    "Mangaluru":  dict(code="MNG", dens=4800, income=62, rain=3500, elev=22,  slope=4.0),
    "Hubballi":   dict(code="HBL", dens=4500, income=50, rain=780,  elev=650, slope=1.5),
    "Belagavi":   dict(code="BGM", dens=3800, income=46, rain=1250, elev=760, slope=3.0),
    "Shivamogga": dict(code="SMG", dens=3200, income=44, rain=1900, elev=570, slope=3.5),
    "Tumakuru":   dict(code="TMK", dens=3000, income=42, rain=700,  elev=820, slope=1.5),
}

# v2 volume / effect settings. v1's effect (+36% of a very small local night rate, ~0.01 extra
# crimes per outage) is far below what a 500-pair matched DiD can detect. v2 therefore uses more
# local crime per pole and a stronger, ward-dependent effect so the demo has a detectable (but
# still noisy and heterogeneous) causal signal. Every number here is an assumption, not an estimate.
N_LOCAL_CRIMES_V2 = 64000       # baseline crime around poles (v1: 16,000)
N_BACKGROUND_V2 = 52000         # city-wide crime not tied to a pole (v1: 15,500); keeps >20% of crime far from outages
EFFECT_MEAN_V2 = 2.0            # mean multiplicative uplift of local night crime for an active outage (v1: 0.55)
EFFECT_DENSITY_EXPONENT = 0.5   # darkness matters more where more people are exposed (density^0.5)

# Strength of each causal link (log-scale coefficients on standardised ward variables).
B_FAIL_RAIN = 0.35        # rainfall            -> failure propensity
B_FAIL_AGE = 0.30         # pole age            -> failure propensity
B_CRIME_DENS = 0.30       # population density  -> baseline crime
B_CRIME_INCOME = -0.20    # income              -> baseline crime
B_REPAIR_DEPOT = 0.18     # depot distance      -> repair delay
B_REPAIR_ROAD = 0.12      # distance to road    -> repair delay
B_REPAIR_DENS = -0.15     # density             -> repair delay (dense wards served first)


def z(x):
    x = np.asarray(x, dtype=float)
    return (x - x.mean()) / x.std()


# ---------------------------------------------------------------------------
# Wards
# ---------------------------------------------------------------------------

def build_wards(rng, spots):
    rows = []
    for city, c in g.CITIES.items():
        s = spots[city]
        cc = CITY_CONTEXT[city]
        dlat, dlon = g.km_to_deg(c["lat"], 1.0, 1.0)
        n = len(s["lat"])
        dist_c = np.hypot((s["lat"] - c["lat"]) / dlat, (s["lon"] - c["lon"]) / dlon)
        for i in range(n):
            rows.append(dict(
                city=city, ward_id=f"{cc['code']}-W{i + 1:02d}",
                latitude=s["lat"][i], longitude=s["lon"][i], sigma_km=s["sigma_km"][i],
                hotspot_weight=s["weight"][i], dist_center_km=dist_c[i],
                radius_km=c["radius_km"],
            ))
    w = pd.DataFrame(rows)
    ct = w["city"].map(CITY_CONTEXT)

    # Urbanicity: near the centre and a busy hotspot -> more urban. Exogenous geography only.
    urban_raw = -(w["dist_center_km"] / w["radius_km"]) + 0.5 * z(np.log(w["hotspot_weight"] * 100))
    zu = z(urban_raw)
    w["pop_density_per_km2"] = (ct.map(lambda d: d["dens"]) * np.exp(0.75 * zu + rng.normal(0, 0.25, len(w)))).round(0)
    w["area_km2"] = (np.pi * (1.5 * w["sigma_km"]) ** 2).round(2)
    w["population"] = (w["pop_density_per_km2"] * w["area_km2"]).round(0).astype(int)

    # Income falls slightly with density (crowded informal areas), plus ward noise.
    w["income_index"] = np.clip(ct.map(lambda d: d["income"]) - 4 * zu + rng.normal(0, 8, len(w)), 10, 95).round(1)
    # Vulnerable share = children + elderly + low-income households (single synthetic index, 0-1).
    w["vulnerable_pop_share"] = np.clip(0.14 + 0.0030 * (60 - w["income_index"]) + rng.normal(0, 0.03, len(w)),
                                        0.05, 0.60).round(3)
    w["rainfall_mm_year"] = (ct.map(lambda d: d["rain"]) * rng.uniform(0.94, 1.06, len(w))).round(0)
    w["elevation_m"] = (ct.map(lambda d: d["elev"]) + rng.normal(0, 25, len(w))).clip(5, None).round(0)
    w["slope_pct"] = np.clip(ct.map(lambda d: d["slope"]) * rng.lognormal(0, 0.4, len(w)), 0.2, 15).round(2)
    # Rural-ish wards sit further from main roads.
    w["dist_main_road_km"] = (rng.exponential(0.9 - 0.35 * np.clip(zu, -1.5, 1.5) / 1.5, len(w))).clip(0.02, 6).round(2)
    w["dist_depot_km"] = w["dist_center_km"].round(2)       # repair depot assumed at the city centre
    w["pole_age_years"] = np.clip(9 + 3 * zu + rng.normal(0, 1.5, len(w)), 3, 20).round(1)
    w["area_class"] = np.select([w["pop_density_per_km2"] >= 6000, w["pop_density_per_km2"] >= 3500],
                                ["Urban core", "Urban"], default="Peri-urban")
    return w.drop(columns=["radius_km"])


def assign_wards(wards, city, lat, lon):
    """Nearest ward centroid within `city` (scaled by ward size so big wards win more area)."""
    sub = wards[wards["city"] == city]
    c = g.CITIES[city]
    dlat, dlon = g.km_to_deg(c["lat"], 1.0, 1.0)
    dx = (lat[:, None] - sub["latitude"].to_numpy()[None, :]) / dlat
    dy = (lon[:, None] - sub["longitude"].to_numpy()[None, :]) / dlon
    d = np.hypot(dx, dy) / sub["sigma_km"].to_numpy()[None, :]
    return sub.index.to_numpy()[d.argmin(1)]


# ---------------------------------------------------------------------------
# Outages
# ---------------------------------------------------------------------------

def generate_streetlights(rng, spots, wards):
    names = list(g.CITIES)
    wts = np.array([g.CITIES[c]["weight"] for c in names])
    n_sites = g.N_LIGHT_SITES
    site_city = rng.choice(names, size=n_sites, p=wts / wts.sum())
    site_lat = np.empty(n_sites)
    site_lon = np.empty(n_sites)
    site_ward = np.empty(n_sites, dtype=int)
    for city in names:
        idx = np.flatnonzero(site_city == city)
        site_lat[idx], site_lon[idx] = g.sample_city_points(rng, city, len(idx), spots, diffuse_share=0.25)
        site_ward[idx] = assign_wards(wards, city, site_lat[idx], site_lon[idx])

    zrain = z(np.log(wards["rainfall_mm_year"]))
    zage = z(wards["pole_age_years"])
    base = rng.gamma(1.4, 1.0 / 1.4, n_sites)
    prop = base * np.exp(B_FAIL_RAIN * zrain[site_ward] + B_FAIL_AGE * zage[site_ward]
                         + rng.normal(0, 0.15, n_sites))      # site-level pole condition noise
    prop = prop / prop.mean() * g.MEAN_COMPLAINTS_PER_SITE

    zdepot = z(wards["dist_depot_km"])
    zroad = z(wards["dist_main_road_km"])
    zdens = z(np.log(wards["pop_density_per_km2"]))
    repair_mult = np.exp(B_REPAIR_DEPOT * zdepot + B_REPAIR_ROAD * zroad + B_REPAIR_DENS * zdens)

    days = pd.date_range(g.PERIOD_START, periods=g.N_DAYS, freq="D")
    day_p = np.where((days.month >= 6) & (days.month <= 9), 1.3, 1.0)
    day_p = day_p / day_p.sum()
    create_hour_p = 0.15 + 1.4 * g._bump(20, 2.5) + 0.9 * g._bump(9, 2.0)
    create_hour_p /= create_hour_p.sum()

    desc_names, desc_p = list(g.DESCRIPTORS), list(g.DESCRIPTORS.values())
    rows = []
    for s in range(n_sites):
        n = rng.poisson(prop[s])
        if n == 0:
            continue
        city = site_city[s]
        start_days = np.sort(rng.choice(g.N_DAYS, size=n, p=day_p))
        last_close = g.PERIOD_START - pd.Timedelta(days=1)
        for d in start_days:
            created = (g.PERIOD_START + pd.Timedelta(days=int(d))
                       + pd.Timedelta(hours=int(rng.choice(24, p=create_hour_p)))
                       + pd.Timedelta(minutes=int(rng.integers(0, 60))))
            if created <= last_close:
                continue
            descriptor = rng.choice(desc_names, p=desc_p)
            med = g.CITIES[city]["repair_median_d"] * repair_mult[site_ward[s]]
            dur_d = rng.lognormal(np.log(med), 0.85)
            if rng.random() < 0.04:
                dur_d += rng.uniform(15, 60)
            dur_d = float(np.clip(dur_d, 2 / 24, 90))
            closed = created + pd.Timedelta(days=dur_d)
            if closed.hour < 8 or closed.hour > 18:
                base_t = closed.normalize() + (pd.Timedelta(days=1) if closed.hour > 18 else pd.Timedelta(0))
                closed = base_t + pd.Timedelta(hours=int(rng.integers(8, 19)), minutes=int(rng.integers(0, 60)))
            jlat, jlon = g.offset_points(rng, np.array([site_lat[s]]), np.array([site_lon[s]]),
                                         np.abs(rng.normal(0, 6, 1)))
            rows.append(dict(site_id=s, borough=city, descriptor=descriptor, created=created, closed=closed,
                             latitude=jlat[0], longitude=jlon[0]))
            last_close = closed

    sl = pd.DataFrame(rows)
    open_now = sl["closed"] > g.PERIOD_END
    sl["status"] = np.where(open_now, rng.choice(["Open", "In Progress"], size=len(sl), p=[0.6, 0.4]), "Closed")
    sl = sl.sort_values(["created", "site_id"], kind="mergesort").reset_index(drop=True)
    sl["unique_key"] = 61000000 + np.sort(rng.choice(900000, size=len(sl), replace=False))
    return sl, site_city, site_lat, site_lon, site_ward


# ---------------------------------------------------------------------------
# Crime
# ---------------------------------------------------------------------------

def generate_crime(rng, spots, wards, sl, site_city, site_lat, site_lon, site_ward):
    names = list(g.CITIES)
    days, day_p = g.day_weights()
    ky_codes = np.array(list(g.OFFENSES))
    ky_p = np.array([g.OFFENSES[k][3] for k in ky_codes])
    ky_p = ky_p / ky_p.sum()
    frames = []

    def timestamps(day_idx, hours):
        mins = g.sample_minutes(rng, len(day_idx))
        secs = np.where(rng.random(len(day_idx)) < 0.85, 0, rng.integers(0, 60, len(day_idx)))
        return (days[day_idx] + pd.to_timedelta(hours, "h") + pd.to_timedelta(mins, "m")
                + pd.to_timedelta(secs, "s"))

    # (a) Background city-wide crime (not tied to any light).
    wts = np.array([g.CITIES[c]["weight"] for c in names])
    city = rng.choice(names, size=N_BACKGROUND_V2, p=wts / wts.sum())
    lat = np.empty(N_BACKGROUND_V2)
    lon = np.empty(N_BACKGROUND_V2)
    for c in names:
        idx = np.flatnonzero(city == c)
        lat[idx], lon[idx] = g.sample_city_points(rng, c, len(idx), spots, diffuse_share=0.35)
    ky = rng.choice(ky_codes, size=N_BACKGROUND_V2, p=ky_p)
    d = rng.choice(g.N_DAYS, size=N_BACKGROUND_V2, p=day_p)
    frames.append(g.make_crime_frame(rng, ky, timestamps(d, g.sample_hours(rng, ky)), lat, lon, "background"))

    # (b) Baseline crime around light sites: busier + poorer wards -> more crime. This is the
    #     confounder: the same wards also have more frequent outages (see generate_streetlights).
    dens = np.empty(g.N_LIGHT_SITES)
    for c in names:
        idx = np.flatnonzero(site_city == c)
        dens[idx] = g.hotspot_density(c, site_lat[idx], site_lon[idx], spots)
    zdens = z(np.log(wards["pop_density_per_km2"]))
    zinc = z(wards["income_index"])
    rate = (np.clip(dens, 0.2, 5) ** 0.6
            * np.exp(B_CRIME_DENS * zdens[site_ward] + B_CRIME_INCOME * zinc[site_ward])
            * rng.gamma(2.0, 0.5, g.N_LIGHT_SITES))
    rate = rate / rate.sum() * N_LOCAL_CRIMES_V2 / g.N_DAYS
    n_local = rng.poisson(rate * g.N_DAYS)
    site_idx = np.repeat(np.arange(g.N_LIGHT_SITES), n_local)
    m = len(site_idx)
    far = rng.random(m) < g.LOCAL_FAR_SHARE
    dist = np.where(far, rng.uniform(100, 300, m), np.abs(rng.normal(0, g.LOCAL_NEAR_SIGMA_M, m)))
    lat, lon = g.offset_points(rng, site_lat[site_idx], site_lon[site_idx], dist)
    ky = rng.choice(ky_codes, size=m, p=ky_p)
    d = rng.choice(g.N_DAYS, size=m, p=day_p)
    frames.append(g.make_crime_frame(rng, ky, timestamps(d, g.sample_hours(rng, ky)), lat, lon, "local"))

    # (c) Planted causal effect: extra NIGHT crime near dark poles (unchanged from v1).
    night_frac = g.night_fraction_of_mix(dict(zip(ky_codes, ky_p)))
    out = sl[sl["descriptor"] == "Street Light Out"]
    eff_codes = np.array(list(g.EFFECT_OFFENSE_MIX))
    eff_p = np.array(list(g.EFFECT_OFFENSE_MIX.values()))
    eff_p = eff_p / eff_p.sum()
    effect_rows = []
    active = rng.random(len(out)) < g.EFFECT_SHARE_OF_OUTAGES
    dens_fac = (wards["pop_density_per_km2"] / wards["pop_density_per_km2"].median()) ** EFFECT_DENSITY_EXPONENT
    dens_fac = (dens_fac / dens_fac.mean()).to_numpy()
    out_ward = site_ward[out["site_id"].to_numpy()]
    uplift = np.where(active, rng.gamma(2.0, EFFECT_MEAN_V2 / 2.0, len(out)) * dens_fac[out_ward], 0.0)
    truth_rows = []
    for (_, o), u, wd in zip(out.iterrows(), uplift, out_ward):
        if u == 0:
            truth_rows.append((o["unique_key"], wards.at[wd, "ward_id"], 0.0, 0.0))
            continue
        close = min(o["closed"], g.PERIOD_END)
        dur_days = (close - o["created"]).total_seconds() / 86400
        night_rate = rate[o["site_id"]] * night_frac
        k = rng.poisson(night_rate * u * dur_days)
        truth_rows.append((o["unique_key"], wards.at[wd, "ward_id"], float(u), float(night_rate * u * dur_days)))
        tail = g.EFFECT_AFTER_DECAY_DAYS * (1 - np.exp(-g.EFFECT_AFTER_MAX_DAYS / g.EFFECT_AFTER_DECAY_DAYS))
        k_after = rng.poisson(night_rate * u * tail) if o["closed"] <= g.PERIOD_END else 0
        for phase, kk in (("during", k), ("after", k_after)):
            for _ in range(kk):
                code = rng.choice(eff_codes, p=eff_p)
                hour = g.sample_hours(rng, np.array([code]), night_only=True)[0]
                for _try in range(20):
                    if phase == "during":
                        day0 = o["created"].normalize() + pd.Timedelta(days=int(rng.integers(0, int(dur_days) + 2)))
                    else:
                        lag = rng.exponential(g.EFFECT_AFTER_DECAY_DAYS)
                        if lag > g.EFFECT_AFTER_MAX_DAYS:
                            continue
                        day0 = (o["closed"] + pd.Timedelta(days=lag)).normalize()
                    t = day0 + pd.Timedelta(hours=int(hour), minutes=int(g.sample_minutes(rng, 1)[0]))
                    lo_, hi_ = (o["created"], close) if phase == "during" else (o["closed"], g.PERIOD_END)
                    if lo_ <= t <= hi_:
                        break
                else:
                    continue
                dd = np.abs(rng.normal(0, g.EFFECT_SIGMA_M, 1))
                la, lo2 = g.offset_points(rng, np.array([o["latitude"]]), np.array([o["longitude"]]), dd)
                effect_rows.append((code, t, la[0], lo2[0], f"effect_{phase}"))
    if effect_rows:
        frames.append(pd.DataFrame(effect_rows, columns=["ky_cd", "ts", "latitude", "longitude", "source"]))

    truth = pd.DataFrame(truth_rows, columns=["unique_key", "ward_id", "uplift_multiplier", "expected_extra_night_crimes_during"])
    cr = pd.concat(frames, ignore_index=True)
    cr = cr[(cr["ts"] >= g.PERIOD_START) & (cr["ts"] <= g.PERIOD_END)]
    cr = cr.sample(frac=1.0, random_state=rng.integers(0, 2**31)).sort_values("ts", kind="mergesort")
    cr = cr.reset_index(drop=True)
    cr["cmplnt_num"] = (300000000 + rng.choice(600000000, size=len(cr), replace=False)).astype(str)
    return cr, truth


# ---------------------------------------------------------------------------
# Validation of the ward layer (the pipeline-level checks come from the original validate())
# ---------------------------------------------------------------------------

def validate_wards(wards, sl, site_ward, log):
    checks = []

    def check(name, ok, detail=""):
        checks.append(bool(ok))
        log(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

    log("\nWard context table")
    check("ward_id unique", wards["ward_id"].is_unique, f"{len(wards)} wards")
    check("no missing values", not wards.isna().any().any())
    check("population density 300-40,000 /km2", wards["pop_density_per_km2"].between(300, 40000).all(),
          f"{wards['pop_density_per_km2'].min():.0f}-{wards['pop_density_per_km2'].max():.0f}")
    check("shares in [0, 1]", wards["vulnerable_pop_share"].between(0, 1).all())
    check("income index 0-100", wards["income_index"].between(0, 100).all())
    check("slope, distances non-negative", (wards[["slope_pct", "dist_main_road_km", "dist_depot_km"]] >= 0).all().all())
    check("all 7 cities have wards", wards["city"].nunique() == 7)

    # Does the intended causal structure show up in the data? (sanity of the generator, not of the model)
    per_ward = sl.assign(ward=site_ward[sl["site_id"].to_numpy()]).groupby("ward").size()
    w = wards.assign(outages=per_ward.reindex(wards.index).fillna(0))
    sites_per_ward = pd.Series(site_ward).value_counts().reindex(wards.index).fillna(0)
    w["outages_per_site"] = w["outages"] / sites_per_ward.replace(0, np.nan)
    ok = w.dropna(subset=["outages_per_site"])
    r_rain = np.corrcoef(np.log(ok["rainfall_mm_year"]), ok["outages_per_site"])[0, 1]
    r_age = np.corrcoef(ok["pole_age_years"], ok["outages_per_site"])[0, 1]
    check("outage rate rises with rainfall and pole age", r_rain > 0 and r_age > 0,
          f"corr(log rain, outages/site)={r_rain:.2f}, corr(pole age, outages/site)={r_age:.2f}")
    closed = sl[sl["status"] == "Closed"].copy()
    closed["dur_d"] = (closed["closed"] - closed["created"]).dt.total_seconds() / 86400
    wd = closed.assign(ward=site_ward[closed["site_id"].to_numpy()]).groupby("ward")["dur_d"].median()
    wd = pd.concat([wd, wards["dist_depot_km"]], axis=1, join="inner")
    r_dep = np.corrcoef(wd["dist_depot_km"], wd["dur_d"])[0, 1]
    check("repair delay rises with depot distance", r_dep > 0, f"corr={r_dep:.2f} (median days vs km)")
    return all(checks)


UPLIFT_CHECK = "night uplift moderate (1.05-1.8x) and larger than daytime"


def run_v1_validation(log):
    """Run the v1 checks. Its 1.05-1.8x uplift bound belongs to v1's weaker effect, so that one
    check is replaced by a v2 bound (1.3-4x, and larger than the daytime placebo)."""
    import re
    buf = []
    ok, per_city, _ = g.validate(DATA_DIR / "synthetic_streetlights.csv", DATA_DIR / "synthetic_crime.csv",
                                 lambda line="": buf.append(line))
    text = "\n".join(buf)
    m = re.search(r"naive night-crime rate.*?during/baseline = ([0-9.]+)", text, re.S)
    d = re.search(r"daytime \(placebo\).*?during/baseline = ([0-9.]+)", text, re.S)
    rr_n, rr_d = float(m.group(1)), float(d.group(1))
    other_fail = [ln for ln in buf if "[FAIL]" in ln and UPLIFT_CHECK not in ln]
    for ln in buf:
        if UPLIFT_CHECK in ln:
            v2_ok = 1.3 <= rr_n <= 4.0 and rr_n > rr_d
            ln = (f"  [{'PASS' if v2_ok else 'FAIL'}] v2 night uplift 1.3-4x and larger than daytime placebo "
                  f"-- night {rr_n:.2f}, day {rr_d:.2f}")
            if not v2_ok:
                other_fail.append(ln)
        log(ln)
    return (not other_fail), per_city


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)

    spots = g.build_hotspots(rng)
    wards = build_wards(rng, spots)
    sl, site_city, site_lat, site_lon, site_ward = generate_streetlights(rng, spots, wards)
    cr, truth = generate_crime(rng, spots, wards, sl, site_city, site_lat, site_lon, site_ward)

    sl_out, cr_out = g.format_streetlights(sl), g.format_crime(cr)
    paths = {
        "synthetic_streetlights.csv": sl_out, "synthetic_crime.csv": cr_out,
        "synthetic_wards.csv": wards.round({"latitude": 6, "longitude": 6, "sigma_km": 3,
                                            "hotspot_weight": 5, "dist_center_km": 2}),
    }
    for name, df in paths.items():
        df.to_csv(DATA_DIR / name, index=False, lineterminator="\n")

    # Ground truth for EVALUATING the causal estimates only. The pipeline never reads this file.
    truth["unique_key"] = truth["unique_key"].astype(str)
    (OUT_DIR / "ground_truth").mkdir(exist_ok=True)
    truth.round(5).to_csv(OUT_DIR / "ground_truth" / "outage_effect_truth.csv", index=False, lineterminator="\n")

    lines = []
    log = lambda s="": (print(s), lines.append(s))  # noqa: E731
    log("LightSafe synthetic Karnataka dataset v2 (ward context + confounding) - summary")
    log("=" * 70)
    log("SYNTHETIC DATA. NOT real Karnataka crime, census, weather or municipal records.")
    log(f"Seed: {args.seed}")
    log(f"Period: {g.PERIOD_START.date()} to {g.PERIOD_END.date()} ({g.N_DAYS} days)")
    log(f"Streetlight records: {len(sl_out)} (Street Light Out: {(sl_out['descriptor'] == 'Street Light Out').sum()})")
    log(f"Crime records: {len(cr_out)}   Wards: {len(wards)}")
    src = cr["source"].value_counts()
    log("Crime components (not stored in CSV): "
        + ", ".join(f"{k}={src.get(k, 0)}" for k in ("background", "local", "effect_during", "effect_after")))
    log("\nSHA-256")
    for name in paths:
        log(f"  {name}: {hashlib.sha256((DATA_DIR / name).read_bytes()).hexdigest()}")

    log("\nVALIDATION (pipeline-level checks reused from the v1 generator)")
    ok1, per_city = run_v1_validation(log)
    ok2 = validate_wards(wards, sl, site_ward, log)
    log("\nPer-city counts")
    log(per_city.to_string())
    log(f"\nOVERALL VALIDATION: {'PASS' if ok1 and ok2 else 'FAIL'}")
    (META_DIR / "dataset_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not (ok1 and ok2):
        raise SystemExit("validation failed")


if __name__ == "__main__":
    main()
