"""
Stage 11b: PPML / fixed-effects Poisson on the reported-open exposure panel.

Reads data/processed/stage11/exposure_{cells,panel}.parquet (Stage 11a) and
writes stage11_estimates.csv and stage11_summary.json to --out
(default outputs/stage11_exposure/). Nothing else is read or written.

ESTIMAND. Coefficients are the change in log expected night crime in an
H3 res-10 cell per additional REPORTED-OPEN light-night in a distance band
(311 open status). They are not physical-darkness or repair effects, and
the frozen result is an association, not a causal estimate. Their
relationship to physical darkness is uncertain: the Stage 11c timing
sensitivity rejected reading them as an attenuated (conservative) darkness
signal. No physical-repair claim is supported. See
docs/stage11_exposure_analysis.md.

Model:  E[y_ct] = exp(alpha_c + gamma_{b(c),t} + sum_k beta_k x_kct)
- c = H3 res-10 cell, t = ISO week (fortnight in the sensitivity spec),
  b(c) = cell borough; alpha_c cell FE, gamma borough x period FE.
- Estimated by Poisson pseudo-maximum likelihood (PPML) with the fixed
  effects partialled out: IRLS where each weighted least-squares step
  removes both FE sets by weighted alternating projections (Guimaraes and
  Portugal 2010; Correia, Guimaraes and Zylkin 2020). The Poisson FE MLE
  equals the conditional (fixed-effects) Poisson estimator for alpha_c.
- Observations in cells or borough-periods with zero total crime are
  dropped iteratively: their FE diverge and they carry no information.
- Standard errors: cluster-robust sandwich on the partialled-out scores,
  clustered on the H3 res-7 parent cell, with the G/(G-1) correction;
  normal reference distribution (as Stages 9 and 11 legacy).

Specifications (approved set only):
- primary:   res-10 x ISO week, bands 0-100, 100-250, 250-500 m
- cutoff_750: as primary plus the 500-750 m band (cutoff sensitivity)
- fortnight: res-10 x fortnight (pairs of ISO weeks), primary bands
"""

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.features import build_exposure_panel as s11a  # noqa: E402
from src.features import match_controls as s7  # noqa: E402
from src.models import did_model  # noqa: E402  (D13 minimum-cluster rule)

PANEL_DIR = s11a.OUT_DIR
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "stage11_exposure"
ESTIMATES_FILENAME = "stage11_estimates.csv"
SUMMARY_FILENAME = "stage11_summary.json"

MIN_CLUSTERS = did_model.MIN_CLUSTERS
Z_95 = 1.959963984540054
NIGHTS_PER_WEEK = 7

IRLS_TOL = 1e-10       # relative deviance change
IRLS_MAXIT = 100
AP_TOL = 1e-12         # max absolute FE-mean update, relative to column scale
AP_MAXIT = 10_000

SPECS = {
    "primary": {"period": "week", "bands": s11a.PRIMARY_BANDS},
    "cutoff_750": {"period": "week", "bands": s11a.BAND_COLUMNS},
    "fortnight": {"period": "fortnight", "bands": s11a.PRIMARY_BANDS,
                  "note": "coefficient is per light-night on fortnight crime; an extra "
                          "night raises one of two weeks, so it is about half the weekly "
                          "coefficient and not directly comparable to it"},
}

# Stored verbatim in the frozen Stage 11 outputs. Its final clause ("an
# attenuated signal of darkness") is withdrawn; docs/stage11_exposure_analysis.md
# gives the frozen interpretation. Left unchanged so the outputs reproduce.
INTERPRETATION = (
    "per additional reported-open light-night (311 open status) in the band; "
    "not a physical-darkness or repair effect; under the non-differential "
    "timing-error assumption an attenuated signal of darkness"
)

COLUMNS = [
    "spec", "band", "estimate", "std_error", "z", "p_value", "ci_lower", "ci_upper",
    "pct_change_per_7_nights", "pct_ci_lower", "pct_ci_upper",
    "n_obs", "n_cells", "n_periods", "n_fe_borough_period", "n_clusters",
    "n_obs_dropped", "exposure_total", "mean_exposure", "period", "estimator",
    "fixed_effects", "cluster", "estimand",
]


# ============================================================
# PPML with two high-dimensional fixed effects
# ============================================================

def _group_means(values, weights, codes, n_groups, weight_sums):
    out = np.empty((n_groups, values.shape[1]))
    for j in range(values.shape[1]):
        out[:, j] = np.bincount(codes, weights=weights * values[:, j], minlength=n_groups)
    return out / weight_sums[:, None]


def demean(values, weights, fe_codes, tol=AP_TOL, maxit=AP_MAXIT):
    """
    Weighted residual of `values` (n x k) on the FE dummies, by alternating
    projections. Returns (residuals, sweeps). Raises if it does not converge.
    """

    v = np.array(values, dtype=np.float64, copy=True)
    if v.ndim == 1:
        v = v[:, None]
    scale = np.maximum(np.abs(v).max(axis=0), 1.0)
    groups = []
    for codes in fe_codes:
        n_groups = int(codes.max()) + 1
        groups.append((codes, n_groups,
                       np.bincount(codes, weights=weights, minlength=n_groups)))

    for sweep in range(1, maxit + 1):
        change = 0.0
        for codes, n_groups, wsum in groups:
            means = _group_means(v, weights, codes, n_groups, wsum)
            v -= means[codes]
            change = max(change, float((np.abs(means) / scale).max()))
        if change < tol:
            return v, sweep
    raise RuntimeError(f"alternating projections did not converge in {maxit} sweeps")


def ppml_hdfe(y, X, fe_codes, cluster_codes):
    """
    PPML with the given FE sets partialled out. Returns a dict with beta,
    the cluster-robust covariance (G/(G-1)), fitted mu and convergence
    information. y must contain no all-zero FE groups (drop them first).
    """

    y = np.asarray(y, dtype=np.float64)
    X = np.asarray(X, dtype=np.float64)
    n, k = X.shape

    mu = (y + y.mean()) / 2.0
    eta = np.log(mu)
    beta = np.zeros(k)
    Xt = None
    deviance_old = np.inf
    history = []

    for iteration in range(1, IRLS_MAXIT + 1):
        u = (y - mu) / mu
        if Xt is None:
            # First step: eta is not yet of the form FE + X beta.
            Zt, sweeps = demean(np.column_stack([eta + u, X]), mu, fe_codes)
            zt, Xt = Zt[:, 0], Zt[:, 1:]
        else:
            # M(z) = M(X) beta + M(u) because eta = FE + X beta; X is
            # warm-started from its previous residual (M kills the FE part).
            Ut, sweeps = demean(np.column_stack([u, Xt]), mu, fe_codes)
            Xt = Ut[:, 1:]
            zt = Xt @ beta + Ut[:, 0]

        WX = Xt * mu[:, None]
        beta = np.linalg.solve(Xt.T @ WX, WX.T @ zt)
        eta = (eta + u) - (zt - Xt @ beta)
        mu = np.exp(eta)

        with np.errstate(divide="ignore", invalid="ignore"):
            ylogy = np.where(y > 0, y * np.log(y / mu), 0.0)
        deviance = 2.0 * float(np.sum(ylogy - (y - mu)))
        history.append({"iteration": iteration, "deviance": deviance, "ap_sweeps": sweeps})
        if abs(deviance - deviance_old) / max(abs(deviance), 0.1) < IRLS_TOL:
            break
        deviance_old = deviance
    else:
        raise RuntimeError(f"IRLS did not converge in {IRLS_MAXIT} iterations")

    # Covariance at the solution: partial out the FE with the final weights.
    Xt, _ = demean(Xt, mu, fe_codes)
    bread = np.linalg.inv(Xt.T @ (Xt * mu[:, None]))
    scores = Xt * (y - mu)[:, None]
    n_clusters = int(cluster_codes.max()) + 1
    cluster_scores = np.zeros((n_clusters, k))
    for j in range(k):
        cluster_scores[:, j] = np.bincount(cluster_codes, weights=scores[:, j],
                                           minlength=n_clusters)
    meat = cluster_scores.T @ cluster_scores
    vcov = (n_clusters / (n_clusters - 1)) * bread @ meat @ bread

    return {
        "beta": beta,
        "vcov": vcov,
        "vcov_uncorrected": bread @ meat @ bread,
        "mu": mu,
        "iterations": len(history),
        "deviance": history[-1]["deviance"],
        "history": history,
        "n_clusters": n_clusters,
        "max_abs_score_sum": float(np.abs(scores.sum(axis=0)).max()),
    }


# ============================================================
# Data preparation
# ============================================================

def load_inputs(panel_dir):
    cells = pd.read_parquet(panel_dir / s11a.CELLS_FILENAME)
    panel = pd.read_parquet(panel_dir / s11a.PANEL_FILENAME)
    if len(panel) != len(cells) * s11a.N_WEEKS:
        raise AssertionError("panel rows != cells x weeks")
    return cells, panel


def aggregate(panel, period):
    """Week panel, or fortnight panel (sums over pairs of ISO weeks)."""

    if period == "week":
        out = panel.rename(columns={"week": "period"})
    elif period == "fortnight":
        if s11a.N_WEEKS % 2:
            raise AssertionError("fortnights need an even number of weeks")
        frame = panel.assign(period=panel["week"] // 2)
        out = (frame.groupby(["cell_idx", "period"], sort=True)
               [["crime", *s11a.BAND_COLUMNS]].sum().reset_index())
    else:
        raise ValueError(period)
    return out


def drop_zero_groups(frame):
    """Iteratively drop cells and borough-periods whose total crime is 0."""

    keep = np.ones(len(frame), dtype=bool)
    rounds = 0
    while True:
        rounds += 1
        sub = frame.loc[keep]
        cell_tot = sub.groupby("cell_idx")["crime"].transform("sum")
        bp_tot = sub.groupby("bp_code")["crime"].transform("sum")
        bad = (cell_tot == 0) | (bp_tot == 0)
        if not bad.any():
            return keep, rounds
        keep[sub.index[bad.to_numpy()]] = False


def prepare(cells, panel, spec):
    frame = aggregate(panel, spec["period"])
    frame = frame.merge(cells[["cell_idx", "borough", "cluster_res7"]], on="cell_idx",
                        how="left", validate="many_to_one")
    if frame["borough"].isna().any() or frame["cluster_res7"].isna().any():
        raise AssertionError("cell without borough or res-7 cluster")
    frame["bp_code"] = pd.factorize(frame["borough"].astype(str) + "|" + frame["period"].astype(str),
                                    sort=True)[0]
    frame = frame.reset_index(drop=True)

    keep, rounds = drop_zero_groups(frame)
    est = frame.loc[keep].reset_index(drop=True)
    est["cell_code"] = pd.factorize(est["cell_idx"], sort=True)[0]
    est["bp_code"] = pd.factorize(est["bp_code"], sort=True)[0]
    est["cluster_code"] = pd.factorize(est["cluster_res7"].astype(str), sort=True)[0]

    info = {
        "n_obs_panel": int(len(frame)),
        "n_obs": int(len(est)),
        "n_obs_dropped": int((~keep).sum()),
        "n_cells_panel": int(frame["cell_idx"].nunique()),
        "n_cells": int(est["cell_code"].nunique()),
        "n_periods": int(est["period"].nunique()),
        "n_fe_borough_period": int(est["bp_code"].nunique()),
        "n_clusters": int(est["cluster_code"].nunique()),
        "drop_rounds": rounds,
        "crime_total_panel": int(frame["crime"].sum()),
        "crime_total_estimation": int(est["crime"].sum()),
    }
    if info["crime_total_panel"] != info["crime_total_estimation"]:
        raise AssertionError("dropping zero groups removed crimes")
    return est, info


def fe_variation(est, bands):
    """Unweighted within (cell + borough x period) variance share per band."""

    X = est[list(bands)].to_numpy(np.float64)
    Xt, _ = demean(X, np.ones(len(est)), [est["cell_code"].to_numpy(), est["bp_code"].to_numpy()],
                   tol=1e-10)
    out = {}
    for j, band in enumerate(bands):
        x = X[:, j]
        cell_sd = est.groupby("cell_code")[band].transform("std").fillna(0)
        out[band] = {
            "nonzero_share": float((x > 0).mean()),
            "mean": float(x.mean()),
            "total": int(x.sum()),
            "within_variance_share": float(Xt[:, j].var() / x.var()) if x.var() > 0 else 0.0,
            "cells_without_within_variation": int((est.assign(_s=cell_sd).groupby("cell_code")["_s"].first() == 0).sum()),
        }
        if out[band]["within_variance_share"] < 1e-6:
            raise AssertionError(f"{band}: no exposure variation left after the fixed effects")
    return out


# ============================================================
# Estimation and output
# ============================================================

def estimate(cells, panel, name, spec):
    est, info = prepare(cells, panel, spec)
    if info["n_clusters"] < MIN_CLUSTERS:
        raise ValueError(f"{name}: {info['n_clusters']} res-7 clusters < {MIN_CLUSTERS} (D13)")
    bands = list(spec["bands"])
    variation = fe_variation(est, bands)

    t0 = time.perf_counter()
    fit = ppml_hdfe(est["crime"].to_numpy(), est[bands].to_numpy(),
                    [est["cell_code"].to_numpy(), est["bp_code"].to_numpy()],
                    est["cluster_code"].to_numpy())
    runtime = time.perf_counter() - t0

    zero_y = est["crime"].to_numpy() == 0
    fit_info = {
        "converged": True,
        "iterations": fit["iterations"],
        "deviance": fit["deviance"],
        "runtime_s": round(runtime, 1),
        "max_abs_score_sum": fit["max_abs_score_sum"],
        "n_zero_y_with_mu_below_1e-12": int((fit["mu"][zero_y] < 1e-12).sum()),
        "fitted_total_minus_observed": float(fit["mu"].sum() - est["crime"].sum()),
        "pearson_dispersion": float(np.sum((est["crime"].to_numpy() - fit["mu"]) ** 2 / fit["mu"])
                                    / (len(est) - info["n_cells"] - info["n_fe_borough_period"] - len(bands))),
    }

    rows = []
    se = np.sqrt(np.diag(fit["vcov"]))
    for j, band in enumerate(bands):
        b, s = float(fit["beta"][j]), float(se[j])
        lo, hi = b - Z_95 * s, b + Z_95 * s
        rows.append({
            "spec": name, "band": band, "estimate": b, "std_error": s, "z": b / s,
            "p_value": math.erfc(abs(b / s) / math.sqrt(2.0)),
            "ci_lower": lo, "ci_upper": hi,
            "pct_change_per_7_nights": 100 * math.expm1(NIGHTS_PER_WEEK * b),
            "pct_ci_lower": 100 * math.expm1(NIGHTS_PER_WEEK * lo),
            "pct_ci_upper": 100 * math.expm1(NIGHTS_PER_WEEK * hi),
            "n_obs": info["n_obs"], "n_cells": info["n_cells"], "n_periods": info["n_periods"],
            "n_fe_borough_period": info["n_fe_borough_period"], "n_clusters": info["n_clusters"],
            "n_obs_dropped": info["n_obs_dropped"],
            "exposure_total": variation[band]["total"], "mean_exposure": variation[band]["mean"],
            "period": spec["period"],
            "estimator": "PPML (Poisson FE, IRLS + alternating projections)",
            "fixed_effects": f"cell (H3 res-10) + borough x {spec['period']}",
            "cluster": "H3 res-7 parent (CR1-type G/(G-1))",
            "estimand": INTERPRETATION,
        })
    summary = {"sample": info, "exposure_variation": variation, "fit": fit_info,
               "vcov": fit["vcov"].tolist(), "bands": bands,
               "irls_history": fit["history"]}
    return pd.DataFrame(rows, columns=COLUMNS), summary


def run(panel_dir, out_dir, specs=None):
    cells, panel = load_inputs(panel_dir)
    diagnostics_11a = json.loads((panel_dir / s11a.DIAGNOSTICS_FILENAME).read_text())
    for fname in (s11a.CELLS_FILENAME, s11a.PANEL_FILENAME):
        if s7._file_fingerprint(panel_dir / fname)["sha256"] != diagnostics_11a["outputs"][fname]:
            raise AssertionError(f"{fname} does not match the Stage 11a diagnostics hash")

    tables, summaries = [], {}
    for name in specs or SPECS:
        print(f"Estimating {name} ...")
        table, summary = estimate(cells, panel, name, SPECS[name])
        tables.append(table)
        summaries[name] = summary
        print(table[["spec", "band", "estimate", "std_error", "p_value",
                     "pct_change_per_7_nights"]].to_string(index=False))

    estimates = pd.concat(tables, ignore_index=True)
    summary = {
        "stage": "11b exposure model",
        "status": "provisional; estimand = reported-open status, not physical darkness",
        "estimand": INTERPRETATION,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "specs": {k: {"period": v["period"], "bands": list(v["bands"]), "note": v.get("note", "")}
                  for k, v in SPECS.items()},
        "inputs": {fname: diagnostics_11a["outputs"][fname]
                   for fname in (s11a.CELLS_FILENAME, s11a.PANEL_FILENAME)},
        "stage11a_definitions": diagnostics_11a["definitions"],
        "results": summaries,
        "tolerances": {"irls_rel_deviance": IRLS_TOL, "ap": AP_TOL},
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


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Stage 11b PPML exposure model")
    parser.add_argument("--panel-dir", type=Path, default=PANEL_DIR)
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--spec", choices=list(SPECS), action="append",
                        help="run only these specs (default: all approved specs)")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    run(args.panel_dir, args.out, args.spec)
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
