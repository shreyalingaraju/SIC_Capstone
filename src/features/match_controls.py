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
    to episode centres (A13 formula).
A9  Prior episodes: an episode counts if its start falls in B and at
    least one of its complaints is within 250 m.
A10 Res-9 density: crimes during B inside the unit's res-9 cell,
    divided by the cell area in km^2.
A11 The S8 pre-window [c - 14 d, c] includes both ends; B is half-open
    (evaluated as in A13).
A12 Distances follow Stage 8: <= 100 m direct, (100, 250] m ring,
    using the A13 formula.
A13 Index times are whole-second int64 timestamps. A half-open window
    [a, b) is evaluated as the closed lookup [a, b - 1]; a query with
    a > b is an empty window (clean / count 0). Radius membership is
    decided only by the explicit float64 formula sqrt(dx**2 + dy**2) <= r
    in EPSG:32118 metres. KD-tree radius queries only generate candidates
    and use a positive tolerance (KD_QUERY_TOLERANCE_M = 1e-6 m); final
    inclusion is decided by the explicit formula.

H9 (approved interpretation; implemented in a later commit)
--
Intervals are closed W windows per site. A control interval must not
overlap another control use of the same site, nor the W of any eligible
treatment at that site. Treatment-treatment overlap at a site is allowed
and reported. H9 rebuilds the intervals from the pairs and eligible
treatments instead of trusting ReuseRegistry.

H17 (Stage 3 -> Stage 7 valid-closure contract)
---
Every Stage 3 treatment's complaint must be a valid closure under the
Stage 3 rule (is_imputed is False). Runs after step 4.

H18 (site and artifact integrity)
---
Rebuilds the site assignment, per-site counts and the artifact
threshold independently and checks is_artifact (ties at T are not
artifacts) and eligible_control. Runs after step 5.

Exit codes
----------
0 Stage 7 completed. 1 H0 failure, hard-check failure, or unhandled
exception (check stderr for a traceback). 2 argparse usage error.
3 Stage 7 incomplete on this branch (no outputs produced).
"""

import argparse
import dataclasses
import functools
import gc
import hashlib
import importlib.metadata
import itertools
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
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
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
KEY_TIME_LIMIT = 2 ** KEY_TIME_BITS

# A13: KD-tree radius queries use radius + this tolerance to generate
# candidates; the explicit distance formula decides inclusion.
KD_QUERY_TOLERANCE_M = 1e-6

# M4 (informational): distance band around each radius that is logged.
BOUNDARY_DIAGNOSTIC_M = 0.001

# A10: H3 resolution of the crime density cell index.
DENSITY_H3_RESOLUTION = 9

if KEY_MAX_GROUP * KEY_TIME_LIMIT > 2 ** 62:
    raise ValueError(
        "index guard [key overflow risk]: KEY_MAX_GROUP * 2**KEY_TIME_BITS "
        "must stay below 2**62 for int64 composite keys"
    )

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

HARD_CHECK_IDS = tuple(f"H{i}" for i in range(19))

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
    "A8": "S-3 and S-4 distances are measured to each complaint's point (A13 formula).",
    "A9": "Prior episodes: episode start in B and at least one complaint within 250 m.",
    "A10": "Res-9 density: crimes during B in the unit's res-9 cell divided by cell area (km^2).",
    "A11": "S8 pre-window [c-14d, c] includes both ends; B is half-open (evaluated as in A13).",
    "A12": "Distances follow Stage 8: <= 100 m direct, (100, 250] m ring, using the A13 formula.",
    "A13": "Index times are whole-second int64 timestamps. A half-open window [a, b) "
           "is evaluated as the closed lookup [a, b - 1]. A query with a > b is an "
           "empty window: clean / count 0. Radius membership is decided only by the "
           "explicit float64 formula sqrt(dx**2 + dy**2) <= r in EPSG:32118 metres. "
           "KD-tree radius queries only generate candidates and use a positive "
           "tolerance (KD_QUERY_TOLERANCE_M = 1e-6 m); final inclusion is decided "
           "by the explicit formula.",
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

    seconds = values.astype("datetime64[s]")

    # A13: index times are whole seconds; never truncate silently.
    _index_guard(
        bool((seconds == values).all()),
        "invalid time range",
        "timestamps with a sub-second part cannot be stored as whole "
        "seconds (A13)",
    )

    return seconds.astype("int64").to_numpy()


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


def _unproject(x, y, crs):
    """Projected x/y in metres -> WGS84 lon/lat."""

    lon, lat = _transformer(crs, WGS84_CRS).transform(
        np.asarray(x, dtype=np.float64),
        np.asarray(y, dtype=np.float64),
    )

    return np.asarray(lon), np.asarray(lat)


def _h3_cells(lat, lon, resolution):
    return np.array(
        [
            h3.latlng_to_cell(float(a), float(b), resolution)
            for a, b in zip(lat, lon)
        ],
        dtype=object,
    )


def _distance(center_x, center_y, x, y):
    """Explicit A13 distance in metres (float64), same formula as Stage 8."""

    return np.sqrt(
        (np.asarray(x, dtype=np.float64) - center_x) ** 2
        + (np.asarray(y, dtype=np.float64) - center_y) ** 2
    )


def _index_guard(condition, category, message):
    """Build-time index guard (M1). category names the violation type."""

    if not condition:
        raise HardCheckError(f"index guard [{category}]: {message}")


def _composite_key(group_idx, t_s):
    """group * 2**KEY_TIME_BITS + t as int64 (A13 whole-second times)."""

    group_idx = np.asarray(group_idx).astype(np.int64)
    t_s = np.asarray(t_s).astype(np.int64)

    _index_guard(
        group_idx.size == 0
        or (group_idx.min() >= 0 and group_idx.max() < KEY_MAX_GROUP),
        "invalid group id",
        f"group ids must be in [0, {KEY_MAX_GROUP})",
    )
    _index_guard(
        t_s.size == 0 or (t_s.min() >= 0 and t_s.max() < KEY_TIME_LIMIT),
        "invalid time range",
        f"times must be in [0, 2**{KEY_TIME_BITS}) seconds",
    )

    return group_idx * KEY_TIME_LIMIT + t_s


def _build_sorted_index(group_idx, t_s, n_groups, end_s=None):
    """
    Sort (group, time) entries into composite keys.

    Returns (key, runmax_end, offsets). offsets has n_groups + 1 entries,
    so every group (including empty ones) has a segment. When end_s is
    given, entries are sorted stably by (key, end) (M3) and runmax_end is
    the running maximum of end within each group; otherwise it is None.
    """

    group_idx = np.asarray(group_idx).astype(np.int64)
    key = _composite_key(group_idx, t_s)

    if end_s is None:
        order = np.argsort(key, kind="stable")
        runmax_end = None
    else:
        end_s = np.asarray(end_s).astype(np.int64)
        _index_guard(
            end_s.size == 0
            or (end_s.min() >= 0 and end_s.max() < KEY_TIME_LIMIT),
            "invalid time range",
            f"interval ends must be in [0, 2**{KEY_TIME_BITS}) seconds",
        )
        order = np.lexsort((end_s, key))
        # Groups are increasing and every end is below KEY_TIME_LIMIT, so
        # a global running maximum of group * LIMIT + end equals the
        # per-group running maximum plus group * LIMIT.
        shifted = group_idx[order] * KEY_TIME_LIMIT + end_s[order]
        runmax_end = (
            np.maximum.accumulate(shifted) - group_idx[order] * KEY_TIME_LIMIT
        )

    key = key[order]

    _index_guard(
        bool(np.all(key[1:] >= key[:-1])),
        "invalid time range",
        "composite keys are not sorted",
    )

    offsets = np.zeros(n_groups + 1, dtype=np.int64)
    offsets[1:] = np.cumsum(
        np.bincount(group_idx, minlength=n_groups)[:n_groups]
    )

    return key, runmax_end, offsets


def _query_window(start_s, end_s, shape):
    """Broadcast and validate query window bounds (A13)."""

    start_s = np.broadcast_to(np.asarray(start_s), shape).astype(np.int64)
    end_s = np.broadcast_to(np.asarray(end_s), shape).astype(np.int64)

    for name, values in (("start", start_s), ("end", end_s)):
        if values.size and (values.min() < 0 or values.max() >= KEY_TIME_LIMIT):
            raise ValueError(
                f"[malformed query window] window {name} must be in "
                f"[0, 2**{KEY_TIME_BITS}) seconds"
            )

    return start_s, end_s


def _dirty_mask(idx, site_idx, win_start_s, win_end_s):
    """
    For each site, whether any stored darkness interval overlaps the
    closed window [win_start_s, win_end_s] (A2): start <= win_end_s and
    end >= win_start_s.

    Stored intervals: every complaint within exclusion_radius_m of the
    site under the A13 formula, including artifact-site complaints (A7)
    and treatment complaints, with their real dates (C4).

    Callers pass half-open windows [a, b) as [a, b - 1] (A13).

    site_idx: integer array (cast to int64), values in [0, n_sites),
    otherwise ValueError. win_start_s / win_end_s: int64 seconds, scalars
    or arrays broadcastable to site_idx, values in [0, 2**34), otherwise
    ValueError. Returns a bool array shaped like site_idx: False for a
    site with an empty segment (ineligible site) and False where
    win_start_s > win_end_s (empty window). Pure and deterministic;
    O(m log N) for m queried sites.
    """

    site_idx = np.asarray(site_idx).astype(np.int64)
    n_sites = len(idx.dirty_offsets) - 1

    if site_idx.size and (site_idx.min() < 0 or site_idx.max() >= n_sites):
        raise ValueError(
            f"[invalid group id] site_idx must be in [0, {n_sites})"
        )

    win_start_s, win_end_s = _query_window(
        win_start_s, win_end_s, site_idx.shape
    )

    position = np.searchsorted(
        idx.dirty_key, site_idx * KEY_TIME_LIMIT + win_end_s, side="right"
    )
    has_entries = position > idx.dirty_offsets[site_idx]

    runmax = np.zeros(site_idx.shape, dtype=np.int64)
    runmax[has_entries] = idx.dirty_runmax_end[position[has_entries] - 1]

    return has_entries & (runmax >= win_start_s) & (win_start_s <= win_end_s)


def _count_in_window(key, group_idx, start_s, end_s, end_inclusive):
    """
    Number of index entries in each group whose time t satisfies
    start_s <= t < end_s, or start_s <= t <= end_s when end_inclusive is
    True. The start is always inclusive.

    key: sorted int64 composite keys (group * 2**34 + t), e.g.
    crime_direct_key, crime_ring_key or crime_cell_key; radius membership
    was decided at build time with the A13 formula. group_idx: integer
    array (cast to int64), values in [0, 2**17) or -1. -1 means "no
    group" (e.g. a unit whose res-9 cell has no crimes) and returns 0;
    any other value is a ValueError. start_s / end_s: int64 seconds,
    scalars or arrays broadcastable to group_idx, values in [0, 2**34),
    otherwise ValueError. end_inclusive: False for B and other half-open
    windows; True for the S8 pre-window (A11).

    Returns int64 counts shaped like group_idx; 0 for an empty window
    (start_s > end_s, or start_s == end_s with end_inclusive False).
    Pure and deterministic; O(m log N).
    """

    group_idx = np.asarray(group_idx).astype(np.int64)

    valid = group_idx >= 0
    if group_idx.size and (
        (group_idx < -1).any() or group_idx.max() >= KEY_MAX_GROUP
    ):
        raise ValueError(
            f"[invalid group id] group_idx must be in [0, {KEY_MAX_GROUP}) "
            f"or -1"
        )

    start_s, end_s = _query_window(start_s, end_s, group_idx.shape)

    group = np.where(valid, group_idx, 0)
    low = np.searchsorted(key, group * KEY_TIME_LIMIT + start_s, side="left")
    high = np.searchsorted(
        key,
        group * KEY_TIME_LIMIT + end_s,
        side="right" if end_inclusive else "left",
    )

    counts = np.maximum(high - low, 0)

    return np.where(valid, counts, 0).astype(np.int64)


def _ball_query_flat(tree, block, query_radius):
    """Flatten one chunked KD-tree ball query: (owner, point, distance)."""

    lists = tree.query_ball_point(block, r=query_radius)

    lengths = np.fromiter(
        (len(item) for item in lists), dtype=np.int64, count=len(lists)
    )
    points = np.fromiter(
        itertools.chain.from_iterable(lists),
        dtype=np.int64,
        count=int(lengths.sum()),
    )
    owner = np.repeat(np.arange(len(block), dtype=np.int64), lengths)

    distance = _distance(
        block[owner, 0], block[owner, 1],
        tree.data[points, 0], tree.data[points, 1],
    )

    return owner, points, distance


def _ball_candidates(tree, centers, radius, chunk):
    """
    Points within radius of each center (A13): the KD-tree query only
    generates candidates (radius + KD_QUERY_TOLERANCE_M); inclusion is
    decided by the explicit formula. Processed in chunks of centers.

    Yields (center positions, point indices, distances) per chunk.
    """

    for begin in range(0, len(centers), chunk):
        block = centers[begin:begin + chunk]
        owner, points, distance = _ball_query_flat(
            tree, block, radius + KD_QUERY_TOLERANCE_M
        )
        keep = distance <= radius

        yield begin + owner[keep], points[keep], distance[keep]


def _boundary_count(tree, centers, radius, chunk):
    """
    Informational (M4): candidates with |distance - radius| <= 1 mm,
    decided by the explicit formula. Never used for membership.
    """

    total = 0

    for begin in range(0, len(centers), chunk):
        block = centers[begin:begin + chunk]
        _, _, distance = _ball_query_flat(
            tree, block, radius + BOUNDARY_DIAGNOSTIC_M
        )
        total += int((np.abs(distance - radius) <= BOUNDARY_DIAGNOSTIC_M).sum())

    return total


def _entry_distribution(group_idx, groups):
    """Per-group entry counts over the given groups (M4)."""

    counts = np.bincount(group_idx, minlength=int(groups.max()) + 1)[groups]

    return {
        "p50": float(np.percentile(counts, 50)),
        "p99": float(np.percentile(counts, 99)),
        "max": int(counts.max()),
        "n_groups_without_entries": int((counts == 0).sum()),
    }


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
            # Untracked files (e.g. robustness outputs) do not count.
            "dirty": bool(
                run_git("status", "--porcelain", "--untracked-files=no")
            ),
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


def build_darkness_intervals(params, complaints, coverage):
    """
    Step 4: add a darkness interval to every complaint.

    A closure is valid when closed_date exists and the duration is
    within [valid_min_duration_h, valid_max_duration_h] (the Stage 3
    rule, A5). Valid: [created - lag, closed]. Otherwise the closure is
    imputed: [created - lag, created + imputed_duration_hours].

    Intervals always use the real complaint dates, never placebo-shifted
    treatment dates (C4). Coverage was completed in step 3 and is
    returned unchanged.
    """

    created_s = _to_seconds(complaints["created_date"])

    has_closed = complaints["closed_date"].notna().to_numpy()
    closed_s = np.zeros(len(complaints), dtype=np.int64)
    closed_s[has_closed] = _to_seconds(
        complaints.loc[has_closed, "closed_date"]
    )

    duration_h = np.full(len(complaints), np.nan)
    duration_h[has_closed] = (
        closed_s[has_closed] - created_s[has_closed]
    ) / 3600

    valid = (
        has_closed
        & (duration_h >= params.valid_min_duration_h)
        & (duration_h <= params.valid_max_duration_h)
    )

    lag_s = params.report_lag_days * 86400
    imputed_s = int(round(params.imputed_duration_hours * 3600))

    complaints["dark_start_s"] = created_s - lag_s
    complaints["dark_end_s"] = np.where(valid, closed_s, created_s + imputed_s)
    complaints["is_imputed"] = ~valid

    # Why each imputed closure is invalid (mutually exclusive).
    reason = np.full(len(complaints), "valid", dtype=object)
    reason[~has_closed] = "missing_closed"
    reason[has_closed & (duration_h < 0)] = "closed_before_created"
    reason[
        has_closed
        & (duration_h >= 0)
        & (duration_h < params.valid_min_duration_h)
    ] = "too_short"
    reason[has_closed & (duration_h > params.valid_max_duration_h)] = (
        "too_long"
    )

    year = complaints["created_date"].dt.year.to_numpy()
    by_year = (
        pd.crosstab(year, reason)
        .reindex(
            columns=[
                "valid",
                "missing_closed",
                "closed_before_created",
                "too_short",
                "too_long",
            ],
            fill_value=0,
        )
    )
    by_year.columns.name = None
    by_year["total"] = by_year.sum(axis=1)
    by_year["imputed"] = by_year["total"] - by_year["valid"]

    darkness_summary = {
        "n_imputed": int((~valid).sum()),
        "n_valid_closure": int(valid.sum()),
        "imputed_by_year": {
            int(y): int(n) for y, n in by_year["imputed"].items()
        },
        "imputed_reasons": {
            name: int((reason == name).sum())
            for name in (
                "missing_closed",
                "closed_before_created",
                "too_short",
                "too_long",
            )
        },
    }

    print(
        f"Darkness intervals: {len(complaints):,} complaints, "
        f"{darkness_summary['n_valid_closure']:,} valid closures, "
        f"{darkness_summary['n_imputed']:,} imputed "
        f"({imputed_s / 3600:g} h, lag {params.report_lag_days} d)"
    )

    table = by_year[
        ["total", "imputed", "too_short", "closed_before_created",
         "missing_closed", "too_long"]
    ].copy()
    table["imputed_share"] = (table["imputed"] / table["total"]).round(3)
    table.index.name = "created_year"
    print(table.to_string())

    return complaints, coverage, darkness_summary


def _modal_label(site_idx, labels, n_sites):
    """
    Most common non-missing label per site, ties broken alphabetically;
    pd.NA when a site has no label (A4). Also returns the number of
    sites with more than one distinct label.
    """

    frame = pd.DataFrame(
        {"site_idx": site_idx, "label": labels.astype("string")}
    )
    frame = frame[frame["label"].notna()]

    counts = (
        frame.groupby(["site_idx", "label"], observed=True)
        .size()
        .rename("n")
        .reset_index()
        .sort_values(
            ["site_idx", "n", "label"],
            ascending=[True, False, True],
            kind="mergesort",
        )
    )

    n_conflicts = int((counts.groupby("site_idx").size() > 1).sum())

    modal = counts.drop_duplicates("site_idx", keep="first")

    result = pd.Series(pd.NA, index=np.arange(n_sites), dtype="string")
    result.iloc[modal["site_idx"].to_numpy()] = modal["label"].to_numpy()

    return result.reset_index(drop=True), n_conflicts


def build_sites(params, complaints):
    """
    Step 5: one site per distinct complaint location, rounded to
    site_round_m in the projected CRS (np.rint, half to even).

    site_id is E{x}_N{y} in whole metres; sites are numbered in sorted
    site_id order and complaints get site_idx. Site latitude/longitude
    are converted back from the rounded coordinates. Artifact rule
    (approved): T = quantile(n_complaints, artifact_quantile, "higher");
    a site is an artifact when n_complaints > T, so ties at T are not.

    first_created/last_created use the real complaint dates (C4).
    n_episodes, n_times_treatment and n_times_control are added in later
    steps.
    """

    step = params.site_round_m

    x_m = (np.rint(complaints["x_m"].to_numpy() / step) * step).astype(np.int64)
    y_m = (np.rint(complaints["y_m"].to_numpy() / step) * step).astype(np.int64)

    complaint_site_id = np.char.add(
        np.char.add("E", x_m.astype(str)),
        np.char.add("_N", y_m.astype(str)),
    )

    site_ids, first_row, site_idx = np.unique(
        complaint_site_id, return_index=True, return_inverse=True
    )
    n_sites = len(site_ids)

    if n_sites >= KEY_MAX_GROUP:
        raise HardCheckError(
            f"{n_sites} sites exceed the composite-key limit {KEY_MAX_GROUP}"
        )

    complaints["site_idx"] = site_idx.astype(np.int32)

    sites = pd.DataFrame(
        {
            "site_id": pd.array(site_ids, dtype="string"),
            "site_idx": np.arange(n_sites, dtype=np.int32),
            "x_m": x_m[first_row],
            "y_m": y_m[first_row],
        }
    )

    longitude, latitude = _unproject(
        sites["x_m"].to_numpy(dtype=np.float64),
        sites["y_m"].to_numpy(dtype=np.float64),
        params.projected_crs,
    )
    sites["latitude"] = latitude
    sites["longitude"] = longitude

    sites["borough"], n_borough_conflicts = _modal_label(
        site_idx, complaints["borough"], n_sites
    )
    sites["police_precinct"], n_precinct_conflicts = _modal_label(
        site_idx, complaints["police_precinct"], n_sites
    )

    for resolution in params.h3_resolutions:
        sites[f"h3_res{resolution}"] = pd.array(
            _h3_cells(latitude, longitude, resolution), dtype="string"
        )

    imputed = complaints["is_imputed"].to_numpy()

    sites["n_complaints"] = np.bincount(site_idx, minlength=n_sites).astype(
        np.int32
    )
    sites["n_complaints_valid_closure"] = np.bincount(
        site_idx, weights=~imputed, minlength=n_sites
    ).astype(np.int32)
    sites["n_complaints_imputed"] = np.bincount(
        site_idx, weights=imputed, minlength=n_sites
    ).astype(np.int32)

    created = complaints.groupby("site_idx", sort=True)["created_date"]
    sites["first_created"] = created.min().to_numpy()
    sites["last_created"] = created.max().to_numpy()

    counts = sites["n_complaints"].to_numpy()
    threshold = int(
        np.quantile(counts, params.artifact_quantile, method="higher")
    )

    sites["is_artifact"] = counts > threshold
    sites["eligible_control"] = (
        ~sites["is_artifact"] & sites["borough"].notna()
    ).astype(bool)

    artifact = sites["is_artifact"].to_numpy()

    universe = {
        "n_sites": n_sites,
        "artifact_quantile_method": "higher",
        "artifact_threshold": threshold,
        "artifact_threshold_is_maximum": bool(threshold == counts.max()),
        "n_artifact_sites": int(artifact.sum()),
        "n_artifact_complaints": int(counts[artifact].sum()),
        "n_sites_at_threshold": int((counts == threshold).sum()),
        "n_sites_without_borough": int(sites["borough"].isna().sum()),
        "n_eligible_control_sites": int(sites["eligible_control"].sum()),
        "n_sites_borough_conflict": n_borough_conflicts,
        "n_sites_precinct_conflict": n_precinct_conflicts,
    }

    print(
        f"Sites: {n_sites:,} from {len(complaints):,} complaints "
        f"(max {int(counts.max())} complaints at one site)"
    )
    print(
        f"Artifact threshold T = {threshold} "
        f"(quantile {params.artifact_quantile}, 'higher'; "
        f"maximum: {universe['artifact_threshold_is_maximum']}); "
        f"{universe['n_artifact_sites']} artifact sites with "
        f"{universe['n_artifact_complaints']:,} complaints; "
        f"{universe['n_sites_at_threshold']} sites at T are not artifacts"
    )
    print(
        f"Eligible control sites: {universe['n_eligible_control_sites']:,}; "
        f"without borough: {universe['n_sites_without_borough']}; "
        f"label conflicts: borough {n_borough_conflicts}, "
        f"precinct {n_precinct_conflicts}"
    )

    top = sites.sort_values(
        ["n_complaints", "site_id"], ascending=[False, True], kind="mergesort"
    ).head(10)
    print("Top 10 sites by complaint count:")
    print(
        top[
            ["site_id", "n_complaints", "latitude", "longitude", "borough",
             "is_artifact"]
        ].to_string(index=False)
    )

    return sites, complaints, universe


def build_episodes(params, complaints, sites):
    """
    Step 6: merge complaints into darkness episodes.

    Two non-artifact complaints are linked when their points are within
    episode_merge_radius_m (A8; A13 explicit formula, the KD-tree only
    generates candidates) and their darkness intervals overlap (A2,
    closed). Episodes are the connected components of the links.
    Artifact-site complaints are left out of the linking graph and form
    single-complaint episodes (A7).

    The first complaint of an episode has the smallest
    (created_date, unique_key). Episodes are numbered in the order of
    their first complaint. Built from the real complaint dates (C4).

    Returns the episode table, the complaints with episode_id and
    is_first_of_episode, the sites with n_episodes, all 25 m links (for
    H4) and a summary.
    """

    n = len(complaints)
    xy = complaints[["x_m", "y_m"]].to_numpy()
    start_s = complaints["dark_start_s"].to_numpy()
    end_s = complaints["dark_end_s"].to_numpy()
    created_s = _to_seconds(complaints["created_date"])
    unique_key = complaints["unique_key"].to_numpy()

    non_artifact = ~sites["is_artifact"].to_numpy()[
        complaints["site_idx"].to_numpy()
    ]
    linkable = np.flatnonzero(non_artifact)

    # A13: the KD-tree only generates candidates; the formula decides.
    linkable_xy = xy[linkable]
    local_pairs = cKDTree(linkable_xy).query_pairs(
        r=params.episode_merge_radius_m + KD_QUERY_TOLERANCE_M,
        output_type="ndarray",
    )

    if len(local_pairs) > MAX_EPISODE_LINKS:
        raise HardCheckError(
            f"{len(local_pairs):,} complaint links within "
            f"{params.episode_merge_radius_m:g} m exceed MAX_EPISODE_LINKS "
            f"({MAX_EPISODE_LINKS:,})"
        )

    local_pairs = local_pairs[
        _distance(
            linkable_xy[local_pairs[:, 0], 0], linkable_xy[local_pairs[:, 0], 1],
            linkable_xy[local_pairs[:, 1], 0], linkable_xy[local_pairs[:, 1], 1],
        )
        <= params.episode_merge_radius_m
    ]

    episode_links = linkable[local_pairs]
    a, b = episode_links[:, 0], episode_links[:, 1]
    overlapping = (start_s[a] <= end_s[b]) & (start_s[b] <= end_s[a])
    edges = episode_links[overlapping]

    graph = coo_matrix(
        (np.ones(len(edges), dtype=np.int8), (edges[:, 0], edges[:, 1])),
        shape=(n, n),
    )
    n_episodes, component = connected_components(graph, directed=False)

    # First complaint per component: smallest (created, unique_key).
    order = np.lexsort((unique_key, created_s, component))
    is_group_start = np.ones(n, dtype=bool)
    is_group_start[1:] = component[order][1:] != component[order][:-1]
    first_idx = order[is_group_start]

    # Number episodes by their first complaint's (created, unique_key).
    episode_rank = np.lexsort((unique_key[first_idx], created_s[first_idx]))
    component_to_episode = np.empty(n_episodes, dtype=np.int64)
    component_to_episode[component[first_idx[episode_rank]]] = np.arange(
        n_episodes, dtype=np.int64
    )

    episode_id = component_to_episode[component]
    is_first = np.zeros(n, dtype=bool)
    is_first[first_idx] = True

    complaints["episode_id"] = episode_id
    complaints["is_first_of_episode"] = is_first

    grouped = pd.DataFrame(
        {
            "episode_id": episode_id,
            "start_s": start_s,
            "end_s": end_s,
            "x_m": xy[:, 0],
            "y_m": xy[:, 1],
        }
    ).groupby("episode_id", sort=True)

    bounds = grouped.agg(
        start_s=("start_s", "min"),
        end_s=("end_s", "max"),
        n_complaints=("start_s", "size"),
        x_min=("x_m", "min"),
        x_max=("x_m", "max"),
        y_min=("y_m", "min"),
        y_max=("y_m", "max"),
    )

    first_by_episode = first_idx[episode_rank]

    episodes = pd.DataFrame(
        {
            "episode_id": np.arange(n_episodes, dtype=np.int64),
            "first_unique_key": unique_key[first_by_episode],
            "start_s": bounds["start_s"].to_numpy(dtype=np.int64),
            "end_s": bounds["end_s"].to_numpy(dtype=np.int64),
            "n_complaints": bounds["n_complaints"].to_numpy(dtype=np.int32),
            # Bounding-box diagonal: an upper bound on the largest
            # distance between two complaints of the episode.
            "span_m": np.hypot(
                bounds["x_max"] - bounds["x_min"],
                bounds["y_max"] - bounds["y_min"],
            ).to_numpy(),
        }
    )
    episodes["span_days"] = (episodes["end_s"] - episodes["start_s"]) / 86400

    n_episodes_per_site = (
        pd.Series(episode_id)
        .groupby(complaints["site_idx"].to_numpy())
        .nunique()
        .reindex(np.arange(len(sites)), fill_value=0)
        .to_numpy(dtype=np.int32)
    )
    sites.insert(
        sites.columns.get_loc("n_complaints_imputed") + 1,
        "n_episodes",
        n_episodes_per_site,
    )

    percentiles = (50, 90, 99, 100)

    def pct(values):
        return {
            f"p{q}": float(np.percentile(values, q)) for q in percentiles
        }

    long_span = episodes["span_m"] > 100
    long_time = episodes["span_days"] > 365
    chained = episodes[long_span | long_time]

    summary = {
        "n_episodes": int(n_episodes),
        "n_links_25m": int(len(episode_links)),
        "n_links_overlapping": int(len(edges)),
        "n_multi_complaint_episodes": int((episodes["n_complaints"] > 1).sum()),
        "episode_size_percentiles": pct(episodes["n_complaints"]),
        "episode_span_days_percentiles": pct(episodes["span_days"]),
        "episode_max_footprint_m": float(episodes["span_m"].max()),
        "d5_episodes_over_100m": int(long_span.sum()),
        "d5_episodes_over_365d": int(long_time.sum()),
        "d5_top10": chained.sort_values(
            ["span_m", "episode_id"], ascending=[False, True]
        )
        .head(10)[["episode_id", "first_unique_key", "n_complaints",
                   "span_m", "span_days"]]
        .to_dict("records"),
    }

    print(
        f"Episodes: {n_episodes:,} from {n:,} complaints; links within "
        f"{params.episode_merge_radius_m:g} m: {len(episode_links):,}, "
        f"of which overlapping in time: {len(edges):,}; "
        f"{summary['n_multi_complaint_episodes']:,} episodes have more "
        f"than one complaint"
    )
    print(
        "Episode size percentiles: "
        + ", ".join(
            f"{k} {v:g}" for k, v in summary["episode_size_percentiles"].items()
        )
    )
    print(
        "Episode span (days) percentiles: "
        + ", ".join(
            f"{k} {v:.1f}"
            for k, v in summary["episode_span_days_percentiles"].items()
        )
    )
    print(
        f"D5 chaining: {summary['d5_episodes_over_100m']} episodes span "
        f"> 100 m, {summary['d5_episodes_over_365d']} span > 365 days; "
        f"largest footprint {summary['episode_max_footprint_m']:.1f} m"
    )
    if len(chained):
        print("Top D5 episodes by footprint:")
        print(
            pd.DataFrame(summary["d5_top10"]).to_string(
                index=False, float_format=lambda v: f"{v:.1f}"
            )
        )

    return episodes, complaints, sites, episode_links, summary


def build_spatial_indexes(params, complaints, sites, crime):
    """
    Step 7: KD-trees and the sorted lookup indexes (A13).

    7a dirty index: for every eligible control site, each complaint
       within exclusion_radius_m (explicit formula) with its darkness
       interval, including artifact-site complaints (A7) and real dates
       (C4). Sorted stably by (site, start, end) with a per-site running
       maximum of the end. dirty_offsets covers all sites; ineligible
       sites have empty segments.
    7b crime indexes: for every eligible control site, crime times at
       <= 100 m (direct) and (100, 250] m (ring), explicit formula (A12).
    Cell index: crime times per H3 res-9 cell (A10), cells ranked in
       ascending cell-id order.

    Returns the SpatialIndexes and a summary (entry counts, radii used,
    per-site distributions, boundary counts within 1 mm, memory).
    """

    n_sites = len(sites)

    complaint_xy = complaints[["x_m", "y_m"]].to_numpy(dtype=np.float64)
    site_xy = sites[["x_m", "y_m"]].to_numpy(dtype=np.float64)
    crime_xy = crime[["x_m", "y_m"]].to_numpy(dtype=np.float64)

    complaint_tree = cKDTree(complaint_xy)
    site_tree = cKDTree(site_xy)
    crime_tree = cKDTree(crime_xy)

    eligible = np.flatnonzero(sites["eligible_control"].to_numpy())
    eligible_xy = site_xy[eligible]

    # 7a: dirty index.
    radius = params.exclusion_radius_m
    dark_start = complaints["dark_start_s"].to_numpy()
    dark_end = complaints["dark_end_s"].to_numpy()

    groups, starts, ends = [], [], []
    for owner, points, _ in _ball_candidates(
        complaint_tree, eligible_xy, radius, SITE_QUERY_CHUNK
    ):
        groups.append(eligible[owner])
        starts.append(dark_start[points])
        ends.append(dark_end[points])

    dirty_groups = np.concatenate(groups)
    dirty_key, dirty_runmax_end, dirty_offsets = _build_sorted_index(
        dirty_groups, np.concatenate(starts), n_sites, np.concatenate(ends)
    )
    del groups, starts, ends

    # 7b: crime indexes.
    crime_t = crime["t_s"].to_numpy()

    direct_groups, direct_times, ring_groups, ring_times = [], [], [], []
    for owner, points, distance in _ball_candidates(
        crime_tree, eligible_xy, params.outcome_radius_m, SITE_QUERY_CHUNK
    ):
        direct = distance <= params.direct_radius_m
        direct_groups.append(eligible[owner[direct]])
        direct_times.append(crime_t[points[direct]])
        ring_groups.append(eligible[owner[~direct]])
        ring_times.append(crime_t[points[~direct]])

    direct_group_idx = np.concatenate(direct_groups)
    ring_group_idx = np.concatenate(ring_groups)
    crime_direct_key, _, _ = _build_sorted_index(
        direct_group_idx, np.concatenate(direct_times), n_sites
    )
    crime_ring_key, _, _ = _build_sorted_index(
        ring_group_idx, np.concatenate(ring_times), n_sites
    )
    del direct_groups, direct_times, ring_groups, ring_times

    # Cell index (A10).
    cell_ids = np.array(
        [
            h3.str_to_int(cell)
            for cell in _h3_cells(
                crime["latitude"].to_numpy(),
                crime["longitude"].to_numpy(),
                DENSITY_H3_RESOLUTION,
            )
        ],
        dtype=np.uint64,
    )
    unique_cells, cell_rank = np.unique(cell_ids, return_inverse=True)
    crime_cell_key, _, _ = _build_sorted_index(
        cell_rank, crime_t, len(unique_cells)
    )
    crime_cell_rank = {
        int(cell): rank for rank, cell in enumerate(unique_cells.tolist())
    }

    indexes = SpatialIndexes(
        complaint_tree=complaint_tree,
        site_tree=site_tree,
        crime_tree=crime_tree,
        dirty_key=dirty_key,
        dirty_runmax_end=dirty_runmax_end,
        dirty_offsets=dirty_offsets,
        crime_direct_key=crime_direct_key,
        crime_ring_key=crime_ring_key,
        crime_cell_key=crime_cell_key,
        crime_cell_rank=crime_cell_rank,
    )

    def megabytes(*arrays):
        return round(sum(array.nbytes for array in arrays) / 2**20, 1)

    # Smallest and largest stored time, without concatenating the keys.
    key_arrays = (dirty_key, crime_direct_key, crime_ring_key, crime_cell_key)
    key_time_min = min(
        int((array % KEY_TIME_LIMIT).min()) for array in key_arrays if len(array)
    )
    key_time_max = max(
        int((array % KEY_TIME_LIMIT).max()) for array in key_arrays if len(array)
    )

    summary = {
        "n_eligible_sites": int(len(eligible)),
        "dirty": {
            "radius_m": float(radius),
            "n_entries": int(len(dirty_key)),
            "per_site": _entry_distribution(dirty_groups, eligible),
            "n_within_1mm_of_radius": _boundary_count(
                complaint_tree, eligible_xy, radius, SITE_QUERY_CHUNK
            ),
            "memory_mb": megabytes(dirty_key, dirty_runmax_end, dirty_offsets),
        },
        "crime_direct": {
            "radius_m": float(params.direct_radius_m),
            "n_entries": int(len(crime_direct_key)),
            "per_site": _entry_distribution(direct_group_idx, eligible),
            "memory_mb": megabytes(crime_direct_key),
        },
        "crime_ring": {
            "inner_radius_m": float(params.direct_radius_m),
            "outer_radius_m": float(params.outcome_radius_m),
            "n_entries": int(len(crime_ring_key)),
            "per_site": _entry_distribution(ring_group_idx, eligible),
            "memory_mb": megabytes(crime_ring_key),
        },
        "crime_within_1mm_of_direct_radius": _boundary_count(
            crime_tree, eligible_xy, params.direct_radius_m, SITE_QUERY_CHUNK
        ),
        "crime_within_1mm_of_outcome_radius": _boundary_count(
            crime_tree, eligible_xy, params.outcome_radius_m, SITE_QUERY_CHUNK
        ),
        "crime_cell": {
            "h3_resolution": DENSITY_H3_RESOLUTION,
            "n_cells": int(len(unique_cells)),
            "n_entries": int(len(crime_cell_key)),
            "memory_mb": megabytes(crime_cell_key),
        },
        "key_time_min_s": key_time_min,
        "key_time_max_s": key_time_max,
    }
    del dirty_groups, direct_group_idx, ring_group_idx

    print(
        f"KD-trees: {len(complaint_xy):,} complaints, {n_sites:,} sites, "
        f"{len(crime_xy):,} crimes; indexes over "
        f"{summary['n_eligible_sites']:,} eligible control sites"
    )
    for name in ("dirty", "crime_direct", "crime_ring"):
        entry = summary[name]
        radius_text = (
            f"{entry['radius_m']:g} m"
            if "radius_m" in entry
            else f"({entry['inner_radius_m']:g}, {entry['outer_radius_m']:g}] m"
        )
        per_site = entry["per_site"]
        print(
            f"  {name:<12} radius {radius_text:<14} entries "
            f"{entry['n_entries']:>11,}  per site p50 {per_site['p50']:g}, "
            f"p99 {per_site['p99']:g}, max {per_site['max']:,}, "
            f"empty {per_site['n_groups_without_entries']:,}  "
            f"{entry['memory_mb']:.1f} MB"
        )
    print(
        f"  crime_cell   res {DENSITY_H3_RESOLUTION}, "
        f"{summary['crime_cell']['n_cells']:,} cells, "
        f"{summary['crime_cell']['n_entries']:,} entries, "
        f"{summary['crime_cell']['memory_mb']:.1f} MB"
    )
    print(
        "  within 1 mm of a boundary (informational): "
        f"complaints at {radius:g} m: {summary['dirty']['n_within_1mm_of_radius']}, "
        f"crimes at {params.direct_radius_m:g} m: "
        f"{summary['crime_within_1mm_of_direct_radius']}, "
        f"crimes at {params.outcome_radius_m:g} m: "
        f"{summary['crime_within_1mm_of_outcome_radius']}"
    )
    print(
        f"  key times: {_format_seconds(summary['key_time_min_s'])} -> "
        f"{_format_seconds(summary['key_time_max_s'])}"
    )

    return indexes, summary


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


def _check_h2(params, complaints):
    """
    H2: darkness intervals are valid. Recomputes the rule with pandas
    Timedelta arithmetic, independently of the int64-second path in
    build_darkness_intervals.
    """

    start = time.perf_counter()

    created = complaints["created_date"]
    closed = complaints["closed_date"]
    duration = closed - created

    expected_valid = (
        closed.notna()
        & (duration >= pd.Timedelta(hours=params.valid_min_duration_h))
        & (duration <= pd.Timedelta(hours=params.valid_max_duration_h))
    ).to_numpy()

    expected_start = created - pd.Timedelta(days=params.report_lag_days)
    expected_end = created + pd.Timedelta(hours=params.imputed_duration_hours)
    expected_end = expected_end.where(~expected_valid, closed)

    expected_start_s = expected_start.astype("datetime64[s]").astype("int64")
    expected_end_s = expected_end.astype("datetime64[s]").astype("int64")

    keys = complaints["unique_key"]

    problems = []
    violations = 0

    for mask, message in (
        (
            complaints["dark_start_s"].to_numpy()
            >= complaints["dark_end_s"].to_numpy(),
            "darkness interval does not start before it ends",
        ),
        (
            complaints["is_imputed"].to_numpy() == expected_valid,
            "is_imputed does not match the valid-closure rule",
        ),
        (
            complaints["dark_start_s"].to_numpy()
            != expected_start_s.to_numpy(),
            "dark_start_s is not created - report lag",
        ),
        (
            complaints["dark_end_s"].to_numpy() != expected_end_s.to_numpy(),
            "dark_end_s is not closed_date (valid) or created + imputed "
            "duration (imputed)",
        ),
    ):
        count = int(mask.sum())
        if count:
            violations += count
            examples = keys[mask].head(10).astype(str)
            problems.append(f"{count:,} {message} (e.g. {', '.join(examples)})")

    return CheckResult(
        check_id="H2",
        passed=not problems,
        n_violations=violations,
        examples=problems,
        seconds=time.perf_counter() - start,
    )


def _check_h17(treatments_all, complaints):
    """
    H17: Stage 3 -> Stage 7 valid-closure contract. Every Stage 3
    treatment's complaint must be a valid closure under the Stage 3 rule
    (is_imputed is False).
    """

    start = time.perf_counter()

    imputed = complaints["is_imputed"].to_numpy()[
        treatments_all["complaint_idx"].to_numpy()
    ]
    count = int(imputed.sum())

    examples = []
    if count:
        keys = treatments_all.loc[imputed, "treatment_key"].head(10).astype(str)
        examples.append(
            f"{count:,} Stage 3 treatments are not valid closures under the "
            f"Stage 7 rule (e.g. {', '.join(keys)})"
        )

    return CheckResult(
        check_id="H17",
        passed=count == 0,
        n_violations=count,
        examples=examples,
        seconds=time.perf_counter() - start,
    )


def _check_h3(complaints, episodes, sites):
    """
    H3: episodes partition the complaints, and each episode's first
    complaint has the smallest (created_date, unique_key). Recomputed
    with pandas sorting on the datetime column.
    """

    start = time.perf_counter()
    problems = []
    violations = 0

    def add(count, message):
        nonlocal violations
        if count:
            violations += int(count)
            problems.append(f"{int(count):,} {message}")

    n_episodes = len(episodes)
    episode_id = complaints["episode_id"]

    add(
        int(
            episode_id.isna().sum()
            + ((episode_id < 0) | (episode_id >= n_episodes)).sum()
        ),
        "complaints without a valid episode_id",
    )
    add(
        int((episodes["episode_id"].to_numpy() != np.arange(n_episodes)).sum()),
        "episodes whose episode_id is not their row position",
    )

    counted = episode_id.value_counts().reindex(
        np.arange(n_episodes), fill_value=0
    )
    add(
        int((counted.to_numpy() == 0).sum()),
        "episodes with no complaints",
    )
    add(
        int((episodes["n_complaints"].to_numpy() != counted.to_numpy()).sum()),
        "episodes with a wrong n_complaints",
    )

    ordered = complaints.sort_values(
        ["episode_id", "created_date", "unique_key"], kind="mergesort"
    )
    expected_first = ~ordered["episode_id"].duplicated()
    expected_flag = pd.Series(False, index=complaints.index)
    expected_flag[ordered.index[expected_first.to_numpy()]] = True

    add(
        int(
            (
                complaints["is_first_of_episode"].to_numpy()
                != expected_flag.to_numpy()
            ).sum()
        ),
        "complaints whose is_first_of_episode differs from the smallest "
        "(created_date, unique_key) in their episode",
    )

    first_keys = ordered.loc[expected_first.to_numpy(), ["episode_id", "unique_key"]]
    first_keys = first_keys.set_index("episode_id")["unique_key"].reindex(
        np.arange(n_episodes)
    )
    add(
        int(
            (episodes["first_unique_key"].to_numpy() != first_keys.to_numpy()).sum()
        ),
        "episodes whose first_unique_key is wrong",
    )

    # Episodes must be numbered by their first complaint.
    firsts = ordered.loc[expected_first.to_numpy()].sort_values(
        ["created_date", "unique_key"], kind="mergesort"
    )
    add(
        int(
            (firsts["episode_id"].to_numpy() != np.arange(len(firsts))).sum()
        ),
        "episodes not numbered in first-complaint order",
    )

    grouped = complaints.groupby("episode_id")
    add(
        int(
            (
                episodes["start_s"].to_numpy()
                != grouped["dark_start_s"].min().reindex(np.arange(n_episodes)).to_numpy()
            ).sum()
            + (
                episodes["end_s"].to_numpy()
                != grouped["dark_end_s"].max().reindex(np.arange(n_episodes)).to_numpy()
            ).sum()
        ),
        "episode start_s/end_s values that differ from their complaints",
    )

    # A7: artifact-site complaints are single-complaint episodes.
    artifact = sites["is_artifact"].to_numpy()[complaints["site_idx"].to_numpy()]
    artifact_sizes = counted.to_numpy()[episode_id.to_numpy()[artifact]]
    add(
        int((artifact_sizes != 1).sum()),
        "artifact-site complaints in an episode with other complaints",
    )

    expected_per_site = (
        complaints.groupby("site_idx")["episode_id"]
        .nunique()
        .reindex(np.arange(len(sites)), fill_value=0)
    )
    add(
        int((sites["n_episodes"].to_numpy() != expected_per_site.to_numpy()).sum()),
        "sites with a wrong n_episodes",
    )

    return CheckResult(
        check_id="H3",
        passed=not problems,
        n_violations=violations,
        examples=problems[:10],
        seconds=time.perf_counter() - start,
    )


def _check_h4(params, complaints, sites, episode_links):
    """
    H4: episodes are complete. For every pair of non-artifact complaints
    within episode_merge_radius_m (the full list, rebuilt with a fresh
    KD-tree for candidates and the A13 formula for membership; only
    partially independent of step 6, which uses the same scipy query),
    complaints in different episodes must not have overlapping darkness
    intervals.
    """

    start = time.perf_counter()
    problems = []

    non_artifact = np.flatnonzero(
        ~sites["is_artifact"].to_numpy()[complaints["site_idx"].to_numpy()]
    )
    xy = complaints[["x_m", "y_m"]].to_numpy()[non_artifact]

    # A13: fresh KD-tree for candidates; the explicit formula decides.
    candidates = cKDTree(xy).query_pairs(
        r=params.episode_merge_radius_m + KD_QUERY_TOLERANCE_M,
        output_type="ndarray",
    )
    within = (
        _distance(
            xy[candidates[:, 0], 0], xy[candidates[:, 0], 1],
            xy[candidates[:, 1], 0], xy[candidates[:, 1], 1],
        )
        <= params.episode_merge_radius_m
    )
    pairs = non_artifact[candidates[within]]

    if len(pairs) != len(episode_links):
        problems.append(
            f"rebuilt pair count {len(pairs):,} differs from the "
            f"episode links {len(episode_links):,}"
        )

    a, b = pairs[:, 0], pairs[:, 1]
    start_s = complaints["dark_start_s"].to_numpy()
    end_s = complaints["dark_end_s"].to_numpy()
    episode_id = complaints["episode_id"].to_numpy()

    split = (
        (episode_id[a] != episode_id[b])
        & (start_s[a] <= end_s[b])
        & (start_s[b] <= end_s[a])
    )
    count = int(split.sum())

    if count:
        keys = complaints["unique_key"].to_numpy()
        examples = [
            f"{keys[i]}/{keys[j]}" for i, j in pairs[split][:10]
        ]
        problems.append(
            f"{count:,} overlapping complaint pairs within "
            f"{params.episode_merge_radius_m:g} m are in different "
            f"episodes (e.g. {', '.join(examples)})"
        )

    return CheckResult(
        check_id="H4",
        passed=not problems,
        n_violations=count + int(len(pairs) != len(episode_links)),
        examples=problems,
        seconds=time.perf_counter() - start,
    )


def _check_h18(params, complaints, sites):
    """
    H18: site and artifact integrity. Rebuilds the site assignment with
    pandas (round + groupby) and the threshold by explicit sorting,
    independently of the numpy path in build_sites.
    """

    start = time.perf_counter()
    problems = []
    violations = 0

    def add(count, message):
        nonlocal violations
        if count:
            violations += int(count)
            problems.append(f"{int(count):,} {message}")

    n_sites = len(sites)
    site_idx = complaints["site_idx"]

    # 1. Every complaint maps to exactly one site.
    add(
        int(site_idx.isna().sum() + ((site_idx < 0) | (site_idx >= n_sites)).sum()),
        "complaints without a valid site_idx",
    )
    add(
        int((sites["site_idx"].to_numpy() != np.arange(n_sites)).sum()),
        "sites whose site_idx is not their row position",
    )
    add(
        int(not sites["site_id"].is_monotonic_increasing)
        + int(sites["site_id"].duplicated().sum()),
        "site_id ordering or uniqueness problems (not strictly increasing)",
    )

    step = params.site_round_m
    rounded = pd.DataFrame(
        {
            "x": ((complaints["x_m"] / step).round() * step).astype("int64"),
            "y": ((complaints["y_m"] / step).round() * step).astype("int64"),
        }
    )
    assigned = sites.iloc[site_idx.to_numpy()]
    add(
        int(
            (
                (assigned["x_m"].to_numpy() != rounded["x"].to_numpy())
                | (assigned["y_m"].to_numpy() != rounded["y"].to_numpy())
            ).sum()
        ),
        "complaints assigned to a site with different rounded coordinates",
    )
    add(
        abs(len(rounded.drop_duplicates()) - n_sites),
        "difference between distinct rounded locations and sites",
    )
    expected_ids = (
        "E" + sites["x_m"].astype(str) + "_N" + sites["y_m"].astype(str)
    )
    add(
        int((sites["site_id"].astype(str) != expected_ids).sum()),
        "site_id values not equal to E{x}_N{y}",
    )

    # 2. Per-site complaint counts.
    grouped = complaints.groupby("site_idx")["is_imputed"]
    counted = grouped.size().reindex(np.arange(n_sites), fill_value=0)
    imputed = grouped.sum().reindex(np.arange(n_sites), fill_value=0)
    add(
        int((sites["n_complaints"].to_numpy() != counted.to_numpy()).sum()),
        "sites with a wrong n_complaints",
    )
    add(
        int((sites["n_complaints_imputed"].to_numpy() != imputed.to_numpy()).sum()),
        "sites with a wrong n_complaints_imputed",
    )
    add(
        int(
            (
                sites["n_complaints_valid_closure"].to_numpy()
                != (counted - imputed).to_numpy()
            ).sum()
        ),
        "sites with a wrong n_complaints_valid_closure",
    )

    # 3. Threshold with 'higher' semantics: 0-based index
    #    ceil((n - 1) * q) of the sorted counts.
    ordered = sorted(int(n) for n in counted.to_numpy())
    index = math.ceil((len(ordered) - 1) * params.artifact_quantile)
    threshold = ordered[index]

    counts = sites["n_complaints"].to_numpy()
    expected_artifact = counts > threshold

    # 4. is_artifact == (n_complaints > T).
    add(
        int((sites["is_artifact"].to_numpy() != expected_artifact).sum()),
        f"sites whose is_artifact differs from n_complaints > T (T = {threshold})",
    )

    # 5. Ties at T are not artifacts.
    add(
        int((sites["is_artifact"].to_numpy() & (counts == threshold)).sum()),
        f"sites at T = {threshold} flagged as artifacts",
    )

    # 6. eligible_control == (not is_artifact and borough present).
    expected_eligible = ~expected_artifact & sites["borough"].notna().to_numpy()
    add(
        int((sites["eligible_control"].to_numpy() != expected_eligible).sum()),
        "sites whose eligible_control differs from (not artifact and "
        "borough present)",
    )

    ceil_rule = ordered[math.ceil(params.artifact_quantile * len(ordered)) - 1]
    note = (
        f"T = {threshold} at sorted index {index}; ceil(q*n)-th smallest "
        f"= {ceil_rule} ({'agrees' if ceil_rule == threshold else 'differs'})"
    )

    return CheckResult(
        check_id="H18",
        passed=not problems,
        n_violations=violations,
        examples=(problems or [note])[:10],
        seconds=time.perf_counter() - start,
    )


def run_hard_checks(params, stage, checks, **tables):
    """
    Step 17: run the hard checks for one pipeline stage, record them and
    raise HardCheckError if any failed.

    Stages implemented so far: "inputs" (H1, H16 after step 2),
    "darkness" (H2, H17 after step 4), "sites" (H18 after step 5) and
    "episodes" (H3, H4 after step 6).
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
    elif stage == "darkness":
        results = [
            _check_h2(params, tables["complaints"]),
            _check_h17(tables["treatments_all"], tables["complaints"]),
        ]
    elif stage == "sites":
        results = [
            _check_h18(params, tables["complaints"], tables["sites"]),
        ]
    elif stage == "episodes":
        results = [
            _check_h3(tables["complaints"], tables["episodes"], tables["sites"]),
            _check_h4(
                params,
                tables["complaints"],
                tables["sites"],
                tables["episode_links"],
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

    print("\n--- Step 4: darkness intervals")
    with StageTimer("build_darkness_intervals", runtime):
        complaints, coverage, darkness_summary = build_darkness_intervals(
            params, complaints, coverage
        )

    print("\n--- Hard checks: darkness")
    try:
        with StageTimer("checks_darkness", runtime):
            run_hard_checks(
                params,
                "darkness",
                checks,
                complaints=complaints,
                treatments_all=treatments_all,
            )
    except HardCheckError as error:
        return _stop(error)

    print("\n--- Step 5: sites")
    with StageTimer("build_sites", runtime):
        sites, complaints, sites_universe = build_sites(params, complaints)

    print("\n--- Hard checks: sites")
    try:
        with StageTimer("checks_sites", runtime):
            run_hard_checks(
                params,
                "sites",
                checks,
                complaints=complaints,
                sites=sites,
            )
    except HardCheckError as error:
        return _stop(error)

    print("\n--- Step 6: episodes")
    with StageTimer("build_episodes", runtime):
        episodes, complaints, sites, episode_links, episode_summary = (
            build_episodes(params, complaints, sites)
        )

    print("\n--- Hard checks: episodes")
    try:
        with StageTimer("checks_episodes", runtime):
            run_hard_checks(
                params,
                "episodes",
                checks,
                complaints=complaints,
                episodes=episodes,
                sites=sites,
                episode_links=episode_links,
            )
    except HardCheckError as error:
        return _stop(error)

    # The 25 m links are only needed for H4.
    del episode_links
    gc.collect()

    print("\n--- Step 7: spatial indexes")
    with StageTimer("build_spatial_indexes", runtime):
        indexes, index_summary = build_spatial_indexes(
            params, complaints, sites, crime
        )

    # Crime latitude/longitude were only needed for the H3 cells; H11
    # rereads the crime file independently.
    crime = crime.drop(columns=["latitude", "longitude"])

    # Steps 8-20 are added in later commits; Commit 12 returns
    # EXIT_COMPLETED once all outputs are written.
    sys.stdout.flush()
    print(
        "\nStage 7 incomplete on this branch; no outputs were produced.",
        file=sys.stderr,
    )

    return EXIT_INCOMPLETE


if __name__ == "__main__":
    sys.exit(main())
