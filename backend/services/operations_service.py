"""
Operations explorer: demand, backlog, capacity and replay views of the frozen FIFO dispatch.

Every number comes from the frozen Stage 13 simulation (dispatch_simulation.simulate, policy
"fifo") run through the Stage 14 wrapper (capacity_analysis.simulate_fifo) on the immutable
Stage 12 job set. Capacity K is the only input varied; metrics use the Stage 13/14 metric
functions unchanged. Runs are cached per K and nothing is written to data/ or outputs/.
"""
from functools import lru_cache
import logging
import threading
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from .. import config

logger = logging.getLogger("lightsafe.operations")

_lock = threading.Lock()
_inputs: Optional[Dict[str, Any]] = None


def _modules():
    from src.features import operational_priority as s12
    from src.models import capacity_analysis as s14
    from src.models import dispatch_simulation as s13
    return s12, s13, s14


def inputs() -> Dict[str, Any]:
    """Stage 12 jobs + arrival flags, frozen K and regime split, site coordinates. Loaded once."""
    global _inputs
    if _inputs is not None:
        return _inputs
    with _lock:
        if _inputs is not None:
            return _inputs
        s12, s13, s14 = _modules()
        jobs, arrivals = s14.load_inputs()
        k0, rate = s14.baseline_capacity(arrivals)
        split_s = s14.regime_split_s(jobs)
        counts = s14.daily_arrivals(arrivals, jobs)
        diag = s14.arrival_diagnostics(counts, split_s)
        sites = pd.read_parquet(config.OUTAGE_SITES_FILE, columns=["site_id", "latitude", "longitude"])
        sites["site_id"] = sites["site_id"].astype(str)
        coords = jobs[["site_id"]].merge(sites.drop_duplicates("site_id"), on="site_id", how="left")
        frozen = (pd.read_csv(config.STAGE14_CAPACITY_METRICS_FILE)
                  if config.STAGE14_CAPACITY_METRICS_FILE.exists() else pd.DataFrame())
        _inputs = {
            "jobs": jobs, "k0": int(k0), "calibration_rate": float(rate), "split_s": int(split_s),
            "t0": int(s13.first_decision_s()), "horizon_end_s": int(s13._s(s12.EVALUATION_END)),
            "day_s": int(s13.DAY_S), "grid": list(s14.CAPACITY_GRID),
            "targets": [{"name": n, "threshold_days": d, "required_pct": p} for n, d, p in s14.SERVICE_TARGETS],
            "daily_counts": counts, "arrival_diag": diag,
            "lat": coords["latitude"].to_numpy(float), "lon": coords["longitude"].to_numpy(float),
            "frozen_metrics": frozen,
        }
        logger.info("Operations inputs loaded: %d jobs, K0=%d.", len(jobs), k0)
        return _inputs


@lru_cache(maxsize=16)
def _run(k: int):
    _, _, s14 = _modules()
    return s14.simulate_fifo(inputs()["jobs"], k)


def _check_k(k: int) -> int:
    k = int(k)
    if not config.CAPACITY_MIN <= k <= config.CAPACITY_MAX:
        raise ValueError(f"capacity must be between {config.CAPACITY_MIN} and {config.CAPACITY_MAX}")
    return k


def _f(v, d=4):
    if v is None:
        return None
    v = float(v)
    return None if np.isnan(v) else round(v, d)


def _day_index(inp, seconds: np.ndarray) -> np.ndarray:
    return (seconds - inp["t0"]) // inp["day_s"]


def daily_series(k: int) -> Dict[str, Any]:
    """Per-decision demand, dispatch, resolutions and backlog for one K (columnar)."""
    inp = inputs()
    log, series = _run(k)
    t = series["decision_s"].to_numpy(np.int64)
    arrival = inp["jobs"]["arrival_s"].to_numpy(np.int64)
    # Jobs that became known at each decision (arrival strictly before t, as in the simulation).
    known = np.searchsorted(arrival, t, side="left")
    new = np.diff(np.concatenate([[0], known]))
    resolved_idx = _day_index(inp, log["resolved_s"].to_numpy(np.int64))
    resolved = np.bincount(resolved_idx[resolved_idx < len(t)], minlength=len(t))[: len(t)]
    return {
        "date": pd.to_datetime(t, unit="s").strftime("%Y-%m-%d").tolist(),
        "new_jobs": new.astype(int).tolist(),
        "dispatched": series["selected"].astype(int).tolist(),
        "resolved": resolved.astype(int).tolist(),
        "backlog": series["waiting_after"].astype(int).tolist(),
        "in_service": series["in_service"].astype(int).tolist(),
    }


def get_capacity(k: int) -> Dict[str, Any]:
    _, s13, s14 = _modules()
    k = _check_k(k)
    inp = inputs()
    log, series = _run(k)
    split_s = inp["split_s"]
    m = s14.capacity_metrics(log, series, k, split_s)
    regimes = s14.regime_rows(log, series, k, split_s)
    targets = s14.service_targets(pd.DataFrame(regimes))

    # Re-run vs the stored frozen Stage 14 table (only for K on the pre-specified grid).
    frozen_check = None
    fz = inp["frozen_metrics"]
    if k in inp["grid"] and not fz.empty and k in set(fz["capacity_k"]):
        row = fz[fz["capacity_k"] == k].iloc[0]
        keys = ("wait_mean_days", "wait_p90_days", "backlog_max", "served_within_7d_pct",
                "drain_days_after_horizon", "n_served")
        frozen_check = all(abs(float(row[c]) - float(m[c])) < 1e-9 for c in keys)

    quarters = inp["arrival_diag"]
    q = quarters[quarters["scope"].str.startswith("quarter:")]
    demand = {
        "overall_mean_per_day": _f(quarters.loc[quarters["scope"] == "all", "arrivals_per_day_mean"].iloc[0], 2),
        "stable_mean_per_day": _f(quarters.loc[quarters["scope"] == "stable", "arrivals_per_day_mean"].iloc[0], 2),
        "overload_mean_per_day": _f(quarters.loc[quarters["scope"] == "overload", "arrivals_per_day_mean"].iloc[0], 2),
        "pct_days_arrivals_gt_k": _f(100 * float((inp["daily_counts"] > k).mean()), 2),
        "quarters": [{"quarter": s.split(":", 1)[1], "mean_per_day": _f(v, 2), "max_per_day": int(mx)}
                     for s, v, mx in zip(q["scope"], q["arrivals_per_day_mean"], q["arrivals_per_day_max"])],
    }

    return {
        "capacity_k": k,
        "policy": "FIFO (frozen baseline)",
        "baseline_k": inp["k0"],
        "baseline_rule": f"ceil(2023 daily non-artifact arrivals {inp['calibration_rate']:.2f} / 0.9)",
        "prespecified_grid": inp["grid"],
        "on_prespecified_grid": k in inp["grid"],
        "matches_frozen_stage14_output": frozen_check,
        "range": {"min": config.CAPACITY_MIN, "max": config.CAPACITY_MAX},
        "overload_start": str(pd.to_datetime(split_s, unit="s").date()),
        "horizon_end": str(pd.to_datetime(inp["horizon_end_s"], unit="s").date()),
        "assumptions": {
            "decision_cadence": f"daily at {s13.DECISION_HOUR:02d}:00",
            "service_days": s13.SERVICE_DAYS,
            "initial_queue": "empty at 2024-01-01",
            "drain": "decisions continue after the last arrival until the queue is empty",
            "unit": "K = simulated complaint-jobs dispatched per daily decision (not crews)",
        },
        "metrics": {key: (_f(v) if isinstance(v, (float, np.floating)) else v) for key, v in m.items()},
        "regimes": [{key: (_f(v) if isinstance(v, (float, np.floating)) else v) for key, v in r.items()}
                    for r in regimes],
        "service_targets": [{**r, "achieved_pct": _f(r["achieved_pct"], 2), "meets_target": bool(r["meets_target"]),
                             "capacity_k": int(r["capacity_k"]), "threshold_days": int(r["threshold_days"])}
                            for r in targets.to_dict("records")],
        "demand": demand,
        "series": daily_series(k),
    }


def get_capacity_curve() -> Dict[str, Any]:
    """Headline metrics across the explorer range (every K re-simulated, cached)."""
    _, _, s14 = _modules()
    inp = inputs()
    rows = []
    for k in range(config.CAPACITY_MIN, config.CAPACITY_MAX + 1, 5):
        log, series = _run(k)
        m = s14.capacity_metrics(log, series, k, inp["split_s"])
        rows.append({"capacity_k": k, "on_prespecified_grid": k in inp["grid"],
                     **{c: _f(m[c]) for c in ("wait_mean_days", "wait_p90_days", "wait_max_days",
                                              "served_within_7d_pct", "served_within_14d_pct",
                                              "served_within_30d_pct", "backlog_mean", "utilisation")},
                     "backlog_max": int(m["backlog_max"]), "drain_days_after_horizon": int(m["drain_days_after_horizon"])})
    return {"rows": rows}


def get_replay(k: int) -> Dict[str, Any]:
    """Columnar per-job replay data: when each job became known and when it was dispatched."""
    k = _check_k(k)
    inp = inputs()
    log, series = _run(k)
    t = series["decision_s"].to_numpy(np.int64)
    arrival = log["arrival_s"].to_numpy(np.int64)
    known_day = np.searchsorted(t, arrival, side="right")       # first decision strictly after arrival
    dispatch_day = _day_index(inp, log["selected_s"].to_numpy(np.int64))
    if np.any(dispatch_day < known_day):
        raise AssertionError("replay: job dispatched before it was known")
    boroughs = sorted(log["borough"].fillna("<missing>").unique().tolist())
    bcode = pd.Categorical(log["borough"].fillna("<missing>"), categories=boroughs).codes
    lat, lon = inp["lat"], inp["lon"]
    ok = ~(np.isnan(lat) | np.isnan(lon))
    return {
        "capacity_k": k,
        "policy": "FIFO (frozen baseline)",
        "service_days": 1,
        "overload_start": str(pd.to_datetime(inp["split_s"], unit="s").date()),
        "horizon_end": str(pd.to_datetime(inp["horizon_end_s"], unit="s").date()),
        "n_jobs": int(len(log)),
        "n_jobs_without_coordinates": int((~ok).sum()),
        "boroughs": boroughs,
        "series": daily_series(k),
        "jobs": {
            "lon": np.where(ok, np.round(lon, 5), 0).tolist(),
            "lat": np.where(ok, np.round(lat, 5), 0).tolist(),
            "has_coords": ok.astype(int).tolist(),
            "known_day": known_day.astype(int).tolist(),
            "dispatch_day": dispatch_day.astype(int).tolist(),
            "borough": bcode.astype(int).tolist(),
        },
    }


def warm() -> None:
    """Load inputs and the baseline run in the background so the first request is fast."""
    try:
        inp = inputs()
        _run(inp["k0"])
    except Exception:  # noqa: BLE001 - surfaced again on request
        logger.exception("Operations warm-up failed")
