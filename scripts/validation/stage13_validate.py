"""
Stage 13 (prioritization engine) validation.

Usage, from the repository root with the project venv:
    python scripts/validation/stage13_validate.py

Checks the committed outputs/prioritized_queue.csv and
outputs/fifo_vs_lightsafe_comparison.csv (create them with
`python src/models/prioritization_engine.py`), tests the engine on synthetic
inputs, re-runs it into a temporary directory to test determinism, and checks
that the Stage 12 input, the Stage 11 output and the causal panel are
unchanged. Expected last line: "FAILS: none".
"""

import hashlib
import math
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from src.models import prioritization_engine as pe  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}: {name}{' - ' + detail if detail else ''}", flush=True)
    if not ok:
        FAILS.append(name)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


PANEL = Path("data/processed/causal_panel.parquet")
STAGE11 = Path("outputs/displacement_estimates.csv")
before = {p: sha(p) for p in (PANEL, STAGE11, pe.SCORED_FILE)}


def synthetic(created, score, scored=None, ids=None):
    n = len(created)
    return pd.DataFrame({
        pe.ID: ids if ids is not None else np.arange(1, n + 1),
        pe.CREATED: pd.to_datetime(created),
        pe.SCORE: np.asarray(score, float),
        "priority_tier": "Low", "raw_priority": 0.0, "local_crime_rate": 0.0,
        "duration_factor": 1.0,
        "scored": scored if scored is not None else [True] * n,
        "exclusion_reason": [None if s else "x" for s in (scored or [True] * n)],
    })


# ---- synthetic engine tests ----------------------------------------------------
df = synthetic(["2024-01-03", "2024-01-01", "2024-01-02", "2024-01-04"], [5, 1, 9, 9], ids=[40, 10, 30, 20])
fifo = pe.fifo_order(df)
check("FIFO is created_date ascending", list(fifo[pe.ID]) == [10, 30, 40, 20])
light = pe.lightsafe_order(df)
check("LightSafe is score descending; tie broken by created_date then id", list(light[pe.ID]) == [30, 20, 40, 10])
tie = synthetic(["2024-01-01"] * 3, [1, 1, 1], ids=[3, 1, 2])
check("equal created_date: FIFO tie broken by id", list(pe.fifo_order(tie)[pe.ID]) == [1, 2, 3])
check("equal scores and dates: LightSafe tie broken by id", list(pe.lightsafe_order(tie)[pe.ID]) == [1, 2, 3])
check("ordering independent of input order",
      list(pe.lightsafe_order(df.sample(frac=1, random_state=1))[pe.ID]) == list(light[pe.ID])
      and list(pe.fifo_order(df.sample(frac=1, random_state=2))[pe.ID]) == list(fifo[pe.ID]))

big = synthetic(pd.date_range("2024-01-01", periods=45, freq="h"), np.arange(45, 0, -1))
q = pe.build_queue(big, pe.METHOD_FIFO, 20)
check("K=20: repair days are ceil(rank/20); <= 20 repairs per day",
      list(q.repair_day.iloc[[0, 19, 20, 39, 40, 44]]) == [1, 1, 2, 2, 3, 3] and q.groupby("repair_day").size().max() == 20)
q7 = pe.build_queue(big, pe.METHOD_FIFO, 7)
check("K is configurable", q7.groupby("repair_day").size().max() == 7 and q7.repair_day.max() == 7)
for bad in (0, -3):
    try:
        pe.build_queue(big, pe.METHOD_FIFO, bad)
        check(f"invalid K={bad} rejected", False)
    except ValueError:
        check(f"invalid K={bad} rejected", True)

mixed = synthetic(["2024-01-01", "2024-01-02", "2024-01-03"], [3, np.nan, 7], scored=[True, False, True])
queue, comp, info = pe.run(mixed, 20)
check("unscored outages excluded from both queues (not high priority, not appended)",
      info["n_simulated"] == 2 and info["n_excluded"] == 1 and set(queue.outage_id) == {1, 3})
check("both queues hold the same outages", set(queue[queue.method == "FIFO"].outage_id) == set(queue[queue.method == "LightSafe"].outage_id))
try:
    pe.select_dispatchable(synthetic(["2024-01-01"], [np.nan], scored=[True]))
    check("scored row with NaN score raises", False)
except ValueError:
    check("scored row with NaN score raises", True)

# comparison on a hand-computed case: K=2, scores by FIFO order 1,2,3,4 -> LightSafe 4,3,2,1
hand = synthetic(pd.date_range("2024-01-01", periods=4), [1, 2, 3, 4])
_, c, _ = pe.run(hand, 2)
check("hand-computed cumulative impact: FIFO 3, 10; LightSafe 7, 10",
      list(c.fifo_cumulative_impact) == [3, 10] and list(c.lightsafe_cumulative_impact) == [7, 10])
check("hand-computed improvement: (7-3)/3*100 = 133.33%, then 0%",
      math.isclose(c.improvement_pct.iloc[0], 400 / 3) and c.improvement_pct.iloc[1] == 0)
check("absolute difference = LightSafe - FIFO", list(c.absolute_difference) == [4, 0])
check("improvement_pct: FIFO zero gives NaN (never inf)",
      np.isnan(pe.improvement_pct([5.0], [0.0])[0]) and np.isnan(pe.improvement_pct([0.0], [0.0])[0]))
check("improvement_pct: FIFO positive and negative direction", math.isclose(pe.improvement_pct([3.0], [4.0])[0], -25.0))
zero = synthetic(pd.date_range("2024-01-01", periods=4), [0, 0, 0, 0])
_, cz, _ = pe.run(zero, 2)
check("all-zero impact: improvement empty, no inf/NaN in impact columns",
      cz.improvement_pct.isna().all() and np.isfinite(cz[["fifo_cumulative_impact", "lightsafe_cumulative_impact"]]).all().all())

# ---- real outputs ------------------------------------------------------------------
check("input exists", pe.SCORED_FILE.exists())
qpath, cpath = pe.OUTPUT_DIR / pe.QUEUE_FILENAME, pe.OUTPUT_DIR / pe.COMPARISON_FILENAME
check("output files exist", qpath.exists() and cpath.exists())
Q, C = pd.read_csv(qpath), pd.read_csv(cpath)
src = pe.load_scored()
dispatch = src[src.scored]
check("queue schema", list(Q.columns) == pe.QUEUE_COLUMNS)
check("comparison schema", list(C.columns) == pe.COMPARISON_COLUMNS)
for m in (pe.METHOD_FIFO, pe.METHOD_LIGHTSAFE):
    qm = Q[Q.method == m]
    check(f"{m}: every scored outage exactly once, none unscored",
          len(qm) == len(dispatch) and qm.outage_id.is_unique and set(qm.outage_id) == set(dispatch[pe.ID]))
    check(f"{m}: ranks are 1..N", (qm.queue_rank.to_numpy() == np.arange(1, len(qm) + 1)).all())
    check(f"{m}: K=20 respected and day = ceil(rank/20)",
          (qm.repair_day == (qm.queue_rank - 1) // 20 + 1).all() and qm.groupby("repair_day").size().max() == 20)
fq = Q[Q.method == "FIFO"]
lq = Q[Q.method == "LightSafe"]
fkey = pd.to_datetime(fq.created_date)
# Order is checked against an independent sort of the Stage 12 data (the CSV
# rounds scores to 10 significant digits, so ties are judged on the source).
fifo_expected = dispatch.sort_values([pe.CREATED, pe.ID], kind="mergesort")[pe.ID].to_numpy()
light_expected = dispatch.sort_values([pe.SCORE, pe.CREATED, pe.ID], ascending=[False, True, True],
                                      kind="mergesort")[pe.ID].to_numpy()
check("FIFO order correct on the real queue", (fq.outage_id.to_numpy() == fifo_expected).all()
      and (fkey.diff().dropna() >= pd.Timedelta(0)).all())
check("LightSafe order correct on the real queue", (lq.outage_id.to_numpy() == light_expected).all()
      and (lq.priority_score.diff().dropna() <= 1e-9).all())
check("expected_impact equals Stage 12 priority_score (no extra factor)",
      np.allclose(lq.expected_impact, lq.priority_score)
      and np.allclose(lq.set_index("outage_id").priority_score.sort_index(),
                      dispatch.set_index(pe.ID).priority_score.sort_index(), rtol=1e-8))
check("cumulative impact non-decreasing, both methods",
      (C.fifo_cumulative_impact.diff().dropna() >= 0).all() and (C.lightsafe_cumulative_impact.diff().dropna() >= 0).all())
check("cumulative repairs: 20 per day until the last", (C.cumulative_repairs.iloc[:-1] == 20 * C.day.iloc[:-1]).all()
      and C.cumulative_repairs.iloc[-1] == len(dispatch))
check("final totals equal (same set repaired)", math.isclose(C.fifo_cumulative_impact.iloc[-1], C.lightsafe_cumulative_impact.iloc[-1], rel_tol=1e-8))
check("cumulative impact equals the queue sums",
      math.isclose(C.fifo_cumulative_impact.iloc[-1], fq.expected_impact.sum(), rel_tol=1e-8)
      and math.isclose(C.lightsafe_cumulative_impact.iloc[29], lq[lq.repair_day <= 30].expected_impact.sum(), rel_tol=1e-8))
check("LightSafe >= FIFO on every day (greedy by score)", (C.absolute_difference >= -1e-9).all())
ok = C.fifo_cumulative_impact != 0
check("improvement_pct recomputed from the CSV",
      np.allclose(C.improvement_pct[ok], (C.lightsafe_cumulative_impact[ok] - C.fifo_cumulative_impact[ok]) / C.fifo_cumulative_impact[ok] * 100, rtol=1e-6)
      and C.improvement_pct[~ok].isna().all() and not np.isinf(C.improvement_pct.dropna()).any())

# ---- determinism ---------------------------------------------------------------------
tmp = Path(tempfile.mkdtemp(prefix="s13_"))
check("re-run succeeds", pe.main(["--out", str(tmp)]) == 0)
check("re-run outputs are byte-identical",
      sha(tmp / pe.QUEUE_FILENAME) == sha(qpath) and sha(tmp / pe.COMPARISON_FILENAME) == sha(cpath))
tmp20 = Path(tempfile.mkdtemp(prefix="s13_k_"))
pe.main(["--out", str(tmp20), "--capacity", "50"])
check("capacity option changes the schedule", pd.read_csv(tmp20 / pe.QUEUE_FILENAME).repair_day.max() < Q.repair_day.max())

# ---- integrity -----------------------------------------------------------------------
check("Stage 12 input unchanged", sha(pe.SCORED_FILE) == before[pe.SCORED_FILE])
check("Stage 11 output unchanged", sha(STAGE11) == before[STAGE11])
check("causal_panel.parquet unchanged",
      sha(PANEL) == before[PANEL] == "eaa0cd74b75d5f99007ff36a96a562c6fde3c02e264d61cbd25aa652c08d1e35")

print("FAILS:", FAILS or "none")
sys.exit(1 if FAILS else 0)
