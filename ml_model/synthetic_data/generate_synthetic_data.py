"""Reproducible synthetic streetlight-complaint generator (external-validation data).

    python synthetic_data/generate_synthetic_data.py            (from ml_model/, seed 42)

The generator never loads or looks at the frozen model's predictions. The only use of the frozen
project code is the final compatibility check (feature engineering + a shape/NaN check of
predict_proba on a few hundred rows; no scores are stored or compared to labels).

Repair durations come from a mechanistic queue simulation (see README), not from a target flag.
"""
import argparse
import hashlib
import heapq
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
SEED = 42

SIM_START = pd.Timestamp("2023-10-01")      # 3-month burn-in (dropped from output)
OUT_START = pd.Timestamp("2024-01-01")
END = pd.Timestamp("2026-09-30")
SNAPSHOT = pd.Timestamp("2026-09-30 23:59:00")
TARGET_ROWS = 50_000
LABEL_HOURS = 168.0

BOROUGHS = ["BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"]
SHARE = np.array([0.18, 0.30, 0.22, 0.20, 0.10])             # NYC: Q .25 / B .23 / Bx .22 / M .14 / SI .10 (+5% blank)
N_DISTRICTS = [10, 16, 10, 12, 4]
ANCHOR = [(40.850, -73.880), (40.650, -73.950), (40.780, -73.970), (40.720, -73.820), (40.580, -74.150)]
SPREAD = [(0.040, 0.035), (0.060, 0.050), (0.060, 0.020), (0.060, 0.070), (0.030, 0.040)]
PRECINCT_RANGE = [(40, 52), (60, 94), (1, 34), (100, 115), (120, 123)]
COUNCIL_RANGE = [(8, 18), (33, 48), (1, 10), (19, 32), (49, 51)]
ZIP_RANGE = [(10451, 10475), (11201, 11239), (10001, 10040), (11101, 11436), (10301, 10314)]

# repair-process parameters per borough
KAPPA_BASE = np.array([1.10, 1.06, 1.30, 1.12, 1.22])       # capacity / mean standard workload
P_QUICK = np.array([0.22, 0.20, 0.34, 0.26, 0.30])          # share handled by quick lane
REGIMES = [("BROOKLYN", "2024-06-01", "2024-10-31", 0.80), ("BRONX", "2025-01-01", "2025-05-31", 0.78),
           ("QUEENS", "2025-08-01", "2026-01-31", 0.82), ("STATEN ISLAND", "2025-06-01", "2026-09-30", 1.25),
           ("MANHATTAN", "2026-03-01", "2026-06-30", 0.85), ("BRONX", "2026-06-01", "2026-09-30", 0.88)]
N_SITES = 15_500
HOUR_W = np.array([0.8] * 6 + [1.6, 2.2, 2.2, 2.0] + [1.5] * 7 + [2.2, 3.0, 3.6, 4.0, 3.2, 2.4, 1.6])
HOUR_P = HOUR_W / HOUR_W.sum()
DOW_ARR = np.array([1.0, 1.0, 1.0, 1.0, 1.0, 0.8, 0.7])
DOW_CAP = np.array([1.25] * 5 + [0.45, 0.30])               # mean = 1

WORDS = ("OAK MAPLE CEDAR ELM PINE BIRCH WILLOW HARBOR RIVER LAKE HILL VALLEY SUNSET MARKET CHURCH MILL "
         "BRIDGE GARDEN SPRING FOREST MEADOW CENTRAL UNION LIBERTY FRANKLIN JEFFERSON MONROE ADAMS CLAY "
         "LINCOLN GRANT HAMILTON MADISON WEBSTER CARROLL BAY CLIFF STONE ROSE LAUREL ASH BEACH DOCK "
         "FERRY QUARRY ORCHARD PRAIRIE SUMMIT RIDGE CANAL FOUNDRY TANNER COOPER SMITH BAKER MILLER "
         "TURNER WALKER HAYES REED PARKER KING QUEEN PRINCE DUKE NORTH SOUTH EAST WEST LINDEN HAZEL").split()
SUFFIX = ["AVENUE", "STREET", "ROAD", "BOULEVARD", "PLACE", "DRIVE", "LANE", "TERRACE"]
PLACE_SUFFIX = ["PARK", "PLAZA", "PLAYGROUND", "COMMONS"]


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


# ------------------------------------------------------------------------------------------ sites
def make_sites(rng):
    n_b = np.round(N_SITES * SHARE).astype(int)
    all_words = [f"{w} {s}" for w in WORDS for s in SUFFIX]
    sites, dist_rows = [], []
    gid = 0
    for b, bname in enumerate(BOROUGHS):
        nd = N_DISTRICTS[b]
        centers = np.array(ANCHOR[b]) + rng.normal(0, 1, (nd, 2)) * np.array(SPREAD[b])
        dw = rng.lognormal(0, 0.35, nd)
        for k in range(nd):
            zips = rng.integers(ZIP_RANGE[b][0], ZIP_RANGE[b][1] + 1, 2)
            dist_rows.append({"gid": gid + k, "b": b, "k": k + 1, "lat": centers[k, 0], "lon": centers[k, 1],
                              "precinct": int(rng.integers(PRECINCT_RANGE[b][0], PRECINCT_RANGE[b][1] + 1)),
                              "council": int(rng.integers(COUNCIL_RANGE[b][0], COUNCIL_RANGE[b][1] + 1)),
                              "zips": zips, "streets": list(rng.choice(all_words, 28, replace=False))})
        dprob = dw / dw.sum()
        d_of_site = rng.choice(nd, n_b[b], p=dprob)
        for j in range(n_b[b]):
            d = dist_rows[gid + d_of_site[j]]
            at = rng.choice(["INTERSECTION", "ADDRESS", "BLOCKFACE", "PLACENAME"], p=[0.64, 0.16, 0.14, 0.06])
            if at == "INTERSECTION":
                lt = rng.choice(["Intersection", "Highway", "Bridge / Tunnel", "Other"], p=[0.88, 0.06, 0.04, 0.02])
            elif at == "ADDRESS":
                lt = rng.choice(["Residential Address", "Other", "Misc."], p=[0.75, 0.2, 0.05])
            elif at == "BLOCKFACE":
                lt = rng.choice(["Residential Address", "Other", "Misc."], p=[0.7, 0.2, 0.1])
            else:
                lt = "Park"
            s1, s2 = rng.choice(d["streets"], 2, replace=False)
            rec = {"borough": bname, "b": b, "district": d["gid"], "address_type": at,
                   "location_type": "Location Type: " + lt,
                   "lat": round(d["lat"] + rng.normal(0, 0.010), 6), "lon": round(d["lon"] + rng.normal(0, 0.010), 6),
                   "community_board": f"{d['k']:02d} {bname}", "council_district": d["council"],
                   "police_precinct": f"Precinct {d['precinct']}", "zip": str(int(rng.choice(d["zips"]))),
                   "int1": None, "int2": None, "addr": None, "street": None, "cross1": None, "cross2": None}
            if at == "INTERSECTION":
                rec["int1"], rec["int2"] = s1, s2
            elif at == "ADDRESS":
                rec["street"] = s1
                rec["addr"] = f"{int(rng.integers(1, 999))} {s1}"
            elif at == "BLOCKFACE":
                rec["street"], rec["cross1"], rec["cross2"] = s1, s2, rng.choice(d["streets"])
            else:
                pn = f"{rng.choice(WORDS)} {rng.choice(PLACE_SUFFIX)}"
                rec["street"] = rec["addr"] = pn
                rec["int1"], rec["int2"] = s1, s2
            # frequency tier + latent fault severity
            u = rng.random()
            tier, wt = ("high", 12.0) if u < 0.03 else (("medium", 3.0) if u < 0.25 else ("low", 1.0))
            rec["tier_design"] = tier
            rec["w"] = wt * rng.lognormal(0, 0.5) * dw[d_of_site[j]]
            rec["sev"] = rng.normal(0.5 if tier == "high" else 0.0, 1.0)
            sites.append(rec)
        gid += nd
    sites = pd.DataFrame(sites)
    sites.insert(0, "site_id", np.arange(len(sites)))
    return sites, sum(N_DISTRICTS)


# ------------------------------------------------------------------------------------ arrivals
def daily_context(rng, days):
    nd = len(days)
    doy = days.dayofyear.values
    season = 1 + 0.30 * np.cos(2 * np.pi * (doy - 15) / 365.0)
    dow = DOW_ARR[days.dayofweek.values]
    yrs = (days - OUT_START).days.values / 365.0
    slope = np.array([0.03, 0.08, -0.05, 0.04, 0.0])
    shock = np.zeros((5, nd))
    for b in range(5):
        x = 0.0
        for d in range(nd):
            x = 0.85 * x + rng.normal(0, 0.12)
            shock[b, d] = x
    storm = np.ones(nd)
    cap_storm = np.ones(nd)
    d = 0
    while d < nd:
        if rng.random() < 0.015:
            ln = 1 + rng.poisson(1.0)
            m, cm = rng.uniform(1.5, 2.2), rng.uniform(0.45, 0.8)
            for e in range(d, min(nd, d + ln)):
                storm[e] = m
            for e in range(d, min(nd, d + ln + 2)):
                cap_storm[e] = cm
            d += ln
        d += 1
    lam = np.zeros((5, nd))
    for b in range(5):
        lam[b] = SHARE[b] * season * dow * (1 + slope[b] * yrs) * np.exp(shock[b]) * storm
    return lam, cap_storm


def generate_arrivals(rng, sites, n_dist_total, days):
    lam, cap_storm = daily_context(rng, days)
    out_mask = days >= OUT_START
    c = (TARGET_ROWS / 1.09) / lam[:, out_mask].sum()
    n_months = 72
    xdist = np.zeros((n_dist_total, n_months))
    for m in range(1, n_months):
        xdist[:, m] = 0.8 * xdist[:, m - 1] + rng.normal(0, 0.25, n_dist_total)
    site_by_b = [sites.index[sites.b == b].values for b in range(5)]
    base_w = sites["w"].values
    dist = sites["district"].values
    cum_cache = {}
    rows_t, rows_s = [], []
    for di, day in enumerate(days):
        mi = (day.year - 2023) * 12 + day.month - 10
        for b in range(5):
            n = rng.poisson(c * lam[b, di])
            if n == 0:
                continue
            key = (b, mi)
            if key not in cum_cache:
                ids = site_by_b[b]
                p = base_w[ids] * np.exp(xdist[dist[ids], mi])
                cum_cache[key] = np.cumsum(p / p.sum())
            ids = site_by_b[b]
            pick = ids[np.minimum(np.searchsorted(cum_cache[key], rng.random(n)), len(ids) - 1)]
            hr = rng.choice(24, n, p=HOUR_P)
            mn = rng.integers(0, 60, n)
            rows_t.append(day.value // 10**9 + hr * 3600 + mn * 60)
            rows_s.append(pick)
    t = np.concatenate(rows_t)
    s = np.concatenate(rows_s)
    prim = pd.DataFrame({"t": t, "site_id": s, "followon": 0})
    # follow-on complaints: re-reports / recurrences at the same site
    pf = 0.07 + 0.08 * (sites["tier_design"].values[prim.site_id.values] == "high")
    f = rng.random(len(prim)) < pf
    fol = prim[f].copy()
    dl_days = np.ceil((2 + rng.exponential(96, f.sum())) / 24)
    day0 = (fol.t.values // 86400) * 86400 + dl_days.astype(int) * 86400
    fol["t"] = day0 + rng.choice(24, f.sum(), p=HOUR_P) * 3600 + rng.integers(0, 60, f.sum()) * 60
    fol["followon"] = 1
    fol = fol[fol.t <= int(SNAPSHOT.value // 10**9)]
    allc = pd.concat([prim, fol], ignore_index=True).sort_values(["t", "site_id"], kind="stable").reset_index(drop=True)
    return allc, xdist, cap_storm, lam


# ------------------------------------------------------------------------------------- repairs
def simulate_repairs(rng, allc, sites, xdist, cap_storm, lam, days):
    N = len(allc)
    t0 = int(SIM_START.value // 10**9)
    created_h = (allc.t.values - t0) / 3600.0
    sid = allc.site_id.values
    bidx = sites.b.values[sid]
    dist = sites.district.values[sid]
    sev = sites.sev.values[sid]
    mi = ((pd.to_datetime(allc.t.values, unit="s").year - 2023) * 12
          + pd.to_datetime(allc.t.values, unit="s").month - 10).values
    # prior complaints at the same site within 90 days (generator-side latent driver of complexity)
    BIG = 10**10
    comb = sid.astype("int64") * BIG + allc.t.values
    srt = np.sort(comb)
    prior90 = (np.searchsorted(srt, comb, "left") - np.searchsorted(srt, comb - 90 * 86400, "left")).astype(float)
    p_cx = sigmoid(-2.3 + 0.55 * np.log1p(prior90) + 0.8 * sev + 0.6 * xdist[dist, mi])
    u_cx, u_q = rng.random(N), rng.random(N)
    cx = u_cx < p_cx
    quick_base = np.exp(np.log(16.0) + 0.85 * rng.normal(size=N))
    lag = np.exp(np.log(2.0) + 0.7 * rng.normal(size=N))
    prio_noise = rng.normal(0, 36.0, N)
    extra = np.exp(np.log(96.0) + 0.7 * rng.normal(size=N))
    svc_u1, svc_u2 = rng.random(N), rng.random(N)

    n_days_obs = len(days)
    n_ext = 600
    # capacity: regime x monthly AR noise x storms x weekday
    month_noise = np.zeros((5, 96))
    for m in range(1, 96):
        month_noise[:, m] = 0.7 * month_noise[:, m - 1] + rng.normal(0, 0.06, 5)
    all_days = pd.date_range(SIM_START, periods=n_days_obs + n_ext)
    kappa = np.zeros((5, len(all_days)))
    mon_idx = (all_days.year - 2023) * 12 + all_days.month - 10
    for b in range(5):
        k = np.full(len(all_days), KAPPA_BASE[b]) * np.exp(month_noise[b, mon_idx])
        for rb, a, z, mult in REGIMES:
            if rb == BOROUGHS[b]:
                k = np.where((all_days >= a) & (all_days <= pd.Timestamp(z)), k * mult, k)
        kappa[b] = k
    cap_s = np.ones(len(all_days))
    cap_s[:n_days_obs] = cap_storm
    out_mask = days >= OUT_START
    n_out_days = out_mask.sum()
    cnt_b = np.bincount(bidx, minlength=5)
    mean_arr = cnt_b / (len(days)) * 1.0        # per-day mean arrivals incl. burn-in days
    f_std = 1 - P_QUICK * 0.85
    C = mean_arr * f_std                         # baseline standard workload per day

    order_b = [np.where(bidx == b)[0] for b in range(5)]
    day_of = np.floor(created_h / 24).astype(int)
    heaps = [[] for _ in range(5)]
    dur = np.full(N, np.nan)
    ptr = [0] * 5
    for d in range(len(all_days)):
        dow = all_days[d].dayofweek
        for b in range(5):
            arr = order_b[b]
            ratio = len(heaps[b]) / max(C[b] * 7, 1e-9)
            while ptr[b] < len(arr) and day_of[arr[ptr[b]]] <= d:
                i = arr[ptr[b]]
                ptr[b] += 1
                if not cx[i] and u_q[i] < P_QUICK[b]:
                    dur[i] = quick_base[i] * np.exp(0.25 * min(ratio, 3.0)) + lag[i]
                else:
                    heapq.heappush(heaps[b], (created_h[i] + prio_noise[i], i))
            K = rng.poisson(max(C[b] * kappa[b, d] * DOW_CAP[dow] * cap_s[d], 0.0))
            for _ in range(min(K, len(heaps[b]))):
                _, i = heapq.heappop(heaps[b])
                svc = max(d * 24 + 8 + svc_u1[i] * 10, created_h[i] + 1 + svc_u2[i] * 5)
                dur[i] = svc + lag[i] + (extra[i] if cx[i] else 0.0) - created_h[i]
        if d >= n_days_obs and all(len(h) == 0 for h in heaps) and all(ptr[b] >= len(order_b[b]) for b in range(5)):
            break
    if np.isnan(dur).any():
        raise RuntimeError("unserved complaints remain; extend the simulation horizon")
    allc = allc.copy()
    allc["created"] = pd.to_datetime(allc.t.values, unit="s")
    closed = allc["created"] + pd.to_timedelta(dur, unit="h")
    allc["closed_true"] = closed.dt.ceil("min")
    allc["closed_true"] = np.maximum(allc["closed_true"], allc["created"] + pd.Timedelta(minutes=1))
    allc["repair_duration_hours"] = (allc["closed_true"] - allc["created"]).dt.total_seconds() / 3600
    allc["complex_repair"] = cx
    return allc


# --------------------------------------------------------------------------------------- assemble
def assemble(rng, c, sites):
    c = c[c.created >= OUT_START].reset_index(drop=True)
    n = len(c)
    s = sites.iloc[c.site_id.values].reset_index(drop=True)
    gt = pd.DataFrame({"complaint_id": 80_000_001 + np.arange(n), "created_date": c.created,
                       "closed_date": c.closed_true, "repair_duration_hours": c.repair_duration_hours.values,
                       "slow_repair": (c.repair_duration_hours.values > LABEL_HOURS).astype(int),
                       "true_borough": s.borough.values, "site_id": c.site_id.values,
                       "followon": c.followon.values, "complex_repair": c.complex_repair.values})
    age = (SNAPSHOT - gt.created_date).dt.total_seconds() / 3600
    closed_by_snap = gt.closed_date <= SNAPSHOT
    gt["closed_in_raw"] = closed_by_snap
    gt["outcome_observable"] = closed_by_snap | (age > LABEL_HOURS)
    gt["history_ready"] = gt.created_date >= OUT_START + pd.Timedelta(days=60)

    raw = pd.DataFrame({"complaint_id": gt.complaint_id, "created_date": gt.created_date,
                        "closed_date": gt.closed_date.where(closed_by_snap), "status": "Closed",
                        "borough": s.borough, "descriptor_2": s.location_type, "address_type": s.address_type,
                        "community_board": s.community_board, "council_district": s.council_district.astype(float),
                        "police_precinct": s.police_precinct, "incident_zip": s.zip,
                        "latitude": s.lat, "longitude": s.lon, "intersection_street_1": s.int1,
                        "intersection_street_2": s.int2, "incident_address": s.addr, "street_name": s.street,
                        "cross_street_1": s.cross1, "cross_street_2": s.cross2})
    opn = ~closed_by_snap.values
    raw.loc[opn, "status"] = np.where(rng.random(opn.sum()) < 0.10, "Assigned", "Open")

    # --- intentional missingness / quality issues (all MCAR except blockface coords) ---
    blk = (raw.address_type == "BLOCKFACE").values
    miss_xy = np.where(blk, rng.random(n) < 0.60, rng.random(n) < 0.04)
    raw.loc[miss_xy, ["latitude", "longitude"]] = np.nan
    cd_miss = miss_xy | (rng.random(n) < 0.02)
    raw.loc[cd_miss, "council_district"] = np.nan
    raw.loc[rng.random(n) < 0.06, "incident_zip"] = np.nan
    raw.loc[rng.random(n) < 0.03, "community_board"] = np.nan
    raw.loc[rng.random(n) < 0.012, "borough"] = np.nan
    slow_closed = (gt.slow_repair.values == 1) & closed_by_snap.values
    blank_closed = slow_closed & (rng.random(n) < 0.008)       # status Closed, closed_date not recorded
    raw.loc[blank_closed, "closed_date"] = pd.NaT
    open_with_date = (~slow_closed & closed_by_snap.values) & (rng.random(n) < 0.003)
    raw.loc[open_with_date, "status"] = "Open"                 # status says Open but closed_date present
    raw.loc[rng.random(n) < 0.010, "status"] = np.nan
    gt["closed_date_blanked"] = blank_closed
    # --- exact duplicate records (~0.5%) ---
    dup_idx = np.where(rng.random(n) < 0.005)[0]
    raw = pd.concat([raw, raw.iloc[dup_idx]], ignore_index=True).sort_values(
        ["created_date", "complaint_id"], kind="stable").reset_index(drop=True)
    return raw, gt


def write_csvs(raw, gt):
    (HERE / "data").mkdir(exist_ok=True)
    (HERE / "validation").mkdir(exist_ok=True)
    r, g = raw.copy(), gt.copy()
    for df, cols in [(r, ["created_date", "closed_date"]), (g, ["created_date", "closed_date"])]:
        for col in cols:
            df[col] = df[col].dt.strftime("%Y-%m-%dT%H:%M:%S")
    r["council_district"] = r["council_district"].astype("Int64")
    r.to_csv(HERE / "data/synthetic_streetlight_complaints.csv", index=False)
    g["repair_duration_hours"] = g["repair_duration_hours"].round(4)
    g.to_csv(HERE / "validation/ground_truth.csv", index=False)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --------------------------------------------------------------------------------------- validate
def validate():
    raw = pd.read_csv(HERE / "data/synthetic_streetlight_complaints.csv", parse_dates=["created_date", "closed_date"])
    gt = pd.read_csv(HERE / "validation/ground_truth.csv", parse_dates=["created_date", "closed_date"])
    rep, checks = {}, {}
    rep["rows"] = len(raw)
    rep["unique_complaint_ids"] = int(raw.complaint_id.nunique())
    rep["date_range"] = [str(raw.created_date.min()), str(raw.created_date.max())]
    rep["borough_distribution_pct"] = (raw.borough.fillna("(blank)").value_counts(normalize=True) * 100).round(2).to_dict()
    obs = gt[gt.outcome_observable]
    rep["slow_repair_rate_observable"] = round(float(obs.slow_repair.mean()), 4)
    rep["slow_repair_rate_observable_history_ready"] = round(float(obs[obs.history_ready].slow_repair.mean()), 4)
    d = gt.repair_duration_hours
    rep["repair_hours_all_latent"] = {"mean": round(d.mean(), 1), "median": round(d.median(), 1),
                                      **{f"p{q}": round(d.quantile(q / 100), 1) for q in [5, 25, 75, 90, 95, 99]}}
    dc = gt[gt.closed_in_raw].repair_duration_hours
    rep["repair_hours_observed_closed"] = {"mean": round(dc.mean(), 1), "median": round(dc.median(), 1),
                                           "p90": round(dc.quantile(.9), 1)}
    rep["unresolved_closed_date_blank_raw"] = int(raw.closed_date.isna().sum())
    rep["unresolved_true_at_snapshot_gt"] = int((~gt.closed_in_raw).sum())
    rep["missing_pct"] = (raw.isna().mean() * 100).round(2).to_dict()
    rep["duplicate_rows"] = int(raw.duplicated().sum())
    rep["status_counts"] = raw.status.fillna("(blank)").value_counts().to_dict()
    rep["location_type_pct"] = (raw.descriptor_2.value_counts(normalize=True) * 100).round(1).to_dict()
    rep["address_type_pct"] = (raw.address_type.value_counts(normalize=True) * 100).round(1).to_dict()
    cnt = gt.site_id.value_counts()
    rep["sites_with_complaints"] = int(len(cnt))
    rep["repeated_sites_ge2"] = int((cnt >= 2).sum())
    rep["site_tiers_by_realized_count"] = {"high_ge10": int((cnt >= 10).sum()), "medium_4_9": int(((cnt >= 4) & (cnt < 10)).sum()),
                                           "low_1_3": int((cnt < 4).sum())}
    rep["share_of_complaints_at_sites_ge4"] = round(float(cnt[cnt >= 4].sum() / cnt.sum()), 3)
    rep["max_complaints_one_site"] = int(cnt.max())
    gt["month"] = gt.created_date.dt.to_period("M").astype(str)
    mo = gt.groupby("month").agg(volume=("complaint_id", "size"))
    mo["slow_rate_observable"] = obs.assign(month=obs.created_date.dt.to_period("M").astype(str)).groupby("month").slow_repair.mean().round(3)
    rep["monthly"] = mo.reset_index().to_dict("records")
    ob = obs.assign(month=obs.created_date.dt.to_period("M").astype(str))
    bm = ob.groupby(["true_borough", "month"]).slow_repair.agg(["mean", "size"])
    bm = bm[bm["size"] >= 50]["mean"].groupby("true_borough").agg(["min", "max"]).round(3)
    rep["borough_slow_rate_overall"] = obs.groupby("true_borough").slow_repair.mean().round(3).to_dict()
    rep["borough_monthly_slow_rate_range"] = bm.to_dict("index")

    checks["all_5_boroughs_present"] = set(raw.borough.dropna()) == set(BOROUGHS)
    checks["dates_parse_and_in_range"] = bool(raw.created_date.between(OUT_START, END + pd.Timedelta(days=1)).all())
    cd = raw.dropna(subset=["closed_date"])
    checks["closed_not_before_created"] = bool((cd.closed_date >= cd.created_date).all())
    checks["duration_non_negative"] = bool((gt.repair_duration_hours >= 0).all())
    chk = gt[gt.closed_in_raw]
    calc = (chk.closed_date - chk.created_date).dt.total_seconds() / 3600
    checks["ground_truth_matches_dates"] = bool(np.allclose(calc, chk.repair_duration_hours, atol=1e-3)
                                                and ((chk.repair_duration_hours > LABEL_HOURS).astype(int) == chk.slow_repair).all())
    checks["every_borough_has_fast_and_slow"] = bool(all(
        0.02 < v < 0.98 for v in rep["borough_slow_rate_overall"].values()))
    checks["history_available"] = bool(gt.history_ready.mean() > 0.9)
    checks["no_outcome_columns_in_raw_except_closed_date_status"] = not ({"repair_duration_hours", "slow_repair"} & set(raw.columns))
    rep["checks"] = checks
    return rep, raw, gt


def compat_check(raw, gt):
    sys.path.insert(0, str(PROJECT))
    import joblib
    from features.build_features import FEATURES, add_labels, engineer_features, standardize
    schema = json.loads((PROJECT / "models/feature_schema.json").read_text())
    df = engineer_features(add_labels(standardize(raw)))
    out = {"schema_features_match_code": schema["features"] == FEATURES,
           "all_features_present": all(f in df.columns for f in FEATURES),
           "rows_after_feature_engineering": int(len(df)), "label_known_rows": int(df.label_known.sum()),
           "feature_missing_pct": (df[FEATURES].isna().mean() * 100).round(2).to_dict()}
    g = gt.drop_duplicates("complaint_id").set_index("complaint_id")
    k = df[df.label_known].drop_duplicates("complaint_id").set_index("complaint_id")
    j = k.join(g[["slow_repair", "outcome_observable"]], rsuffix="_gt")
    out["pipeline_label_vs_ground_truth_mismatches"] = int((j.slow_repair != j.slow_repair_gt).sum())
    out["pipeline_known_but_gt_unobservable"] = int((~j.outcome_observable).sum())
    unseen = {}
    ref = pd.read_pickle(PROJECT / "data/processed/nyc_features.pkl")
    for c in ["community_board", "police_precinct", "zip_code"]:
        sv, rv = set(df[c].dropna()), set(ref[c].dropna())
        unseen[c] = f"{len(sv - rv)} of {len(sv)} synthetic categories never seen in NYC"
    out["category_overlap_with_nyc"] = unseen
    model = joblib.load(PROJECT / "models/frozen_model.joblib")
    sample = df[FEATURES].iloc[:500]
    pr = model.predict_proba(sample)[:, 1]       # shape / finiteness check only; not stored or scored
    out["predict_proba_accepts_features"] = bool(pr.shape == (500,) and np.isfinite(pr).all() and ((pr >= 0) & (pr <= 1)).all())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-compat", action="store_true")
    a = ap.parse_args()
    ss = np.random.SeedSequence(SEED).spawn(5)
    r_sites, r_arr, r_rep, r_dq = [np.random.default_rng(s) for s in ss[:4]]
    sites, nd = make_sites(r_sites)
    days = pd.date_range(SIM_START, END)
    allc, xdist, cap_storm, lam = generate_arrivals(r_arr, sites, nd, days)
    allc = simulate_repairs(r_rep, allc, sites, xdist, cap_storm, lam, days)
    raw, gt = assemble(r_dq, allc, sites)
    write_csvs(raw, gt)
    rep, raw_r, gt_r = validate()
    if not a.skip_compat:
        rep["frozen_pipeline_compatibility"] = compat_check(raw_r, gt_r)
    rep["sha256"] = {"raw": sha(HERE / "data/synthetic_streetlight_complaints.csv"),
                     "ground_truth": sha(HERE / "validation/ground_truth.csv")}
    (HERE / "validation/validation_report.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps({k: v for k, v in rep.items() if k not in ("monthly",)}, indent=1, default=str))
    print("monthly:")
    print(pd.DataFrame(rep["monthly"]).to_string(index=False))


if __name__ == "__main__":
    main()
