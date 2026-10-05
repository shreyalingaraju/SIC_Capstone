"""
Stage 13: repair prioritization engine (FIFO vs LightSafe Causal).

Reads data/processed/outages_scored.parquet (Stage 12, read only) and writes
outputs/prioritized_queue.csv and outputs/fifo_vs_lightsafe_comparison.csv.

Queues (deterministic; ties never depend on input order):
- FIFO:       created_date ascending, then unique_key ascending.
- LightSafe:  priority_score descending, then created_date ascending, then
              unique_key ascending.

Scope. Only outages with scored == True enter the simulation. Stage 12
leaves records it could not score (no crime coverage for the look-back
window) without a score; they are excluded from both queues and reported, not
placed at the end and not treated as high or low priority. Both queues contain
exactly the same outages, so the comparison is like for like.

Simulation. A static backlog: every scored outage is waiting at day 0 and the
crew completes K repairs per day (default 20) in queue order, so the outage at
rank r is repaired on day ceil(r / K). created_date only defines the FIFO
order; it does not delay availability (the data are retrospective).

Expected impact. The impact credited when an outage is repaired is its Stage 12
`priority_score` (0-100), unchanged and with no extra factor. The cumulative
"expected crimes prevented" is therefore the cumulative sum of this index, in
index points, not a count of crimes: Stage 12's score is a decision-support
index, not a probability, built from a tau_net that is not statistically
distinguishable from zero and from observed (post-closure) durations. The
comparison is a benchmark of the index under this simulation, not evidence that
the queue prevents crime. Because both queues eventually repair the same set,
the final cumulative totals are equal; the difference is in how fast impact
accumulates.

Improvement (%) = (LightSafe - FIFO) / FIFO * 100; empty (NaN) when the FIFO
cumulative impact is 0.
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUT_DIR = PROJECT_ROOT / "outputs"

SCORED_FILE = PROCESSED_DIR / "outages_scored.parquet"
QUEUE_FILENAME = "prioritized_queue.csv"
COMPARISON_FILENAME = "fifo_vs_lightsafe_comparison.csv"

DEFAULT_CAPACITY = 20

ID = "unique_key"
CREATED = "created_date"
SCORE = "priority_score"
IMPACT = "priority_score"  # expected impact credited per repair (see module doc)

METHOD_FIFO = "FIFO"
METHOD_LIGHTSAFE = "LightSafe"

REQUIRED_COLUMNS = (ID, CREATED, SCORE, "priority_tier", "raw_priority",
                    "local_crime_rate", "duration_factor", "scored",
                    "exclusion_reason")

QUEUE_COLUMNS = [
    "method", "queue_rank", "repair_day", "outage_id", "created_date",
    "priority_score", "priority_tier", "expected_impact", "raw_priority",
    "local_crime_rate", "duration_factor", "borough",
]
COMPARISON_COLUMNS = [
    "repair_capacity_k", "day", "cumulative_repairs", "fifo_cumulative_impact",
    "lightsafe_cumulative_impact", "absolute_difference", "improvement_pct",
    "impact_unit",
]
IMPACT_UNIT = "priority_score index points (not a crime count)"


# ============================================================
# Inputs
# ============================================================

def load_scored(path=SCORED_FILE):
    frame = pd.read_parquet(path)
    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"{path} is missing required columns: {missing}")
    if frame[ID].isna().any() or not frame[ID].is_unique:
        raise ValueError(f"{ID} must be unique and non-null")
    return frame


def select_dispatchable(frame):
    """Scored outages only; returns (dispatchable, n_excluded, reasons)."""

    ok = frame["scored"].fillna(False).astype(bool)
    dispatch = frame[ok]
    if (dispatch[SCORE].isna().any() or dispatch[CREATED].isna().any()
            or not np.isfinite(dispatch[SCORE].astype(float)).all()):
        raise ValueError("scored outages must have a finite score and created_date")
    reasons = frame.loc[~ok, "exclusion_reason"].fillna("unspecified").value_counts().to_dict()
    return dispatch, int((~ok).sum()), reasons


# ============================================================
# Queues and simulation
# ============================================================

def fifo_order(frame):
    return frame.sort_values([CREATED, ID], ascending=[True, True], kind="mergesort")


def lightsafe_order(frame):
    return frame.sort_values([SCORE, CREATED, ID], ascending=[False, True, True],
                             kind="mergesort")


def build_queue(frame, method, capacity):
    """Ordered queue with rank, repair day (ceil(rank / K)) and the impact used."""

    if capacity < 1 or int(capacity) != capacity:
        raise ValueError("repair capacity K must be a positive integer")
    ordered = {METHOD_FIFO: fifo_order, METHOD_LIGHTSAFE: lightsafe_order}[method](frame)
    queue = pd.DataFrame({
        "method": method,
        "queue_rank": np.arange(1, len(ordered) + 1),
        "outage_id": ordered[ID].to_numpy(),
        "created_date": ordered[CREATED].to_numpy(),
        "priority_score": ordered[SCORE].to_numpy(float),
        "priority_tier": ordered["priority_tier"].to_numpy(),
        "expected_impact": ordered[IMPACT].to_numpy(float),
        "raw_priority": ordered["raw_priority"].to_numpy(float),
        "local_crime_rate": ordered["local_crime_rate"].to_numpy(float),
        "duration_factor": ordered["duration_factor"].to_numpy(float),
        "borough": ordered["borough"].to_numpy() if "borough" in ordered else None,
    })
    queue["repair_day"] = (queue["queue_rank"] - 1) // int(capacity) + 1
    return queue[QUEUE_COLUMNS]


def improvement_pct(lightsafe, fifo):
    """(LightSafe - FIFO) / FIFO * 100, elementwise; NaN where FIFO == 0."""

    lightsafe = np.asarray(lightsafe, dtype=float)
    fifo = np.asarray(fifo, dtype=float)
    out = np.full(np.broadcast(lightsafe, fifo).shape, np.nan)
    np.divide(lightsafe - fifo, fifo, out=out,
              where=(fifo != 0) & np.isfinite(fifo) & np.isfinite(lightsafe))
    return out * 100.0


def compare(fifo_queue, lightsafe_queue, capacity):
    """Per-day cumulative impact for both queues (end of each repair day)."""

    def by_day(queue):
        grouped = queue.groupby("repair_day").agg(
            repairs=("queue_rank", "size"), impact=("expected_impact", "sum"))
        return grouped.assign(cumulative_repairs=grouped["repairs"].cumsum(),
                              cumulative_impact=grouped["impact"].cumsum())

    f, l = by_day(fifo_queue), by_day(lightsafe_queue)
    comparison = pd.DataFrame({
        "repair_capacity_k": int(capacity),
        "day": f.index.to_numpy(),
        "cumulative_repairs": f["cumulative_repairs"].to_numpy(),
        "fifo_cumulative_impact": f["cumulative_impact"].to_numpy(),
        "lightsafe_cumulative_impact": l["cumulative_impact"].to_numpy(),
    })
    comparison["absolute_difference"] = (
        comparison["lightsafe_cumulative_impact"] - comparison["fifo_cumulative_impact"])
    comparison["improvement_pct"] = improvement_pct(
        comparison["lightsafe_cumulative_impact"], comparison["fifo_cumulative_impact"])
    comparison["impact_unit"] = IMPACT_UNIT
    return comparison[COMPARISON_COLUMNS]


def run(frame, capacity=DEFAULT_CAPACITY):
    dispatch, n_excluded, reasons = select_dispatchable(frame)
    fifo = build_queue(dispatch, METHOD_FIFO, capacity)
    lightsafe = build_queue(dispatch, METHOD_LIGHTSAFE, capacity)
    comparison = compare(fifo, lightsafe, capacity)
    info = {"n_input": int(len(frame)), "n_simulated": int(len(dispatch)),
            "n_excluded": n_excluded, "exclusion_reasons": reasons,
            "capacity": int(capacity)}
    return pd.concat([fifo, lightsafe], ignore_index=True), comparison, info


# ============================================================
# Main
# ============================================================

def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Stage 13 prioritization engine")
    parser.add_argument("--scored", type=Path, default=SCORED_FILE,
                        help="Stage 12 outages_scored.parquet (read only)")
    parser.add_argument("--capacity", type=int, default=DEFAULT_CAPACITY,
                        help="repairs completed per day (K)")
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    print("Loading Stage 12 output...")
    queue, comparison, info = run(load_scored(args.scored), args.capacity)

    args.out.mkdir(parents=True, exist_ok=True)
    queue.to_csv(args.out / QUEUE_FILENAME, index=False, float_format="%.10g")
    comparison.to_csv(args.out / COMPARISON_FILENAME, index=False, float_format="%.10g")

    last = comparison.iloc[-1]
    print(f"Outages in: {info['n_input']:,}; simulated: {info['n_simulated']:,}; "
          f"excluded (unscored): {info['n_excluded']:,} {info['exclusion_reasons']}")
    print(f"K = {info['capacity']}; days to clear: {int(last['day']):,}")
    for day in (1, 30, 365):
        if day <= len(comparison):
            r = comparison.iloc[day - 1]
            print(f"day {day}: FIFO {r['fifo_cumulative_impact']:.2f} | LightSafe "
                  f"{r['lightsafe_cumulative_impact']:.2f} | improvement {r['improvement_pct']:.1f}%")
    print(f"\nQueue: {args.out / QUEUE_FILENAME}\nComparison: {args.out / COMPARISON_FILENAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
