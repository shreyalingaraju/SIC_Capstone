"""Pipeline provenance: which artifacts back the API, whether they exist, and how to read them."""
from typing import Any, Dict

from .. import config
from .data_store import store


def get_pipeline_status() -> Dict[str, Any]:
    artifacts = store.artifact_status()
    available = all(a["available"] for a in artifacts)
    return {
        "status": "DATA AVAILABLE" if available else "ARTIFACTS MISSING",
        "all_artifacts_available": available,
        "missing_artifacts": [a["file"] for a in artifacts if not a["available"]],
        "artifacts_last_modified_utc": store.latest_artifact_time(),
        "notice": config.PIPELINE_NOTICE,
        "impact_note": config.IMPACT_UNIT_NOTE,
        "stages": [
            {"stage": 11, "name": "Direct / displacement / net effect estimates", "artifact": "outputs/displacement_estimates.csv"},
            {"stage": 12, "name": "Outage priority score and tiers", "artifact": "data/processed/outages_scored.parquet"},
            {"stage": 13, "name": "FIFO vs LightSafe queue simulation", "artifact": "outputs/prioritized_queue.csv, outputs/fifo_vs_lightsafe_comparison.csv"},
            {"stage": 14, "name": "ILP dispatch plan (budget + borough quotas)", "artifact": "outputs/optimal_dispatch_plan.csv, outputs/optimal_dispatch_summary.json"},
        ],
        "artifacts": artifacts,
    }
