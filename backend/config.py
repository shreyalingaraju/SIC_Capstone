"""
Backend configuration and data path resolutions for LightSafe.

The backend only READS analytical artifacts produced by the pipeline in src/.
Nothing in this package writes to data/ or outputs/.
"""
from pathlib import Path
import os

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DATA_PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

# Parquet files
CLEAN_CRIME_FILE = DATA_PROCESSED_DIR / "clean_crime.parquet"
OUTAGES_SCORED_FILE = DATA_PROCESSED_DIR / "outages_scored.parquet"

# Output artifacts
PRIORITIZED_QUEUE_FILE = OUTPUTS_DIR / "prioritized_queue.csv"
FIFO_COMPARISON_FILE = OUTPUTS_DIR / "fifo_vs_lightsafe_comparison.csv"
OPTIMAL_DISPATCH_FILE = OUTPUTS_DIR / "optimal_dispatch_plan.csv"
OPTIMAL_DISPATCH_SUMMARY_FILE = OUTPUTS_DIR / "optimal_dispatch_summary.json"
DID_SUMMARY_FILE = OUTPUTS_DIR / "did_summary.json"
EVENT_STUDY_FILE = OUTPUTS_DIR / "event_study_coefficients.csv"
DISPLACEMENT_FILE = OUTPUTS_DIR / "displacement_estimates.csv"

# Frozen Stage 11-14 artifacts (read only; used by the evidence/operations explorer views)
OUTAGE_SITES_FILE = DATA_PROCESSED_DIR / "outage_sites.parquet"
STAGE11_DIR = OUTPUTS_DIR / "stage11_exposure"
STAGE11_ESTIMATES_FILE = STAGE11_DIR / "stage11_estimates.csv"
STAGE11_SUMMARY_FILE = STAGE11_DIR / "stage11_summary.json"
STAGE11_SENSITIVITY_FILE = STAGE11_DIR / "sensitivity" / "stage11_sensitivity_estimates.csv"
STAGE14_CAPACITY_METRICS_FILE = OUTPUTS_DIR / "stage14_capacity" / "capacity_metrics.csv"

# Capacity explorer range (jobs per daily decision). The frozen Stage 14 grid is 55-80;
# values outside it are exploratory re-runs of the same frozen FIFO simulation.
CAPACITY_MIN = 50
CAPACITY_MAX = 90

# Runtime settings (see .env.example at the repository root)
API_HOST = os.getenv("LIGHTSAFE_HOST", "127.0.0.1")
API_PORT = int(os.getenv("LIGHTSAFE_PORT", "8000"))

_DEFAULT_ORIGINS = (
    "http://localhost:5173,http://127.0.0.1:5173,"
    "http://localhost:3000,http://127.0.0.1:3000"
)
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("LIGHTSAFE_CORS_ORIGINS", _DEFAULT_ORIGINS).split(",")
    if o.strip()
]

# Largest page the API will return; keeps 100k+ record sets out of the browser.
MAX_PAGE_SIZE = 500
MAX_MAP_POINTS = 5000

# Shown in the UI so the score/optimisation are never read as a crime forecast.
IMPACT_UNIT = "priority index points"
IMPACT_UNIT_NOTE = (
    "Impact is the sum of Stage 12 priority scores (a 0-100 decision-support index). "
    "It is not a number of crimes, a probability, or a prediction of crimes prevented."
)
PIPELINE_NOTICE = (
    "Decision-support view of the Stage 12-14 design (priority index, FIFO comparison, "
    "constrained dispatch). The README's 'Final methodology (frozen 2026-10-06)' marks this "
    "design as superseded by a FIFO baseline plus capacity analysis, because the Stage 11 net "
    "effect used to build the index is not statistically distinguishable from zero. "
    "Results describe a ranking convention, not a causal or crime-prevention effect."
)
