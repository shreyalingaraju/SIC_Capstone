"""
Stage 14 capacity / service-level analysis validation.

    python scripts/validation/stage14_capacity_validate.py

V1  common arrivals: every K ran on exactly the immutable Stage 12 job set
    (57,444 job ids, arrival times, site ids, boroughs); the job file is the
    one Stage 13 recorded
V2  FIFO ordering: in (arrival, job id) order selection times never
    decrease; each decision serves min(K, queue) (no idle capacity while
    jobs wait)
V3  capacity: no decision serves more than K jobs; every job served once
V4  no future information: every job is selected at a decision time after
    its arrival; decisions up to a cutoff are identical when all later
    arrivals are removed (two cutoffs, every K)
V5  service assumption: resolution = selection + exactly 1 day for every job
V6  no closure leakage: during a full run only the Stage 12 jobs/arrivals
    files are read, with the declared columns only, none forbidden; the
    module source names no retired input; importing it loads no retired
    module
V7  monotonic capacity: for K < K', the queue after every decision and
    every job's selection time under K' are <= those under K
V8  metric independence: K is the only simulation input varied; each K's
    run is identical alone or in a reversed sweep; the grid equals the
    pre-specified one; stored metrics recompute from the stored logs
V9  reproducibility: two fresh runs write byte-identical tables and
    identical log hashes (summary equal except its timestamp)
V10 baseline consistency: K=65 reproduces the frozen Stage 13 FIFO
    schedule and backlog series exactly (hashes) and its metrics

Exit 0 when every check passes, 1 otherwise. Writes the results to
outputs/stage14_capacity/validation_results.json.
"""

import ast
import inspect
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.features import match_controls as s7  # noqa: E402
from src.features import operational_priority as s12  # noqa: E402
from src.models import capacity_analysis as ca  # noqa: E402
from src.models import dispatch_simulation as s13  # noqa: E402

RESULTS = []
RETIRED_TOKENS = ("outages_scored", "priority_score", "tau_net", "displacement_estimates",
                  "ilp_solver", "raw_priority", "prioritization_engine", "displacement_model")
RETIRED_MODULES = ("src.optimization.ilp_solver", "src.models.priority_score",
                   "src.models.prioritization_engine", "src.models.displacement_model",
                   "src.models.exposure_model", "src.models.stage11_sensitivity")


def check(name, ok, detail):
    RESULTS.append({"check": name, "pass": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def load_stored():
    logs = {k: pd.read_parquet(ca.LOGS_DIR / f"jobs_K{k}.parquet") for k in ca.CAPACITY_GRID}
    series = {k: pd.read_parquet(ca.LOGS_DIR / f"backlog_K{k}.parquet") for k in ca.CAPACITY_GRID}
    summary = json.loads((ca.OUTPUT_DIR / ca.SUMMARY_FILENAME).read_text())
    return logs, series, summary


def traced_run(out):
    """Full run with every pandas read recorded (path and requested columns)."""

    reads = []
    originals = {name: getattr(pd, name) for name in ("read_parquet", "read_csv", "read_json", "read_excel")}

    def wrap(name):
        def reader(path, *args, **kwargs):
            reads.append({"fn": name, "path": str(path), "columns": kwargs.get("columns", kwargs.get("usecols"))})
            frame = originals[name](path, *args, **kwargs)
            reads[-1]["returned"] = list(frame.columns)
            return frame
        return reader

    for name in originals:
        setattr(pd, name, wrap(name))
    try:
        result = ca.run(logs_dir=out / "logs", out_dir=out / "out")
    finally:
        for name, fn in originals.items():
            setattr(pd, name, fn)
    return result, reads


def source_names_retired(path):
    """Retired tokens in the module's code (string constants outside docstrings, names, imports)."""

    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    found = set()
    for node in ast.walk(tree):
        texts = []
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            texts.append(node.value)
        elif isinstance(node, ast.Name):
            texts.append(node.id)
        elif isinstance(node, ast.Attribute):
            texts.append(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            texts.extend([getattr(node, "module", "") or ""] + [a.name for a in node.names])
        for t in texts:
            found |= {tok for tok in RETIRED_TOKENS + s12.FORBIDDEN_COLUMNS if tok in t}
    return sorted(found)


def main():
    jobs, arrivals = ca.load_inputs()
    logs, series, summary = load_stored()
    grid = ca.CAPACITY_GRID
    ident = ["job_id", "arrival_s", "site_id", "borough"]
    t0 = s13.first_decision_s()
    s13_summary = json.loads((s13.OUTPUT_DIR / s13.SUMMARY_FILENAME).read_text())

    # ---------------- V1 common arrivals ----------------
    jobs_fp = s7._file_fingerprint(s12.OUT_DIR / s12.JOBS_FILENAME)["sha256"]
    v1 = (len(jobs) == 57444 and jobs["job_id"].is_unique
          and jobs_fp == s13_summary["stage12_jobs_sha256"]
          and jobs_fp == summary["common_population"]["stage12_jobs_file_sha256"]
          and all(len(logs[k]) == len(jobs) and logs[k][ident].equals(jobs[ident]) for k in grid))
    check("V1 common arrivals", v1,
          f"{len(jobs)} jobs; identical (job_id, arrival_s, site_id, borough) for K={list(grid)}; "
          f"jobs.parquet sha256 equals the file Stage 13 used={jobs_fp == s13_summary['stage12_jobs_sha256']}")

    # ---------------- V2 FIFO ordering ----------------
    v2_notes, v2 = [], True
    for k in grid:
        ordered = logs[k].sort_values(["arrival_s", "job_id"], kind="mergesort")["selected_s"]
        mono = bool(ordered.is_monotonic_increasing)
        s = series[k]
        conserving = bool(((s["selected"] == k) | (s["waiting_after"] == 0)).all())
        v2 &= mono and conserving
        v2_notes.append(f"K={k}: order ok={mono}, work-conserving={conserving}")
    v2 &= list(inspect.signature(s13.fifo_order).parameters) == ["arrival_s", "job_id"] and ca.POLICY == "fifo"
    check("V2 FIFO ordering", v2, "; ".join(v2_notes) + "; ordering function takes (arrival_s, job_id) only")

    # ---------------- V3 capacity ----------------
    v3_notes, v3 = [], True
    for k in grid:
        per_t = logs[k].groupby("selected_s").size()
        ok = (int(per_t.max()) <= k and int(series[k]["selected"].max()) <= k
              and (logs[k]["selected_s"] >= 0).all() and int(series[k]["selected"].sum()) == len(jobs))
        v3 &= ok
        v3_notes.append(f"K={k}: max/day={int(per_t.max())}")
    check("V3 capacity", v3, "; ".join(v3_notes) + "; every job served exactly once")

    # ---------------- V4 no future information ----------------
    v4, v4_notes = True, []
    for k in grid:
        sel, arr = logs[k]["selected_s"], logs[k]["arrival_s"]
        on_grid = ((sel - t0) % s13.DAY_S == 0).all() and (sel >= t0).all()
        v4 &= bool((sel > arr).all() and on_grid)
    cutoffs = (s13._s("2025-03-15") + s13.DECISION_HOUR * 3600, s13._s("2025-11-20") + s13.DECISION_HOUR * 3600)
    key = ["job_id", "selected_s"]
    for cut in cutoffs:
        for k in grid:
            trunc, tser = s13.simulate(jobs, None, ca.POLICY, k, cutoff_s=cut)
            a = logs[k].loc[logs[k]["selected_s"] <= cut, key].sort_values(key).reset_index(drop=True)
            b = trunc.loc[trunc["selected_s"] <= cut, key].sort_values(key).reset_index(drop=True)
            sa = series[k][series[k]["decision_s"] <= cut].reset_index(drop=True)
            sb = tser[tser["decision_s"] <= cut].reset_index(drop=True)
            v4 &= bool(a.equals(b) and sa.equals(sb))
        v4_notes.append(f"cutoff {pd.to_datetime(cut, unit='s')}: decisions identical for all K")
    check("V4 no future information", v4,
          "selected at an 08:00 decision strictly after arrival for every job and K; " + "; ".join(v4_notes))

    # ---------------- V5 service assumption ----------------
    v5 = all(((logs[k]["resolved_s"] - logs[k]["selected_s"]) == s13.SERVICE_DAYS * s13.DAY_S).all() for k in grid)
    check("V5 service assumption", v5 and s13.SERVICE_DAYS == 1,
          f"resolved = selected + 1 day for all jobs at every K={v5}")

    # ---------------- V6 + V9 (traced runs) ----------------
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        (res1, reads1) = traced_run(Path(d1))
        (res2, _) = traced_run(Path(d2))
        allowed = {str(s12.OUT_DIR / s12.JOBS_FILENAME): list(ca.JOB_READ_COLUMNS),
                   str(s12.OUT_DIR / s12.ARRIVALS_FILENAME): list(ca.ARRIVAL_READ_COLUMNS)}
        bad_reads = [r for r in reads1 if allowed.get(r["path"]) != r["columns"]
                     or set(r["returned"]) & set(s12.FORBIDDEN_COLUMNS)]
        retired_in_source = source_names_retired(inspect.getsourcefile(ca))
        probe = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, r'%s'); import src.models.capacity_analysis; "
             "print(','.join(m for m in %r if m in sys.modules))" % (PROJECT_ROOT, RETIRED_MODULES)],
            capture_output=True, text=True, check=True)
        loaded_retired = probe.stdout.strip()
        v6 = not bad_reads and len(reads1) == 2 and not retired_in_source and loaded_retired == ""
        check("V6 no closure leakage", v6,
              f"files read: {[(Path(r['path']).name, r['columns']) for r in reads1]}; unexpected reads={bad_reads}; "
              f"retired/forbidden names in module code={retired_in_source}; retired modules loaded on import="
              f"{loaded_retired or 'none'}")

        names = (ca.CAPACITY_FILENAME, ca.REGIME_FILENAME, ca.ARRIVAL_FILENAME, ca.TARGETS_FILENAME)
        same_tables = all((Path(d1) / "out" / n).read_bytes() == (Path(d2) / "out" / n).read_bytes() for n in names)
        stored_tables = all((Path(d1) / "out" / n).read_bytes() == (ca.OUTPUT_DIR / n).read_bytes() for n in names)
        j1 = json.loads((Path(d1) / "out" / ca.SUMMARY_FILENAME).read_text())
        j2 = json.loads((Path(d2) / "out" / ca.SUMMARY_FILENAME).read_text())
        for j in (j1, j2, dict(summary)):
            j.pop("created_utc", None)
        same_summary = j1 == j2 == {k: v for k, v in summary.items() if k != "created_utc"}
        same_logs = all(j1["logs"][str(k)]["schedule_sha256"] == s13.frame_hash(logs[k])
                        and j1["logs"][str(k)]["backlog_sha256"] == s13.frame_hash(series[k]) for k in grid)
        v9 = same_tables and stored_tables and same_summary and same_logs
        check("V9 reproducibility", v9,
              f"two fresh runs byte-identical tables={same_tables}; equal to stored tables={stored_tables}; "
              f"summaries equal except timestamp={same_summary}; stored log hashes reproduced={same_logs}")

    # ---------------- V7 monotonic capacity ----------------
    v7, v7_notes = True, []
    for lo, hi in zip(grid[:-1], grid[1:]):
        a, b = series[lo].set_index("decision_s")["waiting_after"], series[hi].set_index("decision_s")["waiting_after"]
        idx = a.index.union(b.index)
        a, b = a.reindex(idx, fill_value=0), b.reindex(idx, fill_value=0)
        q_ok = bool((b <= a).all())
        j_ok = bool((logs[hi]["selected_s"].to_numpy() <= logs[lo]["selected_s"].to_numpy()).all())
        v7 &= q_ok and j_ok
        v7_notes.append(f"K={lo}->{hi}: queue<= {q_ok}, selection<= {j_ok}")
    check("V7 monotonic capacity", v7, "; ".join(v7_notes))

    # ---------------- V8 metric independence ----------------
    sim_params = list(inspect.signature(ca.simulate_fifo).parameters)
    rev_ok = True
    for k in reversed(grid):
        log, ser = ca.simulate_fifo(jobs, k)
        rev_ok &= (log[list(ca.SCHEDULE_COLUMNS)].equals(logs[k]) and ser.equals(series[k]))
    alone_log, alone_ser = ca.simulate_fifo(jobs, grid[0])
    rev_ok &= alone_log[list(ca.SCHEDULE_COLUMNS)].equals(logs[grid[0]]) and alone_ser.equals(series[grid[0]])
    split_s = ca.regime_split_s(jobs)
    stored_cap = pd.read_csv(ca.OUTPUT_DIR / ca.CAPACITY_FILENAME).set_index("capacity_k")
    stored_reg = pd.read_csv(ca.OUTPUT_DIR / ca.REGIME_FILENAME).set_index(["capacity_k", "regime"])
    recompute_ok = True
    for k in grid:
        m = ca.capacity_metrics(logs[k], series[k], k, split_s)
        recompute_ok &= all(str(stored_cap.loc[k, c]) == str(v) if isinstance(v, str)
                            else np.isclose(stored_cap.loc[k, c], v, rtol=1e-12, atol=0)
                            for c, v in m.items() if c != "capacity_k")
        for r in ca.regime_rows(logs[k], series[k], k, split_s):
            recompute_ok &= all(np.isclose(stored_reg.loc[(k, r["regime"]), c], v, rtol=1e-12, atol=0)
                                for c, v in r.items() if c not in ("capacity_k", "regime"))
    grid_ok = (tuple(summary["capacity_grid"]) == ca.CAPACITY_GRID == (55, 60, 65, 70, 75, 80)
               and tuple(summary["capacity_grid_prespecified"]) == ca.CAPACITY_GRID)
    v8 = sim_params == ["jobs", "capacity"] and rev_ok and recompute_ok and grid_ok
    check("V8 metric independence", v8,
          f"simulation inputs={sim_params}; each K identical alone / in reversed sweep={rev_ok}; "
          f"grid = pre-specified {list(ca.CAPACITY_GRID)}={grid_ok}; stored metrics recompute from logs={recompute_ok}")

    # ---------------- V10 baseline consistency ----------------
    frozen_log = pd.read_parquet(s13.JOBS_DIR / "jobs_fifo.parquet")
    frozen_ser = pd.read_parquet(s13.JOBS_DIR / "backlog_fifo.parquet")
    b = ca.BASELINE_K
    sched_ok = logs[b].equals(frozen_log[list(ca.SCHEDULE_COLUMNS)])
    ser_ok = (series[b].equals(frozen_ser)
              and s13.frame_hash(series[b]) == s13_summary["logs"]["fifo"]["backlog_sha256"])
    full_log, _ = s13.simulate(pd.read_parquet(s12.OUT_DIR / s12.JOBS_FILENAME), None, "fifo", b)
    frozen_hash_ok = s13.frame_hash(full_log) == s13_summary["logs"]["fifo"]["jobs_sha256"]
    frozen_m = pd.read_csv(s13.OUTPUT_DIR / s13.METRICS_FILENAME).set_index("policy").loc["fifo"]
    shared = [c for c in frozen_m.index if c in stored_cap.columns]
    diffs = {c: abs(float(stored_cap.loc[b, c]) - float(frozen_m[c])) for c in shared}
    metric_ok = max(diffs.values()) <= 1e-12
    split_ok = summary["regime_split"]["overload_start"] == s13_summary["regime_split"]["overload_start"]
    v10 = sched_ok and ser_ok and frozen_hash_ok and metric_ok and split_ok
    check("V10 baseline consistency", v10,
          f"K={b} schedule equals frozen jobs_fifo={sched_ok}; backlog series/hash equal={ser_ok}; "
          f"frozen Stage 13 job-log hash reproduced={frozen_hash_ok}; {len(shared)} metrics max |diff|="
          f"{max(diffs.values()):.1e} (CSV round-trip); regime split equal ({summary['regime_split']['overload_start']})={split_ok}")

    failed = [r["check"] for r in RESULTS if not r["pass"]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    out = {"validated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "n_checks": len(RESULTS), "n_passed": len(RESULTS) - len(failed), "results": RESULTS}
    (ca.OUTPUT_DIR / "validation_results.json").write_text(json.dumps(out, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
