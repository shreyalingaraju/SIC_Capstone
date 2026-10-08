"""
LightSafe - synthetic Karnataka streetlight-outage and crime data generator.

THIS DATA IS SYNTHETIC. It is not real Karnataka crime or municipal data and
must never be read as real crime statistics.

Produces (relative to this file):
    data/synthetic_streetlights.csv
    data/synthetic_crime.csv
    metadata/dataset_summary.txt     (counts, distributions, validation report)

Column names follow the raw NYC 311 / NYPD extracts used by the LightSafe
pipeline so the files can be swapped in with minimal changes.

Usage:
    python generate_synthetic_data.py            # default seed
    python generate_synthetic_data.py --seed 7   # alternative realisation

Requires: numpy, pandas, scipy (scipy is used only for validation).
Same seed + same library major versions -> byte-identical CSV output.
"""

import argparse
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SEED = 20250101

PERIOD_START = pd.Timestamp("2025-01-01 00:00:00")
PERIOD_END = pd.Timestamp("2025-12-31 23:59:59")
N_DAYS = (PERIOD_END.normalize() - PERIOD_START).days + 1  # 365

OUT_DIR = Path(__file__).resolve().parent
DATA_DIR = OUT_DIR / "data"
META_DIR = OUT_DIR / "metadata"

# City centre (lat, lon), urban radius in km, share of overall activity,
# median repair time in days, and number of neighbourhood hotspots.
CITIES = {
    "Bengaluru":  dict(lat=12.9716, lon=77.5946, radius_km=11.0, weight=0.30, repair_median_d=4.0, hotspots=40),
    "Mysuru":     dict(lat=12.3051, lon=76.6551, radius_km=5.5,  weight=0.16, repair_median_d=4.5, hotspots=20),
    "Mangaluru":  dict(lat=12.8853, lon=74.8560, radius_km=5.0,  weight=0.12, repair_median_d=5.0, hotspots=16),
    "Hubballi":   dict(lat=15.3550, lon=75.1360, radius_km=5.0,  weight=0.12, repair_median_d=5.5, hotspots=16),
    "Belagavi":   dict(lat=15.8560, lon=74.5100, radius_km=5.0,  weight=0.11, repair_median_d=5.5, hotspots=15),
    "Shivamogga": dict(lat=13.9299, lon=75.5681, radius_km=4.0,  weight=0.09, repair_median_d=6.0, hotspots=12),
    "Tumakuru":   dict(lat=13.3400, lon=77.1050, radius_km=4.0,  weight=0.10, repair_median_d=6.0, hotspots=12),
}
# Mangaluru sits on the Arabian Sea; nothing may be placed west of this.
MANGALURU_MIN_LON = 74.838

N_LIGHT_SITES = 4000            # distinct poles/locations that ever get a complaint
MEAN_COMPLAINTS_PER_SITE = 1.9  # gamma-Poisson -> some sites repeat, many once

# Crime volume
N_BACKGROUND_CRIMES = 15500     # city-wide crime not tied to any light site
N_LOCAL_CRIMES = 16000          # baseline crime within ~100-300 m of light sites

# Descriptor mix for 311-style complaints. Only "Street Light Out" darkens.
DESCRIPTORS = {
    "Street Light Out": 0.87,
    "Street Light Cycling": 0.05,
    "Street Light Dayburning": 0.04,
    "Street Light Dim": 0.02,
    "Lamppost Damaged": 0.02,
}

# NYPD-style offence codes (match LightSafe's KY_CD_CATEGORIES).
# ky_cd: (ofns_desc, law_cat_cd, crime_category, share of baseline crime)
OFFENSES = {
    341: ("PETIT LARCENY", "MISDEMEANOR", "PETIT LARCENY", 0.29),
    344: ("ASSAULT 3 & RELATED OFFENSES", "MISDEMEANOR", "ASSAULT", 0.15),
    109: ("GRAND LARCENY", "FELONY", "GRAND LARCENY", 0.15),
    107: ("BURGLARY", "FELONY", "BURGLARY", 0.12),
    105: ("ROBBERY", "FELONY", "ROBBERY", 0.10),
    106: ("FELONY ASSAULT", "FELONY", "ASSAULT", 0.07),
    351: ("CRIMINAL MISCHIEF & RELATED OF", "MISDEMEANOR", "MISCHIEF", 0.09),
    121: ("CRIMINAL MISCHIEF & RELATED OF", "FELONY", "MISCHIEF", 0.03),
}

# Hour-of-day profiles (24 relative weights, index = hour).
_H = np.arange(24)


def _bump(centre, width):
    d = np.minimum(np.abs(_H - centre), 24 - np.abs(_H - centre))
    return np.exp(-0.5 * (d / width) ** 2)


HOUR_PROFILES = {
    "street_night": 0.25 + 1.6 * _bump(22, 3.0) + 0.6 * _bump(1, 2.0),   # robbery
    "assault": 0.35 + 1.2 * _bump(21, 3.5) + 0.5 * _bump(1, 2.0),
    "larceny_day": 0.15 + 1.3 * _bump(15, 4.0) + 0.4 * _bump(20, 2.0),  # petit/grand larceny
    "burglary": 0.35 + 0.9 * _bump(11, 3.0) + 0.9 * _bump(2, 2.5),
    "mischief": 0.30 + 1.0 * _bump(23, 3.0) + 0.3 * _bump(14, 3.0),
}
PROFILE_FOR_KY = {105: "street_night", 106: "assault", 344: "assault",
                  107: "burglary", 109: "larceny_day", 341: "larceny_day",
                  121: "mischief", 351: "mischief"}

# Night = 19:00-05:59 local time (sunset ~18:30, sunrise ~06:15 in Karnataka).
NIGHT_HOURS = np.array([19, 20, 21, 22, 23, 0, 1, 2, 3, 4, 5])

# ---- Planted outage -> night crime effect --------------------------------
# Only "Street Light Out" complaints, only night hours, only near the pole.
EFFECT_SHARE_OF_OUTAGES = 0.65   # rest have zero effect (heterogeneity)
EFFECT_MEAN_IF_ACTIVE = 0.55     # mean multiplicative uplift on local night rate
EFFECT_AFTER_DECAY_DAYS = 2.0    # residual uplift fades after repair
EFFECT_AFTER_MAX_DAYS = 7.0
# Lighting-sensitive mix of the extra crimes (by ky_cd).
EFFECT_OFFENSE_MIX = {105: 0.28, 106: 0.08, 344: 0.17, 107: 0.14,
                      109: 0.15, 341: 0.10, 351: 0.06, 121: 0.02}

# Spatial spread of crimes around a light site (metres).
LOCAL_NEAR_SIGMA_M = 55.0        # half-normal: ~93% within 100 m
LOCAL_FAR_SHARE = 0.15           # remainder placed 100-300 m away
EFFECT_SIGMA_M = 45.0


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def km_to_deg(lat, dx_km, dy_km):
    dlat = dy_km / 110.574
    dlon = dx_km / (111.320 * np.cos(np.radians(lat)))
    return dlat, dlon


def offset_points(rng, lat, lon, dist_m):
    """Move points dist_m metres in a uniformly random direction."""
    theta = rng.uniform(0, 2 * np.pi, size=len(dist_m))
    dlat, dlon = km_to_deg(lat, dist_m / 1000 * np.cos(theta), dist_m / 1000 * np.sin(theta))
    return lat + dlat, lon + dlon


def valid_city_point(city, lat, lon):
    c = CITIES[city]
    dlat, dlon = km_to_deg(c["lat"], 1.0, 1.0)
    dist_km = np.hypot((lat - c["lat"]) / dlat, (lon - c["lon"]) / dlon)
    ok = dist_km <= 1.6 * c["radius_km"]
    if city == "Mangaluru":
        ok &= lon >= MANGALURU_MIN_LON
    return ok


def build_hotspots(rng):
    """Neighbourhood centres per city: location, spread and relative weight."""
    spots = {}
    for city, c in CITIES.items():
        n = c["hotspots"]
        lat = np.empty(0)
        lon = np.empty(0)
        while len(lat) < n:
            r = np.abs(rng.normal(0, 0.55 * c["radius_km"], 4 * n))
            th = rng.uniform(0, 2 * np.pi, 4 * n)
            dlat, dlon = km_to_deg(c["lat"], r * np.cos(th), r * np.sin(th))
            la, lo = c["lat"] + dlat, c["lon"] + dlon
            keep = valid_city_point(city, la, lo)
            lat = np.concatenate([lat, la[keep]])
            lon = np.concatenate([lon, lo[keep]])
        spots[city] = dict(
            lat=lat[:n], lon=lon[:n],
            sigma_km=rng.uniform(0.35, 1.1, n) * (c["radius_km"] / 6) ** 0.5,
            weight=rng.lognormal(0, 0.7, n),
        )
        spots[city]["weight"] /= spots[city]["weight"].sum()
    return spots


def sample_city_points(rng, city, n, spots, diffuse_share):
    """Points from the city's hotspot mixture plus a diffuse urban component."""
    c = CITIES[city]
    s = spots[city]
    out_lat, out_lon = [], []
    need = n
    while need > 0:
        m = int(need * 1.4) + 10
        diffuse = rng.random(m) < diffuse_share
        k = rng.choice(len(s["weight"]), size=m, p=s["weight"])
        sig = np.where(diffuse, 0.6 * c["radius_km"], s["sigma_km"][k])
        cx = np.where(diffuse, c["lat"], s["lat"][k])
        cy = np.where(diffuse, c["lon"], s["lon"][k])
        dx, dy = rng.normal(0, 1, m) * sig, rng.normal(0, 1, m) * sig
        dlat, dlon = km_to_deg(c["lat"], dx, dy)
        la, lo = cx + dlat, cy + dlon
        keep = valid_city_point(city, la, lo)
        out_lat.append(la[keep])
        out_lon.append(lo[keep])
        need -= keep.sum()
    return np.concatenate(out_lat)[:n], np.concatenate(out_lon)[:n]


def hotspot_density(city, lat, lon, spots):
    """Relative crime intensity at a point (used to make site crime rates vary)."""
    c = CITIES[city]
    s = spots[city]
    dlat, dlon = km_to_deg(c["lat"], 1.0, 1.0)
    dx = (lat[:, None] - s["lat"][None, :]) / dlat
    dy = (lon[:, None] - s["lon"][None, :]) / dlon
    d2 = (dx ** 2 + dy ** 2) / s["sigma_km"][None, :] ** 2
    dens = (s["weight"][None, :] / s["sigma_km"][None, :] ** 2 * np.exp(-0.5 * d2)).sum(1)
    return dens / dens.mean()


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def day_weights():
    """Daily relative crime level: mild seasonality + weekday pattern."""
    days = pd.date_range(PERIOD_START, periods=N_DAYS, freq="D")
    doy = days.dayofyear.to_numpy()
    season = 1 + 0.07 * np.cos(2 * np.pi * (doy - 300) / 365)       # peak ~late Oct (festivals)
    monsoon = 1 - 0.05 * ((days.month >= 6) & (days.month <= 8))
    dow = np.array([0.97, 0.96, 0.97, 0.99, 1.05, 1.08, 1.0])[days.dayofweek]
    w = season * monsoon * dow
    return days, w / w.sum()


def sample_minutes(rng, n):
    """Minutes with mild heaping at :00/:30, as in real complaint logs."""
    m = rng.integers(0, 60, n)
    heap = rng.random(n)
    m = np.where(heap < 0.18, 0, np.where(heap < 0.27, 30, m))
    return m


def sample_hours(rng, ky, night_only=False):
    hours = np.empty(len(ky), dtype=int)
    for code in np.unique(ky):
        idx = np.flatnonzero(ky == code)
        prof = HOUR_PROFILES[PROFILE_FOR_KY[code]].copy()
        if night_only:
            mask = np.zeros(24, bool)
            mask[NIGHT_HOURS] = True
            prof = prof * mask
        hours[idx] = rng.choice(24, size=len(idx), p=prof / prof.sum())
    return hours


def is_night(hours):
    return np.isin(hours, NIGHT_HOURS)


def night_fraction_of_mix(mix):
    """Share of baseline crime falling in night hours for a given offence mix."""
    tot = 0.0
    for code, share in mix.items():
        p = HOUR_PROFILES[PROFILE_FOR_KY[code]]
        tot += share * p[NIGHT_HOURS].sum() / p.sum()
    return tot / sum(mix.values())


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def generate_streetlights(rng, spots):
    names = list(CITIES)
    wts = np.array([CITIES[c]["weight"] for c in names])
    site_city = rng.choice(names, size=N_LIGHT_SITES, p=wts / wts.sum())

    site_lat = np.empty(N_LIGHT_SITES)
    site_lon = np.empty(N_LIGHT_SITES)
    for city in names:
        idx = np.flatnonzero(site_city == city)
        site_lat[idx], site_lon[idx] = sample_city_points(rng, city, len(idx), spots, diffuse_share=0.25)

    # Complaint-generating propensity varies by site (old fittings fail more).
    propensity = rng.gamma(1.4, MEAN_COMPLAINTS_PER_SITE / 1.4, N_LIGHT_SITES)

    # Monsoon (Jun-Sep) wiring faults: ~30% more complaints.
    days = pd.date_range(PERIOD_START, periods=N_DAYS, freq="D")
    day_p = np.where((days.month >= 6) & (days.month <= 9), 1.3, 1.0)
    day_p = day_p / day_p.sum()

    # Complaint creation hour: people notice outages in the evening and report
    # them that night or next morning.
    create_hour_p = 0.15 + 1.4 * _bump(20, 2.5) + 0.9 * _bump(9, 2.0)
    create_hour_p /= create_hour_p.sum()

    rows = []
    for s in range(N_LIGHT_SITES):
        n = rng.poisson(propensity[s])
        if n == 0:
            continue
        city = site_city[s]
        start_days = np.sort(rng.choice(N_DAYS, size=n, p=day_p))
        last_close = PERIOD_START - pd.Timedelta(days=1)
        for d in start_days:
            created = (PERIOD_START + pd.Timedelta(days=int(d))
                       + pd.Timedelta(hours=int(rng.choice(24, p=create_hour_p)))
                       + pd.Timedelta(minutes=int(rng.integers(0, 60))))
            if created <= last_close:      # still open from the previous complaint
                continue
            descriptor = rng.choice(list(DESCRIPTORS), p=list(DESCRIPTORS.values()))
            med = CITIES[city]["repair_median_d"]
            dur_d = rng.lognormal(np.log(med), 0.85)
            if rng.random() < 0.04:        # backlog / cable faults
                dur_d += rng.uniform(15, 60)
            dur_d = float(np.clip(dur_d, 2 / 24, 90))
            closed = created + pd.Timedelta(days=dur_d)
            # Crews close jobs in working hours (08:00-18:59).
            if closed.hour < 8 or closed.hour > 18:
                base = closed.normalize() + (pd.Timedelta(days=1) if closed.hour > 18 else pd.Timedelta(0))
                closed = base + pd.Timedelta(hours=int(rng.integers(8, 19)), minutes=int(rng.integers(0, 60)))
            # Tiny GPS jitter between repeat complaints at the same pole.
            jlat, jlon = offset_points(rng, np.array([site_lat[s]]), np.array([site_lon[s]]),
                                       np.abs(rng.normal(0, 6, 1)))
            rows.append(dict(site_id=s, borough=city, descriptor=descriptor,
                             created=created, closed=closed,
                             latitude=jlat[0], longitude=jlon[0]))
            last_close = closed

    sl = pd.DataFrame(rows)
    open_now = sl["closed"] > PERIOD_END
    sl["status"] = np.where(open_now,
                            rng.choice(["Open", "In Progress"], size=len(sl), p=[0.6, 0.4]),
                            "Closed")
    sl = sl.sort_values(["created", "site_id"], kind="mergesort").reset_index(drop=True)
    sl["unique_key"] = 61000000 + np.sort(rng.choice(900000, size=len(sl), replace=False))
    return sl, site_city, site_lat, site_lon


def make_crime_frame(rng, ky, ts, lat, lon, source):
    return pd.DataFrame(dict(ky_cd=ky, ts=ts, latitude=lat, longitude=lon, source=source))


def generate_crime(rng, spots, sl, site_city, site_lat, site_lon):
    names = list(CITIES)
    days, day_p = day_weights()
    ky_codes = np.array(list(OFFENSES))
    ky_p = np.array([OFFENSES[k][3] for k in ky_codes])
    ky_p = ky_p / ky_p.sum()
    frames = []

    def timestamps(day_idx, hours):
        mins = sample_minutes(rng, len(day_idx))
        secs = np.where(rng.random(len(day_idx)) < 0.85, 0, rng.integers(0, 60, len(day_idx)))
        return (days[day_idx] + pd.to_timedelta(hours, "h") + pd.to_timedelta(mins, "m")
                + pd.to_timedelta(secs, "s"))

    # (a) Background city-wide crime ------------------------------------
    wts = np.array([CITIES[c]["weight"] for c in names])
    city = rng.choice(names, size=N_BACKGROUND_CRIMES, p=wts / wts.sum())
    lat = np.empty(N_BACKGROUND_CRIMES)
    lon = np.empty(N_BACKGROUND_CRIMES)
    for c in names:
        idx = np.flatnonzero(city == c)
        lat[idx], lon[idx] = sample_city_points(rng, c, len(idx), spots, diffuse_share=0.35)
    ky = rng.choice(ky_codes, size=N_BACKGROUND_CRIMES, p=ky_p)
    d = rng.choice(N_DAYS, size=N_BACKGROUND_CRIMES, p=day_p)
    frames.append(make_crime_frame(rng, ky, timestamps(d, sample_hours(rng, ky)), lat, lon, "background"))

    # (b) Baseline crime around light sites ------------------------------
    # Every site (whether or not it ever fails) has a local rate that depends
    # on how busy its neighbourhood is, with extra site-level noise.
    dens = np.empty(N_LIGHT_SITES)
    for c in names:
        idx = np.flatnonzero(site_city == c)
        dens[idx] = hotspot_density(c, site_lat[idx], site_lon[idx], spots)
    rate = np.clip(dens, 0.2, 5) ** 0.6 * rng.gamma(2.0, 0.5, N_LIGHT_SITES)
    rate = rate / rate.sum() * N_LOCAL_CRIMES / N_DAYS          # crimes/day/site
    n_local = rng.poisson(rate * N_DAYS)
    site_idx = np.repeat(np.arange(N_LIGHT_SITES), n_local)
    m = len(site_idx)
    far = rng.random(m) < LOCAL_FAR_SHARE
    dist = np.where(far, rng.uniform(100, 300, m), np.abs(rng.normal(0, LOCAL_NEAR_SIGMA_M, m)))
    lat, lon = offset_points(rng, site_lat[site_idx], site_lon[site_idx], dist)
    ky = rng.choice(ky_codes, size=m, p=ky_p)
    d = rng.choice(N_DAYS, size=m, p=day_p)
    frames.append(make_crime_frame(rng, ky, timestamps(d, sample_hours(rng, ky)), lat, lon, "local"))

    # (c) Planted effect: extra NIGHT crime near dark poles ----------------
    night_frac = night_fraction_of_mix(dict(zip(ky_codes, ky_p)))
    out = sl[sl["descriptor"] == "Street Light Out"]
    eff_codes = np.array(list(EFFECT_OFFENSE_MIX))
    eff_p = np.array(list(EFFECT_OFFENSE_MIX.values()))
    eff_p = eff_p / eff_p.sum()
    effect_rows = []
    active = rng.random(len(out)) < EFFECT_SHARE_OF_OUTAGES
    uplift = np.where(active, rng.gamma(2.0, EFFECT_MEAN_IF_ACTIVE / 2.0, len(out)), 0.0)
    for (_, o), u in zip(out.iterrows(), uplift):
        if u == 0:
            continue
        close = min(o["closed"], PERIOD_END)
        dur_days = (close - o["created"]).total_seconds() / 86400
        night_rate = rate[o["site_id"]] * night_frac      # baseline local night crimes/day
        # during the outage
        k = rng.poisson(night_rate * u * dur_days)
        # residual after repair, decaying
        tail = EFFECT_AFTER_DECAY_DAYS * (1 - np.exp(-EFFECT_AFTER_MAX_DAYS / EFFECT_AFTER_DECAY_DAYS))
        k_after = rng.poisson(night_rate * u * tail) if o["closed"] <= PERIOD_END else 0
        for phase, kk in (("during", k), ("after", k_after)):
            for _ in range(kk):
                code = rng.choice(eff_codes, p=eff_p)
                hour = sample_hours(rng, np.array([code]), night_only=True)[0]
                for _try in range(20):
                    if phase == "during":
                        day0 = o["created"].normalize() + pd.Timedelta(days=int(rng.integers(0, int(dur_days) + 2)))
                    else:
                        lag = rng.exponential(EFFECT_AFTER_DECAY_DAYS)
                        if lag > EFFECT_AFTER_MAX_DAYS:
                            continue
                        day0 = (o["closed"] + pd.Timedelta(days=lag)).normalize()
                    t = day0 + pd.Timedelta(hours=int(hour), minutes=int(sample_minutes(rng, 1)[0]))
                    lo_, hi_ = (o["created"], close) if phase == "during" else (o["closed"], PERIOD_END)
                    if lo_ <= t <= hi_:
                        break
                else:
                    continue
                dd = np.abs(rng.normal(0, EFFECT_SIGMA_M, 1))
                la, lo2 = offset_points(rng, np.array([o["latitude"]]), np.array([o["longitude"]]), dd)
                effect_rows.append((code, t, la[0], lo2[0], f"effect_{phase}"))
    if effect_rows:
        e = pd.DataFrame(effect_rows, columns=["ky_cd", "ts", "latitude", "longitude", "source"])
        frames.append(e)

    cr = pd.concat(frames, ignore_index=True)
    cr = cr[(cr["ts"] >= PERIOD_START) & (cr["ts"] <= PERIOD_END)]
    # Shuffle before sorting so same-second ties do not reveal the source.
    cr = cr.sample(frac=1.0, random_state=rng.integers(0, 2**31)).sort_values("ts", kind="mergesort")
    cr = cr.reset_index(drop=True)
    cr["cmplnt_num"] = (300000000 + rng.choice(600000000, size=len(cr), replace=False)).astype(str)
    return cr


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_streetlights(sl):
    fmt = "%Y-%m-%dT%H:%M:%S.000"
    out = pd.DataFrame({
        "unique_key": sl["unique_key"].astype(str),
        "created_date": sl["created"].dt.strftime(fmt),
        "closed_date": np.where(sl["status"] == "Closed", sl["closed"].dt.strftime(fmt), ""),
        "descriptor": sl["descriptor"],
        "status": sl["status"],
        "borough": sl["borough"],
        "latitude": sl["latitude"].round(6),
        "longitude": sl["longitude"].round(6),
    })
    return out


def format_crime(cr):
    out = pd.DataFrame({
        "cmplnt_num": cr["cmplnt_num"],
        "cmplnt_fr_dt": cr["ts"].dt.strftime("%Y-%m-%d"),
        "cmplnt_fr_tm": cr["ts"].dt.strftime("%H:%M:%S"),
        "ky_cd": cr["ky_cd"].astype(int),
        "ofns_desc": cr["ky_cd"].map(lambda k: OFFENSES[k][0]),
        "law_cat_cd": cr["ky_cd"].map(lambda k: OFFENSES[k][1]),
        "crime_category": cr["ky_cd"].map(lambda k: OFFENSES[k][2]),
        "latitude": cr["latitude"].round(6),
        "longitude": cr["longitude"].round(6),
    })
    return out


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def nearest_city(lat, lon):
    names = list(CITIES)
    c_lat = np.array([CITIES[c]["lat"] for c in names])
    c_lon = np.array([CITIES[c]["lon"] for c in names])
    d = (lat[:, None] - c_lat) ** 2 + ((lon[:, None] - c_lon) * np.cos(np.radians(14))) ** 2
    return np.array(names)[d.argmin(1)]


def to_metres(lat, lon):
    """Equirectangular projection around Karnataka (error < 0.5% at 100 m scale)."""
    lat0 = 14.0
    return np.column_stack([lon * 111320 * np.cos(np.radians(lat0)), lat * 110574])


def validate(sl_csv, cr_csv, log):
    from scipy.spatial import cKDTree

    checks = []

    def check(name, ok, detail=""):
        checks.append(ok)
        log(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

    sl = pd.read_csv(sl_csv, dtype={"unique_key": str})
    cr = pd.read_csv(cr_csv, dtype={"cmplnt_num": str})

    log("\nStreetlight dataset")
    req = ["created_date", "closed_date", "latitude", "longitude", "status", "descriptor"]
    check("required columns present", all(c in sl.columns for c in req), ", ".join(req))
    created = pd.to_datetime(sl["created_date"], errors="coerce")
    closed = pd.to_datetime(sl["closed_date"], errors="coerce")
    check("all created_date parse", created.notna().all())
    is_closed = sl["status"] == "Closed"
    check("closed_date parses for every Closed record", closed[is_closed].notna().all())
    check("open records have no closed_date", closed[~is_closed].isna().all(),
          f"{(~is_closed).sum()} open/in-progress at period end")
    check("closed_date >= created_date", (closed[is_closed] >= created[is_closed]).all())
    check("unique_key unique", sl["unique_key"].is_unique)
    check("coordinates inside Karnataka bbox",
          sl["latitude"].between(11.5, 18.5).all() and sl["longitude"].between(74.0, 78.6).all())
    check("borough matches nearest city centre",
          (nearest_city(sl["latitude"].to_numpy(), sl["longitude"].to_numpy()) == sl["borough"]).all())
    check("all 7 cities represented", sl["borough"].nunique() == 7)
    out_mask = sl["descriptor"] == "Street Light Out"
    check("'Street Light Out' records exist", out_mask.sum() > 0, f"{out_mask.sum()} records")
    dur_h = (closed - created).dt.total_seconds() / 3600
    dq = dur_h[is_closed].quantile([0.0, 0.25, 0.5, 0.75, 0.95, 1.0])
    check("outage durations reasonable (2 h - 120 d, not constant)",
          dq.iloc[0] >= 1.9 and dq.iloc[-1] <= 24 * 120 and dur_h[is_closed].nunique() > 1000,
          "hours p0/p25/p50/p75/p95/max = " + "/".join(f"{v:.1f}" for v in dq))

    log("\nCrime dataset")
    req = ["cmplnt_fr_dt", "cmplnt_fr_tm", "latitude", "longitude", "ofns_desc", "crime_category"]
    check("required columns present", all(c in cr.columns for c in req), ", ".join(req))
    ts = pd.to_datetime(cr["cmplnt_fr_dt"] + " " + cr["cmplnt_fr_tm"], errors="coerce")
    check("all date+time parse", ts.notna().all())
    check("dates within period", ts.between(PERIOD_START, PERIOD_END).all())
    check("cmplnt_num unique", cr["cmplnt_num"].is_unique)
    check("coordinates inside Karnataka bbox",
          cr["latitude"].between(11.5, 18.5).all() and cr["longitude"].between(74.0, 78.6).all())
    cr_city = nearest_city(cr["latitude"].to_numpy(), cr["longitude"].to_numpy())
    check("all 7 cities represented", len(set(cr_city)) == 7)
    cats = set(cr["crime_category"])
    check("required categories present", {"ROBBERY", "BURGLARY", "ASSAULT", "GRAND LARCENY"} <= cats,
          ", ".join(sorted(cats)))
    night = is_night(ts.dt.hour.to_numpy())
    check("night-time crimes exist", night.sum() > 0, f"{night.mean():.1%} at 19:00-05:59")

    log("\nCross-dataset")
    sl_xy = to_metres(sl["latitude"].to_numpy(), sl["longitude"].to_numpy())
    cr_xy = to_metres(cr["latitude"].to_numpy(), cr["longitude"].to_numpy())
    out_xy = sl_xy[out_mask.to_numpy()]
    d_nn, _ = cKDTree(out_xy).query(cr_xy)
    bands = [(0, 100), (100, 250), (250, 1000), (1000, np.inf)]
    band_txt = ", ".join(f"{lo}-{hi if np.isfinite(hi) else 'inf'} m: {((d_nn >= lo) & (d_nn < hi)).mean():.1%}"
                         for lo, hi in bands)
    check("some crimes within 100 m of an outage location", (d_nn < 100).mean() > 0.05, band_txt)
    check("not all crimes near outages", (d_nn >= 250).mean() > 0.2)

    # Before / during / after night-crime rates around each closed outage.
    tree = cKDTree(cr_xy)
    o = sl[out_mask & is_closed].copy()
    o["created"], o["closed"] = created[o.index], closed[o.index]
    o = o[(o["created"] >= PERIOD_START + pd.Timedelta(days=14))
          & (o["closed"] <= PERIOD_END - pd.Timedelta(days=21))]
    neigh = tree.query_ball_point(to_metres(o["latitude"].to_numpy(), o["longitude"].to_numpy()), r=100)
    t_ns = ts.to_numpy().astype("datetime64[ns]").astype(np.int64)
    hrs = ts.dt.hour.to_numpy()
    day_ns = 86400 * 10**9
    tot = {k: [0, 0.0] for k in ("pre_night", "dur_night", "post_night", "pre_day", "dur_day", "post_day")}
    has = {"pre": 0, "dur": 0, "post": 0}
    for (c0, c1), nb in zip(zip(o["created"].to_numpy().astype("datetime64[ns]").astype(np.int64),
                                 o["closed"].to_numpy().astype("datetime64[ns]").astype(np.int64)), neigh):
        nb = np.asarray(nb, dtype=int)
        tt, nn = t_ns[nb], is_night(hrs[nb])
        windows = {"pre": (c0 - 14 * day_ns, c0), "dur": (c0, c1), "post": (c1 + 7 * day_ns, c1 + 21 * day_ns)}
        for w, (a, b) in windows.items():
            inw = (tt >= a) & (tt < b)
            days_w = (b - a) / day_ns
            tot[f"{w}_night"][0] += (inw & nn).sum()
            tot[f"{w}_day"][0] += (inw & ~nn).sum()
            tot[f"{w}_night"][1] += days_w
            tot[f"{w}_day"][1] += days_w
            has[w] += int(inw.any())
    rate = {k: v[0] / v[1] * 1000 for k, v in tot.items()}   # per 1000 site-days
    check("outage periods contain nearby crime observations", tot["dur_night"][0] > 0,
          f"{tot['dur_night'][0]} night crimes within 100 m during outages")
    check("pre-outage and post-repair observations exist", tot["pre_night"][0] > 0 and tot["post_night"][0] > 0,
          f"pre={tot['pre_night'][0]}, post={tot['post_night'][0]} night crimes")
    rr_n = rate["dur_night"] / ((rate["pre_night"] + rate["post_night"]) / 2)
    rr_d = rate["dur_day"] / ((rate["pre_day"] + rate["post_day"]) / 2)
    log(f"     naive night-crime rate within 100 m, per 1000 site-days (n={len(o)} outages):")
    log(f"       pre (14 d before) = {rate['pre_night']:.1f}, during = {rate['dur_night']:.1f}, "
        f"post (days 7-21 after repair) = {rate['post_night']:.1f}  -> during/baseline = {rr_n:.2f}")
    log(f"     same for daytime (placebo): pre = {rate['pre_day']:.1f}, during = {rate['dur_day']:.1f}, "
        f"post = {rate['post_day']:.1f}  -> during/baseline = {rr_d:.2f}")
    check("night uplift moderate (1.05-1.8x) and larger than daytime", 1.05 <= rr_n <= 1.8 and rr_n > rr_d)

    per_city = pd.DataFrame({
        "streetlight_records": sl["borough"].value_counts(),
        "street_light_out": sl.loc[out_mask, "borough"].value_counts(),
        "crime_records": pd.Series(cr_city).value_counts(),
    }).reindex(list(CITIES)).fillna(0).astype(int)
    check("every city has >= 200 outages and >= 1000 crimes",
          (per_city["street_light_out"] >= 200).all() and (per_city["crime_records"] >= 1000).all())

    return all(checks), per_city, cr_city


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    DATA_DIR.mkdir(exist_ok=True)
    META_DIR.mkdir(exist_ok=True)

    spots = build_hotspots(rng)
    sl, site_city, site_lat, site_lon = generate_streetlights(rng, spots)
    cr = generate_crime(rng, spots, sl, site_city, site_lat, site_lon)

    sl_out, cr_out = format_streetlights(sl), format_crime(cr)
    sl_path, cr_path = DATA_DIR / "synthetic_streetlights.csv", DATA_DIR / "synthetic_crime.csv"
    sl_out.to_csv(sl_path, index=False, lineterminator="\n")
    cr_out.to_csv(cr_path, index=False, lineterminator="\n")

    lines = []
    log = lambda s="": (print(s), lines.append(s))  # noqa: E731

    log("LightSafe synthetic Karnataka dataset - summary")
    log("=" * 60)
    log("SYNTHETIC DATA. NOT real Karnataka crime or municipal records.")
    log(f"Seed: {args.seed}")
    log(f"Period: {PERIOD_START.date()} to {PERIOD_END.date()} ({N_DAYS} days)")
    log(f"Streetlight records: {len(sl_out)}  (Street Light Out: {(sl_out['descriptor'] == 'Street Light Out').sum()})")
    log(f"Crime records: {len(cr_out)}")
    src = cr["source"].value_counts()
    log("Crime generation components (not stored in the CSV): "
        + ", ".join(f"{k}={src.get(k, 0)}" for k in ("background", "local", "effect_during", "effect_after")))
    log("\nSHA-256")
    for p in (sl_path, cr_path):
        log(f"  {p.name}: {hashlib.sha256(p.read_bytes()).hexdigest()}")

    log("\nVALIDATION")
    ok, per_city, cr_city = validate(sl_path, cr_path, log)

    log("\nPer-city counts")
    log(per_city.to_string())
    log("\nStreetlight descriptor counts")
    log(sl_out["descriptor"].value_counts().to_string())
    log("\nStreetlight status counts")
    log(sl_out["status"].value_counts().to_string())
    log("\nCrime category counts")
    log(cr_out["crime_category"].value_counts().to_string())
    log("\nCrime offence (ky_cd / ofns_desc / law_cat_cd) counts")
    log(cr_out.groupby(["ky_cd", "ofns_desc", "law_cat_cd"]).size().to_string())
    hours = pd.to_datetime(cr_out["cmplnt_fr_tm"], format="%H:%M:%S").dt.hour
    log("\nCrime count by hour of day")
    log(hours.value_counts().sort_index().to_string())
    log(f"\nOVERALL VALIDATION: {'PASS' if ok else 'FAIL'}")

    (META_DIR / "dataset_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not ok:
        raise SystemExit("validation failed")


if __name__ == "__main__":
    main()
