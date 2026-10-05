"""
Stage 14 (ILP optimisation layer) validation.

Usage, from the repository root with the project venv:
    python scripts/validation/stage14_validate.py

Tests the solver on small synthetic instances (including a brute-force
optimum), checks the committed outputs/optimal_dispatch_plan.csv and its
summary against the Stage 12 input, re-runs the full optimisation into a
temporary directory (about 2-3 min) to test determinism, and checks that the
Stage 8-13 artifacts are unchanged. Expected last line: "FAILS: none".
"""

import hashlib
import itertools
import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
import pulp  # noqa: E402
from src.optimization import ilp_solver as ilp  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}: {name}{' - ' + detail if detail else ''}", flush=True)
    if not ok:
        FAILS.append(name)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


GUARDED = [Path(p) for p in (
    "data/processed/causal_panel.parquet", "outputs/displacement_estimates.csv",
    "data/processed/outages_scored.parquet", "outputs/prioritized_queue.csv",
    "outputs/fifo_vs_lightsafe_comparison.csv", "outputs/did_summary.json")]
before = {p: sha(p) for p in GUARDED if p.exists()}


def frame(scores, boroughs, created=None, scored=None):
    n = len(scores)
    return pd.DataFrame({
        ilp.ID: np.arange(1, n + 1),
        ilp.BOROUGH: boroughs,
        ilp.CREATED: pd.to_datetime(created if created is not None else pd.date_range("2024-01-01", periods=n)),
        ilp.SCORE: np.asarray(scores, float),
        "scored": scored if scored is not None else [True] * n,
    })


def run(f, budget, **kw):
    return ilp.optimise(f, daily_budget=budget, **kw)


# ---- solver availability -------------------------------------------------------
check("CBC (PULP_CBC_CMD) available", pulp.PULP_CBC_CMD(msg=False).available())
orig = pulp.PULP_CBC_CMD.available
pulp.PULP_CBC_CMD.available = lambda self: False
try:
    ilp.solve_dispatch(ilp.candidates(frame([1.0], ["A"])), 1, {}, False)
    check("missing CBC fails clearly (no fallback)", False)
except ilp.SolverUnavailableError:
    check("missing CBC fails clearly (no fallback)", True)
finally:
    pulp.PULP_CBC_CMD.available = orig

# ---- synthetic edge cases -----------------------------------------------------------
plan, s = run(frame([], []), 5)
check("empty input: empty plan, optimal, objective 0", len(plan) == 0 and s["solver_status"] == "Optimal" and s["objective_value"] == 0)
plan, s = run(frame([7.0], ["A"]), 5)
check("one outage, ample budget: repaired", plan.selected_for_repair.tolist() == [True] and s["objective_value"] == 7.0)
plan, s = run(frame([5, 4, 3], ["A", "A", "A"]), 0)
check("zero budget: nothing repaired, all deferred, plan non-empty",
      not plan.selected_for_repair.any() and (plan.decision == "defer").all() and len(plan) == 3 and s["objective_value"] == 0)
plan, s = run(frame([5, 4, 3], ["A", "A", "A"]), 0.5)
check("budget smaller than one repair: nothing repaired", not plan.selected_for_repair.any())
plan, s = run(frame([5, 4, 3], ["A", "B", "B"]), 100)
check("budget for all outages: all repaired, objective = sum", plan.selected_for_repair.all() and s["objective_value"] == 12)
plan, s = run(frame([9, 8, 7, 1], ["A", "A", "A", "B"]), 2, quota_fraction=0, quota_overrides={"B": 1})
check("quota forces a low-benefit outage in; exact quota satisfaction",
      set(plan[plan.selected_for_repair].borough) == {"A", "B"} and s["borough_allocation"] == {"A": 1, "B": 1}
      and math.isclose(s["objective_value"], 10) and s["quota_compliance"]["B"])
check("price of fairness = unconstrained - constrained", math.isclose(s["price_of_fairness"], 17 - 10))
plan, s = run(frame([9, 8, 7, 1], ["A", "A", "A", "B"]), 2, quota_fraction=0)
check("no quota: pure top-benefit selection", math.isclose(s["objective_value"], 17))
for label, overrides, budget in (("quota above the borough's outages", {"B": 2}, 5),
                                 ("quotas exceeding the budget", {"A": 2, "B": 1}, 2)):
    try:
        run(frame([9, 8, 7, 1], ["A", "A", "A", "B"]), budget, quota_fraction=0, quota_overrides=overrides)
        check(f"infeasible quotas detected: {label}", False)
    except ilp.InfeasibleQuotaError as e:
        check(f"infeasible quotas detected: {label}", True, str(e)[:70])
try:
    run(frame([1.0], ["A"]), 5, quota_overrides={"ZZZ": 1})
    check("quota for an unknown borough rejected", False)
except ValueError:
    check("quota for an unknown borough rejected", True)

# ties: deterministic and independent of input order
tied = frame([5.0] * 6, ["A"] * 6, created=pd.date_range("2024-01-01", periods=6)[::-1])
p1, _ = run(tied, 2)
p2, _ = run(tied.sample(frac=1, random_state=3), 2)
chosen = lambda p: sorted(p[p.selected_for_repair].outage_id)
check("tied benefits: same selection for any input order", chosen(p1) == chosen(p2))
check("tied benefits: earliest created_date wins the tie", chosen(p1) == [5, 6])
check("plan ordering is canonical (benefit desc, created, id)", p1.equals(p2))

# unscored / unspecified boroughs
plan, s = run(frame([5, 99, 3], ["A", "A", None], scored=[True, False, True]), 5)
check("unscored excluded; missing borough labelled Unspecified",
      s["n_candidates"] == 2 and set(plan.borough) == {"A", ilp.UNSPECIFIED} and 2 not in set(plan.outage_id))

# brute-force optimum on random instances
rng = np.random.default_rng(14)
ok = True
for trial in range(12):
    n = 10
    f = frame(np.round(rng.uniform(0, 50, n), 3), rng.choice(["A", "B", "C"], n))
    budget = float(rng.integers(2, 6))
    quotas = {"A": 1, "B": 1}
    try:
        plan, s = run(f, budget, quota_fraction=0, quota_overrides=quotas)
    except ilp.InfeasibleQuotaError:
        continue
    best = -1
    for k in range(int(budget) + 1):
        for combo in itertools.combinations(range(n), k):
            b = f.iloc[list(combo)].borough
            if (b == "A").sum() >= 1 and (b == "B").sum() >= 1:
                best = max(best, f.iloc[list(combo)][ilp.SCORE].sum())
    ok &= math.isclose(s["objective_value"], best, abs_tol=1e-4 * budget)
check("12 random instances match the brute-force optimum", ok)

# ---- real outputs -----------------------------------------------------------------------
plan_path = ilp.OUTPUT_DIR / ilp.PLAN_FILENAME
summary_path = ilp.OUTPUT_DIR / ilp.SUMMARY_FILENAME
check("input artifact exists", ilp.SCORED_FILE.exists())
check("output files exist", plan_path.exists() and summary_path.exists())
src = ilp.load_scored_outages()
check("required input columns", all(c in src.columns for c in ilp.REQUIRED_COLUMNS))
P = pd.read_csv(plan_path)
S = json.loads(summary_path.read_text(encoding="utf-8"))
dispatch = src[src.scored]
check("plan schema", list(P.columns) == ilp.PLAN_COLUMNS)
check("plan non-empty and covers every candidate exactly once",
      len(P) == len(dispatch) and P.outage_id.is_unique and set(P.outage_id) == set(dispatch[ilp.ID]))
sel = P[P.selected_for_repair]
check("selected rows are a subset of the input outages", set(sel.outage_id) <= set(src[ilp.ID]))
check("decision variables binary: selected_for_repair boolean, decision consistent",
      P.selected_for_repair.isin([True, False]).all()
      and (P.decision == np.where(P.selected_for_repair, "repair", "defer")).all())
check("solver status Optimal", S["solver_status"] == "Optimal")
check("daily budget respected", sel.repair_cost.sum() <= S["parameters"]["daily_budget"] + 1e-9 and math.isclose(sel.repair_cost.sum(), S["budget_used"]))
check("budget remaining correct", math.isclose(S["budget_remaining"], S["parameters"]["daily_budget"] - S["budget_used"]))
alloc = sel.borough.value_counts().to_dict()
check("every borough meets its minimum quota", all(alloc.get(b, 0) >= q for b, q in S["quotas"].items()), str(S["quotas"]))
check("objective equals the sum of the selected benefits", math.isclose(S["objective_value"], sel.benefit.sum(), rel_tol=1e-8))
check("benefit equals Stage 12 priority_score for every row",
      np.allclose(P.set_index("outage_id").benefit.sort_index(),
                  dispatch.set_index(ilp.ID).priority_score.sort_index(), rtol=1e-8))
check("counts consistent", S["n_selected"] == len(sel) and S["n_deferred"] == len(P) - len(sel) and S["n_candidates"] == len(P))
check("optimization_rank: 1..n for selected, 0 for deferred",
      sorted(sel.optimization_rank) == list(range(1, len(sel) + 1)) and (P[~P.selected_for_repair].optimization_rank == 0).all())
check("deterministic ordering (benefit desc, created_date, id)",
      (P.benefit.diff().dropna() <= 1e-9).all())
check("unconstrained optimum >= constrained optimum",
      S["unconstrained_objective"] >= S["objective_value"] - 1e-9 and S["price_of_fairness"] >= -1e-9)
stage13_day1 = pd.read_csv("outputs/fifo_vs_lightsafe_comparison.csv").lightsafe_cumulative_impact.iloc[0]
check("unconstrained optimum equals Stage 13 LightSafe day-1 impact (K=20)",
      math.isclose(S["unconstrained_objective"], stage13_day1, rel_tol=1e-6))

# ---- determinism --------------------------------------------------------------------------
tmp = Path(tempfile.mkdtemp(prefix="s14_"))
check("full re-run succeeds", ilp.main(["--out", str(tmp)]) == 0)
check("re-run plan is byte-identical", sha(tmp / ilp.PLAN_FILENAME) == sha(plan_path))
check("re-run summary identical", sha(tmp / ilp.SUMMARY_FILENAME) == sha(summary_path))

# ---- integrity ------------------------------------------------------------------------------
check("Stage 8-13 artifacts unchanged", all(sha(p) == h for p, h in before.items()) and len(before) >= 5)
check("causal_panel.parquet canonical hash",
      before[GUARDED[0]] == "eaa0cd74b75d5f99007ff36a96a562c6fde3c02e264d61cbd25aa652c08d1e35")

print("FAILS:", FAILS or "none")
sys.exit(1 if FAILS else 0)
