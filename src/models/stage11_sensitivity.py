"""
Stage 11c: D-S11-1 comparison and the approved timing-sensitivity grid.

Reuses the frozen Stage 11a functions (complaints, sites, intervals, the
stored cell universe, crime assignment, band incidence) and the Stage 11b
estimator unchanged. Only the site x night reported-open matrix differs
between scenarios, so cells, crimes and the estimation sample are identical
in every run. E0 is rebuilt first and must equal the stored Stage 11a panel.

Scenarios (blocker study, approved grid; primary bands, res-10 x ISO week):
- E0           reported-open [created, closed or snapshot), 8,760 h cap
- strict       D-S11-1 alternative B: open-without-closure and > 8,760 h
               closures excluded (Stage 3 validity rule)
- start_L7     interval start brought forward to created - 7 days
               (start_L0 is E0 by definition)
- end_early    end = created + 0.5 x (E0 end - created)
- merge_g7/14  per site, gaps of <= g nights between reported-open runs
               (closure followed by a new report at the same site) filled
- deadline     E0 split into deadline-closure exposure (closed 222-240 h)
               and all other exposure; nights open under both count as other

Writes outputs/stage11_exposure/sensitivity/stage11_sensitivity_estimates.csv
and stage11_sensitivity_summary.json. Estimand wording as Stage 11b.
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.features import build_exposure_panel as s11a  # noqa: E402
from src.features import match_controls as s7  # noqa: E402
from src.models import exposure_model as s11b  # noqa: E402

OUT_DIR = s11b.OUTPUT_DIR / "sensitivity"
ESTIMATES_FILENAME = "stage11_sensitivity_estimates.csv"
SUMMARY_FILENAME = "stage11_sensitivity_summary.json"

LAG_DAYS = 7
MERGE_GAPS = (7, 14)
DEADLINE_HOURS = (222.0, 240.0)
END_FRACTION = 0.5
SCENARIOS = ("E0", "strict", "start_L7", "end_early", "merge_g7", "merge_g14", "deadline")


# ============================================================
# Scenario site-night matrices
# ============================================================

def with_intervals(complaints, keep=None, start_shift_s=0, end_fraction=None):
    """Copy of the complaint table with a scenario interval rule applied."""

    c = complaints.copy()
    kept = np.isin(c["interval_class"], ("closed", "open_no_closure"))
    if keep is not None:
        c.loc[kept & ~keep, "interval_class"] = "excluded_scenario"
        kept = kept & keep
    if end_fraction is not None:
        length = c.loc[kept, "end_s"] - c.loc[kept, "start_s"]
        c.loc[kept, "end_s"] = c.loc[kept, "start_s"] + np.floor(end_fraction * length).astype(np.int64)
    if start_shift_s:
        c.loc[kept, "start_s"] = c.loc[kept, "start_s"] - start_shift_s
    return c


def fill_gaps(open_nights, max_gap):
    """Fill gaps of <= max_gap closed nights between open runs at a site."""

    out = open_nights.copy()
    filled = 0
    for site in np.flatnonzero(out.any(axis=1)):
        row = out[site]
        on = np.flatnonzero(row)
        gaps = np.diff(on) - 1
        for a, gap in zip(on[:-1], gaps):
            if 0 < gap <= max_gap:
                row[a + 1:a + 1 + gap] = 1
                filled += int(gap)
    return out, filled


def deadline_mask(complaints):
    hours = (complaints["closed_date"] - complaints["created_date"]).dt.total_seconds() / 3600
    lo, hi = DEADLINE_HOURS
    return ((complaints["interval_class"] == "closed") & hours.between(lo, hi)).to_numpy()


# ============================================================
# Run
# ============================================================

def run(out_dir, scenarios=SCENARIOS):
    complaints, snapshot, _ = s11a.load_complaints()
    sites, _ = s11a.build_sites(complaints)
    complaints, _ = s11a.build_intervals(complaints, snapshot)
    cells = pd.read_parquet(s11a.OUT_DIR / s11a.CELLS_FILENAME)
    stored = pd.read_parquet(s11a.OUT_DIR / s11a.PANEL_FILENAME)
    crime_cell, crime_week, _ = s11a.load_crimes(cells)

    def panel_for(open_nights, suffix=""):
        panel, totals, _ = s11a.build_panel(cells, sites, open_nights, crime_cell, crime_week)
        if suffix:
            panel = panel.rename(columns={b: b + suffix for b in s11a.BAND_COLUMNS})
        return panel

    m0, _, _, _ = s11a.build_site_nights(complaints, sites)
    e0 = panel_for(m0)
    if not e0.equals(stored):
        raise AssertionError("rebuilt E0 panel differs from the stored Stage 11a panel")

    artifact = sites["is_artifact"].to_numpy()
    matrices, notes = {}, {}
    affected = None
    for name in scenarios:
        if name == "E0":
            matrices[name] = m0
        elif name == "strict":
            affected = (complaints["interval_class"].eq("open_no_closure") | complaints["capped"]).to_numpy()
            matrices[name], _, _, _ = s11a.build_site_nights(with_intervals(complaints, keep=~affected), sites)
            m_aff, _, n_aff_panel, _ = s11a.build_site_nights(with_intervals(complaints, keep=affected), sites)
            notes[name] = {
                "n_complaints_affected": int(affected.sum()),
                "n_open_no_closure": int(complaints["interval_class"].eq("open_no_closure").sum()),
                "n_closed_over_8760h": int((complaints["capped"] & complaints["interval_class"].eq("closed")).sum()),
                "n_affected_with_panel_nights": n_aff_panel,
                "n_sites_affected_in_panel": int((m_aff[~artifact].sum(axis=1) > 0).sum()),
                "site_nights_from_affected": int(m_aff[~artifact].sum()),
                "site_nights_only_from_affected": int((m0 & ~matrices[name])[~artifact].sum()),
            }
        elif name == "start_L7":
            matrices[name], _, _, _ = s11a.build_site_nights(
                with_intervals(complaints, start_shift_s=LAG_DAYS * s11a.SECONDS_PER_DAY), sites)
        elif name == "end_early":
            matrices[name], _, _, _ = s11a.build_site_nights(
                with_intervals(complaints, end_fraction=END_FRACTION), sites)
        elif name.startswith("merge_g"):
            g = int(name.removeprefix("merge_g"))
            matrices[name], filled = fill_gaps(m0, g)
            notes[name] = {"site_nights_filled": int(np.sum(matrices[name][~artifact]) - np.sum(m0[~artifact]))}
        elif name == "deadline":
            dl = deadline_mask(complaints)
            m_dl, _, _, _ = s11a.build_site_nights(with_intervals(complaints, keep=dl), sites)
            m_ot, _, _, _ = s11a.build_site_nights(with_intervals(complaints, keep=~dl), sites)
            m_dl_only = m_dl & ~m_ot
            if not np.array_equal(m_dl_only | m_ot, m0):
                raise AssertionError("deadline split does not partition E0")
            matrices[name] = (m_dl_only, m_ot)
            notes[name] = {"n_deadline_complaints": int(dl.sum()),
                           "site_nights_deadline_only": int(m_dl_only[~artifact].sum()),
                           "site_nights_other": int(m_ot[~artifact].sum())}

    tables, summaries = [], {}
    for name in scenarios:
        if name == "deadline":
            m_dl_only, m_ot = matrices[name]
            p_dl, p_ot = panel_for(m_dl_only, "_deadline"), panel_for(m_ot, "_other")
            panel = p_ot.merge(p_dl[["cell_idx", "week", *[b + "_deadline" for b in s11a.BAND_COLUMNS]]],
                               on=["cell_idx", "week"], validate="one_to_one")
            bands = [b + sfx for sfx in ("_deadline", "_other") for b in s11a.PRIMARY_BANDS]
        else:
            panel = panel_for(matrices[name])
            bands = list(s11a.PRIMARY_BANDS)
        print(f"Estimating {name} ...")
        table, summary = s11b.estimate(cells, panel, name, {"period": "week", "bands": bands})
        table["site_nights"] = int((matrices[name][1] | matrices[name][0])[~artifact].sum()
                                   if name == "deadline" else matrices[name][~artifact].sum())
        tables.append(table)
        summaries[name] = {k: summary[k] for k in ("sample", "exposure_variation", "fit", "vcov", "bands")}
        summaries[name]["notes"] = notes.get(name, {})
        print(table[["spec", "band", "estimate", "std_error", "p_value"]].to_string(index=False))

    estimates = pd.concat(tables, ignore_index=True)
    base = estimates[estimates["spec"] == "E0"].set_index("band")["estimate"]
    estimates["ratio_to_E0"] = [
        r.estimate / base[r.band] if r.band in base.index else
        r.estimate / base[r.band.rsplit("_", 1)[0]] for r in estimates.itertuples()]
    estimates["sign_matches_E0"] = [
        np.sign(r.estimate) == np.sign(base[r.band if r.band in base.index else r.band.rsplit("_", 1)[0]])
        for r in estimates.itertuples()]

    if "deadline" in summaries:
        dl = summaries["deadline"]
        dl["difference_deadline_minus_other"] = deadline_differences(
            estimates[estimates["spec"] == "deadline"], dl["vcov"], dl["bands"])

    summary = {
        "stage": "11c sensitivity",
        "status": "sensitivity only; estimand = reported-open status, not physical darkness",
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scenarios": list(scenarios),
        "definitions": {"lag_days": LAG_DAYS, "merge_gaps_nights": list(MERGE_GAPS),
                        "deadline_hours": list(DEADLINE_HOURS), "end_fraction": END_FRACTION},
        "e0_equals_stored_panel": True,
        "inputs": {f: s7._file_fingerprint(s11a.OUT_DIR / f)["sha256"]
                   for f in (s11a.CELLS_FILENAME, s11a.PANEL_FILENAME)},
        "results": summaries,
        "git": s7._git_state(),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / (ESTIMATES_FILENAME + ".tmp")
    estimates.to_csv(tmp, index=False)
    os.replace(tmp, out_dir / ESTIMATES_FILENAME)
    tmp = out_dir / (SUMMARY_FILENAME + ".tmp")
    tmp.write_text(json.dumps(summary, indent=2, allow_nan=False, default=str))
    os.replace(tmp, out_dir / SUMMARY_FILENAME)
    return estimates, summary


def deadline_differences(table, vcov, bands):
    """Deadline minus other coefficient per primary band, with covariance-based SE."""

    est = table.set_index("band")["estimate"]
    v = np.asarray(vcov)
    out = {}
    for band in s11a.PRIMARY_BANDS:
        i, j = bands.index(band + "_deadline"), bands.index(band + "_other")
        diff = float(est[band + "_deadline"] - est[band + "_other"])
        se = math.sqrt(v[i, i] + v[j, j] - 2 * v[i, j])
        out[band] = {"difference": diff, "std_error": se, "z": diff / se,
                     "p_value": math.erfc(abs(diff / se) / math.sqrt(2.0))}
    return out


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Stage 11c D-S11-1 and timing sensitivity")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--scenario", choices=SCENARIOS, action="append")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    run(args.out, tuple(args.scenario) if args.scenario else SCENARIOS)
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
