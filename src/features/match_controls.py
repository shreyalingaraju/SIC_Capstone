"""
Stage 7: control matching (Issue 4 redesign).

Each eligible treatment outage is matched 1:1 to a control *site*: a
distinct geocoded 311 "Street Light Out" location that is provably not
dark during the treatment's full analysis window W, lies 500-1,500 m
away in the same borough, and has similar baseline night crime.

Inputs
------
data/raw/streetlight_complaints.csv          darkness universe (all complaints)
data/processed/clean_streetlights.parquet    treatments (Stage 3 output)
data/processed/clean_crime.parquet           baseline crime counts (Stage 5)

Outputs (in --out-dir, default data/processed)
-------
control_area_pairs.parquet     one row per matched pair
outage_sites.parquet           site table
unmatched_treatments.parquet   one row per unmatched treatment, with reason
match_diagnostics.json         provenance, attrition, balance, checks, runtime

Conventions (also written to diagnostics.parameters.conventions)
-----------
A1  311 and NYPD timestamps are naive NYC local time; the repeated
    DST hour is ignored.
A2  Overlap is closed on both ends: darkness [s, e] overlaps window
    [a, b] when s <= b and e >= a. S-4 pre [c - 35 d, c): s < c and
    e >= c - 35 d. S-4 post (closed, W_end]: e > closed and s <= W_end.
A3  Times are stored as int64 seconds.
A4  Site borough and precinct are the most common value across the
    site's complaints, ties broken alphabetically; empty or
    "Unspecified" becomes null.
A5  The Stage 3 validity rule (0.5-8,760 h) is duplicated here and
    must stay in sync with src/data/clean_streetlights.py.
A6  Darkness coverage runs from the earliest to the latest raw
    created_date. Crime coverage is the observed min/max crime_datetime.
A7  Artifact-site complaints form single-complaint episodes and still
    count as darkness.
A8  S-3 and S-4 distances are measured to each complaint's point, not
    to episode centres.
A9  Prior episodes: an episode counts if its start falls in B and at
    least one of its complaints is within 250 m.
A10 Res-9 density: crimes during B inside the unit's res-9 cell,
    divided by the cell area in km^2.
A11 The S8 pre-window [c - 14 d, c] includes both ends; B is half-open.
A12 Distances follow Stage 8: <= 100 m direct, (100, 250] m ring.

H9 (approved interpretation; implemented in a later commit)
--
Intervals are closed W windows per site. A control interval must not
overlap another control use of the same site, nor the W of any eligible
treatment at that site. Treatment-treatment overlap at a site is allowed
and reported. H9 rebuilds the intervals from the pairs and eligible
treatments instead of trusting ReuseRegistry.

Exit codes
----------
0 Stage 7 completed. 1 H0 or hard-check failure. 2 argparse usage
error. 3 Stage 7 incomplete on this branch (no outputs produced).
"""

import argparse
import dataclasses
import functools
import gc
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import h3
import numpy as np
import pandas as pd
import psutil
from pyproj import CRS, Transformer
from scipy.spatial import cKDTree


# ---------------------------------------------------------
# File paths
# ---------------------------------------------------------

RAW_COMPLAINTS_FILE = Path("data/raw/streetlight_complaints.csv")
TREATMENT_FILE = Path("data/processed/clean_streetlights.parquet")
CRIME_FILE = Path("data/processed/clean_crime.parquet")

DEFAULT_OUT_DIR = Path("data/processed")

PAIRS_FILENAME = "control_area_pairs.parquet"
SITES_FILENAME = "outage_sites.parquet"
UNMATCHED_FILENAME = "unmatched_treatments.parquet"
DIAGNOSTICS_FILENAME = "match_diagnostics.json"
FAILED_DIAGNOSTICS_FILENAME = "match_diagnostics.failed.json"


# ---------------------------------------------------------
# Fixed spatial settings (not exposed on the command line)
# ---------------------------------------------------------

# NAD83 / New York Long Island in metres (EPSG:2263 is US feet).
PROJECTED_CRS = "EPSG:32118"

# (lat_min, lat_max, lon_min, lon_max), same box as Stage 3.
NYC_BBOX = (40.49, 40.92, -74.26, -73.69)

H3_RESOLUTIONS = (7, 9, 10)

# ---------------------------------------------------------
# Cross-stage contracts (locked; must match other stages)
# ---------------------------------------------------------

# Stage 8 outcome definitions.
DIRECT_RADIUS_M = 100.0
OUTCOME_RADIUS_M = 250.0
S8_PRE_WINDOW_DAYS = 14

# Stage 3 valid-closure rule (src/data/clean_streetlights.py).
VALID_MIN_DURATION_H = 0.5
VALID_MAX_DURATION_H = 8760.0

# Stage 8 post-window length.
POST_WINDOW_DAYS = 14

# Stage 10 event window (days -35 to +35). Also sets the W start, the
# S-4 pre-window length and the end of the baseline window B.
EVENT_WINDOW_DAYS = 35


# ---------------------------------------------------------
# Fixed methodology values (locked; no sensitivity values)
# ---------------------------------------------------------

SITE_ROUND_M = 1
EPISODE_MERGE_RADIUS_M = 25.0
ARTIFACT_QUANTILE = 0.999

# Fixed at 2 x OUTCOME_RADIUS_M so outcome zones never overlap (C5).
MATCH_BAND_MIN_M = 500.0
TREATMENT_CLEAN_RADIUS_M = 100.0
PRIOR_EPISODE_RADIUS_M = 250.0

BASELINE_DAYS = 365

RECHECK_SAMPLE_N = 2000
RECHECK_SEED = 20260927

SMD_MAX = 0.1
VR_MIN = 0.5
VR_MAX = 2.0


# ---------------------------------------------------------
# D19 sensitivity parameters (command-line defaults)
# ---------------------------------------------------------

REPORT_LAG_DAYS = 7
IMPUTED_DURATION_HOURS = 160.0

EXCLUSION_RADIUS_M = 350.0
MATCH_BAND_MAX_M = 1500.0

CALIPER_SD = 0.5
REUSE_POLICY = "non_overlapping"
CONTROL_SELECTION = "full_window"
MATCH_SEED = 42
PLACEBO_SHIFT_DAYS = 0

# Exclusion radii allowed below DIRECT + OUTCOME radius (D19 sensitivity).
EXCLUSION_RADIUS_SENSITIVITY_VALUES = (250.0,)

# Seeds used for the D19 seed-stability runs.
SENSITIVITY_SEEDS = (42, 101, 202, 303, 404)

# Largest number of 25 m complaint links accepted before stopping.
MAX_EPISODE_LINKS = 20_000_000

# Chunk sizes for ball queries.
SITE_QUERY_CHUNK = 5_000
TREATMENT_QUERY_CHUNK = 5_000
RULE_QUERY_CHUNK = 20_000

# Composite sorted keys: group index * 2**34 + seconds.
KEY_TIME_BITS = 34
KEY_MAX_GROUP = 2 ** 17

# Exit codes (2 is raised by argparse for usage errors).
EXIT_COMPLETED = 0
EXIT_HARD_CHECK_FAILED = 1
EXIT_USAGE_ERROR = 2
EXIT_INCOMPLETE = 3


# ---------------------------------------------------------
# Enums and reason codes
# ---------------------------------------------------------

CONTROL_SELECTION_CHOICES = ("full_window", "pre_only")
REUSE_POLICY_CHOICES = ("non_overlapping", "never")

# (reason_code, reason_step) in the order the rules are applied.
REASON_CODES = (
    ("W_OUTSIDE_CRIME", 1),
    ("W_OUTSIDE_DARKNESS", 2),
    ("B_OUTSIDE_CRIME", 3),
    ("ARTIFACT_SITE", 4),
    ("NO_BOROUGH", 5),
    ("NOT_FIRST_OF_EPISODE", 6),
    ("S4_PRE_DIRTY", 7),
    ("S4_POST_DIRTY", 8),
    ("NO_CANDIDATE_IN_BAND", 9),
    ("NO_CLEAN_CANDIDATE", 10),
    ("NO_CANDIDATE_IN_CALIPER", 11),
    ("REUSE_CONFLICT", 12),
)

REASON_STEP = dict(REASON_CODES)

HARD_CHECK_IDS = tuple(f"H{i}" for i in range(17))

CONVENTIONS = {
    "A1": "311 and NYPD timestamps are naive NYC local time; the repeated DST hour is ignored.",
    "A2": "Overlap is closed: [s, e] overlaps [a, b] when s <= b and e >= a. "
          "S-4 pre [c-35d, c): s < c and e >= c-35d. "
          "S-4 post (closed, W_end]: e > closed and s <= W_end.",
    "A3": "Times are stored as int64 seconds.",
    "A4": "Site borough and precinct are the modal value, ties broken alphabetically; "
          "empty or 'Unspecified' becomes null.",
    "A5": "The Stage 3 validity rule (0.5-8760 h) is duplicated here and must stay in sync "
          "with src/data/clean_streetlights.py.",
    "A6": "Darkness coverage is min/max raw created_date; crime coverage is min/max crime_datetime.",
    "A7": "Artifact-site complaints form single-complaint episodes and still count as darkness.",
    "A8": "S-3 and S-4 distances are measured to each complaint's point.",
    "A9": "Prior episodes: episode start in B and at least one complaint within 250 m.",
    "A10": "Res-9 density: crimes during B in the unit's res-9 cell divided by cell area (km^2).",
    "A11": "S8 pre-window [c-14d, c] includes both ends; B is half-open.",
    "A12": "Distances follow Stage 8: <= 100 m direct, (100, 250] m ring.",
}


# ---------------------------------------------------------
# Output schemas: column order and dtypes
# ---------------------------------------------------------

PAIRS_DTYPES = {
    "pair_id": "string",
    "treatment_key": "int64",
    "treatment_site_id": "string",
    "control_site_id": "string",
    "treatment_episode_id": "int64",
    "created_date": "datetime64[us]",
    "closed_date": "datetime64[us]",
    "outage_duration_hours": "float64",
    "treatment_latitude": "float64",
    "treatment_longitude": "float64",
    "treatment_x_m": "float64",
    "treatment_y_m": "float64",
    "control_latitude": "float64",
    "control_longitude": "float64",
    "control_x_m": "float64",
    "control_y_m": "float64",
    "treatment_borough": "string",
    "control_borough": "string",
    "treatment_police_precinct": "string",
    "control_police_precinct": "string",
    "treatment_h3_res7": "string",
    "treatment_h3_res9": "string",
    "treatment_h3_res10": "string",
    "control_h3_res7": "string",
    "control_h3_res9": "string",
    "control_h3_res10": "string",
    "distance_m": "float64",
    "window_start": "datetime64[us]",
    "window_end": "datetime64[us]",
    "baseline_start": "datetime64[us]",
    "baseline_end": "datetime64[us]",
    "treatment_base_100m": "int32",
    "treatment_base_250m": "int32",
    "control_base_100m": "int32",
    "control_base_250m": "int32",
    "match_distance": "float64",
    "n_candidates_band": "int32",
    "n_candidates_clean": "int32",
    "n_candidates_caliper": "int32",
    "n_candidates_reuse_blocked": "int32",
    "control_reuse_count": "int32",
    "bal_treatment_prior_episodes_250m": "int32",
    "bal_control_prior_episodes_250m": "int32",
    "bal_treatment_h3r9_density": "float64",
    "bal_control_h3r9_density": "float64",
    "bal_treatment_pre_100m": "int32",
    "bal_treatment_pre_250m": "int32",
    "bal_control_pre_100m": "int32",
    "bal_control_pre_250m": "int32",
    "match_seed": "int64",
    "match_order": "int32",
    "control_selection": "string",
    "reuse_policy": "string",
    "placebo_shift_days": "int32",
}

# Pair columns allowed to contain nulls (H12 checks the rest).
PAIRS_NULLABLE_COLUMNS = (
    "treatment_police_precinct",
    "control_police_precinct",
)

PAIRS_REQUIRED_COLUMNS = tuple(
    column for column in PAIRS_DTYPES
    if column not in PAIRS_NULLABLE_COLUMNS
)

SITES_DTYPES = {
    "site_id": "string",
    "site_idx": "int32",
    "x_m": "int64",
    "y_m": "int64",
    "latitude": "float64",
    "longitude": "float64",
    "borough": "string",
    "police_precinct": "string",
    "h3_res7": "string",
    "h3_res9": "string",
    "h3_res10": "string",
    "n_complaints": "int32",
    "n_complaints_valid_closure": "int32",
    "n_complaints_imputed": "int32",
    "n_episodes": "int32",
    "first_created": "datetime64[us]",
    "last_created": "datetime64[us]",
    "is_artifact": "bool",
    "eligible_control": "bool",
    "n_times_treatment": "int32",
    "n_times_control": "int32",
}

SITES_NULLABLE_COLUMNS = (
    "borough",
    "police_precinct",
)

SITES_REQUIRED_COLUMNS = tuple(
    column for column in SITES_DTYPES
    if column not in SITES_NULLABLE_COLUMNS
)

UNMATCHED_DTYPES = {
    "treatment_key": "int64",
    "treatment_site_id": "string",
    "treatment_episode_id": "int64",
    "created_date": "datetime64[us]",
    "closed_date": "datetime64[us]",
    "borough": "string",
    "reason_code": "string",
    "reason_step": "int8",
    "treatment_base_100m": "Int32",
    "treatment_base_250m": "Int32",
    "n_candidates_band": "Int32",
    "n_candidates_clean": "Int32",
    "n_candidates_caliper": "Int32",
}

UNMATCHED_NULLABLE_COLUMNS = (
    "borough",
    "treatment_base_100m",
    "treatment_base_250m",
    "n_candidates_band",
    "n_candidates_clean",
    "n_candidates_caliper",
)

UNMATCHED_REQUIRED_COLUMNS = tuple(
    column for column in UNMATCHED_DTYPES
    if column not in UNMATCHED_NULLABLE_COLUMNS
)

# Raw 311 columns read by Stage 7.
RAW_COMPLAINT_COLUMNS = (
    "unique_key",
    "created_date",
    "closed_date",
    "status",
    "borough",
    "police_precinct",
    "latitude",
    "longitude",
)

TREATMENT_COLUMNS = (
    "unique_key",
    "created_date",
    "closed_date",
    "latitude",
    "longitude",
)

CRIME_COLUMNS = (
    "crime_datetime",
    "latitude",
    "longitude",
)

# Stage 3 values carried next to the raw values until H1 compares them.
S3_CHECK_COLUMNS = (
    "s3_created_date",
    "s3_closed_date",
    "s3_latitude",
    "s3_longitude",
)

# H1: largest allowed Stage 3 vs raw coordinate difference (degrees).
COORD_TOLERANCE_DEG = 1e-6

# Label values treated as missing for borough and precinct (A4).
MISSING_LABELS = ("", "unspecified")

WGS84_CRS = "EPSG:4326"

HASH_BLOCK_BYTES = 2 ** 20

DIAGNOSTICS_SECTIONS = (
    "provenance",
    "parameters",
    "coverage",
    "universe",
    "attrition",
    "unmatched_reasons",
    "balance",
    "reuse",
    "contamination",
    "candidates",
    "hard_checks",
    "recheck",
    "determinism",
    "runtime",
)

DIAGNOSTICS_UNIVERSE_KEYS = (
    "n_raw_complaints",
    "n_with_coordinates",
    "n_in_bbox",
    "n_imputed",
    "imputed_by_year",
    "n_sites",
    "artifact_quantile_method",
    "artifact_threshold",
    "artifact_threshold_is_maximum",
    "n_artifact_sites",
    "n_artifact_complaints",
    "n_episodes",
    "episode_size_percentiles",
    "episode_span_days_percentiles",
    "episode_max_footprint_m",
)

PACKAGE_NAMES = (
    "numpy",
    "pandas",
    "scipy",
    "geopandas",
    "shapely",
    "h3",
    "pyproj",
    "pyarrow",
    "psutil",
)


# ---------------------------------------------------------
# CRS unit check (H0, part a)
# ---------------------------------------------------------

if CRS(PROJECTED_CRS).axis_info[0].unit_name != "metre":
    raise ValueError(f"{PROJECTED_CRS} must use metre units")


# ---------------------------------------------------------
# Exceptions
# ---------------------------------------------------------

class HardCheckError(RuntimeError):
    """A Stage 7 hard check failed; the stage must stop."""


# ---------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------

@dataclass(frozen=True)
class Params:
    projected_crs: str
    nyc_bbox: tuple[float, float, float, float]
    site_round_m: int
    episode_merge_radius_m: float
    artifact_quantile: float
    valid_min_duration_h: float
    valid_max_duration_h: float
    report_lag_days: int
    imputed_duration_hours: float
    direct_radius_m: float
    outcome_radius_m: float
    exclusion_radius_m: float
    match_band_min_m: float
    match_band_max_m: float
    treatment_clean_radius_m: float
    event_window_days: int
    post_window_days: int
    baseline_days: int
    s8_pre_window_days: int
    prior_episode_radius_m: float
    h3_resolutions: tuple[int, int, int]
    caliper_sd: float
    reuse_policy: str
    control_selection: str
    match_seed: int
    placebo_shift_days: int
    recheck_sample_n: int
    recheck_seed: int
    smd_max: float
    vr_range: tuple[float, float]
    out_dir: Path
    skip_recheck: bool
    check_determinism: bool


# Params fields that only control how the run is executed, not what it
# computes. They are ignored when deciding whether a run is canonical.
RUN_CONTROL_FIELDS = (
    "out_dir",
    "skip_recheck",
    "check_determinism",
)

# The only methodology fields with a command-line option (D19).
SENSITIVITY_FIELDS = (
    "exclusion_radius_m",
    "match_band_max_m",
    "caliper_sd",
    "reuse_policy",
    "report_lag_days",
    "imputed_duration_hours",
    "control_selection",
    "match_seed",
    "placebo_shift_days",
)

# Cross-stage contracts: field -> (required value, stage it must match).
# Written to diagnostics.parameters.locked.
LOCKED_PARAMETERS = {
    "valid_min_duration_h": (VALID_MIN_DURATION_H, "Stage 3"),
    "valid_max_duration_h": (VALID_MAX_DURATION_H, "Stage 3"),
    "post_window_days": (POST_WINDOW_DAYS, "Stage 8"),
    "event_window_days": (EVENT_WINDOW_DAYS, "Stage 10"),
    "direct_radius_m": (DIRECT_RADIUS_M, "Stage 8"),
    "outcome_radius_m": (OUTCOME_RADIUS_M, "Stage 8"),
    "s8_pre_window_days": (S8_PRE_WINDOW_DAYS, "Stage 8"),
}

# Fixed methodology values with no sensitivity runs: field -> value.
FIXED_PARAMETERS = {
    "projected_crs": PROJECTED_CRS,
    "nyc_bbox": NYC_BBOX,
    "h3_resolutions": H3_RESOLUTIONS,
    "site_round_m": SITE_ROUND_M,
    "episode_merge_radius_m": EPISODE_MERGE_RADIUS_M,
    "artifact_quantile": ARTIFACT_QUANTILE,
    "match_band_min_m": MATCH_BAND_MIN_M,
    "treatment_clean_radius_m": TREATMENT_CLEAN_RADIUS_M,
    "prior_episode_radius_m": PRIOR_EPISODE_RADIUS_M,
    "baseline_days": BASELINE_DAYS,
    "recheck_sample_n": RECHECK_SAMPLE_N,
    "recheck_seed": RECHECK_SEED,
    "smd_max": SMD_MAX,
    "vr_range": (VR_MIN, VR_MAX),
}


def locked_parameters_metadata():
    """Structure for diagnostics.parameters.locked."""

    return {
        name: {"value": value, "must_match": stage}
        for name, (value, stage) in LOCKED_PARAMETERS.items()
    }


# Every Params field must be in exactly one group, so a new field can't
# silently skip H0.
_FIELD_GROUPS = (
    tuple(LOCKED_PARAMETERS),
    tuple(FIXED_PARAMETERS),
    SENSITIVITY_FIELDS,
    RUN_CONTROL_FIELDS,
)

if sorted(name for group in _FIELD_GROUPS for name in group) != sorted(
    field.name for field in dataclasses.fields(Params)
):
    raise ValueError(
        "Params fields must each belong to exactly one of LOCKED_PARAMETERS, "
        "FIXED_PARAMETERS, SENSITIVITY_FIELDS or RUN_CONTROL_FIELDS"
    )


@dataclass
class Coverage:
    crime_min_s: int
    crime_max_s: int
    dark_min_s: int
    dark_max_s: int


@dataclass
class SpatialIndexes:
    complaint_tree: cKDTree
    site_tree: cKDTree
    crime_tree: cKDTree | None
    dirty_key: np.ndarray
    dirty_runmax_end: np.ndarray
    dirty_offsets: np.ndarray
    crime_direct_key: np.ndarray
    crime_ring_key: np.ndarray
    crime_cell_key: np.ndarray
    crime_cell_rank: dict[int, int]
    band_sites: np.ndarray | None = None
    band_dist: np.ndarray | None = None
    band_offsets: np.ndarray | None = None


@dataclass(frozen=True)
class Scales:
    sd_100: float
    sd_250: float
    n_treatments: int


@dataclass
class MatchResult:
    pairs_raw: pd.DataFrame
    rejected: pd.DataFrame
    order_hash: str
    blocked_by_preregistration: int


@dataclass
class CheckResult:
    check_id: str
    passed: bool
    n_violations: int
    examples: list[str]
    seconds: float


# ---------------------------------------------------------
# Helper classes
# ---------------------------------------------------------

class StageTimer:
    """Record wall time and memory for one pipeline step."""

    def __init__(self, name, runtime_log):
        self.name = name
        self.runtime_log = runtime_log
        self._process = psutil.Process()
        self._start = 0.0
        self._rss_start = 0

    def _peak_bytes(self):
        info = self._process.memory_info()
        # peak_wset exists on Windows only; elsewhere fall back to rss.
        return getattr(info, "peak_wset", info.rss)

    def __enter__(self):
        self._rss_start = self._process.memory_info().rss
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        # argparse exits via SystemExit for --help and usage errors.
        if exc_type is not None and issubclass(exc_type, SystemExit):
            return False

        seconds = time.perf_counter() - self._start
        rss_end = self._process.memory_info().rss
        entry = {
            "seconds": round(seconds, 3),
            "rss_start_mb": round(self._rss_start / 2**20, 1),
            "rss_end_mb": round(rss_end / 2**20, 1),
            "peak_mb": round(self._peak_bytes() / 2**20, 1),
            "completed": exc_type is None,
        }
        self.runtime_log[self.name] = entry
        print(
            f"[{self.name}] {entry['seconds']:.2f} s, "
            f"rss {entry['rss_end_mb']:.0f} MB, "
            f"peak {entry['peak_mb']:.0f} MB"
        )
        return False


class ReuseRegistry:
    """
    Occupied analysis windows W per site, in int64 seconds.

    Pre-registered windows are the W of every eligible treatment at its
    own site (C3). Assigned windows are control uses. Overlap is closed
    on both ends (A2).

    H9 must not use this class to verify itself: it rebuilds the
    intervals from the pairs and eligible treatments (see the module
    docstring).
    """

    def __init__(self, policy):
        if policy not in REUSE_POLICY_CHOICES:
            raise ValueError(
                f"reuse policy must be one of {REUSE_POLICY_CHOICES}, "
                f"got {policy!r}"
            )
        self.policy = policy
        self._preregistered = {}
        self._assigned = {}

    @staticmethod
    def _overlaps(intervals, w_start_s, w_end_s):
        for start_s, end_s in intervals:
            if start_s <= w_end_s and end_s >= w_start_s:
                return True
        return False

    def preregister(self, site_idx, w_start_s, w_end_s):
        site_idx = np.asarray(site_idx, dtype=np.int64)
        w_start_s = np.asarray(w_start_s, dtype=np.int64)
        w_end_s = np.asarray(w_end_s, dtype=np.int64)

        if not (len(site_idx) == len(w_start_s) == len(w_end_s)):
            raise ValueError("preregister inputs must have equal length")

        if np.any(w_end_s < w_start_s):
            raise ValueError("preregistered window ends before it starts")

        for site, start_s, end_s in zip(
            site_idx.tolist(), w_start_s.tolist(), w_end_s.tolist()
        ):
            self._preregistered.setdefault(site, []).append((start_s, end_s))

    def blocked_by_preregistration(self, site_idx, w_start_s, w_end_s):
        return self._overlaps(
            self._preregistered.get(int(site_idx), ()),
            int(w_start_s),
            int(w_end_s),
        )

    def is_free(self, site_idx, w_start_s, w_end_s):
        site_idx = int(site_idx)
        w_start_s = int(w_start_s)
        w_end_s = int(w_end_s)

        assigned = self._assigned.get(site_idx, ())

        if self.policy == "never" and assigned:
            return False

        if self._overlaps(assigned, w_start_s, w_end_s):
            return False

        return not self.blocked_by_preregistration(
            site_idx, w_start_s, w_end_s
        )

    def assign(self, site_idx, w_start_s, w_end_s):
        if int(w_end_s) < int(w_start_s):
            raise ValueError("assigned window ends before it starts")

        if not self.is_free(site_idx, w_start_s, w_end_s):
            raise HardCheckError(
                f"site {site_idx} is not free for window "
                f"[{w_start_s}, {w_end_s}]"
            )

        self._assigned.setdefault(int(site_idx), []).append(
            (int(w_start_s), int(w_end_s))
        )

    def n_uses(self, site_idx):
        return len(self._assigned.get(int(site_idx), ()))

    def all_intervals(self):
        rows = []

        for role, registry in (
            ("treatment_prereg", self._preregistered),
            ("control", self._assigned),
        ):
            for site, intervals in registry.items():
                for start_s, end_s in intervals:
                    rows.append((site, start_s, end_s, role))

        return pd.DataFrame(
            rows,
            columns=["site_idx", "w_start_s", "w_end_s", "role"],
        ).astype(
            {
                "site_idx": "int64",
                "w_start_s": "int64",
                "w_end_s": "int64",
                "role": "string",
            }
        )


# ---------------------------------------------------------
# Private helpers
# ---------------------------------------------------------

def _record(checks, result):
    checks[result.check_id] = result

    status = "PASS" if result.passed else "FAIL"

    print(
        f"{result.check_id}: {status} "
        f"({result.n_violations} violations, {result.seconds:.2f} s)"
    )

    for example in result.examples:
        print(f"    - {example}")


def _to_seconds(values):
    """datetime64 values (any resolution) -> int64 seconds since 1970 (A3)."""

    values = pd.Series(values)

    if values.isna().any():
        raise ValueError("cannot convert missing timestamps to seconds")

    return values.astype("datetime64[s]").astype("int64").to_numpy()


@functools.lru_cache(maxsize=None)
def _transformer(source_crs, target_crs):
    return Transformer.from_crs(source_crs, target_crs, always_xy=True)


def _project(lon, lat, crs):
    """WGS84 lon/lat -> projected x/y in metres."""

    x, y = _transformer(WGS84_CRS, crs).transform(
        np.asarray(lon, dtype=np.float64),
        np.asarray(lat, dtype=np.float64),
    )

    return np.asarray(x), np.asarray(y)


def _normalise_label(values):
    """Strip labels and turn empty or 'Unspecified' into missing (A4)."""

    values = values.astype("string").str.strip()

    return values.mask(values.str.casefold().isin(MISSING_LABELS))


def _file_fingerprint(path):
    path = Path(path)
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(HASH_BLOCK_BYTES), b""):
            digest.update(block)

    stat = path.stat()

    return {
        "path": path.as_posix(),
        "size_bytes": stat.st_size,
        "modified_utc": datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat(timespec="seconds"),
        "sha256": digest.hexdigest(),
    }


def _git_state():
    def run_git(*args):
        return subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()

    try:
        return {
            "sha": run_git("rev-parse", "HEAD"),
            "branch": run_git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(run_git("status", "--porcelain")),
        }
    except (OSError, subprocess.SubprocessError) as error:
        print(f"Warning: git state unavailable ({error})")
        return {"sha": "unknown", "branch": "unknown", "dirty": None}


def _package_versions():
    versions = {
        "python": platform.python_version(),
    }

    for name in PACKAGE_NAMES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not installed"

    return versions


def _collect_provenance():
    return {
        "run_started_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "platform": platform.platform(),
        "git": _git_state(),
        "packages": _package_versions(),
        "inputs": {
            "raw_complaints": _file_fingerprint(RAW_COMPLAINTS_FILE),
            "treatments": _file_fingerprint(TREATMENT_FILE),
            "crime": _file_fingerprint(CRIME_FILE),
        },
    }


def _build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Stage 7: match each eligible treatment outage to a clean "
            "control site (Issue 4 design). Only the D19 sensitivity "
            "parameters can be changed; all other values are locked."
        ),
        allow_abbrev=False,
    )

    darkness = parser.add_argument_group("darkness (D19 sensitivity)")
    darkness.add_argument(
        "--report-lag-days", type=int, default=REPORT_LAG_DAYS
    )
    darkness.add_argument(
        "--imputed-duration-hours", type=float, default=IMPUTED_DURATION_HOURS
    )

    spatial = parser.add_argument_group("spatial rules in metres (D19 sensitivity)")
    spatial.add_argument(
        "--exclusion-radius-m", type=float, default=EXCLUSION_RADIUS_M
    )
    spatial.add_argument(
        "--match-band-max-m", type=float, default=MATCH_BAND_MAX_M
    )

    matching = parser.add_argument_group("matching (D19 sensitivity)")
    matching.add_argument("--caliper-sd", type=float, default=CALIPER_SD)
    matching.add_argument(
        "--reuse-policy", choices=REUSE_POLICY_CHOICES, default=REUSE_POLICY
    )
    matching.add_argument(
        "--control-selection",
        choices=CONTROL_SELECTION_CHOICES,
        default=CONTROL_SELECTION,
    )
    matching.add_argument(
        "--match-seed", "--seed", dest="match_seed", type=int,
        default=MATCH_SEED,
    )
    matching.add_argument(
        "--placebo-shift-days",
        type=int,
        default=PLACEBO_SHIFT_DAYS,
        help="move treatment dates back by this many days (D18 placebo)",
    )

    run = parser.add_argument_group("run control")
    run.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    run.add_argument(
        "--skip-recheck",
        action="store_true",
        help="skip H10/H11 (sensitivity runs only; not allowed in the "
             "canonical output directory)",
    )
    run.add_argument(
        "--check-determinism",
        action="store_true",
        help="re-run the matching loop and compare pair-list hashes",
    )

    return parser


def _params_from_namespace(namespace):
    return Params(
        projected_crs=PROJECTED_CRS,
        nyc_bbox=NYC_BBOX,
        site_round_m=SITE_ROUND_M,
        episode_merge_radius_m=EPISODE_MERGE_RADIUS_M,
        artifact_quantile=ARTIFACT_QUANTILE,
        valid_min_duration_h=VALID_MIN_DURATION_H,
        valid_max_duration_h=VALID_MAX_DURATION_H,
        report_lag_days=namespace.report_lag_days,
        imputed_duration_hours=namespace.imputed_duration_hours,
        direct_radius_m=DIRECT_RADIUS_M,
        outcome_radius_m=OUTCOME_RADIUS_M,
        exclusion_radius_m=namespace.exclusion_radius_m,
        match_band_min_m=MATCH_BAND_MIN_M,
        match_band_max_m=namespace.match_band_max_m,
        treatment_clean_radius_m=TREATMENT_CLEAN_RADIUS_M,
        event_window_days=EVENT_WINDOW_DAYS,
        post_window_days=POST_WINDOW_DAYS,
        baseline_days=BASELINE_DAYS,
        s8_pre_window_days=S8_PRE_WINDOW_DAYS,
        prior_episode_radius_m=PRIOR_EPISODE_RADIUS_M,
        h3_resolutions=H3_RESOLUTIONS,
        caliper_sd=namespace.caliper_sd,
        reuse_policy=namespace.reuse_policy,
        control_selection=namespace.control_selection,
        match_seed=namespace.match_seed,
        placebo_shift_days=namespace.placebo_shift_days,
        recheck_sample_n=RECHECK_SAMPLE_N,
        recheck_seed=RECHECK_SEED,
        smd_max=SMD_MAX,
        vr_range=(VR_MIN, VR_MAX),
        out_dir=namespace.out_dir,
        skip_recheck=namespace.skip_recheck,
        check_determinism=namespace.check_determinism,
    )


def _non_default_fields(params, defaults):
    changed = []

    for field in dataclasses.fields(Params):
        if field.name in RUN_CONTROL_FIELDS:
            continue

        if getattr(params, field.name) != getattr(defaults, field.name):
            changed.append(field.name)

    return changed


def _is_default_out_dir(out_dir):
    return Path(out_dir).resolve() == DEFAULT_OUT_DIR.resolve()


def _is_float(value):
    return isinstance(value, float) and not isinstance(value, bool)


def _non_finite_violations(params):
    """Float parameters (including tuple members) that are NaN or infinite."""

    violations = []

    for field in dataclasses.fields(Params):
        value = getattr(params, field.name)

        if _is_float(value):
            if not math.isfinite(value):
                violations.append(
                    f"{field.name} must be finite (got {value})"
                )

        elif isinstance(value, tuple):
            for position, item in enumerate(value):
                if _is_float(item) and not math.isfinite(item):
                    violations.append(
                        f"{field.name}[{position}] must be finite "
                        f"(got {item})"
                    )

    return violations


def _validate_params(params, defaults):
    """H0: return a list of violations (empty when the parameters are valid)."""

    # Non-finite values make every later comparison False, so report
    # them first.
    violations = _non_finite_violations(params)

    # Cross-stage contracts.
    for name, (value, stage) in LOCKED_PARAMETERS.items():
        if getattr(params, name) != value:
            violations.append(
                f"{name} must equal the {stage} value ({value:g}); "
                f"got {getattr(params, name)}"
            )

    # Fixed methodology values.
    for name, value in FIXED_PARAMETERS.items():
        if getattr(params, name) != value:
            violations.append(
                f"{name} is a fixed methodology value and must equal "
                f"{value!r}; got {getattr(params, name)!r}"
            )

    # Units and structural settings.
    if CRS(params.projected_crs).axis_info[0].unit_name != "metre":
        violations.append(f"{params.projected_crs} must use metre units")

    lat_min, lat_max, lon_min, lon_max = params.nyc_bbox
    if not (lat_min < lat_max and lon_min < lon_max):
        violations.append("nyc_bbox must be (lat_min, lat_max, lon_min, lon_max)")

    if any(not 0 <= res <= 15 for res in params.h3_resolutions):
        violations.append("h3_resolutions must be between 0 and 15")

    # Enums.
    if params.control_selection not in CONTROL_SELECTION_CHOICES:
        violations.append(
            f"control_selection must be one of {CONTROL_SELECTION_CHOICES}"
        )

    if params.reuse_policy not in REUSE_POLICY_CHOICES:
        violations.append(f"reuse_policy must be one of {REUSE_POLICY_CHOICES}")

    # Spatial rules.
    if not 0 < params.direct_radius_m < params.outcome_radius_m:
        violations.append("need 0 < direct_radius_m < outcome_radius_m")

    if params.match_band_min_m < 2 * params.outcome_radius_m:
        violations.append(
            f"match_band_min_m ({params.match_band_min_m:g}) must be >= "
            f"2 x outcome_radius_m ({2 * params.outcome_radius_m:g}) so the "
            f"treatment and control outcome zones cannot overlap"
        )

    if params.match_band_max_m <= params.match_band_min_m:
        violations.append(
            f"match_band_max_m ({params.match_band_max_m:g}) must be > "
            f"match_band_min_m ({params.match_band_min_m:g})"
        )

    minimum_exclusion = params.outcome_radius_m + params.direct_radius_m
    if (
        params.exclusion_radius_m < minimum_exclusion
        and params.exclusion_radius_m not in EXCLUSION_RADIUS_SENSITIVITY_VALUES
    ):
        violations.append(
            f"exclusion_radius_m ({params.exclusion_radius_m:g}) must be >= "
            f"{minimum_exclusion:g} or one of the sensitivity values "
            f"{EXCLUSION_RADIUS_SENSITIVITY_VALUES}"
        )

    for name in (
        "episode_merge_radius_m",
        "treatment_clean_radius_m",
        "prior_episode_radius_m",
    ):
        if getattr(params, name) <= 0:
            violations.append(f"{name} must be > 0")

    if params.site_round_m < 1:
        violations.append("site_round_m must be >= 1")

    # Darkness rules.
    if not 0 < params.artifact_quantile < 1:
        violations.append("artifact_quantile must be between 0 and 1")

    if not 0 <= params.valid_min_duration_h < params.valid_max_duration_h:
        violations.append(
            "need 0 <= valid_min_duration_h < valid_max_duration_h"
        )

    if params.imputed_duration_hours <= 0:
        violations.append("imputed_duration_hours must be > 0")

    if params.report_lag_days < 0:
        violations.append("report_lag_days must be >= 0")

    # Windows.
    for name in (
        "event_window_days",
        "post_window_days",
        "baseline_days",
        "s8_pre_window_days",
    ):
        if getattr(params, name) <= 0:
            violations.append(f"{name} must be > 0")

    if params.placebo_shift_days < 0:
        violations.append(
            "placebo_shift_days must be >= 0 (dates are moved back)"
        )

    # Matching.
    if params.caliper_sd <= 0:
        violations.append("caliper_sd must be > 0")

    if params.match_seed < 0 or params.recheck_seed < 0:
        violations.append("match_seed and recheck_seed must be >= 0")

    # Validation settings.
    if params.recheck_sample_n <= 0:
        violations.append("recheck_sample_n must be > 0")

    if params.smd_max <= 0:
        violations.append("smd_max must be > 0")

    vr_min, vr_max = params.vr_range
    if not 0 < vr_min < vr_max:
        violations.append("need 0 < vr_min < vr_max")

    # Only D19 sensitivity parameters may differ from their defaults.
    changed = _non_default_fields(params, defaults)

    not_sensitivity = [
        name for name in changed if name not in SENSITIVITY_FIELDS
    ]
    if not_sensitivity:
        violations.append(
            "only D19 sensitivity parameters may differ from their "
            f"defaults; also changed: {', '.join(not_sensitivity)}"
        )

    # Canonical output protection.
    default_dir = _is_default_out_dir(params.out_dir)

    if changed and default_dir:
        violations.append(
            "non-default parameters "
            f"({', '.join(changed)}) require --out-dir other than "
            f"{DEFAULT_OUT_DIR.as_posix()}"
        )

    if params.skip_recheck and default_dir:
        violations.append(
            "--skip-recheck is not allowed for the canonical output "
            f"directory {DEFAULT_OUT_DIR.as_posix()}"
        )

    return violations


# ---------------------------------------------------------
# Pipeline
# ---------------------------------------------------------

def parse_args(argv=None, checks=None):
    """Step 0: build Params from defaults and CLI overrides, then run H0."""

    parser = _build_parser()

    params = _params_from_namespace(parser.parse_args(argv))
    defaults = _params_from_namespace(parser.parse_args([]))

    start = time.perf_counter()
    violations = _validate_params(params, defaults)

    result = CheckResult(
        check_id="H0",
        passed=not violations,
        n_violations=len(violations),
        examples=violations[:10],
        seconds=time.perf_counter() - start,
    )

    if checks is not None:
        _record(checks, result)

    if violations:
        raise HardCheckError(
            f"H0 parameter validation failed "
            f"({len(violations)} violations)\n  - "
            + "\n  - ".join(violations)
        )

    return params


def load_raw_complaints(params):
    """
    Step 1: read the raw 311 file and keep geocoded complaints inside the
    NYC bounding box, sorted by unique_key.

    Returns the complaint table and a summary with the raw counts and
    the darkness coverage (A6: min/max created_date over all raw rows).
    """

    raw = pd.read_csv(
        RAW_COMPLAINTS_FILE,
        usecols=list(RAW_COMPLAINT_COLUMNS),
        low_memory=False,
    )

    n_raw = len(raw)
    n_duplicate_keys = int(raw["unique_key"].duplicated().sum())

    raw["created_date"] = pd.to_datetime(
        raw["created_date"], format="ISO8601", errors="coerce"
    )
    raw["closed_date"] = pd.to_datetime(
        raw["closed_date"], format="ISO8601", errors="coerce"
    )

    has_created = raw["created_date"].notna()
    dark_min_s, dark_max_s = _to_seconds(
        [raw.loc[has_created, "created_date"].min(),
         raw.loc[has_created, "created_date"].max()]
    )

    has_coordinates = raw["latitude"].notna() & raw["longitude"].notna()

    lat_min, lat_max, lon_min, lon_max = params.nyc_bbox
    in_bbox = (
        has_coordinates
        & raw["latitude"].between(lat_min, lat_max)
        & raw["longitude"].between(lon_min, lon_max)
    )

    complaints = (
        raw.loc[in_bbox]
        .sort_values("unique_key", kind="mergesort")
        .reset_index(drop=True)
    )
    del raw

    n_unparseable_created = int(complaints["created_date"].isna().sum())

    complaints["status"] = complaints["status"].astype("string")
    complaints["borough"] = _normalise_label(complaints["borough"])
    complaints["police_precinct"] = _normalise_label(
        complaints["police_precinct"]
    )

    x_m, y_m = _project(
        complaints["longitude"], complaints["latitude"], params.projected_crs
    )
    complaints["x_m"] = x_m
    complaints["y_m"] = y_m

    summary = {
        "n_raw_complaints": n_raw,
        "n_with_coordinates": int(has_coordinates.sum()),
        "n_in_bbox": int(in_bbox.sum()),
        "n_duplicate_unique_keys": n_duplicate_keys,
        "n_unparseable_created": n_unparseable_created,
        "n_missing_closed": int(complaints["closed_date"].isna().sum()),
        "n_missing_borough": int(complaints["borough"].isna().sum()),
        "n_missing_precinct": int(complaints["police_precinct"].isna().sum()),
        "dark_min_s": int(dark_min_s),
        "dark_max_s": int(dark_max_s),
    }

    print(
        f"Raw complaints: {n_raw:,} -> with coordinates "
        f"{summary['n_with_coordinates']:,} -> inside bbox "
        f"{summary['n_in_bbox']:,}"
    )

    return complaints, summary


def load_treatments(params, complaints):
    """
    Step 2: read the Stage 3 treatments and join them to the raw
    complaints on unique_key. Returns the joined table and the number
    of Stage 3 rows read (for H1).

    The Stage 3 values are kept as s3_* columns for H1. created_date and
    closed_date are the treatment times, moved back by
    placebo_shift_days (C4); the *_original columns keep the real times.
    The real complaint in `complaints` is never shifted.
    """

    s3 = (
        pd.read_parquet(TREATMENT_FILE, columns=list(TREATMENT_COLUMNS))
        .rename(
            columns={
                "unique_key": "treatment_key",
                "created_date": "s3_created_date",
                "closed_date": "s3_closed_date",
                "latitude": "s3_latitude",
                "longitude": "s3_longitude",
            }
        )
        .sort_values("treatment_key", kind="mergesort")
        .reset_index(drop=True)
    )

    raw_side = complaints[
        [
            "unique_key",
            "created_date",
            "closed_date",
            "latitude",
            "longitude",
            "x_m",
            "y_m",
            "borough",
            "police_precinct",
        ]
    ].rename(
        columns={
            "unique_key": "treatment_key",
            "created_date": "created_date_original",
            "closed_date": "closed_date_original",
        }
    )
    raw_side["complaint_idx"] = np.arange(len(raw_side), dtype=np.int64)

    treatments_all = s3.merge(raw_side, on="treatment_key", how="left")

    shift = pd.Timedelta(days=params.placebo_shift_days)
    treatments_all["created_date"] = (
        treatments_all["created_date_original"] - shift
    )
    treatments_all["closed_date"] = (
        treatments_all["closed_date_original"] - shift
    )

    print(
        f"Stage 3 treatments: {len(s3):,} "
        f"(placebo shift {params.placebo_shift_days} days)"
    )

    return treatments_all, len(s3)


def load_crime(params, raw_summary):
    """
    Step 3: read crime times and coordinates (not the WKB geometry),
    project them, and build the coverage record (A6).

    Crime latitude/longitude are kept for the H3 cell index built later.
    Rows with missing values are kept so H1 can report them; their t_s
    is 0 and they are excluded from the coverage bounds.
    """

    crime = pd.read_parquet(CRIME_FILE, columns=list(CRIME_COLUMNS))

    valid = (
        crime["crime_datetime"].notna()
        & crime["latitude"].notna()
        & crime["longitude"].notna()
    )

    t_s = np.zeros(len(crime), dtype=np.int64)
    t_s[valid.to_numpy()] = _to_seconds(crime.loc[valid, "crime_datetime"])
    crime["t_s"] = t_s

    x_m, y_m = _project(
        crime["longitude"], crime["latitude"], params.projected_crs
    )
    crime["x_m"] = x_m
    crime["y_m"] = y_m

    crime = crime.drop(columns=["crime_datetime"])

    valid_t = t_s[valid.to_numpy()]
    coverage = Coverage(
        crime_min_s=int(valid_t.min()),
        crime_max_s=int(valid_t.max()),
        dark_min_s=raw_summary["dark_min_s"],
        dark_max_s=raw_summary["dark_max_s"],
    )

    crime_summary = {
        "n_crime": len(crime),
        "n_crime_invalid": int((~valid).sum()),
    }

    print(f"Crimes: {len(crime):,}")

    return crime, coverage, crime_summary


# ---------------------------------------------------------
# Hard checks
# ---------------------------------------------------------

def _keys(frame, mask, limit=10):
    return frame.loc[mask, "treatment_key"].head(limit).astype(str).tolist()


def _check_h1(raw_summary, treatments_all, n_s3_rows, crime_summary):
    """H1: input integrity of the raw 311 file, Stage 3 and crime."""

    start = time.perf_counter()
    problems = []

    def add(count, message, examples=()):
        if count:
            problems.append((count, message, list(examples)))

    add(
        raw_summary["n_duplicate_unique_keys"],
        "duplicate unique_key values in the raw 311 file",
    )
    add(
        raw_summary["n_unparseable_created"],
        "geocoded raw complaints with an unparseable created_date",
    )

    duplicated = treatments_all["treatment_key"].duplicated(keep=False)
    add(
        int(duplicated.sum()),
        "duplicate treatment keys after the join",
        _keys(treatments_all, duplicated),
    )
    add(
        abs(len(treatments_all) - n_s3_rows),
        f"joined rows ({len(treatments_all)}) differ from Stage 3 rows "
        f"({n_s3_rows})",
    )

    missing = treatments_all["complaint_idx"].isna()
    add(
        int(missing.sum()),
        "Stage 3 keys not found among geocoded raw complaints",
        _keys(treatments_all, missing),
    )

    found = ~missing

    for s3_column, raw_column in (
        ("s3_created_date", "created_date_original"),
        ("s3_closed_date", "closed_date_original"),
    ):
        differs = found & ~(
            treatments_all[s3_column] == treatments_all[raw_column]
        ).fillna(False)
        add(
            int(differs.sum()),
            f"{s3_column} differs from the raw value",
            _keys(treatments_all, differs),
        )

    for s3_column, raw_column in (
        ("s3_latitude", "latitude"),
        ("s3_longitude", "longitude"),
    ):
        gap = (treatments_all[s3_column] - treatments_all[raw_column]).abs()
        differs = found & ~(gap <= COORD_TOLERANCE_DEG).fillna(False)
        add(
            int(differs.sum()),
            f"{s3_column} differs from the raw value by more than "
            f"{COORD_TOLERANCE_DEG:g} degrees",
            _keys(treatments_all, differs),
        )

    add(
        crime_summary["n_crime_invalid"],
        "crime rows with a missing time or coordinate",
    )

    examples = []
    for count, message, keys in problems:
        suffix = f" (e.g. {', '.join(keys)})" if keys else ""
        examples.append(f"{count:,} {message}{suffix}")

    return CheckResult(
        check_id="H1",
        passed=not problems,
        n_violations=sum(count for count, _, _ in problems),
        examples=examples[:10],
        seconds=time.perf_counter() - start,
    )


def _check_h16_inputs(params, treatments_all, complaints):
    """
    H16 (after step 2): placebo dates equal the real dates minus the
    shift, and the real complaints keep their real dates (C4).
    """

    start = time.perf_counter()

    if params.placebo_shift_days == 0:
        return CheckResult(
            check_id="H16",
            passed=True,
            n_violations=0,
            examples=["not applicable (placebo_shift_days = 0)"],
            seconds=time.perf_counter() - start,
        )

    shift = pd.Timedelta(days=params.placebo_shift_days)
    found = treatments_all["complaint_idx"].notna()
    rows = treatments_all.loc[found]

    created_wrong = rows["created_date"] != rows["created_date_original"] - shift
    closed_wrong = rows["closed_date"] != rows["closed_date_original"] - shift

    real_created = complaints["created_date"].to_numpy()[
        rows["complaint_idx"].astype("int64").to_numpy()
    ]
    real_wrong = rows["created_date_original"].to_numpy() != real_created

    problems = []
    for mask, message in (
        (created_wrong.to_numpy(), "shifted created_date is wrong"),
        (closed_wrong.to_numpy(), "shifted closed_date is wrong"),
        (real_wrong, "real complaint created_date was changed"),
    ):
        count = int(mask.sum())
        if count:
            keys = rows.loc[mask, "treatment_key"].head(10).astype(str)
            problems.append(f"{count:,} {message} (e.g. {', '.join(keys)})")

    return CheckResult(
        check_id="H16",
        passed=not problems,
        n_violations=int(
            created_wrong.sum() + closed_wrong.sum() + real_wrong.sum()
        ),
        examples=problems,
        seconds=time.perf_counter() - start,
    )


def run_hard_checks(params, stage, checks, **tables):
    """
    Step 17: run the hard checks for one pipeline stage, record them and
    raise HardCheckError if any failed.

    Stages implemented so far: "inputs" (H1, H16 after step 2).
    """

    if stage == "inputs":
        results = [
            _check_h1(
                tables["raw_summary"],
                tables["treatments_all"],
                tables["n_s3_rows"],
                tables["crime_summary"],
            ),
            _check_h16_inputs(
                params, tables["treatments_all"], tables["complaints"]
            ),
        ]
    else:
        raise ValueError(f"unknown hard-check stage {stage!r}")

    for result in results:
        _record(checks, result)

    failed = [result.check_id for result in results if not result.passed]

    if failed:
        raise HardCheckError(
            f"hard check(s) failed at stage '{stage}': {', '.join(failed)}"
        )


def _finalise_treatments(treatments_all):
    """Drop the Stage 3 comparison columns once H1 has passed."""

    treatments_all = treatments_all.drop(columns=list(S3_CHECK_COLUMNS))
    treatments_all["complaint_idx"] = treatments_all["complaint_idx"].astype(
        "int64"
    )

    return treatments_all


def _format_seconds(seconds):
    return pd.Timestamp(int(seconds), unit="s").isoformat()


def _print_run_header(params):
    print("========================================")
    print("Stage 7: control matching (Issue 4)")
    print("========================================")

    print("\nParameters:")
    for field in dataclasses.fields(Params):
        print(f"  {field.name}: {getattr(params, field.name)}")

    print("\nLocked cross-stage parameters:")
    for name, entry in locked_parameters_metadata().items():
        print(f"  {name} = {entry['value']:g} (must match {entry['must_match']})")

    print("\nConventions:")
    for key, text in CONVENTIONS.items():
        print(f"  {key}: {text}")


def _stop(error):
    # The violations were already printed by _record.
    sys.stdout.flush()
    print(
        f"\nStage 7 stopped: {str(error).splitlines()[0]}",
        file=sys.stderr,
    )
    return EXIT_HARD_CHECK_FAILED


def main(argv=None):
    runtime = {}
    checks = {}

    try:
        with StageTimer("parse_args", runtime):
            params = parse_args(argv, checks)
    except HardCheckError as error:
        return _stop(error)

    _print_run_header(params)

    print("\n--- Provenance")
    with StageTimer("provenance", runtime):
        provenance = _collect_provenance()

    git = provenance["git"]
    print(f"git {git['sha'][:12]} on {git['branch']} (dirty: {git['dirty']})")
    for name, info in provenance["inputs"].items():
        print(
            f"{name}: {info['path']} {info['size_bytes']:,} bytes "
            f"sha256 {info['sha256'][:16]}..."
        )

    print("\n--- Step 1: raw complaints")
    with StageTimer("load_raw_complaints", runtime):
        complaints, raw_summary = load_raw_complaints(params)

    print("\n--- Step 2: treatments")
    with StageTimer("load_treatments", runtime):
        treatments_all, n_s3_rows = load_treatments(params, complaints)

    print("\n--- Step 3: crime")
    with StageTimer("load_crime", runtime):
        crime, coverage, crime_summary = load_crime(params, raw_summary)

    print(
        f"Crime coverage: {_format_seconds(coverage.crime_min_s)} -> "
        f"{_format_seconds(coverage.crime_max_s)}"
    )
    print(
        f"Darkness coverage: {_format_seconds(coverage.dark_min_s)} -> "
        f"{_format_seconds(coverage.dark_max_s)}"
    )

    print("\n--- Hard checks: inputs")
    try:
        with StageTimer("checks_inputs", runtime):
            run_hard_checks(
                params,
                "inputs",
                checks,
                raw_summary=raw_summary,
                treatments_all=treatments_all,
                n_s3_rows=n_s3_rows,
                crime_summary=crime_summary,
                complaints=complaints,
            )
    except HardCheckError as error:
        return _stop(error)

    treatments_all = _finalise_treatments(treatments_all)

    print(
        f"\nLoaded: {len(complaints):,} complaints, "
        f"{len(treatments_all):,} treatments, {len(crime):,} crimes"
    )

    # Steps 4-20 are added in later commits; Commit 12 returns
    # EXIT_COMPLETED once all outputs are written.
    sys.stdout.flush()
    print(
        "\nStage 7 incomplete on this branch; no outputs were produced.",
        file=sys.stderr,
    )

    return EXIT_INCOMPLETE


if __name__ == "__main__":
    sys.exit(main())
