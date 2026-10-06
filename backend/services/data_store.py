"""
In-memory, read-only cache of the LightSafe analytical artifacts.

Artifacts are loaded once at startup (never recomputed per request). Any artifact that is
missing is recorded in ``missing`` and exposed as an empty frame, so endpoints degrade to
an explicit "unavailable" state instead of crashing or inventing values.
"""
from typing import Any, Dict, Optional
import json
import logging
import threading
from datetime import datetime, timezone

import pandas as pd

from .. import config

logger = logging.getLogger("lightsafe.data_store")

OUTAGE_COLUMNS = [
    "unique_key", "created_date", "closed_date", "incident_address", "street_name",
    "cross_street_1", "cross_street_2", "intersection_street_1", "intersection_street_2",
    "borough", "police_precinct", "latitude", "longitude", "outage_duration_hours",
    "scored", "exclusion_reason", "tau_net", "tau_net_source", "local_crime_rate",
    "duration_factor", "raw_priority", "priority_score", "priority_tier",
]

def _display_path(path) -> str:
    try:
        return str(path.relative_to(config.PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return path.name


def artifacts() -> dict:
    """Artifact name -> path, resolved from config at call time."""
    return {
        "outages_scored": config.OUTAGES_SCORED_FILE,
        "prioritized_queue": config.PRIORITIZED_QUEUE_FILE,
        "fifo_vs_lightsafe_comparison": config.FIFO_COMPARISON_FILE,
        "optimal_dispatch_plan": config.OPTIMAL_DISPATCH_FILE,
        "optimal_dispatch_summary": config.OPTIMAL_DISPATCH_SUMMARY_FILE,
        "displacement_estimates": config.DISPLACEMENT_FILE,
        "did_summary": config.DID_SUMMARY_FILE,
        "event_study_coefficients": config.EVENT_STUDY_FILE,
    }


class DataStore:
    def __init__(self) -> None:
        self.loaded = False
        self.missing: list = []
        self.outages_df = pd.DataFrame()        # all rows, indexed by string outage id
        self.scored_sorted = pd.DataFrame()     # scored rows, priority desc / created asc
        self.queue_df = pd.DataFrame()
        self.fifo_rank: Dict[str, int] = {}
        self.lightsafe_rank: Dict[str, int] = {}
        self.comparison_df = pd.DataFrame()
        self.dispatch_df = pd.DataFrame()       # indexed by string outage id
        self.dispatch_summary: Dict[str, Any] = {}
        self.did_summary: Dict[str, Any] = {}
        self.event_study_df = pd.DataFrame()
        self.displacement_df = pd.DataFrame()

        # Operator annotations (outage_id -> status). Session-local UI notes only;
        # they never change the analytical plan.
        self.operator_actions: Dict[str, str] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ loading
    def load_all(self) -> None:
        with self._lock:
            self.missing = []
            self._load_outages()
            self._load_queue()
            self._load_comparison()
            self._load_dispatch()
            self._attach_dispatch()
            self.dispatch_summary = self._read_json(config.OPTIMAL_DISPATCH_SUMMARY_FILE)
            self.did_summary = self._read_json(config.DID_SUMMARY_FILE)
            self.event_study_df = self._read_csv(config.EVENT_STUDY_FILE)
            self.displacement_df = self._read_csv(config.DISPLACEMENT_FILE)
            self.loaded = True
            if self.missing:
                logger.warning("Missing artifacts: %s", ", ".join(self.missing))
            else:
                logger.info("All LightSafe artifacts loaded.")

    def ensure_loaded(self) -> None:
        if not self.loaded:
            self.load_all()

    def _note_missing(self, path) -> None:
        self.missing.append(path.name)

    def _read_csv(self, path) -> pd.DataFrame:
        if not path.exists():
            self._note_missing(path)
            return pd.DataFrame()
        return pd.read_csv(path)

    def _read_json(self, path) -> Dict[str, Any]:
        if not path.exists():
            self._note_missing(path)
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_outages(self) -> None:
        path = config.OUTAGES_SCORED_FILE
        if not path.exists():
            self._note_missing(path)
            self.outages_df = pd.DataFrame()
            self.scored_sorted = pd.DataFrame()
            return
        df = pd.read_parquet(path, columns=OUTAGE_COLUMNS)
        df["outage_id"] = df["unique_key"].astype(str)
        df["scored"] = df["scored"].fillna(False).astype(bool)
        df["borough"] = df["borough"].fillna("Unspecified").astype(str)
        self.scored_sorted = (
            df[df["scored"]]
            .sort_values(["priority_score", "created_date"], ascending=[False, True], kind="mergesort")
            .set_index("outage_id", drop=False)
        )
        self.outages_df = df.set_index("outage_id", drop=False)
        logger.info("Loaded %d outages (%d scored).", len(df), len(self.scored_sorted))

    def _load_queue(self) -> None:
        df = self._read_csv(config.PRIORITIZED_QUEUE_FILE)
        if df.empty:
            self.queue_df = df
            return
        df["outage_id"] = df["outage_id"].astype(str)
        self.queue_df = df
        fifo = df[df["method"] == "FIFO"]
        ls = df[df["method"] == "LightSafe"]
        self.fifo_rank = dict(zip(fifo["outage_id"], fifo["queue_rank"].astype(int)))
        self.lightsafe_rank = dict(zip(ls["outage_id"], ls["queue_rank"].astype(int)))

    def _load_comparison(self) -> None:
        self.comparison_df = self._read_csv(config.FIFO_COMPARISON_FILE)

    def _load_dispatch(self) -> None:
        df = self._read_csv(config.OPTIMAL_DISPATCH_FILE)
        if df.empty:
            self.dispatch_df = df
            return
        df["outage_id"] = df["outage_id"].astype(str)
        df["selected_for_repair"] = df["selected_for_repair"].astype(bool)
        self.dispatch_df = df.set_index("outage_id", drop=False)

    def _attach_dispatch(self) -> None:
        """Add Stage 14 decision columns to the outage frames (read-only join)."""
        for name in ("scored_sorted", "outages_df"):
            df = getattr(self, name)
            if df.empty:
                continue
            if self.dispatch_df.empty:
                df["dispatch_status"] = None
                df["optimization_rank"] = float("nan")
                continue
            sel = self.dispatch_df["selected_for_repair"].reindex(df.index)
            df["dispatch_status"] = sel.map({True: "recommended", False: "deferred"})
            df.loc[~df["scored"], "dispatch_status"] = "not_scored"
            rank = self.dispatch_df["optimization_rank"].where(self.dispatch_df["selected_for_repair"])
            df["optimization_rank"] = rank.reindex(df.index)

    # ----------------------------------------------------------------- metadata
    def artifact_status(self) -> list:
        rows = []
        for name, path in artifacts().items():
            exists = path.exists()
            modified = None
            if exists:
                modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
            rows.append({
                "artifact": name,
                "file": _display_path(path),
                "available": exists,
                "modified_utc": modified,
            })
        return rows

    def latest_artifact_time(self) -> Optional[str]:
        times = [r["modified_utc"] for r in self.artifact_status() if r["modified_utc"]]
        return max(times) if times else None


store = DataStore()
