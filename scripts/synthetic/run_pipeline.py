"""
Run the whole LightSafe pipeline on the synthetic Karnataka dataset.

    python scripts/synthetic/run_pipeline.py [--regenerate] [--from STEP]

Steps (each is an existing pipeline stage, run with LIGHTSAFE_PROFILE=karnataka_synthetic so it
reads data/synthetic/ and writes data/synthetic/processed and outputs/synthetic):

    generate   LightSafe_Synthetic_Karnataka_Data/generate_synthetic_data_v2.py   (only with --regenerate)
    prepare    scripts/synthetic/prepare_raw.py
    clean_sl   Stage 3  src/data/clean_streetlights.py
    clean_cr   Stage 5  src/data/clean_crime.py
    match      Stage 7  src/features/match_controls.py        matched controls
    panel      Stage 8  src/features/build_causal_panel.py
    did        Stage 9  src/models/did_model.py               difference-in-differences
    event      Stage 10 src/models/event_study.py
    displace   Stage 11 src/models/displacement_model.py      direct + ring effect = tau_net
    score      Stage 12 src/features/priority_score.py        priority index
    queue      Stage 13 src/models/prioritization_engine.py   FIFO vs priority queue
    ilp        Stage 14 src/optimization/ilp_solver.py        constrained dispatch
    context    scripts/synthetic/build_context_outputs.py     ward / population / scenario outputs
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable

STEPS = [
    ("generate", ["LightSafe_Synthetic_Karnataka_Data/generate_synthetic_data_v2.py"]),
    ("prepare", ["scripts/synthetic/prepare_raw.py"]),
    ("clean_sl", ["src/data/clean_streetlights.py"]),
    ("clean_cr", ["src/data/clean_crime.py"]),
    ("match", ["src/features/match_controls.py"]),
    ("panel", ["src/features/build_causal_panel.py"]),
    ("did", ["src/models/did_model.py"]),
    ("event", ["src/models/event_study.py"]),
    ("displace", ["src/models/displacement_model.py"]),
    ("score", ["src/features/priority_score.py"]),
    ("queue", ["src/models/prioritization_engine.py"]),
    ("ilp", ["src/optimization/ilp_solver.py"]),
    ("context", ["scripts/synthetic/build_context_outputs.py"]),
    ("pressure", ["scripts/synthetic/build_repair_pressure.py"]),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--regenerate", action="store_true", help="re-run the data generator first")
    ap.add_argument("--from", dest="start", default=None, choices=[n for n, _ in STEPS])
    args = ap.parse_args()

    env = dict(os.environ, LIGHTSAFE_PROFILE="karnataka_synthetic", PYTHONIOENCODING="utf-8")
    names = [n for n, _ in STEPS]
    first = names.index(args.start) if args.start else (0 if args.regenerate else 1)
    for name, cmd in STEPS[first:]:
        t0 = time.time()
        print(f"\n=== {name}: {' '.join(cmd)}", flush=True)
        r = subprocess.run([PY, *cmd], cwd=ROOT, env=env)
        print(f"=== {name} finished in {time.time() - t0:.1f}s (exit {r.returncode})", flush=True)
        if r.returncode != 0:
            print(f"Pipeline stopped at step '{name}'.", file=sys.stderr)
            return r.returncode
    print("\nPipeline complete. Outputs: outputs/synthetic/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
