"""
Stage 11 (reported-open exposure redesign) validation.

Independent checks of the Stage 11a panel and the Stage 11b PPML estimator.
Each check re-derives its quantity with separate code (pandas timestamps,
direct h3 calls, statsmodels) instead of calling the production helpers.

    python scripts/validation/stage11_exposure_validate.py [--estimates-dir DIR]

V1  every in-period night crime is assigned to exactly one (cell, week);
    full recount equals the panel outcome; outside-universe count matches
V2  brute-force exposure recount (all four bands) for sampled cells, from
    the raw 311 file, with site-level merging and artifact exclusion
V3  as-of reconstruction: exposure for week w rebuilt from records known
    by the end of week w (later closures treated as not yet closed)
    equals the panel (no future information)
V4  intervals: classes partition the complaints, no kept interval with
    end < start, no exposure outside the 130 panel weeks, merged
    site-nights <= complaint-nights, no site-night counted twice
V5  panel dimensions: cells x 130 weeks, unique (cell, week), hashes
    match the Stage 11a diagnostics
V6  zero-crime handling and FE variation in the Stage 11b summary
V7  PPML-HDFE equals statsmodels Poisson GLM with explicit cell and
    borough x week dummies on a subsample (coefficients and uncorrected
    cluster-robust SEs)

Exit 0 when every check passes, 1 otherwise.
"""

import argparse
import json
import sys
from pathlib import Path

import h3
import numpy as np
import pandas as pd
from pyproj import Transformer

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.features import build_exposure_panel as s11a  # noqa: E402  (constants and file names only)
from src.models import exposure_model as s11b  # noqa: E402

PANEL_DIR = s11a.OUT_DIR
SEED = 20261006
N_SAMPLE_CELLS = 150
N_ASOF_WEEKS = 12

RESULTS = []


def check(name, ok, detail):
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def load():
    cells = pd.read_parquet(PANEL_DIR / s11a.CELLS_FILENAME)
    panel = pd.read_parquet(PANEL_DIR / s11a.PANEL_FILENAME)
    diag = json.loads((PANEL_DIR / s11a.DIAGNOSTICS_FILENAME).read_text())
    return cells, panel, diag


# ------------------------------------------------------------
# Independent raw rebuild (pandas timestamps, own rules)
# ------------------------------------------------------------

def raw_sites():
    raw = pd.read_csv(s11a.RAW_COMPLAINTS_FILE,
                      usecols=["unique_key", "created_date", "closed_date", "latitude", "longitude"],
                      low_memory=False)
    raw["created_date"] = pd.to_datetime(raw["created_date"], format="ISO8601", errors="coerce")
    raw["closed_date"] = pd.to_datetime(raw["closed_date"], format="ISO8601", errors="coerce")
    snapshot = raw["created_date"].max()
    raw = raw[raw["latitude"].between(40.49, 40.92) & raw["longitude"].between(-74.26, -73.69)].copy()
    x, y = Transformer.from_crs("EPSG:4326", "EPSG:32118", always_xy=True).transform(
        raw["longitude"].to_numpy(), raw["latitude"].to_numpy())
    raw["sx"] = np.rint(x).astype(np.int64)
    raw["sy"] = np.rint(y).astype(np.int64)
    counts = raw.groupby(["sx", "sy"]).size()
    threshold = np.quantile(counts.to_numpy(), 0.999, method="higher")
    artifact = set(counts[counts > threshold].index)
    raw["artifact"] = [k in artifact for k in zip(raw["sx"], raw["sy"])]
    return raw, snapshot


def intervals(raw, snapshot, as_of=None):
    """Reported-open [start, end) per complaint under the Stage 11 rule."""

    r = raw[raw["created_date"].notna() & ~raw["artifact"]].copy()
    # Record-level exclusion (duplicate / administrative closure within
    # 0.5 h of creation, or closed before created). It uses at most 30 min
    # of look-ahead, so the as-of rebuild applies it from the true record.
    true_hours = (r["closed_date"] - r["created_date"]).dt.total_seconds() / 3600
    r = r[~(r["closed_date"].notna() & (true_hours < 0.5))].copy()
    closed = r["closed_date"]
    if as_of is not None:
        r = r[r["created_date"] <= as_of].copy()
        closed = r["closed_date"].where(r["closed_date"] <= as_of)   # later closures unknown
    end = closed.fillna(snapshot)
    cap = r["created_date"] + pd.Timedelta(hours=8760)
    r["end"] = end.where(end <= cap, cap)
    return r


def lookahead_records(raw, as_of_list):
    """Records whose too-short exclusion is only knowable after an as-of time."""

    hours = (raw["closed_date"] - raw["created_date"]).dt.total_seconds() / 3600
    short = raw[raw["closed_date"].notna() & (hours >= 0) & (hours < 0.5)]
    return int(sum(((short["created_date"] <= a) & (short["closed_date"] > a)).sum() for a in as_of_list))


def site_night_open(r, nights):
    """Set of (sx, sy, night_index) open nights; night d = [d 18:00, d+1 07:00)."""

    starts = nights + pd.Timedelta(hours=18)
    ends = nights + pd.Timedelta(hours=31)
    out = set()
    for sx, sy, c, e in zip(r["sx"], r["sy"], r["created_date"], r["end"]):
        if e <= c:
            continue
        idx = np.flatnonzero((c < ends) & (e > starts))
        for k in idx:
            out.add((sx, sy, int(k)))
    return out


def band_of(d):
    if d <= 100:
        return "x_0_100"
    if d <= 250:
        return "x_100_250"
    if d <= 500:
        return "x_250_500"
    if d <= 750:
        return "x_500_750"
    return None


def exposure_for_cells(sample, r, nights, weeks):
    """Brute-force exposure per (cell_idx, week, band) from (site, night) sets."""

    ok = site_night_open(r, nights)
    site_xy = np.array(sorted({(a, b) for a, b, _ in ok}), dtype=np.float64)
    per_site = {}
    for a, b, k in ok:
        per_site.setdefault((a, b), []).append(k)
    result = {}
    for _, cell in sample.iterrows():
        d = np.sqrt((site_xy[:, 0] - cell["x_m"]) ** 2 + (site_xy[:, 1] - cell["y_m"]) ** 2)
        for (sx, sy), dist in zip(site_xy, d):
            band = band_of(dist)
            if band is None:
                continue
            for k in per_site[(int(sx), int(sy))]:
                w = k // 7
                if w in weeks:
                    key = (int(cell["cell_idx"]), w, band)
                    result[key] = result.get(key, 0) + 1
    return result


# ------------------------------------------------------------
# Checks
# ------------------------------------------------------------

def v1_crime(cells, panel, diag):
    crime = pd.read_parquet(s11a.CRIME_FILE, columns=["crime_datetime", "latitude", "longitude"]).dropna()
    t = crime["crime_datetime"]
    evening = t.dt.normalize() - pd.to_timedelta((t.dt.hour <= 6).astype(int), unit="D")
    k = (evening - s11a.PANEL_START).dt.days
    crime = crime[(k >= 0) & (k < s11a.N_NIGHTS)].copy()
    crime["week"] = ((evening - s11a.PANEL_START).dt.days // 7)[crime.index]
    crime["cell"] = [h3.latlng_to_cell(a, b, 10) for a, b in zip(crime["latitude"], crime["longitude"])]
    idx = dict(zip(cells["cell"].astype(str), cells["cell_idx"]))
    crime["cell_idx"] = crime["cell"].map(idx)
    outside = int(crime["cell_idx"].isna().sum())
    counts = (crime.dropna(subset=["cell_idx"]).astype({"cell_idx": np.int64, "week": np.int64})
              .groupby(["cell_idx", "week"]).size().rename("recount").reset_index())
    got = panel.loc[panel["crime"] > 0, ["cell_idx", "week", "crime"]].astype(np.int64)
    both = counts.merge(got, on=["cell_idx", "week"], how="outer").fillna(0)
    same = bool((both["recount"] == both["crime"]).all())
    check("V1 crime assigned exactly once", same and outside == diag["crime"]["n_crime_outside_universe"]
          and len(crime) == diag["crime"]["n_crime_in_panel_nights"],
          f"{len(crime):,} in-period crimes; recount equal={same}; outside universe {outside} "
          f"(diagnostics {diag['crime']['n_crime_outside_universe']})")


def v2_v3_exposure(cells, panel):
    raw, snapshot = raw_sites()
    rng = np.random.default_rng(SEED)
    exposed = panel.groupby("cell_idx")["x_0_100"].sum()
    pool_hi = exposed[exposed > 0].index.to_numpy()
    pick = np.concatenate([rng.choice(pool_hi, N_SAMPLE_CELLS // 2, replace=False),
                           rng.choice(cells["cell_idx"].to_numpy(), N_SAMPLE_CELLS // 2, replace=False)])
    sample = cells[cells["cell_idx"].isin(pick)][["cell_idx", "cell"]].copy()
    centre = np.array([h3.cell_to_latlng(c) for c in sample["cell"]])
    sample["x_m"], sample["y_m"] = Transformer.from_crs(
        "EPSG:4326", "EPSG:32118", always_xy=True).transform(centre[:, 1], centre[:, 0])
    nights = s11a.PANEL_START + pd.to_timedelta(np.arange(s11a.N_NIGHTS), unit="D")

    # Restrict raw records to sites within 750 m of a sampled cell (speed only).
    xy = sample[["x_m", "y_m"]].to_numpy()
    near = np.zeros(len(raw), dtype=bool)
    for cx, cy in xy:
        near |= np.sqrt((raw["sx"] - cx) ** 2 + (raw["sy"] - cy) ** 2).to_numpy() <= 750 + 1
    sub = raw[near]

    weeks = set(range(s11a.N_WEEKS))
    brute = exposure_for_cells(sample, intervals(sub, snapshot), nights, weeks)
    p = panel[panel["cell_idx"].isin(pick)].set_index(["cell_idx", "week"])
    mismatch = 0
    for (c, w), row in p.iterrows():
        for band in s11a.BAND_COLUMNS:
            if int(row[band]) != brute.get((c, w, band), 0):
                mismatch += 1
    check("V2 brute-force exposure recount", mismatch == 0,
          f"{len(sample)} cells x {s11a.N_WEEKS} weeks x 4 bands; mismatches {mismatch}")

    asof_weeks = sorted(rng.choice(s11a.N_WEEKS, N_ASOF_WEEKS, replace=False).tolist())
    mismatch = 0
    for w in asof_weeks:
        as_of = s11a.PANEL_START + pd.Timedelta(days=7 * (w + 1), hours=7) - pd.Timedelta(seconds=1)
        brute_w = exposure_for_cells(sample, intervals(sub, snapshot, as_of=as_of), nights, {w})
        for c in pick:
            for band in s11a.BAND_COLUMNS:
                if int(p.loc[(c, w), band]) != brute_w.get((c, w, band), 0):
                    mismatch += 1
    look = lookahead_records(raw, [s11a.PANEL_START + pd.Timedelta(days=7 * (w + 1), hours=7)
                                   - pd.Timedelta(seconds=1) for w in asof_weeks])
    check("V3 no future information (as-of-week-end rebuild)", mismatch == 0,
          f"weeks {asof_weeks}; {len(pick)} cells x 4 bands; mismatches {mismatch}; "
          f"records whose <0.5 h exclusion straddles an as-of time (30-min look-ahead): {look}")


def v4_intervals(diag, panel):
    c = diag["complaints"]
    classes = sum(c[k] for k in s11a.INTERVAL_CLASSES)
    s = diag["sites"]
    ok = (classes == c["n_in_bbox"]
          and s["site_nights_after_merge"] <= s["complaint_nights_before_merge"]
          and panel["week"].min() == 0 and panel["week"].max() == s11a.N_WEEKS - 1
          and sum(s["complaint_nights_by_class"].values()) == s["complaint_nights_before_merge"])
    check("V4 interval accounting", ok,
          f"classes {classes:,} = in-bbox {c['n_in_bbox']:,}; merged site-nights "
          f"{s['site_nights_after_merge']:,} <= complaint-nights {s['complaint_nights_before_merge']:,}; "
          f"weeks 0..{panel['week'].max()}; excluded too_short {c['excluded_too_short']:,}, "
          f"closed<created {c['excluded_closed_before_created']:,}")


def v5_panel(cells, panel, diag):
    from src.features import match_controls as s7
    hashes = all(s7._file_fingerprint(PANEL_DIR / f)["sha256"] == diag["outputs"][f]
                 for f in (s11a.CELLS_FILENAME, s11a.PANEL_FILENAME))
    ok = (len(panel) == len(cells) * s11a.N_WEEKS
          and not panel.duplicated(["cell_idx", "week"]).any()
          and cells["cell_idx"].is_unique and hashes)
    check("V5 panel dimensions and hashes", ok,
          f"{len(cells):,} cells x {s11a.N_WEEKS} weeks = {len(panel):,} rows; hashes match={hashes}")


def v6_model(estimates_dir, panel):
    path = estimates_dir / s11b.SUMMARY_FILENAME
    if not path.exists():
        check("V6 zero-crime handling / FE variation", False, f"{path} missing (run Stage 11b)")
        return
    summ = json.loads(path.read_text())
    ok, notes = True, []
    for name, res in summ["results"].items():
        smp = res["sample"]
        ok &= smp["n_obs"] + smp["n_obs_dropped"] == smp["n_obs_panel"]
        ok &= smp["crime_total_estimation"] == int(panel["crime"].sum())
        ok &= all(v["within_variance_share"] > 0 for v in res["exposure_variation"].values())
        ok &= res["fit"]["n_zero_y_with_mu_below_1e-12"] == 0
        notes.append(f"{name}: obs {smp['n_obs']:,} (+{smp['n_obs_dropped']:,} dropped), "
                     f"clusters {smp['n_clusters']}, dispersion {res['fit']['pearson_dispersion']:.2f}")
    check("V6 zero-crime handling / FE variation", ok, "; ".join(notes))


def v7_estimator(cells, panel):
    import statsmodels.api as sm

    rng = np.random.default_rng(SEED)
    sub_cells = cells[cells["borough"].isin(["BRONX", "MANHATTAN"])]
    with_crime = panel.groupby("cell_idx")["crime"].sum()
    pool = sub_cells[sub_cells["cell_idx"].isin(with_crime[with_crime > 0].index)]["cell_idx"].to_numpy()
    pick = rng.choice(pool, 250, replace=False)
    df = panel[panel["cell_idx"].isin(pick)].merge(cells[["cell_idx", "borough", "cluster_res7"]], on="cell_idx")
    df["bp"] = df["borough"] + "|" + df["week"].astype(str)
    bp_tot = df.groupby("bp")["crime"].transform("sum")
    df = df[bp_tot > 0].reset_index(drop=True)
    bands = list(s11a.PRIMARY_BANDS)

    fit = s11b.ppml_hdfe(df["crime"].to_numpy(), df[bands].to_numpy(),
                         [pd.factorize(df["cell_idx"])[0], pd.factorize(df["bp"])[0]],
                         pd.factorize(df["cluster_res7"])[0])
    D = pd.get_dummies(df[["cell_idx", "bp"]].astype(str), drop_first=False, dtype=float)
    # Cells are nested in boroughs: drop one borough x week dummy per borough.
    first_bp = df.groupby("borough")["bp"].min()
    D = D.drop(columns=[f"bp_{b}" for b in first_bp])
    if np.linalg.matrix_rank(D.to_numpy()) != D.shape[1]:
        raise AssertionError("V7 reference design is still rank-deficient")
    Xf = np.column_stack([df[bands].to_numpy(float), D.to_numpy()])
    ref = sm.GLM(df["crime"].to_numpy(), Xf, family=sm.families.Poisson()).fit(
        method="newton", tol=1e-12, maxiter=200,
        cov_type="cluster", cov_kwds={"groups": pd.factorize(df["cluster_res7"])[0], "use_correction": False})
    b_ref, se_ref = ref.params[:len(bands)], ref.bse[:len(bands)]
    se = np.sqrt(np.diag(fit["vcov_uncorrected"]))
    db = float(np.max(np.abs(fit["beta"] - b_ref) / np.maximum(np.abs(b_ref), 1e-8)))
    ds = float(np.max(np.abs(se - se_ref) / se_ref))
    check("V7 PPML-HDFE == statsmodels LSDV Poisson", db < 1e-6 and ds < 1e-5,
          f"{len(df):,} obs, {len(pick)} cells, {D.shape[1]} dummies; max rel diff beta {db:.2e}, SE {ds:.2e}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stage 11 exposure redesign validation")
    parser.add_argument("--estimates-dir", type=Path, default=s11b.OUTPUT_DIR)
    parser.add_argument("--skip", action="append", default=[], help="check ids to skip, e.g. V2")
    args = parser.parse_args(argv)

    cells, panel, diag = load()
    steps = [("V5", lambda: v5_panel(cells, panel, diag)),
             ("V4", lambda: v4_intervals(diag, panel)),
             ("V1", lambda: v1_crime(cells, panel, diag)),
             ("V2", lambda: v2_v3_exposure(cells, panel)),
             ("V6", lambda: v6_model(args.estimates_dir, panel)),
             ("V7", lambda: v7_estimator(cells, panel))]
    for key, step in steps:
        if key not in args.skip:
            step()

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
