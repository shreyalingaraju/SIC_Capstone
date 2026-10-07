"""Label construction and leakage-safe feature engineering.

Works on any dataframe that follows the canonical schema below (NYC 311 raw
columns). The same code must be used for the synthetic dataset.

Everything is computed using only information available when a complaint is
created. History-based features use strictly earlier timestamps (location
repeats) or state as of 00:00 of the creation day (borough backlog / closure
speed), so no same-day or future information is used.
"""
import numpy as np
import pandas as pd

LABEL_HOURS = 168.0          # "slow repair" = not resolved within 7 days
BACKLOG_DAYS = 60
CLOSURE_DAYS = 14
VOLUME_DAYS = 7

REQUIRED_COLS = ["created_date", "closed_date", "status", "borough"]
OPTIONAL_COLS = ["descriptor_2", "address_type", "community_board", "council_district",
                 "police_precinct", "incident_zip", "latitude", "longitude",
                 "intersection_street_1", "intersection_street_2", "incident_address",
                 "street_name", "cross_street_1", "cross_street_2"]

CAT_FEATURES = ["borough", "location_type", "address_type", "community_board",
                "police_precinct", "zip_code"]
NUM_FEATURES = ["hour", "dow", "month", "is_weekend", "is_business_hours",
                "latitude", "longitude", "has_coords", "council_district",
                "loc_prior_90d", "loc_prior_365d", "loc_days_since_prev",
                "boro_backlog_60d", "boro_created_7d", "boro_closed_14d",
                "boro_median_dur_14d", "city_median_dur_14d", "boro_share_gt168_14d"]
FEATURES = CAT_FEATURES + NUM_FEATURES

# Present in raw data but NOT usable as features (contain post-creation information).
LEAKY_COLS = ["closed_date", "resolution_action_updated_date", "resolution_description",
              "status", "due_date", "duration_hours"]


def load_raw(path):
    df = pd.read_csv(path, low_memory=False,
                     parse_dates=["created_date", "closed_date"])
    return standardize(df)


def standardize(df):
    df = df.copy()
    if "status" not in df.columns and "closed_date" in df.columns:
        # no status column: closed_date present => Closed, otherwise Open
        df["status"] = np.where(pd.to_datetime(df["closed_date"]).notna(), "Closed", "Open")
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")
    for c in OPTIONAL_COLS:
        if c not in df.columns:
            df[c] = np.nan
    df["created_date"] = pd.to_datetime(df["created_date"])
    df["closed_date"] = pd.to_datetime(df["closed_date"])
    df["borough"] = df["borough"].fillna("UNKNOWN").astype(str).str.upper().replace({"UNSPECIFIED": "UNKNOWN"})
    return df.sort_values("created_date", kind="stable").reset_index(drop=True)


def add_labels(df, snapshot=None):
    """Target: slow_repair = 1 if the complaint was not resolved within LABEL_HOURS.

    Rows whose outcome is unknown at the snapshot are dropped (label_known=False):
      * created less than LABEL_HOURS before the snapshot and still unresolved (censored)
      * status == 'Pending': closed_date is a placeholder earlier than created_date
    Still-unresolved (Open/Assigned) complaints older than LABEL_HOURS are positives.
    """
    df = df.copy()
    snapshot = snapshot or df["created_date"].max()
    dur = (df["closed_date"] - df["created_date"]).dt.total_seconds() / 3600
    valid_close = dur >= 0
    df["duration_hours"] = dur.where(valid_close)
    age = (snapshot - df["created_date"]).dt.total_seconds() / 3600
    pending = df["status"].eq("Pending")

    y = pd.Series(np.nan, index=df.index)
    resolved = valid_close & ~pending
    y[resolved] = (dur[resolved] > LABEL_HOURS).astype(float)
    unresolved = ~valid_close & ~pending & df["closed_date"].isna()
    y[unresolved & (age > LABEL_HOURS)] = 1.0
    df["slow_repair"] = y
    df["label_known"] = y.notna()
    return df


def _loc_key(df):
    """Best available location identifier: coordinates > intersection > address > street."""
    b = df["borough"].astype("string")
    key = pd.Series(pd.NA, index=df.index, dtype="string")
    s = ("S|" + df["street_name"].astype("string").fillna("") + "|" + df["cross_street_1"].astype("string").fillna("")
         + "|" + df["cross_street_2"].astype("string").fillna("") + "|" + b)
    key = key.mask(df["street_name"].notna(), s)
    a = "A|" + df["incident_address"].astype("string") + "|" + b
    key = key.mask(df["incident_address"].notna(), a)
    i = "I|" + df["intersection_street_1"].astype("string") + "|" + df["intersection_street_2"].astype("string")
    key = key.mask(df["intersection_street_1"].notna(), i)
    c = "C|" + df["latitude"].round(4).astype("string") + "," + df["longitude"].round(4).astype("string")
    key = key.mask(df["latitude"].notna() & df["longitude"].notna(), c)
    return key


def _location_history(df):
    """Counts of strictly earlier complaints at the same location."""
    codes, _ = pd.factorize(_loc_key(df))
    codes = codes.astype("int64")
    t = (df["created_date"] - pd.Timestamp("2000-01-01")).dt.total_seconds().to_numpy("int64")
    BIG = np.int64(10**10)
    ok = codes >= 0
    comb = np.where(ok, codes * BIG + t, -1)
    sorted_comb = np.sort(comb)
    base = codes * BIG
    hi = np.searchsorted(sorted_comb, base + t, side="left")     # earlier-than-t entries
    out = {}
    for name, days in [("loc_prior_90d", 90), ("loc_prior_365d", 365)]:
        lo = np.searchsorted(sorted_comb, base + t - days * 86400, side="left")
        out[name] = np.where(ok, (hi - lo).astype(float), np.nan)
    prev = sorted_comb[np.clip(hi - 1, 0, None)]
    same = ok & (hi > 0) & (prev // BIG == codes)
    out["loc_days_since_prev"] = np.where(same, (t - (prev % BIG)) / 86400, np.nan)
    return pd.DataFrame(out, index=df.index)


def _daily_context(df):
    """State of each borough as of 00:00 of each creation day (prior info only).

    'Pending' complaints are excluded: their closed_date is not a real closure.
    """
    known = ~df["status"].eq("Pending")
    dur = ((df["closed_date"] - df["created_date"]).dt.total_seconds() / 3600).to_numpy()
    valid_close = (dur >= 0) & known.to_numpy()
    open_forever = ~valid_close & known.to_numpy() & df["closed_date"].isna().to_numpy()
    created = df["created_date"].to_numpy("datetime64[s]")
    close = np.where(valid_close, df["closed_date"].to_numpy("datetime64[s]"), np.datetime64("NaT"))
    boro = df["borough"].to_numpy()
    known_np = known.to_numpy()

    closed_idx = np.where(valid_close)[0]
    close_order = closed_idx[np.argsort(close[closed_idx], kind="stable")]
    close_sorted = close[close_order]

    days = pd.date_range(df["created_date"].min().normalize(), df["created_date"].max().normalize())
    boroughs = sorted(set(boro))
    rows = []
    for d in days:
        d64 = np.datetime64(d, "s")
        lo = np.searchsorted(created, d64 - np.timedelta64(BACKLOG_DAYS, "D"))
        hi = np.searchsorted(created, d64)
        idx = np.arange(lo, hi)
        idx = idx[known_np[idx]]
        still_open = np.where(np.isnat(close[idx]), open_forever[idx], close[idx] >= d64)
        recent = created[idx] >= d64 - np.timedelta64(VOLUME_DAYS, "D")
        clo = np.searchsorted(close_sorted, d64 - np.timedelta64(CLOSURE_DAYS, "D"))
        chi = np.searchsorted(close_sorted, d64)
        cidx = close_order[clo:chi]
        cd, cb = dur[cidx], boro[cidx]
        city_med = np.median(cd) if len(cd) else np.nan
        for b in boroughs:
            m, mb = boro[idx] == b, cb == b
            rows.append((d, b, int((still_open & m).sum()), int((recent & m).sum()), int(mb.sum()),
                         np.median(cd[mb]) if mb.any() else np.nan,
                         (cd[mb] > LABEL_HOURS).mean() if mb.any() else np.nan, city_med))
    return pd.DataFrame(rows, columns=["day", "borough", "boro_backlog_60d", "boro_created_7d",
                                       "boro_closed_14d", "boro_median_dur_14d",
                                       "boro_share_gt168_14d", "city_median_dur_14d"])


def engineer_features(df):
    """Return df with FEATURES columns added. df must come from standardize()."""
    df = df.copy()
    ts = df["created_date"]
    df["hour"] = ts.dt.hour
    df["dow"] = ts.dt.dayofweek
    df["month"] = ts.dt.month
    df["is_weekend"] = (df["dow"] >= 5).astype(int)
    df["is_business_hours"] = ((df["hour"] >= 8) & (df["hour"] < 18) & (df["dow"] < 5)).astype(int)
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df["council_district"] = pd.to_numeric(df["council_district"], errors="coerce")
    df["has_coords"] = (df["latitude"].notna() & df["longitude"].notna()).astype(int)
    df["location_type"] = df["descriptor_2"].astype("string").str.replace("Location Type: ", "", regex=False)
    zc = pd.to_numeric(df["incident_zip"], errors="coerce")
    df["zip_code"] = zc.round().astype("Int64").astype("string")
    for c in ["address_type", "community_board", "police_precinct"]:
        df[c] = df[c].astype("string")
    df = pd.concat([df, _location_history(df)], axis=1)
    ctx = _daily_context(df)
    df["day"] = ts.dt.normalize()
    df = df.merge(ctx, on=["day", "borough"], how="left").drop(columns="day")
    for c in CAT_FEATURES:
        df[c] = df[c].astype(object).where(df[c].notna(), np.nan)
    return df
