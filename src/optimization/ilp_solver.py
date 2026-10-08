"""
Stage 14: ILP optimization layer (daily repair budget + borough quotas).

Reads data/processed/outages_scored.parquet (Stage 12, read only) and writes
outputs/optimal_dispatch_plan.csv (+ optimal_dispatch_summary.json).

Model (PuLP, CBC):

    LpProblem("LightSafe_Dispatch", LpMaximize)
    x_i in {0, 1}                       1 = repair, 0 = defer
    maximise   sum_i benefit_i * x_i
    s.t.       sum_i repair_cost_i * x_i <= daily_budget
               sum_{i in borough b} x_i  >= minimum_quota_b   for every borough b

Parameter conventions. The Guide names Daily_Budget, repair_cost[i] and
Minimum_Quota_b but gives no values, and the repository defines none, except
the Stage 13 crew capacity K = 20 repairs per day. Stage 14-only conventions
(all configurable, none a real-world claim):
- repair_cost_i = 1.0 for every outage (no cost field exists; outage duration
  is not repair effort), so daily_budget = 20.0 is the Stage 13 capacity of 20
  repairs per day.
- minimum_quota_b = floor(quota_fraction * capacity * share_b), where capacity =
  floor(daily_budget / repair_cost), share_b = borough b's share of the
  candidate outages (capped at the borough's outage count), and quota_fraction = 0.5: half of the day's capacity is
  guaranteed to boroughs in proportion to their backlog. Explicit overrides
  replace the default per borough. Quotas are checked for feasibility before
  solving.

Benefit. The Guide's objective sum(x_i * tau_net_i) cannot be applied
literally: Stage 11's tau_net is one global estimate, so sum(x_i * tau_net) only
counts repairs. The outage-specific quantity the project already derived from
it is the Stage 12 `priority_score` (tau_net x local_crime_rate x duration_factor,
clipped at 0 and min-max scaled to 0-100), which Stage 13 also credits per
repair. benefit_i = priority_score_i, in index points; the optimum is the
maximum total index of the repaired outages, not a count of crimes prevented
and not an estimate of outage-level causal effects.

Candidates: outages with scored == True (Stage 13 backlog). The plan is one
day's dispatch from that backlog.

Determinism. Candidates are put in a canonical order (score descending,
created_date, unique_key). Ties between equal-benefit outages are broken
toward that order by a perturbation of at most TIE_EPSILON per outage on the
solver's coefficients, so the chosen set does not depend on input row order.
Reported objective values use the unperturbed benefits. CBC runs
single-threaded. A missing CBC solver raises an error; there is no fallback.
"""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pulp

PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from src import profile as _profile  # noqa: E402  (dataset profile: paths, bbox, CRS)

PROCESSED_DIR = PROJECT_ROOT / _profile.PROCESSED_DIR
OUTPUT_DIR = PROJECT_ROOT / _profile.OUTPUTS_DIR

SCORED_FILE = PROCESSED_DIR / "outages_scored.parquet"
PLAN_FILENAME = "optimal_dispatch_plan.csv"
SUMMARY_FILENAME = "optimal_dispatch_summary.json"

ID = "unique_key"
BOROUGH = "borough"
CREATED = "created_date"
SCORE = "priority_score"
UNSPECIFIED = "Unspecified"

DEFAULT_DAILY_BUDGET = 20.0   # cost units; = Stage 13 K = 20 repairs/day at cost 1
DEFAULT_REPAIR_COST = 1.0
DEFAULT_QUOTA_FRACTION = 0.5
TIE_EPSILON = 1e-6            # max solver-side perturbation per outage (index points)
DEFAULT_TIME_LIMIT_S = 600

REQUIRED_COLUMNS = (ID, BOROUGH, CREATED, SCORE, "scored")

PLAN_COLUMNS = [
    "outage_id", "borough", "created_date", "priority_score", "benefit",
    "repair_cost", "selected_for_repair", "decision", "optimization_rank",
]


class InfeasibleQuotaError(ValueError):
    """The budget/quota configuration admits no solution."""


class SolverUnavailableError(RuntimeError):
    """CBC is not available; no other solver is substituted."""


# ============================================================
# Inputs
# ============================================================

def load_scored_outages(path=SCORED_FILE):
    frame = pd.read_parquet(path)
    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"{path} is missing required columns: {missing}")
    if frame[ID].isna().any() or not frame[ID].is_unique:
        raise ValueError(f"{ID} must be unique and non-null")
    return frame


def candidates(frame, repair_cost=DEFAULT_REPAIR_COST):
    """Scored outages in canonical order with benefit, cost and a borough label."""

    if not (np.isfinite(repair_cost) and repair_cost > 0):
        raise ValueError("repair_cost must be a positive finite number")
    c = frame[frame["scored"].fillna(False).astype(bool)].copy()
    if c[SCORE].isna().any() or not np.isfinite(c[SCORE].astype(float)).all():
        raise ValueError("scored outages must have a finite priority_score")
    c["benefit"] = c[SCORE].astype(float)
    if (c["benefit"] < 0).any():
        raise ValueError("benefit must be non-negative")
    c["repair_cost"] = float(repair_cost)
    b = c[BOROUGH].astype("object")
    c[BOROUGH] = b.where(b.notna() & (b.astype(str).str.strip() != ""), UNSPECIFIED)
    c = c.sort_values(["benefit", CREATED, ID], ascending=[False, True, True],
                      kind="mergesort").reset_index(drop=True)
    return c


# ============================================================
# Quotas and feasibility
# ============================================================

def default_quotas(cands, daily_budget=DEFAULT_DAILY_BUDGET,
                   quota_fraction=DEFAULT_QUOTA_FRACTION):
    """floor(quota_fraction * capacity * borough share) per borough (see module doc)."""

    if not (0 <= quota_fraction <= 1):
        raise ValueError("quota_fraction must be in [0, 1]")
    counts = cands[BOROUGH].value_counts()
    if len(cands) == 0:
        return {}
    cost = float(cands["repair_cost"].min())
    capacity = math.floor(daily_budget / cost + 1e-9)
    return {b: int(min(n, math.floor(quota_fraction * capacity * n / len(cands) + 1e-9)))
            for b, n in sorted(counts.items())}


def resolve_quotas(cands, daily_budget, quota_fraction=DEFAULT_QUOTA_FRACTION,
                   overrides=None):
    quotas = default_quotas(cands, daily_budget, quota_fraction)
    for b, q in (overrides or {}).items():
        if b not in quotas and len(cands) > 0 and b not in set(cands[BOROUGH]):
            raise ValueError(f"quota given for unknown borough {b!r}; known: {sorted(set(cands[BOROUGH]))}")
        if q < 0 or int(q) != q:
            raise ValueError(f"quota for {b!r} must be a non-negative integer")
        quotas[b] = int(q)
    return quotas


def check_feasibility(cands, quotas, daily_budget):
    """Raise InfeasibleQuotaError with a diagnostic if no selection can satisfy everything."""

    if daily_budget < 0 or not np.isfinite(daily_budget):
        raise ValueError("daily_budget must be a non-negative finite number")
    problems = []
    cheapest_total = 0.0
    for b, q in quotas.items():
        sub = cands[cands[BOROUGH] == b]
        if q > len(sub):
            problems.append(f"{b}: quota {q} exceeds its {len(sub)} candidate outages")
            continue
        cheapest_total += float(sub["repair_cost"].nsmallest(q).sum()) if q else 0.0
    if not problems and cheapest_total > daily_budget + 1e-9:
        problems.append(
            f"quotas {quotas} need at least {cheapest_total:g} budget units but the "
            f"daily budget is {daily_budget:g}")
    if problems:
        raise InfeasibleQuotaError("; ".join(problems))


# ============================================================
# Model and solution
# ============================================================

def build_optimization_model(cands, daily_budget, quotas, use_quotas=True):
    """Returns (problem, {row index: variable}). Variables are binary."""

    prob = pulp.LpProblem("LightSafe_Dispatch", pulp.LpMaximize)
    x = {i: pulp.LpVariable(f"x_{i}", cat=pulp.LpBinary) for i in range(len(cands))}

    n = max(len(cands), 1)
    rank_penalty = TIE_EPSILON * np.arange(len(cands)) / n   # canonical order wins ties
    benefit = cands["benefit"].to_numpy(float)
    prob += pulp.lpSum((benefit[i] - rank_penalty[i]) * x[i] for i in x), "total_benefit"

    cost = cands["repair_cost"].to_numpy(float)
    prob += pulp.lpSum(cost[i] * x[i] for i in x) <= float(daily_budget), "daily_budget"

    if use_quotas:
        by_borough = cands.groupby(BOROUGH).indices
        for b, q in quotas.items():
            if q > 0:
                prob += (pulp.lpSum(x[i] for i in by_borough[b]) >= q,
                         f"quota_{str(b).replace(' ', '_')}")
    return prob, x


def solve_dispatch(cands, daily_budget=DEFAULT_DAILY_BUDGET, quotas=None,
                   use_quotas=True, time_limit=DEFAULT_TIME_LIMIT_S):
    """
    Solve the ILP with CBC. Returns a dict: status, selected (boolean array in
    candidate order), objective, budget_used. Raises InfeasibleQuotaError before
    solving if the quotas cannot be met, and SolverUnavailableError without CBC.
    """

    quotas = quotas or {}
    if use_quotas:
        check_feasibility(cands, quotas, daily_budget)
    elif daily_budget < 0:
        raise ValueError("daily_budget must be non-negative")

    if len(cands) == 0:
        return {"status": "Optimal", "selected": np.zeros(0, dtype=bool),
                "objective": 0.0, "budget_used": 0.0}

    solver = pulp.PULP_CBC_CMD(msg=False, threads=1, timeLimit=time_limit)
    if not solver.available():
        raise SolverUnavailableError(
            "CBC (PULP_CBC_CMD) is not available; install pulp==3.3.2 (which bundles CBC). "
            "No other solver is substituted.")

    prob, x = build_optimization_model(cands, daily_budget, quotas, use_quotas)
    prob.solve(solver)
    status = pulp.LpStatus[prob.status]
    if status != "Optimal":
        raise RuntimeError(f"CBC returned status {status!r}, not Optimal")

    selected = np.array([x[i].value() is not None and x[i].value() > 0.5 for i in range(len(cands))])
    return {
        "status": status,
        "selected": selected,
        "objective": float(cands["benefit"].to_numpy(float)[selected].sum()),
        "budget_used": float(cands["repair_cost"].to_numpy(float)[selected].sum()),
    }


def build_plan(cands, solution):
    plan = pd.DataFrame({
        "outage_id": cands[ID].to_numpy(),
        "borough": cands[BOROUGH].to_numpy(),
        "created_date": cands[CREATED].to_numpy(),
        "priority_score": cands[SCORE].to_numpy(float),
        "benefit": cands["benefit"].to_numpy(float),
        "repair_cost": cands["repair_cost"].to_numpy(float),
        "selected_for_repair": solution["selected"],
    })
    plan["decision"] = np.where(plan["selected_for_repair"], "repair", "defer")
    rank = np.zeros(len(plan), dtype=np.int64)
    order = np.flatnonzero(solution["selected"])       # already in canonical order
    rank[order] = np.arange(1, len(order) + 1)
    plan["optimization_rank"] = rank
    return plan[PLAN_COLUMNS]


def validate_solution(cands, plan, solution, daily_budget, quotas, use_quotas=True):
    """Raise AssertionError if the plan violates the model. Returns a summary dict."""

    sel = plan[plan["selected_for_repair"]]
    assert plan["outage_id"].is_unique, "an outage appears twice"
    assert set(plan["outage_id"]) == set(cands[ID]), "plan does not cover the candidates"
    assert solution["budget_used"] <= daily_budget + 1e-9, "daily budget exceeded"
    assert math.isclose(sel["repair_cost"].sum(), solution["budget_used"], abs_tol=1e-9)
    assert math.isclose(sel["benefit"].sum(), solution["objective"], rel_tol=1e-9, abs_tol=1e-9)
    allocation = sel["borough"].value_counts().to_dict()
    if use_quotas:
        for b, q in quotas.items():
            assert allocation.get(b, 0) >= q, f"quota not met for {b}: {allocation.get(b, 0)} < {q}"
    return {"allocation": allocation}


def save_dispatch_plan(plan, summary, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plan.to_csv(out_dir / PLAN_FILENAME, index=False, float_format="%.10g")
    (out_dir / SUMMARY_FILENAME).write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")


def optimise(frame, daily_budget=DEFAULT_DAILY_BUDGET, repair_cost=DEFAULT_REPAIR_COST,
             quota_fraction=DEFAULT_QUOTA_FRACTION, quota_overrides=None,
             time_limit=DEFAULT_TIME_LIMIT_S):
    """Full pipeline on a scored-outage frame. Returns (plan, summary)."""

    cands = candidates(frame, repair_cost)
    quotas = resolve_quotas(cands, daily_budget, quota_fraction, quota_overrides)
    solution = solve_dispatch(cands, daily_budget, quotas, True, time_limit)
    plan = build_plan(cands, solution)
    checked = validate_solution(cands, plan, solution, daily_budget, quotas)

    free = solve_dispatch(cands, daily_budget, quotas, False, time_limit)
    n_sel = int(solution["selected"].sum())
    summary = {
        "model": "LightSafe_Dispatch (PuLP, CBC)",
        "solver_status": solution["status"],
        "benefit_definition": "Stage 12 priority_score (index points; not a crime count)",
        "parameters": {"daily_budget": daily_budget, "repair_cost": repair_cost,
                       "quota_fraction": quota_fraction, "quota_overrides": quota_overrides or {},
                       "tie_epsilon": TIE_EPSILON},
        "quotas": quotas,
        "n_candidates": int(len(cands)),
        "n_selected": n_sel,
        "n_deferred": int(len(cands) - n_sel),
        "objective_value": solution["objective"],
        "budget_used": solution["budget_used"],
        "budget_remaining": float(daily_budget - solution["budget_used"]),
        "borough_allocation": checked["allocation"],
        "quota_compliance": {b: checked["allocation"].get(b, 0) >= q for b, q in quotas.items()},
        "unconstrained_objective": free["objective"],
        "price_of_fairness": free["objective"] - solution["objective"],
        "unconstrained_borough_allocation": (
            plan.assign(s=free["selected"]).query("s")["borough"].value_counts().to_dict()
            if len(cands) else {}),
        "pulp_version": pulp.__version__,
    }
    return plan, summary


# ============================================================
# Main
# ============================================================

def _quota_arg(text):
    name, _, value = text.rpartition("=")
    if not name or not value.lstrip("-").isdigit():
        raise argparse.ArgumentTypeError("use BOROUGH=INTEGER, e.g. BRONX=2")
    return name.strip().upper() if name.strip().upper() != UNSPECIFIED.upper() else UNSPECIFIED, int(value)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Stage 14 ILP dispatch optimisation")
    parser.add_argument("--scored", type=Path, default=SCORED_FILE,
                        help="Stage 12 outages_scored.parquet (read only)")
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--daily-budget", type=float, default=DEFAULT_DAILY_BUDGET,
                        help="daily crew budget in cost units (default 20 = Stage 13 K at cost 1)")
    parser.add_argument("--repair-cost", type=float, default=DEFAULT_REPAIR_COST,
                        help="uniform cost per repair")
    parser.add_argument("--quota-fraction", type=float, default=DEFAULT_QUOTA_FRACTION,
                        help="share of daily capacity guaranteed to boroughs in proportion to backlog")
    parser.add_argument("--quota", type=_quota_arg, action="append", default=[],
                        metavar="BOROUGH=N", help="explicit minimum quota (repeatable)")
    parser.add_argument("--time-limit", type=int, default=DEFAULT_TIME_LIMIT_S)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    print("Loading Stage 12 output...")
    frame = load_scored_outages(args.scored)
    plan, summary = optimise(frame, args.daily_budget, args.repair_cost,
                             args.quota_fraction, dict(args.quota), args.time_limit)
    save_dispatch_plan(plan, summary, args.out)

    print(f"Status: {summary['solver_status']}; candidates {summary['n_candidates']:,}; "
          f"selected {summary['n_selected']}; deferred {summary['n_deferred']:,}")
    print(f"Objective {summary['objective_value']:.4f} (unconstrained {summary['unconstrained_objective']:.4f}); "
          f"budget used {summary['budget_used']:g} / {args.daily_budget:g}")
    print(f"Quotas: {summary['quotas']}\nAllocation: {summary['borough_allocation']}")
    print(f"\nPlan: {args.out / PLAN_FILENAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
