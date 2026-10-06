"""
Stage 11a: reported-open exposure panel (H3 res-10 cell x ISO week).

Builds the Stage 11 estimation panel from the raw 311 "Street Light Out"
records and the cleaned Stage 5 night crimes. Nothing upstream is read or
written except those two inputs; the Stage 7 pair outputs are not used.

ESTIMAND NOTE. The exposure is REPORTED-OPEN light-nights, not physical
darkness: 311 closed_date is not a verified repair time (Stage 11 blocker
study, path B). Exposure counts nights on which a 311 complaint at a
streetlight site was open (created, not yet closed) as recorded in the
311 file. Nothing here supports a physical-repair claim.

Definitions
- Panel: nights 2024-01-01 (start of ISO week 2024-W01) through the
  130 ISO weeks ending 2026-06-28; the crime data end 2026-06-30 23:40.
- Night d: [d 18:00, d+1 07:00), the Stage 5 night rule (hour >= 18 or
  hour <= 6). A night belongs to the ISO week of its evening date d.
- Site: complaint location rounded to 1 m in EPSG:32118 (Stage 7 rule).
  Artifact sites (Stage 7 rule: n_complaints > quantile 0.999 "higher")
  carry no exposure and do not define the cell universe.
- Reported-open interval per complaint: [created, end), end = closed_date,
  or the 311 snapshot time (the last created_date in the raw file) when
  there is no closed_date; end is capped at created + 8,760 h (the Stage 3
  maximum duration). Closures under 0.5 h (the Stage 3 minimum; duplicate
  or administrative closures) and closed < created are excluded and
  counted. A site is open on night d when any of its complaints' intervals
  overlaps the night window; duplicates at a site are merged, so a
  site-night counts at most once.
- Cells: H3 res-10 cells whose centre lies within 500 m of a non-artifact
  site. Cell borough = modal borough of the nearest site with a borough;
  cluster = the res-7 parent cell.
- Exposure bands (cell centre to site point, A13 explicit distance):
  [0, 100], (100, 250], (250, 500], (500, 750] m. Exposure = sum over
  sites in the band of open nights in the week.
- Outcome: night crimes in the cell; each crime is assigned to exactly one
  H3 res-10 cell (h3.latlng_to_cell) and one night.

Outputs (default data/processed/stage11/): exposure_cells.parquet,
exposure_panel.parquet, exposure_diagnostics.json. Every accounting check
is a hard stop (AssertionError).
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import h3
import numpy as np
import pandas as pd
from scipy import sparse
from scipy.spatial import cKDTree

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.features import match_controls as s7  # noqa: E402  (locked Stage 7 conventions)


# ============================================================
# Paths and constants
# ============================================================

RAW_COMPLAINTS_FILE = PROJECT_ROOT / s7.RAW_COMPLAINTS_FILE
CRIME_FILE = PROJECT_ROOT / s7.CRIME_FILE
OUT_DIR = PROJECT_ROOT / "data" / "processed" / "stage11"

CELLS_FILENAME = "exposure_cells.parquet"
PANEL_FILENAME = "exposure_panel.parquet"
DIAGNOSTICS_FILENAME = "exposure_diagnostics.json"

ESTIMAND = "reported-open light-nights (311 open status), not physical darkness"

PANEL_START = pd.Timestamp("2024-01-01")  # Monday, ISO 2024-W01
N_WEEKS = 130                              # last week ends Sunday 2026-06-28
N_NIGHTS = 7 * N_WEEKS
SECONDS_PER_DAY = s7.SECONDS_PER_DAY

# Stage 5 night rule: hour >= 18 or hour <= 6 -> [18:00, 07:00).
NIGHT_START_S = 18 * 3600
NIGHT_LENGTH_S = 13 * 3600

H3_RESOLUTION = 10
CLUSTER_RESOLUTION = 7
UNIVERSE_RADIUS_M = 500.0
BANDS = (("x_0_100", 0.0, 100.0), ("x_100_250", 100.0, 250.0),
         ("x_250_500", 250.0, 500.0), ("x_500_750", 500.0, 750.0))
BAND_COLUMNS = tuple(name for name, _, _ in BANDS)
MAX_BAND_M = BANDS[-1][2]
PRIMARY_BANDS = BAND_COLUMNS[:3]  # 500-750 m is the cutoff sensitivity

MIN_DURATION_H = s7.VALID_MIN_DURATION_H
MAX_DURATION_H = s7.VALID_MAX_DURATION_H
KD_TOL = s7.KD_QUERY_TOLERANCE_M

# Rings of H3 res-10 neighbours searched for universe candidates: centre
# spacing is about 115 m, so 9 rings (> 1 km) cover the 500 m radius.
UNIVERSE_GRID_K = 9

INTERVAL_CLASSES = ("closed", "open_no_closure", "excluded_too_short",
                    "excluded_closed_before_created",
                    "excluded_unparseable_created")


# ============================================================
# Complaints, sites and reported-open intervals
# ============================================================

def load_complaints():
    """Raw 311 complaints inside the NYC box, projected (Stage 7 step 1)."""

    raw = pd.read_csv(RAW_COMPLAINTS_FILE, usecols=list(s7.RAW_COMPLAINT_COLUMNS),
                      low_memory=False)
    n_raw = len(raw)
    if raw["unique_key"].duplicated().any():
        raise AssertionError("raw 311 unique_key is not unique")

    for column in ("created_date", "closed_date"):
        raw[column] = pd.to_datetime(raw[column], format="ISO8601", errors="coerce")

    snapshot = raw["created_date"].max()

    lat_min, lat_max, lon_min, lon_max = s7.NYC_BBOX
    in_bbox = (raw["latitude"].notna() & raw["longitude"].notna()
               & raw["latitude"].between(lat_min, lat_max)
               & raw["longitude"].between(lon_min, lon_max))
    complaints = (raw.loc[in_bbox].sort_values("unique_key", kind="mergesort")
                  .reset_index(drop=True))
    complaints["borough"] = s7._normalise_label(complaints["borough"])
    x_m, y_m = s7._project(complaints["longitude"], complaints["latitude"],
                           s7.PROJECTED_CRS)
    complaints["x_m"] = x_m
    complaints["y_m"] = y_m

    summary = {"n_raw": n_raw, "n_in_bbox": int(in_bbox.sum()),
               "snapshot_time": str(snapshot)}
    return complaints, snapshot, summary


def build_sites(complaints):
    """Stage 7 site rule (1 m rounding) and artifact rule (A7/D17)."""

    step = s7.SITE_ROUND_M
    x_m = (np.rint(complaints["x_m"].to_numpy() / step) * step).astype(np.int64)
    y_m = (np.rint(complaints["y_m"].to_numpy() / step) * step).astype(np.int64)
    site_key = np.char.add(np.char.add("E", x_m.astype(str)),
                           np.char.add("_N", y_m.astype(str)))
    site_ids, first_row, site_idx = np.unique(site_key, return_index=True,
                                              return_inverse=True)
    complaints["site_idx"] = site_idx.astype(np.int32)

    sites = pd.DataFrame({
        "site_id": pd.array(site_ids, dtype="string"),
        "x_m": x_m[first_row],
        "y_m": y_m[first_row],
    })
    sites["borough"], _ = s7._modal_label(site_idx, complaints["borough"], len(sites))
    sites["n_complaints"] = np.bincount(site_idx, minlength=len(sites))

    threshold = int(np.quantile(sites["n_complaints"], s7.ARTIFACT_QUANTILE,
                                method="higher"))
    sites["is_artifact"] = sites["n_complaints"] > threshold
    return sites, threshold


def build_intervals(complaints, snapshot):
    """
    Reported-open interval [start_s, end_s) per complaint, in seconds since
    PANEL_START, with its class (INTERVAL_CLASSES). Excluded rows get
    start_s = end_s = 0 and are never used.
    """

    created = complaints["created_date"]
    closed = complaints["closed_date"]
    hours = (closed - created).dt.total_seconds() / 3600

    cls = np.full(len(complaints), "closed", dtype=object)
    cls[closed.isna().to_numpy()] = "open_no_closure"
    cls[(hours < 0).to_numpy()] = "excluded_closed_before_created"
    cls[((hours >= 0) & (hours < MIN_DURATION_H)).to_numpy()] = "excluded_too_short"
    cls[created.isna().to_numpy()] = "excluded_unparseable_created"

    keep = np.isin(cls, ("closed", "open_no_closure"))
    end = closed.where(closed.notna(), snapshot)
    cap = created + pd.Timedelta(hours=MAX_DURATION_H)
    capped = keep & (end > cap).to_numpy()
    end = end.where(~pd.Series(capped, index=end.index), cap)

    start_s = np.zeros(len(complaints), dtype=np.int64)
    end_s = np.zeros(len(complaints), dtype=np.int64)
    start_s[keep] = s7._to_seconds(created[keep]) - _seconds(PANEL_START)
    end_s[keep] = s7._to_seconds(end[keep]) - _seconds(PANEL_START)

    complaints["interval_class"] = cls
    complaints["start_s"] = start_s
    complaints["end_s"] = end_s
    complaints["capped"] = capped

    if np.any(end_s[keep] < start_s[keep]):
        raise AssertionError("impossible interval: end before start")
    zero_length = keep & (end_s == start_s)
    if np.any(zero_length & (cls != "open_no_closure")):
        raise AssertionError("zero-length interval on a closed complaint")

    counts = {name: int((cls == name).sum()) for name in INTERVAL_CLASSES}
    if sum(counts.values()) != len(complaints):
        raise AssertionError("interval classes do not partition the complaints")
    # Open complaints created at the snapshot instant: [t, t) is empty.
    counts["n_zero_length_open_at_snapshot"] = int(zero_length.sum())
    counts["n_capped_at_8760h"] = int(capped.sum())
    counts["n_capped_open_no_closure"] = int((capped & (cls == "open_no_closure")).sum())
    return complaints, counts


def _seconds(timestamp):
    return int(s7._to_seconds([timestamp])[0])


def night_range(start_s, end_s):
    """
    Nights k whose window [k*D + 18h, k*D + 31h) overlaps [start_s, end_s),
    clipped to [0, N_NIGHTS - 1]. Returns (k_first, k_last); empty if
    k_last < k_first.
    """

    k_first = (start_s - NIGHT_START_S - NIGHT_LENGTH_S) // SECONDS_PER_DAY + 1
    k_last = -((-(end_s - NIGHT_START_S)) // SECONDS_PER_DAY) - 1
    return np.clip(k_first, 0, N_NIGHTS), np.clip(k_last, -1, N_NIGHTS - 1)


def build_site_nights(complaints, sites):
    """
    Site x night open indicator (uint8, 0/1) for all sites. Duplicate
    complaints at a site are merged by the indicator, so a site-night is
    counted at most once. Also returns the per-complaint night total
    (before merging) for accounting.
    """

    keep = (np.isin(complaints["interval_class"], ("closed", "open_no_closure"))
            & (complaints["end_s"] > complaints["start_s"]).to_numpy())
    rows = complaints.loc[keep]
    k_first, k_last = night_range(rows["start_s"].to_numpy(), rows["end_s"].to_numpy())
    site_idx = rows["site_idx"].to_numpy()

    open_nights = np.zeros((len(sites), N_NIGHTS), dtype=np.uint8)
    complaint_nights = 0
    for site, a, b in zip(site_idx, k_first, k_last):
        if b >= a:
            open_nights[site, a:b + 1] = 1
            complaint_nights += int(b - a + 1)

    # Complaint-nights (before merging) by interval class, capped separately.
    n = np.clip(k_last - k_first + 1, 0, None)
    group = np.where(rows["capped"].to_numpy(), "capped_", "") + rows["interval_class"].to_numpy().astype(str)
    by_class = {str(k): int(v) for k, v in pd.Series(n).groupby(group).sum().items()}

    n_in_panel = int((k_last >= k_first).sum())
    return open_nights, complaint_nights, n_in_panel, by_class


# ============================================================
# Cells, bands and crimes
# ============================================================

def build_cells(sites):
    """H3 res-10 cells whose centre is within UNIVERSE_RADIUS_M of a non-artifact site."""

    active = sites.loc[~sites["is_artifact"]].reset_index(drop=True)
    lon, lat = s7._unproject(active["x_m"].to_numpy(float), active["y_m"].to_numpy(float),
                             s7.PROJECTED_CRS)
    site_cells = set(s7._h3_cells(lat, lon, H3_RESOLUTION))

    candidates = set()
    for cell in site_cells:
        candidates.update(h3.grid_disk(cell, UNIVERSE_GRID_K))
    candidates = np.array(sorted(candidates), dtype=object)

    centre = np.array([h3.cell_to_latlng(c) for c in candidates])
    cx, cy = s7._project(centre[:, 1], centre[:, 0], s7.PROJECTED_CRS)
    site_xy = active[["x_m", "y_m"]].to_numpy(float)
    _, nearest = cKDTree(site_xy).query(np.c_[cx, cy])
    d = np.hypot(site_xy[nearest, 0] - cx, site_xy[nearest, 1] - cy)
    inside = d <= UNIVERSE_RADIUS_M

    cells = pd.DataFrame({
        "cell": pd.array(candidates[inside], dtype="string"),
        "x_m": cx[inside],
        "y_m": cy[inside],
        "latitude": centre[inside, 0],
        "longitude": centre[inside, 1],
    })
    cells["cell_idx"] = np.arange(len(cells), dtype=np.int32)
    cells["cluster_res7"] = pd.array(
        [h3.cell_to_parent(c, CLUSTER_RESOLUTION) for c in cells["cell"]], dtype="string")

    # Borough: nearest non-artifact site that has a borough.
    labelled = sites.loc[sites["borough"].notna() & ~sites["is_artifact"]].reset_index(drop=True)
    lab_xy = labelled[["x_m", "y_m"]].to_numpy(float)
    _, nearest = cKDTree(lab_xy).query(cells[["x_m", "y_m"]].to_numpy())
    cells["borough"] = labelled["borough"].to_numpy()[nearest]
    return cells


def band_matrices(cells, sites):
    """Sparse cell x site incidence matrix for each band (A13 explicit distance)."""

    site_xy = sites[["x_m", "y_m"]].to_numpy(float)
    cell_xy = cells[["x_m", "y_m"]].to_numpy(float)
    usable = np.flatnonzero(~sites["is_artifact"].to_numpy())

    tree = cKDTree(site_xy[usable])
    lists = cKDTree(cell_xy).query_ball_tree(tree, MAX_BAND_M + KD_TOL)
    row = np.repeat(np.arange(len(cells)), [len(x) for x in lists])
    col = usable[np.concatenate([np.asarray(x, dtype=np.int64) for x in lists])]
    d = np.sqrt((site_xy[col, 0] - cell_xy[row, 0]) ** 2
                + (site_xy[col, 1] - cell_xy[row, 1]) ** 2)

    matrices, pair_counts = {}, {}
    for name, lo, hi in BANDS:
        m = (d <= hi) & ((d > lo) if lo > 0 else (d >= 0))
        matrices[name] = sparse.csr_matrix(
            (np.ones(m.sum(), dtype=np.float64), (row[m], col[m])),
            shape=(len(cells), len(sites)))
        pair_counts[name] = int(m.sum())
    return matrices, pair_counts


def load_crimes(cells):
    """
    Night crimes in the panel nights, each assigned to one night and one
    H3 res-10 cell. Returns (cell_idx, week) for crimes inside the cell
    universe plus the accounting summary.
    """

    crime = pd.read_parquet(CRIME_FILE, columns=list(s7.CRIME_COLUMNS))
    n_total = len(crime)
    valid = (crime["crime_datetime"].notna() & crime["latitude"].notna()
             & crime["longitude"].notna())
    crime = crime.loc[valid].reset_index(drop=True)

    t = s7._to_seconds(crime["crime_datetime"]) - _seconds(PANEL_START)
    crime_max_s = int(t.max())
    rel = t - NIGHT_START_S
    night = rel // SECONDS_PER_DAY
    in_night_window = (rel % SECONDS_PER_DAY) < NIGHT_LENGTH_S
    if not in_night_window.all():
        raise AssertionError(
            f"{int((~in_night_window).sum())} crimes fall outside the Stage 5 night window")

    in_panel = (night >= 0) & (night < N_NIGHTS)
    crime = crime.loc[in_panel].reset_index(drop=True)
    night = night[in_panel]

    cell_of = s7._h3_cells(crime["latitude"].to_numpy(), crime["longitude"].to_numpy(),
                           H3_RESOLUTION)
    lookup = pd.Series(cells["cell_idx"].to_numpy(), index=cells["cell"].astype(str))
    cell_idx = lookup.reindex(cell_of).to_numpy()
    inside = ~np.isnan(cell_idx)

    summary = {
        "n_crime_file": n_total,
        "n_crime_invalid": int((~valid).sum()),
        "n_crime_in_panel_nights": int(in_panel.sum()),
        "n_crime_in_universe": int(inside.sum()),
        "n_crime_outside_universe": int((~inside).sum()),
        "crime_max": str(PANEL_START + pd.Timedelta(seconds=crime_max_s)),
    }
    if summary["n_crime_in_universe"] + summary["n_crime_outside_universe"] != summary["n_crime_in_panel_nights"]:
        raise AssertionError("crime accounting does not add up")
    if crime_max_s < N_NIGHTS * SECONDS_PER_DAY + NIGHT_START_S + NIGHT_LENGTH_S - SECONDS_PER_DAY:
        raise AssertionError("crime data end before the last panel night ends")
    return cell_idx[inside].astype(np.int64), (night[inside] // 7).astype(np.int64), summary


# ============================================================
# Panel assembly
# ============================================================

def build_panel(cells, sites, open_nights, crime_cell, crime_week):
    n_cells = len(cells)
    site_weeks = open_nights.reshape(len(sites), N_WEEKS, 7).sum(axis=2).astype(np.float64)

    matrices, pair_counts = band_matrices(cells, sites)
    exposure = {name: np.asarray(matrices[name] @ site_weeks) for name in BAND_COLUMNS}

    y = np.zeros((n_cells, N_WEEKS), dtype=np.int64)
    np.add.at(y, (crime_cell, crime_week), 1)

    panel = pd.DataFrame({
        "cell_idx": np.repeat(np.arange(n_cells, dtype=np.int32), N_WEEKS),
        "week": np.tile(np.arange(N_WEEKS, dtype=np.int16), n_cells),
        "crime": y.ravel().astype(np.int32),
    })
    for name in BAND_COLUMNS:
        values = exposure[name].ravel()
        if not np.allclose(values, np.rint(values)):
            raise AssertionError(f"{name}: non-integer light-night counts")
        panel[name] = np.rint(values).astype(np.int32)

    totals = {}
    for name in BAND_COLUMNS:
        expected = float(np.asarray(matrices[name].sum(axis=0)).ravel() @ site_weeks.sum(axis=1))
        totals[name] = int(panel[name].sum())
        if totals[name] != int(round(expected)):
            raise AssertionError(f"{name}: exposure total {totals[name]} != incidence identity {expected}")
    return panel, totals, pair_counts


def week_labels():
    starts = PANEL_START + pd.to_timedelta(np.arange(N_WEEKS) * 7, unit="D")
    iso = starts.isocalendar()
    return [f"{y}-W{w:02d}" for y, w in zip(iso["year"], iso["week"])]


def run(out_dir):
    complaints, snapshot, load_summary = load_complaints()
    sites, threshold = build_sites(complaints)
    complaints, interval_counts = build_intervals(complaints, snapshot)
    open_nights, complaint_nights, n_complaints_in_panel, nights_by_class = build_site_nights(complaints, sites)

    artifact = sites["is_artifact"].to_numpy()
    site_nights_total = int(open_nights.sum())
    if open_nights.max() > 1:
        raise AssertionError("site-night counted more than once")
    if site_nights_total > complaint_nights:
        raise AssertionError("merged site-nights exceed complaint-nights")

    cells = build_cells(sites)
    crime_cell, crime_week, crime_summary = load_crimes(cells)
    panel, totals, pair_counts = build_panel(cells, sites, open_nights, crime_cell, crime_week)

    if len(panel) != len(cells) * N_WEEKS:
        raise AssertionError("panel is not cells x weeks")
    if int(panel["crime"].sum()) != crime_summary["n_crime_in_universe"]:
        raise AssertionError("panel crime total != crimes assigned to the universe")
    if panel[list(BAND_COLUMNS)].isna().any().any() or (panel[list(BAND_COLUMNS)] < 0).any().any():
        raise AssertionError("negative or missing exposure")

    labels = week_labels()
    if labels[0] != "2024-W01" or len(set(labels)) != N_WEEKS:
        raise AssertionError("ISO week labels are not 130 distinct weeks from 2024-W01")

    diagnostics = {
        "stage": "11a exposure panel",
        "estimand": ESTIMAND,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "definitions": {
            "panel_start": str(PANEL_START.date()),
            "n_weeks": N_WEEKS,
            "first_week": labels[0],
            "last_week": labels[-1],
            "night_window": "[d 18:00, d+1 07:00) (Stage 5 hour >= 18 or <= 6)",
            "h3_resolution": H3_RESOLUTION,
            "cluster_resolution": CLUSTER_RESOLUTION,
            "universe_radius_m": UNIVERSE_RADIUS_M,
            "bands_m": {name: [lo, hi] for name, lo, hi in BANDS},
            "primary_bands": list(PRIMARY_BANDS),
            "interval_rule": "[created, closed or snapshot), capped at created + 8760 h; "
                             "closures < 0.5 h and closed < created excluded",
            "artifact_rule": "n_complaints > quantile(0.999, 'higher'); no exposure",
        },
        "complaints": {**load_summary, **interval_counts,
                       "n_complaints_with_panel_nights": n_complaints_in_panel},
        "sites": {
            "n_sites": int(len(sites)),
            "artifact_threshold": threshold,
            "n_artifact_sites": int(artifact.sum()),
            "n_sites_open_in_panel": int((open_nights.sum(axis=1) > 0).sum()),
            "complaint_nights_before_merge": complaint_nights,
            "complaint_nights_by_class": nights_by_class,
            "site_nights_after_merge": site_nights_total,
            "site_nights_artifact_excluded": int(open_nights[artifact].sum()),
        },
        "cells": {
            "n_cells": int(len(cells)),
            "n_clusters_res7": int(cells["cluster_res7"].nunique()),
            "borough_counts": cells["borough"].value_counts().to_dict(),
        },
        "crime": crime_summary,
        "panel": {
            "n_rows": int(len(panel)),
            "crime_total": int(panel["crime"].sum()),
            "zero_crime_share": float((panel["crime"] == 0).mean()),
            "cells_all_zero_crime": int((panel.groupby("cell_idx")["crime"].sum() == 0).sum()),
            "exposure_totals": totals,
            "cell_site_pairs_by_band": pair_counts,
            "nonzero_exposure_share": {n: float((panel[n] > 0).mean()) for n in BAND_COLUMNS},
        },
        "inputs": {
            "raw_311": s7._file_fingerprint(RAW_COMPLAINTS_FILE),
            "clean_crime": s7._file_fingerprint(CRIME_FILE),
        },
        "git": s7._git_state(),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    _atomic_parquet(cells, out_dir / CELLS_FILENAME)
    _atomic_parquet(panel, out_dir / PANEL_FILENAME)
    diagnostics["outputs"] = {
        CELLS_FILENAME: s7._file_fingerprint(out_dir / CELLS_FILENAME)["sha256"],
        PANEL_FILENAME: s7._file_fingerprint(out_dir / PANEL_FILENAME)["sha256"],
    }
    tmp = out_dir / (DIAGNOSTICS_FILENAME + ".tmp")
    tmp.write_text(json.dumps(diagnostics, indent=2, allow_nan=False, default=str))
    os.replace(tmp, out_dir / DIAGNOSTICS_FILENAME)
    return diagnostics


def _atomic_parquet(frame, path):
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Stage 11a reported-open exposure panel")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    diagnostics = run(args.out_dir)
    print(json.dumps({k: diagnostics[k] for k in ("complaints", "sites", "cells", "crime", "panel")},
                     indent=2, default=str))
    print(f"\nWrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
