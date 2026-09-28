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
    [a, b) is evaluated as the closed lookup [a, b - 1]; a left-open
    window (a, b] is evaluated as the closed lookup [a + 1, b]; a query
    with a > b is an empty window (clean / count 0). Radius membership is
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

H10 / H11 (independent recheck, step 18)
---------
Rebuilt from the raw files for min(RECHECK_SAMPLE_N, n) pairs drawn with
RECHECK_SEED from pair_id order: complaint universe, pyproj projection,
darkness intervals, 1 m sites, artifact flags and own episodes (breadth-
first closure), windows, S-3, S-4 and the four baseline counts. Spatial
indexes only generate candidates at radius + 1 m; membership is the
explicit formula with the run's radii. See independent_recheck.

Outputs and failures
--------------------
Files are written to temporary names, schema-checked with pyarrow and
renamed (diagnostics last). A pre-Issue-4 control_area_pairs.parquet in
out_dir is first copied to control_area_pairs.pre_issue4.parquet (sha256
verified; never overwritten). Every stop writes
match_diagnostics.failed.json; a successful run removes a stale one.

Exit codes
----------
0 Stage 7 completed and all files written. 1 H0 failure, hard-check
failure, determinism failure, or unhandled exception (check stderr for a
traceback). 2 argparse usage error. (3 was "incomplete" during the
Issue 4 rollout and is no longer returned.)
"""

import argparse
import contextlib
import dataclasses
import functools
import gc
import hashlib
import importlib.metadata
import io
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

SECONDS_PER_DAY = 86400

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
           "is evaluated as the closed lookup [a, b - 1]; a left-open window (a, b] "
           "is evaluated as the closed lookup [a + 1, b]. A query with a > b is an "
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


def _from_seconds(values):
    """int64 seconds since 1970 -> datetime64[us] (inverse of _to_seconds)."""

    return pd.Series(
        pd.to_datetime(np.asarray(values, dtype=np.int64), unit="s")
    ).astype("datetime64[us]")


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
        error = HardCheckError(
            f"H0 parameter validation failed "
            f"({len(violations)} violations)\n  - "
            + "\n  - ".join(violations)
        )
        # Lets the failure diagnostics use the requested out_dir.
        error.params = params
        raise error

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


def compute_windows(params, treatments_all):
    """
    Step 8: treatment windows in int64 seconds (A3, A13), from the
    treatment dates (placebo-shifted when placebo_shift_days > 0, C4).

    W  = [c - event_window, max(closed + post_window, c + event_window)]
         closed (A2).
    B  = [c - event_window - baseline_days, c - event_window), half-open
         (A11), i.e. the baseline ends where W starts.
    S8 pre-window = [c - s8_pre_window_days, c], closed (A11); balance
         only.
    """

    event = params.event_window_days * SECONDS_PER_DAY
    post = params.post_window_days * SECONDS_PER_DAY
    baseline = params.baseline_days * SECONDS_PER_DAY
    s8_pre = params.s8_pre_window_days * SECONDS_PER_DAY

    c_s = _to_seconds(treatments_all["created_date"])
    closed_s = _to_seconds(treatments_all["closed_date"])

    treatments_all["created_s"] = c_s
    treatments_all["closed_s"] = closed_s
    treatments_all["w_start_s"] = c_s - event
    treatments_all["w_end_s"] = np.maximum(closed_s + post, c_s + event)
    treatments_all["b_start_s"] = c_s - event - baseline
    treatments_all["b_end_s"] = c_s - event
    treatments_all["s8_pre_start_s"] = c_s - s8_pre
    treatments_all["s8_pre_end_s"] = c_s

    for column, seconds in (
        ("window_start", "w_start_s"),
        ("window_end", "w_end_s"),
        ("baseline_start", "b_start_s"),
        ("baseline_end", "b_end_s"),
    ):
        treatments_all[column] = _from_seconds(treatments_all[seconds])

    return treatments_all


def _s4_dirty(params, treatments, complaints, idx):
    """
    S-4 clean-treatment rule (A8, A13, A2). Returns (pre_dirty,
    post_dirty) bool arrays over the treatments.

    Candidates: complaints within treatment_clean_radius_m of the
    treatment complaint's exact point, from _ball_candidates (the KD-tree
    only generates candidates; the explicit formula decides, A13).
    Candidate darkness intervals [s, e] are real complaint dates.

    Pre-window [c - event_window, c) -> [c - event_window, c - 1] (A13):
        hit = s <= c - 1 and e >= c - event_window        (A2 closed)
    Post-window (closed, W_end] -> [closed + 1, W_end] (A13):
        hit = s <= W_end and e >= closed + 1              (A2 closed)

    Exemptions, canonical run (C2): pre ignores complaints of the
    treatment's own episode; post ignores only the treatment complaint.

    Exemptions, placebo run (placebo_shift_days > 0), C4:
    - Post: no complaint is exempt. C4 makes the real treatment
      complaint ordinary darkness, and C2's only post exemption is that
      complaint, so nothing is left to exempt. (In canonical runs the
      exemption never matters: H17 makes the treatment's darkness end
      exactly at closed, outside (closed, W_end].)
    - Pre: no episode is exempt. This is an implementation choice
      consistent with C4/C2 (the exemption exists for duplicates of the
      treated outage, and the placebo event is not that outage). It is
      output-equivalent to keeping the episode exemption whenever
      placebo_shift_days > report_lag_days (approved: 90 > 7): rule 6
      makes every treatment reaching S-4 the first complaint of its real
      episode, so every other member starts at or after c_real - lag,
      while the placebo pre-window ends at c_real - shift - 1 s. The raw
      pre flag computed here can differ between the two variants only
      for treatments that are not first of their episode; rule 6 has
      already removed those, so reason codes are identical.
    """

    placebo = params.placebo_shift_days > 0
    event = params.event_window_days * SECONDS_PER_DAY

    centers = treatments[["x_m", "y_m"]].to_numpy(dtype=np.float64)
    c_s = treatments["created_s"].to_numpy()
    closed_s = treatments["closed_s"].to_numpy()
    w_end_s = treatments["w_end_s"].to_numpy()
    own_episode = treatments["episode_id"].to_numpy()
    own_complaint = treatments["complaint_idx"].to_numpy()

    dark_start = complaints["dark_start_s"].to_numpy()
    dark_end = complaints["dark_end_s"].to_numpy()
    episode_id = complaints["episode_id"].to_numpy()

    pre_dirty = np.zeros(len(treatments), dtype=bool)
    post_dirty = np.zeros(len(treatments), dtype=bool)

    for owner, points, _ in _ball_candidates(
        idx.complaint_tree,
        centers,
        params.treatment_clean_radius_m,
        RULE_QUERY_CHUNK,
    ):
        s = dark_start[points]
        e = dark_end[points]

        pre_a = c_s[owner] - event
        pre_b = c_s[owner] - 1
        pre_hit = (s <= pre_b) & (e >= pre_a) & (pre_a <= pre_b)

        post_a = closed_s[owner] + 1
        post_b = w_end_s[owner]
        post_hit = (s <= post_b) & (e >= post_a) & (post_a <= post_b)

        if not placebo:
            pre_hit &= episode_id[points] != own_episode[owner]
            post_hit &= points != own_complaint[owner]

        pre_dirty[owner[pre_hit]] = True
        post_dirty[owner[post_hit]] = True

    return pre_dirty, post_dirty


def apply_treatment_rules(params, treatments_all, complaints, sites, idx,
                          coverage):
    """
    Step 9: treatment eligibility rules in the approved order; each
    treatment gets only the first rule it fails (reason_code /
    reason_step, missing when eligible).

    Borough and precinct are the site's modal values, as for controls.
    First-of-episode is judged on the real episode (C4). Coverage uses
    closed W and half-open B (A11, A13).

    Returns (treatments_all, treatments_eligible, rejected_rules,
    attrition).
    """

    lag = params.report_lag_days * SECONDS_PER_DAY
    complaint_idx = treatments_all["complaint_idx"].to_numpy()
    site_idx = complaints["site_idx"].to_numpy()[complaint_idx]

    treatments_all["site_idx"] = site_idx
    treatments_all["episode_id"] = complaints["episode_id"].to_numpy()[
        complaint_idx
    ]
    treatments_all["is_first_of_episode"] = complaints[
        "is_first_of_episode"
    ].to_numpy()[complaint_idx]

    # Site modal labels replace the complaint's own labels.
    treatments_all = treatments_all.drop(columns=["borough", "police_precinct"])
    treatments_all["treatment_borough"] = (
        sites["borough"].iloc[site_idx].reset_index(drop=True)
    )
    treatments_all["treatment_police_precinct"] = (
        sites["police_precinct"].iloc[site_idx].reset_index(drop=True)
    )

    w_start = treatments_all["w_start_s"].to_numpy()
    w_end = treatments_all["w_end_s"].to_numpy()
    b_start = treatments_all["b_start_s"].to_numpy()
    b_end = treatments_all["b_end_s"].to_numpy()

    pre_dirty, post_dirty = _s4_dirty(params, treatments_all, complaints, idx)

    rule_masks = [
        (w_start < coverage.crime_min_s) | (w_end > coverage.crime_max_s),
        (w_start - lag < coverage.dark_min_s)
        | (w_end + lag > coverage.dark_max_s),
        (b_start < coverage.crime_min_s) | (b_end - 1 > coverage.crime_max_s),
        sites["is_artifact"].to_numpy()[site_idx],
        treatments_all["treatment_borough"].isna().to_numpy(),
        ~treatments_all["is_first_of_episode"].to_numpy(),
        pre_dirty,
        post_dirty,
    ]
    rule_codes = [code for code, step in REASON_CODES if step <= 8]

    step = np.zeros(len(treatments_all), dtype=np.int8)
    for number in range(len(rule_masks), 0, -1):
        step[rule_masks[number - 1]] = number

    treatments_all["reason_step"] = pd.Series(step, dtype="Int8").mask(
        step == 0
    )
    treatments_all["reason_code"] = (
        pd.Series(np.array(rule_codes, dtype=object)[np.maximum(step, 1) - 1])
        .mask(step == 0)
        .astype("string")
    )

    attrition = [
        {
            "step": 0,
            "rule": "Stage 3 valid closures (H17)",
            "remaining": len(treatments_all),
            "dropped": 0,
        }
    ]
    for number, code in enumerate(rule_codes, start=1):
        dropped = int((step == number).sum())
        attrition.append(
            {
                "step": number,
                "rule": code,
                "remaining": attrition[-1]["remaining"] - dropped,
                "dropped": dropped,
            }
        )

    eligible = step == 0
    treatments_eligible = treatments_all.loc[eligible].reset_index(drop=True)
    rejected_rules = treatments_all.loc[~eligible].reset_index(drop=True)

    # Internal accounting guard (does not replace H13).
    if not (
        attrition[-1]["remaining"] == len(treatments_eligible)
        and len(treatments_eligible) + len(rejected_rules)
        == len(treatments_all)
        and set(np.unique(step).tolist()) <= set(range(len(rule_codes) + 1))
    ):
        raise HardCheckError(
            "treatment attrition guard: the attrition table does not "
            "telescope or a reason step is outside 1-8"
        )

    print(
        f"Treatment rules (placebo shift {params.placebo_shift_days} d): "
        f"{len(treatments_eligible):,} of {len(treatments_all):,} eligible"
    )
    print(
        pd.DataFrame(attrition).to_string(
            index=False, formatters={"remaining": "{:,}".format,
                                     "dropped": "{:,}".format}
        )
    )

    return treatments_all, treatments_eligible, rejected_rules, attrition


def compute_treatment_baselines(params, treatments_eligible, idx, crime):
    """
    Step 10: each eligible treatment's night crimes during B, counted
    around the treatment complaint's exact point (A8).

    Radius membership (A12, A13; explicit formula d = sqrt(dx**2 + dy**2),
    KD-tree candidates from _ball_candidates at outcome_radius_m):
        base_100m: d <= 100               (direct)
        base_250m: 100 < d <= 250         (ring only, NOT cumulative <= 250)
    The two bands are disjoint and together cover 0-250 m, matching the
    Stage 8 direct/displacement split.

    Time membership: B = [b_start, b_end) is half-open (A11), evaluated
    as the closed lookup [b_start, b_end - 1] (A13):
        b_start <= t <= b_end - 1
    clean_crime.parquet is already restricted to the approved night-time
    offenses and is used as-is, as in Stage 8. In placebo runs B is the
    shifted baseline (C4); crimes are real.
    """

    n = len(treatments_eligible)
    centers = treatments_eligible[["x_m", "y_m"]].to_numpy(dtype=np.float64)
    b_start = treatments_eligible["b_start_s"].to_numpy()
    b_last = treatments_eligible["b_end_s"].to_numpy() - 1
    crime_t = crime["t_s"].to_numpy()

    base_100 = np.zeros(n, dtype=np.int64)
    base_250 = np.zeros(n, dtype=np.int64)

    for owner, points, distance in _ball_candidates(
        idx.crime_tree, centers, params.outcome_radius_m, TREATMENT_QUERY_CHUNK
    ):
        t = crime_t[points]
        in_b = (
            (t >= b_start[owner])
            & (t <= b_last[owner])
            & (b_start[owner] <= b_last[owner])
        )
        direct = distance <= params.direct_radius_m

        base_100 += np.bincount(owner[in_b & direct], minlength=n)
        base_250 += np.bincount(owner[in_b & ~direct], minlength=n)

    treatments_eligible["base_100m"] = base_100.astype(np.int32)
    treatments_eligible["base_250m"] = base_250.astype(np.int32)

    def describe(values):
        return {
            "mean": float(values.mean()) if len(values) else float("nan"),
            "median": float(np.median(values)) if len(values) else float("nan"),
            "p90": float(np.percentile(values, 90)) if len(values) else float("nan"),
            "max": int(values.max()) if len(values) else 0,
            "share_zero": float((values == 0).mean()) if len(values) else float("nan"),
        }

    summary = {
        "n_treatments": n,
        "base_100m": describe(base_100),
        "base_250m": describe(base_250),
    }

    for name in ("base_100m", "base_250m"):
        entry = summary[name]
        print(
            f"{name}: mean {entry['mean']:.3f}, median {entry['median']:g}, "
            f"p90 {entry['p90']:g}, max {entry['max']:,}, "
            f"zero share {entry['share_zero']:.3f}  (n = {n:,})"
        )

    return treatments_eligible, summary


def standardise(params, treatments_eligible):
    """
    Step 11: matching-variable scales.

    log_base = log1p(count); sd = sample standard deviation (ddof = 1)
    of log_base across the eligible treatments of this run (placebo runs
    use their own eligible population, D18).

    z = log1p(count) / sd, with no centring. Every downstream use (the
    caliper test and the standardized Euclidean match distance) depends
    only on differences between units, so subtracting a common mean
    would cancel exactly; it is deliberately omitted.
    """

    log_100 = np.log1p(treatments_eligible["base_100m"].to_numpy(np.float64))
    log_250 = np.log1p(treatments_eligible["base_250m"].to_numpy(np.float64))

    n = len(treatments_eligible)
    sd_100 = float(np.std(log_100, ddof=1)) if n > 1 else float("nan")
    sd_250 = float(np.std(log_250, ddof=1)) if n > 1 else float("nan")

    treatments_eligible["log_base_100m"] = log_100
    treatments_eligible["log_base_250m"] = log_250
    treatments_eligible["z100"] = log_100 / sd_100
    treatments_eligible["z250"] = log_250 / sd_250

    scales = Scales(sd_100=sd_100, sd_250=sd_250, n_treatments=n)

    print(
        f"Scales (sample SD of log1p, n = {n:,}): "
        f"sd_100 = {sd_100:.6f}, sd_250 = {sd_250:.6f}"
    )

    return treatments_eligible, scales


def init_reuse_registry(params, treatments_eligible):
    """
    Step 12: reuse registry, pre-filled with every eligible treatment's
    closed W at its own site (C3). In placebo runs these are the shifted
    windows. The occupied interval is always the full W, also under
    control_selection = pre_only.
    """

    registry = ReuseRegistry(params.reuse_policy)
    registry.preregister(
        treatments_eligible["site_idx"].to_numpy(),
        treatments_eligible["w_start_s"].to_numpy(),
        treatments_eligible["w_end_s"].to_numpy(),
    )

    n_sites = treatments_eligible["site_idx"].nunique()
    print(
        f"Reuse registry ({params.reuse_policy}): "
        f"{len(treatments_eligible):,} treatment windows pre-registered "
        f"at {n_sites:,} sites"
    )

    return registry


def build_band_candidates(params, treatments_eligible, sites, idx):
    """
    Step 13 prep: usable band candidates per eligible treatment (S-1,
    S-2, D17), stored as CSR arrays on idx.

    Distance from the treatment complaint's exact point to the site's
    rounded point, explicit float64 formula (A13; KD-tree candidates at
    match_band_max_m + KD_QUERY_TOLERANCE_M). A site is a band candidate
    when
        match_band_min_m <= d <= match_band_max_m
        and sites.borough == treatment_borough
        and sites.eligible_control
    Within a treatment, candidates are ordered by site_idx.

    band_sites (int32), band_dist (float64), band_offsets (int64, length
    n + 1) are set on idx. Returns (idx, summary); the summary has the
    raw spatial-band counts (distance only), diagnostics only.
    """

    n = len(treatments_eligible)
    centers = treatments_eligible[["x_m", "y_m"]].to_numpy(dtype=np.float64)

    borough_codes, borough_labels = pd.factorize(sites["borough"], sort=True)
    site_borough = np.asarray(borough_codes, dtype=np.int64)
    treatment_borough = pd.Categorical(
        treatments_eligible["treatment_borough"], categories=borough_labels
    ).codes.astype(np.int64)
    eligible_control = sites["eligible_control"].to_numpy(dtype=bool)

    band_raw = np.zeros(n, dtype=np.int64)
    owners, points_all, distances = [], [], []

    for owner, points, distance in _ball_candidates(
        idx.site_tree, centers, params.match_band_max_m, TREATMENT_QUERY_CHUNK
    ):
        in_band = distance >= params.match_band_min_m
        owner, points, distance = owner[in_band], points[in_band], distance[in_band]
        band_raw += np.bincount(owner, minlength=n)

        usable = (
            eligible_control[points]
            & (site_borough[points] == treatment_borough[owner])
            & (treatment_borough[owner] >= 0)
        )
        owner, points, distance = owner[usable], points[usable], distance[usable]

        order = np.lexsort((points, owner))
        owners.append(owner[order])
        points_all.append(points[order])
        distances.append(distance[order])

    owner = np.concatenate(owners)
    idx.band_sites = np.concatenate(points_all).astype(np.int32)
    idx.band_dist = np.concatenate(distances).astype(np.float64)
    idx.band_offsets = np.concatenate(
        ([0], np.cumsum(np.bincount(owner, minlength=n)))
    ).astype(np.int64)
    del owners, points_all, distances, owner

    usable_counts = np.diff(idx.band_offsets)
    summary = {
        "n_treatments": n,
        "n_entries": int(len(idx.band_sites)),
        "band_raw": _count_percentiles(band_raw),
        "band": _count_percentiles(usable_counts),
        "memory_mb": round(
            (idx.band_sites.nbytes + idx.band_dist.nbytes
             + idx.band_offsets.nbytes) / 2**20, 1
        ),
    }

    print(
        f"Band candidates [{params.match_band_min_m:g}, "
        f"{params.match_band_max_m:g}] m: {summary['n_entries']:,} usable "
        f"entries for {n:,} treatments ({summary['memory_mb']:.1f} MB); "
        f"raw spatial p50 {summary['band_raw']['p50']:g}, "
        f"usable p50 {summary['band']['p50']:g}"
    )

    return idx, summary


def _count_percentiles(values):
    values = np.asarray(values)

    if not len(values):
        return {"n": 0}

    return {
        "n": int(len(values)),
        "mean": float(values.mean()),
        "p10": float(np.percentile(values, 10)),
        "p50": float(np.percentile(values, 50)),
        "p90": float(np.percentile(values, 90)),
        "max": int(values.max()),
        "n_zero": int((values == 0).sum()),
    }


def _caliper_candidates(params, treatments_eligible, idx, scales, rows):
    """
    Clean -> baselines -> caliper -> ranking for a block of treatment
    rows. None of these depend on the registry, so they are computed
    vectorized before the sequential reuse pass.

    Clean (S-3): _dirty_mask over the selection window, closed:
        full_window: [w_start_s, w_end_s]
        pre_only:    [w_start_s, created_s - 1]   ([c - 35 d, c), A13)
    Control baselines: crimes during the treatment's B, [b_start_s,
    b_end_s - 1] (A13), from the direct and ring indexes.
    Caliper (H8 form, both variables):
        |log1p(t) - log1p(c)| <= caliper_sd * sd
    Match distance: sqrt((z100_t - z100_c)**2 + (z250_t - z250_c)**2),
    z_c = log1p(c) / sd from this run's treatment scales.
    Ranking: match_distance, then distance_m, then site_id (site_idx is
    numbered in sorted site_id order).

    Returns per-row counts (n_clean, n_caliper) and the ranked caliper
    candidates as flat arrays with a per-row owner.
    """

    # rows is a contiguous block, so its band entries are contiguous too.
    first, last = idx.band_offsets[rows[0]], idx.band_offsets[rows[-1] + 1]
    owner = np.repeat(rows, np.diff(idx.band_offsets[rows[0]:rows[-1] + 2]))
    site = idx.band_sites[first:last].astype(np.int64)
    distance = idx.band_dist[first:last]

    w_start = treatments_eligible["w_start_s"].to_numpy()
    if params.control_selection == "full_window":
        sel_end = treatments_eligible["w_end_s"].to_numpy()
    else:
        sel_end = treatments_eligible["created_s"].to_numpy() - 1

    clean = ~_dirty_mask(idx, site, w_start[owner], sel_end[owner])
    owner, site, distance = owner[clean], site[clean], distance[clean]

    b_start = treatments_eligible["b_start_s"].to_numpy()[owner]
    b_end = treatments_eligible["b_end_s"].to_numpy()[owner]
    base_100 = _count_in_window(
        idx.crime_direct_key, site, b_start, b_end, end_inclusive=False
    )
    base_250 = _count_in_window(
        idx.crime_ring_key, site, b_start, b_end, end_inclusive=False
    )

    log_100_c = np.log1p(base_100.astype(np.float64))
    log_250_c = np.log1p(base_250.astype(np.float64))
    log_100_t = treatments_eligible["log_base_100m"].to_numpy()[owner]
    log_250_t = treatments_eligible["log_base_250m"].to_numpy()[owner]

    in_caliper = (
        (np.abs(log_100_t - log_100_c) <= params.caliper_sd * scales.sd_100)
        & (np.abs(log_250_t - log_250_c) <= params.caliper_sd * scales.sd_250)
    )

    n_clean = np.bincount(owner, minlength=len(treatments_eligible))[rows]
    n_caliper = np.bincount(
        owner[in_caliper], minlength=len(treatments_eligible)
    )[rows]

    owner = owner[in_caliper]
    site = site[in_caliper]
    distance = distance[in_caliper]
    base_100 = base_100[in_caliper]
    base_250 = base_250[in_caliper]

    dz_100 = (
        treatments_eligible["z100"].to_numpy()[owner]
        - log_100_c[in_caliper] / scales.sd_100
    )
    dz_250 = (
        treatments_eligible["z250"].to_numpy()[owner]
        - log_250_c[in_caliper] / scales.sd_250
    )
    match_distance = np.sqrt(dz_100 ** 2 + dz_250 ** 2)

    order = np.lexsort((site, distance, match_distance, owner))

    return {
        "n_clean": n_clean,
        "n_caliper": n_caliper,
        "owner": owner[order],
        "site": site[order],
        "distance": distance[order],
        "match_distance": match_distance[order],
        "base_100": base_100[order],
        "base_250": base_250[order],
    }


def match_treatments(params, treatments_eligible, sites, idx, scales,
                     registry):
    """
    Step 13: greedy 1:1 nearest-neighbour matching with a caliper (D10,
    D12).

    Order: rows sorted by treatment_key, then
    np.random.default_rng(match_seed).permutation(n); match_order is the
    position in that order. For each treatment: band (usable candidates,
    see build_band_candidates) -> clean -> caliper -> ranking -> the
    first candidate the registry reports free, which is assigned.

    Counts per treatment (nested sets): n_candidates_band >=
    n_candidates_clean >= n_candidates_caliper. n_candidates_reuse_blocked
    counts only the ranked caliper candidates examined before the
    selected one (all of them for REUSE_CONFLICT); unexamined candidates
    are never counted. blocked_by_preregistration counts examined
    candidates whose W overlaps a pre-registered treatment window, also
    when an assigned window overlaps too.

    Reason codes, first failure: NO_CANDIDATE_IN_BAND (no usable control
    candidate in the band), NO_CLEAN_CANDIDATE, NO_CANDIDATE_IN_CALIPER,
    REUSE_CONFLICT.

    Returns (MatchResult, summary).
    """

    site_ids = sites["site_id"].to_numpy(dtype=object)
    if len(site_ids) > 1 and not (site_ids[1:] > site_ids[:-1]).all():
        raise HardCheckError(
            "tie-break guard: site_idx is not in sorted site_id order"
        )

    n = len(treatments_eligible)
    keys = treatments_eligible["treatment_key"].to_numpy(dtype=np.int64)
    w_start = treatments_eligible["w_start_s"].to_numpy()
    w_end = treatments_eligible["w_end_s"].to_numpy()

    n_band = np.diff(idx.band_offsets)
    n_clean = np.zeros(n, dtype=np.int64)
    n_caliper = np.zeros(n, dtype=np.int64)
    ranked = {name: [] for name in (
        "owner", "site", "distance", "match_distance", "base_100", "base_250"
    )}

    for begin in range(0, n, TREATMENT_QUERY_CHUNK):
        rows = np.arange(begin, min(begin + TREATMENT_QUERY_CHUNK, n))
        block = _caliper_candidates(
            params, treatments_eligible, idx, scales, rows
        )
        n_clean[rows] = block["n_clean"]
        n_caliper[rows] = block["n_caliper"]
        for name in ranked:
            ranked[name].append(block[name])

    ranked = {name: np.concatenate(parts) for name, parts in ranked.items()}
    cal_offsets = np.concatenate(
        ([0], np.cumsum(np.bincount(ranked["owner"], minlength=n)))
    )

    # Internal guards (do not replace H14).
    if not ((n_band >= n_clean).all() and (n_clean >= n_caliper).all()):
        raise HardCheckError("candidate guard: counts are not nested")

    sorted_rows = np.argsort(keys, kind="stable")
    rng = np.random.default_rng(params.match_seed)
    sequence = sorted_rows[rng.permutation(n)]
    order_hash = hashlib.sha256(keys[sequence].tobytes()).hexdigest()

    code_of = {step: code for code, step in REASON_CODES}
    matched, rejected = [], []
    n_prereg_blocked = 0
    n_conflict_blocked = 0

    for position, row in enumerate(sequence.tolist()):
        counts = (int(n_band[row]), int(n_clean[row]), int(n_caliper[row]))

        if counts[0] == 0:
            rejected.append((keys[row], 9, *counts))
            continue
        if counts[1] == 0:
            rejected.append((keys[row], 10, *counts))
            continue
        if counts[2] == 0:
            rejected.append((keys[row], 11, *counts))
            continue

        blocked = 0
        chosen = -1
        for entry in range(cal_offsets[row], cal_offsets[row + 1]):
            site = int(ranked["site"][entry])
            if registry.is_free(site, w_start[row], w_end[row]):
                registry.assign(site, w_start[row], w_end[row])
                chosen = entry
                break
            blocked += 1
            if registry.blocked_by_preregistration(
                site, w_start[row], w_end[row]
            ):
                n_prereg_blocked += 1

        if chosen < 0:
            n_conflict_blocked += blocked
            rejected.append((keys[row], 12, *counts))
            continue

        matched.append((
            keys[row],
            int(ranked["site"][chosen]),
            float(ranked["distance"][chosen]),
            float(ranked["match_distance"][chosen]),
            int(ranked["base_100"][chosen]),
            int(ranked["base_250"][chosen]),
            *counts,
            blocked,
            position,
        ))

    pairs_raw = pd.DataFrame(
        matched,
        columns=[
            "treatment_key", "control_site_idx", "distance_m",
            "match_distance", "control_base_100m", "control_base_250m",
            "n_candidates_band", "n_candidates_clean", "n_candidates_caliper",
            "n_candidates_reuse_blocked", "match_order",
        ],
    ).astype({
        "treatment_key": "int64",
        "control_site_idx": "int32",
        "distance_m": "float64",
        "match_distance": "float64",
        "control_base_100m": "int32",
        "control_base_250m": "int32",
        "n_candidates_band": "int32",
        "n_candidates_clean": "int32",
        "n_candidates_caliper": "int32",
        "n_candidates_reuse_blocked": "int32",
        "match_order": "int32",
    })

    rejected = pd.DataFrame(
        rejected,
        columns=[
            "treatment_key", "reason_step",
            "n_candidates_band", "n_candidates_clean", "n_candidates_caliper",
        ],
    )
    rejected.insert(
        1,
        "reason_code",
        rejected["reason_step"].map(code_of).astype("string"),
    )
    rejected = rejected.astype({
        "treatment_key": "int64",
        "reason_step": "int8",
        "n_candidates_band": "Int32",
        "n_candidates_clean": "Int32",
        "n_candidates_caliper": "Int32",
    })

    if len(pairs_raw) + len(rejected) != n:
        raise HardCheckError(
            "match accounting guard: pairs + rejected != eligible treatments"
        )

    result = MatchResult(
        pairs_raw=pairs_raw,
        rejected=rejected,
        order_hash=order_hash,
        blocked_by_preregistration=n_prereg_blocked,
    )

    reasons = {
        code: int((rejected["reason_step"] == step).sum())
        for code, step in REASON_CODES
        if step >= 9
    }
    reuse = pairs_raw["control_site_idx"].value_counts()
    summary = {
        "n_treatments": n,
        "n_pairs": len(pairs_raw),
        "unmatched_reasons": reasons,
        "candidates": {
            "band": _count_percentiles(n_band),
            "clean": _count_percentiles(n_clean),
            "caliper": _count_percentiles(n_caliper),
        },
        "reuse_blocked_matched": _count_percentiles(
            pairs_raw["n_candidates_reuse_blocked"].to_numpy()
        ),
        "reuse_blocked_conflict_total": n_conflict_blocked,
        "blocked_by_preregistration": n_prereg_blocked,
        "n_control_sites": int(len(reuse)),
        "control_reuse_max": int(reuse.max()) if len(reuse) else 0,
        "control_reuse_mean": float(reuse.mean()) if len(reuse) else 0.0,
        "order_hash": order_hash,
    }

    attrition = [("eligible treatments", n)]
    remaining = n
    for code, count in reasons.items():
        remaining -= count
        attrition.append((f"after {code}", remaining))

    print(
        f"Matching (seed {params.match_seed}, caliper {params.caliper_sd:g} "
        f"SD, {params.control_selection}, {params.reuse_policy}): "
        f"{len(pairs_raw):,} pairs from {n:,} eligible treatments"
    )
    for label, remaining in attrition:
        print(f"  {label:<32} {remaining:>8,}")
    for name in ("band", "clean", "caliper"):
        entry = summary["candidates"][name]
        print(
            f"  candidates {name:<8} p10 {entry['p10']:g}, "
            f"p50 {entry['p50']:g}, p90 {entry['p90']:g}, "
            f"max {entry['max']:,}, zero {entry['n_zero']:,}"
        )
    print(
        f"  reuse: {summary['n_control_sites']:,} control sites, "
        f"max {summary['control_reuse_max']} uses, "
        f"mean {summary['control_reuse_mean']:.3f}; blocked by "
        f"pre-registration {n_prereg_blocked:,}; order hash {order_hash[:16]}"
    )

    return result, summary


BALANCE_COLUMNS = tuple(column for column in PAIRS_DTYPES if column.startswith("bal_"))


def assemble_pairs(params, match, treatments_eligible, sites, registry):
    """
    Step 14: one row per matched pair, in match_order, with the
    PAIRS_DTYPES columns except the bal_* columns (step 15).

    Treatment: exact complaint point (x/y and latitude/longitude), site
    modal borough and precinct, treatment-site H3 cells (U1), episode,
    windows and Commit 8 baselines. Control: the site's rounded point,
    modal labels and H3 cells. pair_id = {treatment_key}_{control_site_id};
    outage_duration_hours = (closed_date - created_date) / 3600 s (U8);
    control_reuse_count = pairs using the same control site, counted from
    the pairs (cross-checked against the registry).
    """

    raw = match.pairs_raw
    treatment = (
        treatments_eligible.set_index("treatment_key")
        .loc[raw["treatment_key"].to_numpy()]
        .reset_index()
    )
    t_site = treatment["site_idx"].to_numpy()
    c_site = raw["control_site_idx"].to_numpy().astype(np.int64)

    def site_column(name, index):
        return sites[name].to_numpy()[index]

    reuse_count = raw.groupby("control_site_idx")["treatment_key"].transform(
        "size"
    )
    if len(raw) and not all(
        registry.n_uses(site) == count
        for site, count in zip(c_site.tolist(), reuse_count.tolist())
    ):
        raise HardCheckError(
            "reuse guard: control_reuse_count differs from the registry"
        )

    columns = {
        "pair_id": [
            f"{key}_{site_id}"
            for key, site_id in zip(
                raw["treatment_key"].tolist(), site_column("site_id", c_site)
            )
        ],
        "treatment_key": raw["treatment_key"].to_numpy(),
        "treatment_site_id": site_column("site_id", t_site),
        "control_site_id": site_column("site_id", c_site),
        "treatment_episode_id": treatment["episode_id"].to_numpy(),
        "created_date": treatment["created_date"].to_numpy(),
        "closed_date": treatment["closed_date"].to_numpy(),
        "outage_duration_hours": (
            _to_seconds(treatment["closed_date"])
            - _to_seconds(treatment["created_date"])
        ) / 3600.0,
        "treatment_latitude": treatment["latitude"].to_numpy(),
        "treatment_longitude": treatment["longitude"].to_numpy(),
        "treatment_x_m": treatment["x_m"].to_numpy(),
        "treatment_y_m": treatment["y_m"].to_numpy(),
        "control_latitude": site_column("latitude", c_site),
        "control_longitude": site_column("longitude", c_site),
        "control_x_m": site_column("x_m", c_site).astype(np.float64),
        "control_y_m": site_column("y_m", c_site).astype(np.float64),
        "treatment_borough": treatment["treatment_borough"].to_numpy(),
        "control_borough": site_column("borough", c_site),
        "treatment_police_precinct": treatment[
            "treatment_police_precinct"
        ].to_numpy(),
        "control_police_precinct": site_column("police_precinct", c_site),
    }
    for role, index in (("treatment", t_site), ("control", c_site)):
        for resolution in params.h3_resolutions:
            columns[f"{role}_h3_res{resolution}"] = site_column(
                f"h3_res{resolution}", index
            )
    columns.update({
        "distance_m": raw["distance_m"].to_numpy(),
        "window_start": treatment["window_start"].to_numpy(),
        "window_end": treatment["window_end"].to_numpy(),
        "baseline_start": treatment["baseline_start"].to_numpy(),
        "baseline_end": treatment["baseline_end"].to_numpy(),
        "treatment_base_100m": treatment["base_100m"].to_numpy(),
        "treatment_base_250m": treatment["base_250m"].to_numpy(),
        "control_base_100m": raw["control_base_100m"].to_numpy(),
        "control_base_250m": raw["control_base_250m"].to_numpy(),
        "match_distance": raw["match_distance"].to_numpy(),
        "n_candidates_band": raw["n_candidates_band"].to_numpy(),
        "n_candidates_clean": raw["n_candidates_clean"].to_numpy(),
        "n_candidates_caliper": raw["n_candidates_caliper"].to_numpy(),
        "n_candidates_reuse_blocked": raw[
            "n_candidates_reuse_blocked"
        ].to_numpy(),
        "control_reuse_count": reuse_count.to_numpy(),
        "match_seed": np.full(len(raw), params.match_seed),
        "match_order": raw["match_order"].to_numpy(),
        "control_selection": np.full(len(raw), params.control_selection),
        "reuse_policy": np.full(len(raw), params.reuse_policy),
        "placebo_shift_days": np.full(len(raw), params.placebo_shift_days),
    })

    order = [column for column in PAIRS_DTYPES if column not in BALANCE_COLUMNS]
    if list(columns) != order:
        raise HardCheckError("assembly guard: pair columns out of schema order")

    pairs = pd.DataFrame(columns).astype(
        {column: PAIRS_DTYPES[column] for column in order}
    )

    print(
        f"Pairs assembled: {len(pairs):,} rows x {len(pairs.columns)} columns "
        f"({len(BALANCE_COLUMNS)} bal_* columns follow in step 15); "
        f"{pairs['control_site_id'].nunique():,} control sites, "
        f"max control_reuse_count {int(pairs['control_reuse_count'].max()) if len(pairs) else 0}"
    )

    return pairs


# Step 1 acceptance item 8: contamination shares above this are explained.
CONTAMINATION_REVIEW_SHARE = 0.05


def _smd_vr(treated, control):
    """
    SMD = (mean_T - mean_C) / sqrt((s_T**2 + s_C**2) / 2) and
    VR = s_T**2 / s_C**2, sample variances (ddof = 1) (B1, B2).
    Both variances 0: SMD = 0 if the means are equal, else +/-inf;
    VR = 1. Only s_C**2 = 0: VR = inf.
    """

    treated = np.asarray(treated, dtype=np.float64)
    control = np.asarray(control, dtype=np.float64)

    mean_t, mean_c = float(treated.mean()), float(control.mean())
    var_t = float(treated.var(ddof=1)) if len(treated) > 1 else float("nan")
    var_c = float(control.var(ddof=1)) if len(control) > 1 else float("nan")

    pooled = math.sqrt((var_t + var_c) / 2)
    if pooled == 0:
        smd = 0.0 if mean_t == mean_c else math.copysign(math.inf, mean_t - mean_c)
    else:
        smd = (mean_t - mean_c) / pooled

    if var_t == 0 and var_c == 0:
        vr = 1.0
    elif var_c == 0:
        vr = math.inf
    else:
        vr = var_t / var_c

    return {
        "treatment_mean": mean_t,
        "treatment_sd": math.sqrt(var_t),
        "control_mean": mean_c,
        "control_sd": math.sqrt(var_c),
        "smd": smd,
        "variance_ratio": vr,
    }


def _balance_entry(params, treated, control):
    entry = _smd_vr(treated, control)
    low, high = params.vr_range
    entry["pass"] = bool(
        abs(entry["smd"]) < params.smd_max
        and low <= entry["variance_ratio"] <= high
    )
    return entry


def _crime_counts(params, crime_tree, crime_t, centers, start_s, end_s):
    """
    Night crimes with start_s <= t <= end_s (closed) around each center:
    d <= direct_radius_m and direct_radius_m < d <= outcome_radius_m
    (A12, A13 explicit formula).
    """

    n = len(centers)
    direct = np.zeros(n, dtype=np.int64)
    ring = np.zeros(n, dtype=np.int64)

    for owner, points, distance in _ball_candidates(
        crime_tree, centers, params.outcome_radius_m, TREATMENT_QUERY_CHUNK
    ):
        t = crime_t[points]
        inside = (t >= start_s[owner]) & (t <= end_s[owner])
        near = distance <= params.direct_radius_m
        direct += np.bincount(owner[inside & near], minlength=n)
        ring += np.bincount(owner[inside & ~near], minlength=n)

    return direct, ring


def _prior_episodes(params, idx, complaints, episodes, centers, b_start,
                    b_last):
    """
    A9 (frozen): distinct episodes with b_start <= episodes.start_s <=
    b_last that have at least one complaint (any, artifact sites
    included) within prior_episode_radius_m of the center, measured to
    the complaint's own point (A8, A13).
    """

    n = len(centers)
    episode_id = complaints["episode_id"].to_numpy()
    episode_start = episodes["start_s"].to_numpy()
    n_episodes = len(episodes)
    counts = np.zeros(n, dtype=np.int64)

    for owner, points, _ in _ball_candidates(
        idx.complaint_tree, centers, params.prior_episode_radius_m,
        RULE_QUERY_CHUNK,
    ):
        episode = episode_id[points]
        start = episode_start[episode]
        keep = (start >= b_start[owner]) & (start <= b_last[owner])
        unique = np.unique(owner[keep] * n_episodes + episode[keep])
        counts += np.bincount(unique // n_episodes, minlength=n)

    return counts


def _cell_density(idx, cells, b_start, b_end):
    """A10: crimes during B = [b_start, b_end) in the res-9 cell / km^2."""

    unique_cells, inverse = np.unique(np.asarray(cells, dtype=object),
                                      return_inverse=True)
    rank = np.array(
        [idx.crime_cell_rank.get(h3.str_to_int(cell), -1) for cell in unique_cells],
        dtype=np.int64,
    )[inverse]
    area = np.array(
        [h3.cell_area(cell, unit="km^2") for cell in unique_cells],
        dtype=np.float64,
    )[inverse]
    counts = _count_in_window(
        idx.crime_cell_key, rank, b_start, b_end, end_inclusive=False
    )
    return counts / area


def compute_balance(params, pairs, treatments_eligible, episodes, complaints,
                    crime, idx):
    """
    Step 15: the 8 bal_* columns and the balance diagnostics.

    Units: treatment exact point (treatment_x_m/y_m); control rounded
    site point (control_x_m/y_m). Windows from the pair columns (shifted
    in placebo runs; complaints and crimes keep real dates, C4).

    bal_*_prior_episodes_250m  _prior_episodes over B (A9, B5)
    bal_*_h3r9_density         crimes during B in the pair's res-9 site
                               cell per km^2 (A10, B4)
    bal_*_pre_100m / _250m     night crimes d <= 100 / 100 < d <= 250
                               during [created - 14 d, created] (A11, A12)

    Matched variables: log1p of both baselines. Balance-only: prior
    episodes and density (raw, B3), pre-window counts (raw and log1p).
    Per variable: means, SDs, SMD, VR, pass (B1, B2, B12). Descriptive:
    distance_m, pairs by created year and month (B14). Representativeness:
    matched treatments vs all eligible treatments (B13).

    Returns (pairs with every PAIRS_DTYPES column, balance dict).
    """

    b_start = _to_seconds(pairs["baseline_start"])
    b_end = _to_seconds(pairs["baseline_end"])
    created = _to_seconds(pairs["created_date"])
    pre_start = created - params.s8_pre_window_days * SECONDS_PER_DAY
    crime_t = crime["t_s"].to_numpy()

    values = {}
    for role in ("treatment", "control"):
        centers = pairs[[f"{role}_x_m", f"{role}_y_m"]].to_numpy(np.float64)
        values[f"bal_{role}_prior_episodes_250m"] = _prior_episodes(
            params, idx, complaints, episodes, centers, b_start, b_end - 1
        )
        values[f"bal_{role}_h3r9_density"] = _cell_density(
            idx, pairs[f"{role}_h3_res9"].to_numpy(dtype=object), b_start, b_end
        )
        direct, ring = _crime_counts(
            params, idx.crime_tree, crime_t, centers, pre_start, created
        )
        values[f"bal_{role}_pre_100m"] = direct
        values[f"bal_{role}_pre_250m"] = ring

    pairs = pairs.assign(**{column: values[column] for column in BALANCE_COLUMNS})
    pairs = pairs[list(PAIRS_DTYPES)].astype(PAIRS_DTYPES)

    def column(role, name):
        return pairs[f"bal_{role}_{name}"].to_numpy(np.float64)

    matched = {
        f"log_base_{radius}": _balance_entry(
            params,
            np.log1p(pairs[f"treatment_base_{radius}"].to_numpy(np.float64)),
            np.log1p(pairs[f"control_base_{radius}"].to_numpy(np.float64)),
        )
        for radius in ("100m", "250m")
    }
    balance_only = {}
    for name in ("prior_episodes_250m", "h3r9_density", "pre_100m", "pre_250m"):
        balance_only[name] = _balance_entry(
            params, column("treatment", name), column("control", name)
        )
    for name in ("pre_100m", "pre_250m"):
        balance_only[f"log_{name}"] = _balance_entry(
            params,
            np.log1p(column("treatment", name)),
            np.log1p(column("control", name)),
        )

    created_dates = pairs["created_date"]
    descriptive = {
        "distance_m": {
            key: float(value) for key, value in
            zip(("mean", "p10", "p50", "p90", "min", "max"),
                (pairs["distance_m"].mean(),
                 *np.percentile(pairs["distance_m"], [10, 50, 90]),
                 pairs["distance_m"].min(), pairs["distance_m"].max()))
        } if len(pairs) else {},
        "pairs_by_year": {
            int(k): int(v)
            for k, v in created_dates.dt.year.value_counts().sort_index().items()
        },
        "pairs_by_month": {
            int(k): int(v)
            for k, v in created_dates.dt.month.value_counts().sort_index().items()
        },
    }

    def describe(values):
        values = np.asarray(values, dtype=np.float64)
        return {
            "mean": float(values.mean()),
            "median": float(np.median(values)),
            "p90": float(np.percentile(values, 90)),
        }

    def shares(series):
        return (series.value_counts(normalize=True).sort_index()
                .astype(float).to_dict())

    eligible_years = treatments_eligible["created_date"].dt.year
    representativeness = {
        "n_matched": int(len(pairs)),
        "n_eligible": int(len(treatments_eligible)),
        "baseline": {
            f"base_{radius}": {
                "matched": describe(pairs[f"treatment_base_{radius}"]),
                "eligible": describe(treatments_eligible[f"base_{radius}"]),
                "smd_log1p": _smd_vr(
                    np.log1p(pairs[f"treatment_base_{radius}"].to_numpy(np.float64)),
                    np.log1p(treatments_eligible[f"base_{radius}"].to_numpy(np.float64)),
                )["smd"],
            }
            for radius in ("100m", "250m")
        },
        "borough_share": {
            "matched": {str(k): v for k, v in shares(pairs["treatment_borough"]).items()},
            "eligible": {
                str(k): v
                for k, v in shares(treatments_eligible["treatment_borough"]).items()
            },
        },
        "year_share": {
            "matched": {int(k): v for k, v in shares(created_dates.dt.year).items()},
            "eligible": {int(k): v for k, v in shares(eligible_years).items()},
        },
    }

    balance = {
        "thresholds": {
            "smd_max": params.smd_max,
            "vr_min": params.vr_range[0],
            "vr_max": params.vr_range[1],
            "smd_formula": "(mean_T - mean_C) / sqrt((s_T^2 + s_C^2) / 2), ddof 1",
            "vr_formula": "s_T^2 / s_C^2, ddof 1",
        },
        "matched": matched,
        "balance_only": balance_only,
        "matched_pass": all(entry["pass"] for entry in matched.values()),
        "balance_only_pass": all(entry["pass"] for entry in balance_only.values()),
        "descriptive": descriptive,
        "representativeness": representativeness,
    }

    print(
        f"Balance (n = {len(pairs):,} pairs; |SMD| < {params.smd_max:g}, "
        f"VR in [{params.vr_range[0]:g}, {params.vr_range[1]:g}]):"
    )
    print(
        f"  {'variable':<24} {'T mean':>9} {'C mean':>9} {'T sd':>8} "
        f"{'C sd':>8} {'SMD':>8} {'VR':>7}  pass"
    )
    for group, entries in (("matched", matched), ("balance-only", balance_only)):
        for name, entry in entries.items():
            print(
                f"  {name:<24} {entry['treatment_mean']:>9.3f} "
                f"{entry['control_mean']:>9.3f} {entry['treatment_sd']:>8.3f} "
                f"{entry['control_sd']:>8.3f} {entry['smd']:>+8.4f} "
                f"{entry['variance_ratio']:>7.3f}  {entry['pass']}  ({group})"
            )
    print(
        f"  matched_pass {balance['matched_pass']}, "
        f"balance_only_pass {balance['balance_only_pass']}"
    )
    for radius, entry in representativeness["baseline"].items():
        print(
            f"  representativeness {radius}: matched mean "
            f"{entry['matched']['mean']:.3f} / median {entry['matched']['median']:g} "
            f"vs eligible {entry['eligible']['mean']:.3f} / "
            f"{entry['eligible']['median']:g} (SMD log1p {entry['smd_log1p']:+.4f})"
        )

    return pairs, balance


def _pairs_near_other_pairs(centers, other, radius, w_start, w_end):
    """
    Pairs i with some pair j != i whose `other` point is within radius
    (explicit formula, A13) and whose closed W overlaps (A2).
    """

    tree = cKDTree(other)
    flagged = np.zeros(len(centers), dtype=bool)

    for owner, points, _ in _ball_candidates(
        tree, centers, radius, TREATMENT_QUERY_CHUNK
    ):
        hit = (
            (points != owner)
            & (w_start[points] <= w_end[owner])
            & (w_end[points] >= w_start[owner])
        )
        flagged[owner[hit]] = True

    return flagged


def _dark_near(idx, complaints, centers, inner, outer, win_start, win_end,
               exclude=None):
    """
    Per center, complaints with inner < d <= outer (inner < 0 for d <= outer)
    whose darkness interval overlaps the closed window (A2); `exclude`
    is an optional complaint row per center that is not counted.
    """

    dark_start = complaints["dark_start_s"].to_numpy()
    dark_end = complaints["dark_end_s"].to_numpy()
    counts = np.zeros(len(centers), dtype=np.int64)

    for owner, points, distance in _ball_candidates(
        idx.complaint_tree, centers, outer, RULE_QUERY_CHUNK
    ):
        hit = (
            (distance > inner)
            & (dark_start[points] <= win_end[owner])
            & (dark_end[points] >= win_start[owner])
        )
        if exclude is not None:
            hit &= points != exclude[owner]
        counts += np.bincount(owner[hit], minlength=len(centers))

    return counts


def compute_contamination(params, pairs, complaints, episodes, idx,
                          episode_summary):
    """
    Step 16: contamination diagnostics (report only; shares above
    CONTAMINATION_REVIEW_SHARE are flagged for explanation). Shares are
    over pairs (B10); W is closed (A2); distances by the explicit formula.

    D1  control within 500 m (<=, B6) of another pair's treatment with
        overlapping W (matched treatments only, B7)
    D2  control with a complaint at exclusion_radius_m < d <= 500
        overlapping the full W (B8)
    D2b pre_only only: control with a complaint within exclusion_radius_m
        overlapping [created_s, w_end_s] (went dark after selection)
    D3  control within 500 m (<=) of another pair's control with
        overlapping W
    D4  per pair, other complaints within 100 m of the treatment
        overlapping [created_s, closed_s]; the treatment complaint is
        excluded, same-episode duplicates are included (B9)
    D5  episode chaining, taken from the step 6 episode summary (B11)
    """

    n = len(pairs)
    zone = 2 * params.outcome_radius_m
    treatment_xy = pairs[["treatment_x_m", "treatment_y_m"]].to_numpy(np.float64)
    control_xy = pairs[["control_x_m", "control_y_m"]].to_numpy(np.float64)
    w_start = _to_seconds(pairs["window_start"])
    w_end = _to_seconds(pairs["window_end"])
    created = _to_seconds(pairs["created_date"])
    closed = _to_seconds(pairs["closed_date"])

    def share(flagged):
        count = int(np.asarray(flagged).sum())
        value = count / n if n else float("nan")
        return {
            "n_pairs": count,
            "share": value,
            "above_review_share": bool(value > CONTAMINATION_REVIEW_SHARE),
        }

    contamination = {
        "review_share": CONTAMINATION_REVIEW_SHARE,
        "n_pairs": n,
        "D1": share(_pairs_near_other_pairs(
            control_xy, treatment_xy, zone, w_start, w_end
        )),
        "D2": share(_dark_near(
            idx, complaints, control_xy, params.exclusion_radius_m, zone,
            w_start, w_end,
        ) > 0),
        "D3": share(_pairs_near_other_pairs(
            control_xy, control_xy, zone, w_start, w_end
        )),
    }
    if params.control_selection == "pre_only":
        contamination["D2b"] = share(_dark_near(
            idx, complaints, control_xy, -1.0, params.exclusion_radius_m,
            created, w_end,
        ) > 0)

    own = pd.Index(complaints["unique_key"].to_numpy()).get_indexer(
        pairs["treatment_key"].to_numpy()
    )
    d4 = _dark_near(
        idx, complaints, treatment_xy, -1.0, params.direct_radius_m,
        created, closed, exclude=own,
    )
    contamination["D4"] = {
        "mean": float(d4.mean()) if n else float("nan"),
        "p50": float(np.percentile(d4, 50)) if n else float("nan"),
        "p90": float(np.percentile(d4, 90)) if n else float("nan"),
        "max": int(d4.max()) if n else 0,
        "share_zero": float((d4 == 0).mean()) if n else float("nan"),
    }
    contamination["D5"] = {
        "n_episodes": episode_summary["n_episodes"],
        "episodes_over_100m": episode_summary["d5_episodes_over_100m"],
        "episodes_over_365d": episode_summary["d5_episodes_over_365d"],
        "max_footprint_m": episode_summary["episode_max_footprint_m"],
        "top10": episode_summary["d5_top10"],
    }

    print(f"Contamination (n = {n:,} pairs; review share {CONTAMINATION_REVIEW_SHARE:.0%}):")
    for key in ("D1", "D2", "D2b", "D3"):
        if key in contamination:
            entry = contamination[key]
            print(
                f"  {key:<3} {entry['n_pairs']:>7,} pairs  share {entry['share']:.4f}"
                f"{'  ABOVE REVIEW SHARE' if entry['above_review_share'] else ''}"
            )
    entry = contamination["D4"]
    print(
        f"  D4  other complaints within {params.direct_radius_m:g} m during "
        f"[created, closed]: mean {entry['mean']:.3f}, p50 {entry['p50']:g}, "
        f"p90 {entry['p90']:g}, max {entry['max']}, zero share "
        f"{entry['share_zero']:.3f}"
    )
    entry = contamination["D5"]
    print(
        f"  D5  {entry['episodes_over_100m']} episodes > 100 m, "
        f"{entry['episodes_over_365d']} > 365 days (of {entry['n_episodes']:,}); "
        f"max footprint {entry['max_footprint_m']:.1f} m"
    )

    return contamination


# ---------------------------------------------------------
# Step 18: independent recheck (H10, H11)
# ---------------------------------------------------------

# Candidate margin for the recheck's spatial index (R3): candidates are
# generated at radius + margin; membership is the explicit formula.
RECHECK_CANDIDATE_MARGIN_M = 1.0


def _recheck_sample(pairs, params):
    """min(recheck_sample_n, n) pairs, seeded, drawn from pair_id order."""

    ordered = pairs.sort_values("pair_id", kind="mergesort").reset_index(drop=True)
    size = min(params.recheck_sample_n, len(ordered))
    rng = np.random.default_rng(params.recheck_seed)
    rows = np.sort(rng.choice(len(ordered), size=size, replace=False))
    return ordered.iloc[rows].reset_index(drop=True)


def _whole_seconds(values):
    """datetime64 -> (int64 seconds, bool mask of sub-second values)."""

    values = np.asarray(values, dtype="datetime64[us]")
    seconds = values.astype("datetime64[s]")
    return seconds.astype(np.int64), seconds.astype("datetime64[us]") != values


def _recheck_candidates(sindex, points_x, points_y, query_x, query_y, radius):
    """
    (owner, point, distance) with distance <= radius (A13 explicit
    formula); the spatial index only generates candidates at
    radius + RECHECK_CANDIDATE_MARGIN_M (R3).
    """

    import geopandas as gpd

    if len(query_x) == 0:
        empty = np.zeros(0, dtype=np.int64)
        return empty, empty, np.zeros(0)

    owner, point = sindex.query(
        gpd.points_from_xy(query_x, query_y),
        predicate="dwithin",
        distance=radius + RECHECK_CANDIDATE_MARGIN_M,
    )
    owner = owner.astype(np.int64)
    point = point.astype(np.int64)
    distance = np.sqrt(
        (points_x[point] - query_x[owner]) ** 2
        + (points_y[point] - query_y[owner]) ** 2
    )
    keep = distance <= radius
    return owner[keep], point[keep], distance[keep]


def _recheck_complaints(params):
    """
    Raw complaint universe for H10, rebuilt from the raw CSV only (E2,
    R2, R3): rows with latitude and longitude inside NYC_BBOX (bounds
    inclusive), projected with pyproj (always_xy), darkness intervals,
    1 m sites and artifact flags. Returns the table and a dict of
    universe violations (unparseable created_date, duplicate unique_key,
    sub-second times).
    """

    raw = pd.read_csv(
        RAW_COMPLAINTS_FILE,
        usecols=["unique_key", "created_date", "closed_date", "latitude",
                 "longitude"],
        low_memory=False,
    )
    lat_min, lat_max, lon_min, lon_max = params.nyc_bbox
    keep = (
        raw["latitude"].notna()
        & raw["longitude"].notna()
        & raw["latitude"].between(lat_min, lat_max)
        & raw["longitude"].between(lon_min, lon_max)
    )
    raw = raw.loc[keep].reset_index(drop=True)

    created = pd.to_datetime(raw["created_date"], format="ISO8601", errors="coerce")
    closed = pd.to_datetime(raw["closed_date"], format="ISO8601", errors="coerce")

    problems = {
        "unparseable_created_date": int(created.isna().sum()),
        "duplicate_unique_key": int(raw["unique_key"].duplicated(keep=False).sum()),
    }

    transformer = Transformer.from_crs(
        "EPSG:4326", params.projected_crs, always_xy=True
    )
    x, y = transformer.transform(
        raw["longitude"].to_numpy(dtype=np.float64),
        raw["latitude"].to_numpy(dtype=np.float64),
    )
    x, y = np.asarray(x), np.asarray(y)

    has_created = created.notna().to_numpy()
    has_closed = closed.notna().to_numpy()
    created_s = np.zeros(len(raw), dtype=np.int64)
    closed_s = np.zeros(len(raw), dtype=np.int64)
    created_s[has_created], sub_c = _whole_seconds(created[has_created])
    closed_s[has_closed], sub_d = _whole_seconds(closed[has_closed])
    problems["sub_second_complaint_time"] = int(sub_c.sum() + sub_d.sum())

    both = has_created & has_closed
    duration_h = np.full(len(raw), np.nan)
    duration_h[both] = (closed_s[both] - created_s[both]) / 3600
    valid = (
        both
        & (duration_h >= params.valid_min_duration_h)
        & (duration_h <= params.valid_max_duration_h)
    )
    lag_s = params.report_lag_days * SECONDS_PER_DAY
    imputed_s = int(round(params.imputed_duration_hours * 3600))

    step = params.site_round_m
    x_site = (np.rint(x / step) * step).astype(np.int64)
    y_site = (np.rint(y / step) * step).astype(np.int64)
    site_xy, site_of, counts = np.unique(
        np.stack([x_site, y_site], axis=1), axis=0,
        return_inverse=True, return_counts=True,
    )
    threshold = int(np.quantile(counts, params.artifact_quantile, method="higher"))
    artifact_site = counts > threshold

    table = pd.DataFrame({
        "unique_key": raw["unique_key"].to_numpy(),
        "x": x,
        "y": y,
        "created_s": created_s,
        "closed_s": closed_s,
        "has_created": has_created,
        "has_closed": has_closed,
        "dark_start_s": created_s - lag_s,
        "dark_end_s": np.where(valid, closed_s, created_s + imputed_s),
        "site": site_of.reshape(-1).astype(np.int64),
        "is_artifact": artifact_site[site_of.reshape(-1)],
    })

    sites = {
        (int(sx), int(sy)): index
        for index, (sx, sy) in enumerate(site_xy.tolist())
    }
    universe = {
        "n_complaints": int(len(table)),
        "n_sites": int(len(site_xy)),
        "artifact_threshold": threshold,
        "n_artifact_sites": int(artifact_site.sum()),
    }

    return table, sites, artifact_site, universe, problems


def _recheck_own_episode(start_row, table, sindex, radius):
    """
    R2: full transitive closure (breadth-first search, no caps) of links
    between non-artifact complaints with d <= radius (explicit formula)
    and overlapping darkness intervals (A2 closed).
    """

    x = table["x"].to_numpy()
    y = table["y"].to_numpy()
    s = table["dark_start_s"].to_numpy()
    e = table["dark_end_s"].to_numpy()
    linkable = ~table["is_artifact"].to_numpy() & table["has_created"].to_numpy()

    members = {int(start_row)}
    frontier = np.array([start_row], dtype=np.int64)

    while len(frontier):
        owner, point, _ = _recheck_candidates(
            sindex, x, y, x[frontier], y[frontier], radius
        )
        source = frontier[owner]
        linked = (
            linkable[point]
            & linkable[source]
            & (s[point] <= e[source])
            & (e[point] >= s[source])
        )
        new = set(point[linked].tolist()) - members
        members |= new
        frontier = np.array(sorted(new), dtype=np.int64)

    return members


def independent_recheck(params, pairs):
    """
    Step 18: H10 and H11, rebuilt from the raw files for a seeded sample
    of pairs (min(recheck_sample_n, n) from pair_id order). Uses no
    production episode ids, artifact flags, darkness intervals, sites,
    windows or spatial indexes; production values are read only from the
    pair columns being checked. Same libraries as production (pandas,
    pyproj, numpy); geopandas/shapely STRtree only generates candidates
    at radius + 1 m, membership is sqrt(dx**2 + dy**2) <= r in
    projected_crs (A13, R3, E1).

    H10, per sampled pair (one violation per pair per failed condition):
      universe (E2): unparseable created_date or duplicate unique_key in
        the complaint universe, sub-second times (counted once per run)
      treatment key not in the universe; treatment coordinates rebuilt
        with pyproj differ from treatment_x_m/y_m (exact)
      windows rebuilt from the raw dates and placebo shift differ from the
        pair columns (created, closed, W, B)
      control site not rebuilt, control_x/y not whole metres of that
        site, control_site_id text differs, or the rebuilt site is an
        artifact; treatment site is an artifact
      S-3 (E3): a complaint within exclusion_radius_m of the control
        overlapping the S-3 clean window (full_window [w_start, w_end];
        pre_only [w_start, created - 1 s]); no exemptions
      S-4 pre (R1): complaint within treatment_clean_radius_m with
        s <= c - 1 s and e >= c - event_window; canonical exempts the
        rebuilt own episode, placebo exempts nothing
      S-4 post (R1): s <= W_end and e >= closed + 1 s; canonical exempts
        only the treatment complaint, placebo exempts nothing
    Treatment windows use shifted dates; complaint darkness uses real
    dates (C4).

    H11: crimes (clean_crime.parquet, rows with null crime_datetime,
    latitude or longitude dropped) with t in [b_start, b_end - 1 s],
    d <= direct_radius_m and direct_radius_m < d <= outcome_radius_m,
    around both units; must equal the four base columns exactly.

    Returns (h10 CheckResult, h11 CheckResult, summary).
    """

    import geopandas as gpd

    start = time.perf_counter()
    sample = _recheck_sample(pairs, params)
    n = len(sample)
    shift_s = params.placebo_shift_days * SECONDS_PER_DAY
    event_s = params.event_window_days * SECONDS_PER_DAY
    post_s = params.post_window_days * SECONDS_PER_DAY
    baseline_s = params.baseline_days * SECONDS_PER_DAY
    placebo = params.placebo_shift_days > 0

    table, site_index, artifact_site, universe, universe_problems = (
        _recheck_complaints(params)
    )
    x = table["x"].to_numpy()
    y = table["y"].to_numpy()
    s = table["dark_start_s"].to_numpy()
    e = table["dark_end_s"].to_numpy()
    usable = table["has_created"].to_numpy()
    sindex = gpd.GeoSeries(gpd.points_from_xy(x, y)).sindex

    h10 = {}

    def flag(name, mask):
        mask = np.asarray(mask, dtype=bool)
        entry = h10.setdefault(name, np.zeros(n, dtype=bool))
        entry |= mask

    # Treatment rows, coordinates and windows.
    # First occurrence per key: duplicates are universe violations (E2),
    # not a reason to stop the recheck.
    keys = table["unique_key"].to_numpy()
    first = ~pd.Series(keys).duplicated(keep="first").to_numpy()
    row = np.full(n, -1, dtype=np.int64)
    lookup = pd.Index(keys[first]).get_indexer(sample["treatment_key"].to_numpy())
    row[lookup >= 0] = np.flatnonzero(first)[lookup[lookup >= 0]]
    found = row >= 0
    safe = np.where(found, row, 0)
    flag("treatment_key_not_in_universe", ~found)
    flag("treatment_coordinates_differ", found & (
        (x[safe] != sample["treatment_x_m"].to_numpy())
        | (y[safe] != sample["treatment_y_m"].to_numpy())
    ))

    c = table["created_s"].to_numpy()[safe] - shift_s
    closed = table["closed_s"].to_numpy()[safe] - shift_s
    flag("treatment_closed_missing", found & ~table["has_closed"].to_numpy()[safe])
    w_start = c - event_s
    w_end = np.maximum(closed + post_s, c + event_s)
    b_start = c - event_s - baseline_s
    b_end = c - event_s
    for column, rebuilt in (
        ("created_date", c), ("closed_date", closed),
        ("window_start", w_start), ("window_end", w_end),
        ("baseline_start", b_start), ("baseline_end", b_end),
    ):
        stored = _to_seconds(sample[column])
        flag(f"{column}_differs", found & (stored != rebuilt))

    flag("treatment_site_is_artifact",
         found & table["is_artifact"].to_numpy()[safe])

    # Control site.
    cx = sample["control_x_m"].to_numpy()
    cy = sample["control_y_m"].to_numpy()
    whole = (cx == np.round(cx)) & (cy == np.round(cy))
    site = np.array([
        site_index.get((int(a), int(b)), -1) if ok else -1
        for a, b, ok in zip(cx, cy, whole)
    ])
    flag("control_site_not_rebuilt", site < 0)
    flag("control_site_id_differs", ~whole | np.array([
        text != f"E{int(a)}_N{int(b)}"
        for text, a, b in zip(sample["control_site_id"].astype(str), cx, cy)
    ]))
    flag("control_site_is_artifact", (site >= 0) & artifact_site[np.maximum(site, 0)])

    # S-3 (E3).
    s3_end = w_end if params.control_selection == "full_window" else c - 1
    owner, point, _ = _recheck_candidates(
        sindex, x, y, cx, cy, params.exclusion_radius_m
    )
    hit = usable[point] & (s[point] <= s3_end[owner]) & (e[point] >= w_start[owner])
    flag("s3_dark_control", np.bincount(owner[hit], minlength=n) > 0)

    # S-4 (R1), around the rebuilt treatment point.
    tx, ty = x[safe], y[safe]
    owner, point, _ = _recheck_candidates(
        sindex, x, y, tx, ty, params.treatment_clean_radius_m
    )
    pre_hit = usable[point] & (s[point] <= c[owner] - 1) & (e[point] >= c[owner] - event_s)
    post_hit = usable[point] & (s[point] <= w_end[owner]) & (e[point] >= closed[owner] + 1)
    # Hits by own-episode complaints other than the treatment complaint
    # itself (duplicate reports), before the exemption.
    duplicate_hit = pre_hit & (point != safe[owner])
    if not placebo:
        post_hit &= point != safe[owner]
        own = {}
        for index in np.unique(owner[pre_hit]).tolist():
            own[index] = _recheck_own_episode(
                safe[index], table, sindex, params.episode_merge_radius_m
            )
        pre_hit &= np.array([
            int(p) not in own.get(int(o), ()) for o, p in zip(owner, point)
        ], dtype=bool) if len(owner) else pre_hit
    if not placebo:
        duplicate_hit &= ~pre_hit
    else:
        duplicate_hit &= False
    n_pre_exempted = int(
        (found & (np.bincount(owner[duplicate_hit], minlength=n) > 0)).sum()
    )
    flag("s4_pre_dirty", found & (np.bincount(owner[pre_hit], minlength=n) > 0))
    flag("s4_post_dirty", found & (np.bincount(owner[post_hit], minlength=n) > 0))

    # H11: crime recount.
    crime = pd.read_parquet(CRIME_FILE, columns=["crime_datetime", "latitude", "longitude"])
    crime = crime.dropna(subset=["crime_datetime", "latitude", "longitude"])
    kx, ky = Transformer.from_crs(
        "EPSG:4326", params.projected_crs, always_xy=True
    ).transform(
        crime["longitude"].to_numpy(dtype=np.float64),
        crime["latitude"].to_numpy(dtype=np.float64),
    )
    kx, ky = np.asarray(kx), np.asarray(ky)
    kt, sub_k = _whole_seconds(crime["crime_datetime"])
    universe_problems["sub_second_crime_time"] = int(sub_k.sum())
    crime_sindex = gpd.GeoSeries(gpd.points_from_xy(kx, ky)).sindex
    universe["n_crimes"] = int(len(kt))
    del crime

    h11 = {}
    for role, ux, uy in (("treatment", tx, ty), ("control", cx, cy)):
        owner, point, distance = _recheck_candidates(
            crime_sindex, kx, ky, ux, uy, params.outcome_radius_m
        )
        in_b = (kt[point] >= b_start[owner]) & (kt[point] <= b_end[owner] - 1)
        near = distance <= params.direct_radius_m
        direct = np.bincount(owner[in_b & near], minlength=n)
        ring = np.bincount(owner[in_b & ~near], minlength=n)
        for radius, counts in (("100m", direct), ("250m", ring)):
            column = f"{role}_base_{radius}"
            mask = counts != sample[column].to_numpy()
            if role == "treatment":
                mask &= found
            h11[column] = mask

    def result(check_id, masks, extra):
        problems = []
        violations = 0
        for name, count in extra.items():
            if count:
                violations += count
                problems.append(f"{count:,} {name} in the recheck universe")
        for name, mask in masks.items():
            count = int(mask.sum())
            if count:
                violations += count
                problems.append(
                    f"{count:,} {name}: "
                    + ", ".join(sample.loc[mask, "pair_id"].head(10).astype(str))
                )
        return CheckResult(
            check_id=check_id,
            passed=violations == 0,
            n_violations=violations,
            examples=problems,
            seconds=time.perf_counter() - start,
        )

    h10_universe = {
        key: value for key, value in universe_problems.items()
        if key != "sub_second_crime_time"
    }
    h11_universe = {"sub_second_crime_time": universe_problems["sub_second_crime_time"]}

    summary = {
        "skipped": False,
        "sample_n": n,
        "seed": params.recheck_seed,
        "n_pairs": int(len(pairs)),
        "candidate_margin_m": RECHECK_CANDIDATE_MARGIN_M,
        "universe": universe,
        "universe_problems": universe_problems,
        # Sampled treatments with an S-4 pre hit by another complaint of
        # their rebuilt own episode that the canonical exemption removed
        # (the exemption is exercised; 0 in placebo runs).
        "n_s4_pre_own_episode_exempted": n_pre_exempted,
        "h10": {name: int(mask.sum()) for name, mask in h10.items()},
        "h11": {name: int(mask.sum()) for name, mask in h11.items()},
    }

    return (
        result("H10", h10, h10_universe),
        result("H11", h11, h11_universe),
        summary,
    )


def skipped_recheck(params, pairs):
    """--skip-recheck: H10 and H11 are recorded as skipped, not passed."""

    def skipped(check_id):
        return CheckResult(
            check_id=check_id, passed=True, n_violations=0,
            examples=["skipped (--skip-recheck)"], seconds=0.0,
        )

    summary = {
        "skipped": True,
        "sample_n": 0,
        "seed": params.recheck_seed,
        "n_pairs": int(len(pairs)),
    }
    return skipped("H10"), skipped("H11"), summary


# ---------------------------------------------------------
# Step 19: unmatched table and site roles
# ---------------------------------------------------------

def build_unmatched(treatments_all, treatments_eligible, match, pairs, sites):
    """
    Step 19: one row per Stage 3 treatment that is not in the pairs.

    Steps 1-8 (treatment rules): reason from treatments_all; baselines and
    candidate counts null. Steps 9-12 (matching): reason and candidate
    counts from match.rejected; baselines from step 10 (N14). Rows sorted
    by treatment_key; dtypes as UNMATCHED_DTYPES.
    """

    matched = set(pairs["treatment_key"].tolist())
    rules = treatments_all.loc[
        treatments_all["reason_code"].notna()
    ].copy()

    rejected = match.rejected.merge(
        treatments_eligible[
            ["treatment_key", "base_100m", "base_250m"]
        ],
        on="treatment_key",
        how="left",
    )
    rejected = rejected.merge(
        treatments_all.drop(columns=["reason_code", "reason_step"]),
        on="treatment_key",
        how="left",
    )

    frames = []
    for frame, has_matching in ((rules, False), (rejected, True)):
        site_idx = frame["site_idx"].to_numpy().astype(np.int64)
        part = pd.DataFrame({
            "treatment_key": frame["treatment_key"].to_numpy(),
            "treatment_site_id": sites["site_id"].to_numpy(dtype=object)[site_idx],
            "treatment_episode_id": frame["episode_id"].to_numpy(),
            "created_date": frame["created_date"].to_numpy(),
            "closed_date": frame["closed_date"].to_numpy(),
            "borough": frame["treatment_borough"].to_numpy(dtype=object),
            "reason_code": frame["reason_code"].to_numpy(dtype=object),
            "reason_step": frame["reason_step"].to_numpy(),
        })
        for column in ("treatment_base_100m", "treatment_base_250m",
                       "n_candidates_band", "n_candidates_clean",
                       "n_candidates_caliper"):
            if has_matching:
                source = column.replace("treatment_", "")
                part[column] = frame[source].to_numpy()
            else:
                part[column] = pd.NA
        frames.append(part)

    unmatched = (
        pd.concat(frames, ignore_index=True)
        .sort_values("treatment_key", kind="mergesort")
        .reset_index(drop=True)
    )
    unmatched["borough"] = unmatched["borough"].where(
        pd.notna(unmatched["borough"]), None
    )
    unmatched = unmatched[list(UNMATCHED_DTYPES)].astype(UNMATCHED_DTYPES)

    if set(unmatched["treatment_key"]) & matched:
        raise HardCheckError("unmatched guard: a matched treatment is unmatched")

    print(
        f"Unmatched treatments: {len(unmatched):,} "
        f"(rules {len(rules):,}, matching {len(match.rejected):,})"
    )

    return unmatched


def assemble_sites(sites, pairs):
    """Site table with n_times_treatment / n_times_control (U7)."""

    out = sites.copy()
    for column, role in (("n_times_treatment", "treatment_site_id"),
                         ("n_times_control", "control_site_id")):
        counts = pairs[role].value_counts()
        out[column] = (
            out["site_id"].map(counts).fillna(0).astype(np.int32).to_numpy()
        )
    return out[list(SITES_DTYPES)].astype(SITES_DTYPES)


def full_attrition(attrition, match_summary):
    """Steps 0-8 from the treatment rules, then matching steps 9-12 (N4)."""

    steps = [dict(entry) for entry in attrition]
    for code, step in REASON_CODES:
        if step < 9:
            continue
        dropped = int(match_summary["unmatched_reasons"][code])
        steps.append({
            "step": step,
            "rule": code,
            "remaining": steps[-1]["remaining"] - dropped,
            "dropped": dropped,
        })
    return steps


# ---------------------------------------------------------
# Determinism (N6)
# ---------------------------------------------------------

def pair_list_hash(pair_ids):
    """sha256 of the sorted pair_id list, one id per line."""

    text = "\n".join(sorted(str(value) for value in pair_ids))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_determinism(params, treatments_eligible, sites, idx, scales, match):
    """
    --check-determinism: fresh registry, band lists and matching (steps
    12-13) on the same inputs; the sorted pair_id hash, the order hash and
    pairs_raw must be identical. Returns the determinism record.
    """

    site_ids = sites["site_id"].to_numpy(dtype=object)

    def ids(result):
        raw = result.pairs_raw
        return [
            f"{key}_{site_ids[site]}"
            for key, site in zip(raw["treatment_key"].tolist(),
                                 raw["control_site_idx"].tolist())
        ]

    with contextlib.redirect_stdout(io.StringIO()):
        registry = init_reuse_registry(params, treatments_eligible)
        idx, _ = build_band_candidates(params, treatments_eligible, sites, idx)
        second, _ = match_treatments(
            params, treatments_eligible, sites, idx, scales, registry
        )
    idx.band_sites = idx.band_dist = idx.band_offsets = None

    first_hash = pair_list_hash(ids(match))
    second_hash = pair_list_hash(ids(second))
    record = {
        "second_pass_pair_list_sha256": second_hash,
        "second_pass_order_hash": second.order_hash,
        "identical": bool(
            first_hash == second_hash
            and match.order_hash == second.order_hash
            and match.pairs_raw.equals(second.pairs_raw)
            and match.rejected.equals(second.rejected)
        ),
    }
    print(
        f"Determinism second pass: pair-list hash {second_hash[:16]}, "
        f"identical {record['identical']}"
    )
    return record


# ---------------------------------------------------------
# Step 20: diagnostics and output writing
# ---------------------------------------------------------

# A1-A3 and the earlier item 11 amendment (recorded in the diagnostics).
ACCEPTANCE_AMENDMENTS = {
    "item_11": (
        "No treatment is removed by W_OUTSIDE_DARKNESS alone: treatments "
        "attributed to rule 2 by first-failure order must also fail "
        "B_OUTSIDE_CRIME (attribution, not eligibility)."
    ),
    "A1": (
        "Balance-only diagnostics are reported and reviewed. Each failed "
        "balance-only flag needs a written explanation in the Step 1 report; "
        "Step 1 fails on it only if the reviewer concludes it shows a "
        "matching defect, leakage, an implementation error or a "
        "methodological violation (known example: prior_episodes_250m)."
    ),
    "A2": (
        "Contamination shares above the review share require reporting, "
        "verification and a written explanation. The Commit 11 review "
        "package satisfies this for the canonical c2bb341 definitions; "
        "changes to the definitions or implementation require "
        "re-verification. Sensitivity and placebo runs are report-only."
    ),
    "A3": (
        "The single-commit requirement of item 20 is replaced by the "
        "approved multi-commit plan; the baseline-pre-issue4 tag "
        "requirement remains and is met."
    ),
}

H16_POST_DEFINITION = (
    "Placebo runs only (N5): every pair's created_date and closed_date equal "
    "the real complaint dates minus placebo_shift_days; every complaint's "
    "darkness interval equals the Stage 7 rule applied to the unshifted "
    "dates; no matched treatment has an S-4 pre or post hit when no "
    "exemption is applied. Not applicable when placebo_shift_days = 0."
)

LEGACY_PAIRS_BACKUP = "control_area_pairs.pre_issue4.parquet"

# Expected Arrow types for the parquet schema check (N10).
ARROW_TYPES = {
    "string": ("string", "large_string"),
    "int64": ("int64",),
    "int32": ("int32",),
    "Int32": ("int32",),
    "int8": ("int8",),
    "float64": ("double",),
    "bool": ("bool",),
    "datetime64[us]": ("timestamp[us]",),
}


def _json_safe(value, path, non_finite):
    """
    JSON-ready copy (N1, N2): numpy scalars and arrays, timestamps and
    paths converted explicitly; non-finite floats become null and their
    paths are listed in non_finite.
    """

    if isinstance(value, dict):
        return {
            str(key): _json_safe(item, f"{path}.{key}", non_finite)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _json_safe(item, f"{path}[{index}]", non_finite)
            for index, item in enumerate(value)
        ]
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist(), path, non_finite)
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        value = float(value)
        if not math.isfinite(value):
            non_finite.append({"path": path, "value": str(value)})
            return None
        return value
    if isinstance(value, (pd.Timestamp, datetime, np.datetime64)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    if value is None or isinstance(value, str):
        return value
    if value is pd.NA or value is pd.NaT:
        return None
    raise TypeError(f"diagnostics value at {path} is not serialisable: {type(value)}")


def diagnostics_json(diagnostics):
    non_finite = []
    safe = _json_safe(diagnostics, "$", non_finite)
    safe["non_finite"] = non_finite
    return json.dumps(safe, indent=2, allow_nan=False, ensure_ascii=False)


def _check_result_record(result, skipped):
    status = "skipped" if result.check_id in skipped else (
        "pass" if result.passed else "fail"
    )
    return {
        "status": status,
        "n_violations": result.n_violations,
        "examples": result.examples[:10],
        "seconds": round(result.seconds, 3),
    }


def build_diagnostics(state):
    """
    match_diagnostics.json content (blueprint §3.4) from the run state.
    Missing sections (failed runs) are omitted; `status` says which.
    """

    params = state.get("params")
    diagnostics = {"status": state.get("status", "failed")}
    if "error" in state:
        diagnostics["error"] = state["error"]

    provenance = dict(state.get("provenance", {}))
    provenance["python"] = sys.version
    provenance["argv"] = list(state.get("argv", []))
    diagnostics["provenance"] = provenance

    if params is not None:
        diagnostics["parameters"] = {
            "values": dataclasses.asdict(params),
            "cli_args": list(state.get("argv", [])),
            "locked": locked_parameters_metadata(),
            "conventions": CONVENTIONS,
            "contamination_review_share": CONTAMINATION_REVIEW_SHARE,
            "recheck_candidate_margin_m": RECHECK_CANDIDATE_MARGIN_M,
            "h16_post_definition": H16_POST_DEFINITION,
            "acceptance_amendments": ACCEPTANCE_AMENDMENTS,
        }

    coverage = state.get("coverage")
    if coverage is not None:
        diagnostics["coverage"] = {
            "crime_min": _format_seconds(coverage.crime_min_s),
            "crime_max": _format_seconds(coverage.crime_max_s),
            "darkness_min": _format_seconds(coverage.dark_min_s),
            "darkness_max": _format_seconds(coverage.dark_max_s),
        }
        eligible = state.get("treatments_eligible")
        if eligible is not None and len(eligible):
            diagnostics["coverage"]["eligible_created_min"] = (
                eligible["created_date"].min()
            )
            diagnostics["coverage"]["eligible_created_max"] = (
                eligible["created_date"].max()
            )

    sources = {}
    for name in ("raw_summary", "darkness_summary", "sites_universe",
                 "episode_summary"):
        sources.update(state.get(name) or {})
    if sources:
        diagnostics["universe"] = {
            key: sources[key] for key in DIAGNOSTICS_UNIVERSE_KEYS
            if key in sources
        }

    for key in ("attrition", "balance", "contamination", "recheck",
                "determinism"):
        if key in state:
            diagnostics[key] = state[key]

    match_summary = state.get("match_summary")
    if match_summary is not None:
        diagnostics["unmatched_reasons"] = {
            code: int((state["unmatched"]["reason_code"] == code).sum())
            for code, _ in REASON_CODES
        } if "unmatched" in state else None
        diagnostics["candidates"] = {
            "band": state.get("band_summary"),
            "matching": match_summary["candidates"],
            "reuse_blocked_matched": match_summary["reuse_blocked_matched"],
            "reuse_blocked_conflict_total": (
                match_summary["reuse_blocked_conflict_total"]
            ),
        }
        reuse = {
            "blocked_by_preregistration": match_summary["blocked_by_preregistration"],
            "n_control_sites": match_summary["n_control_sites"],
            "control_reuse_max": match_summary["control_reuse_max"],
            "control_reuse_mean": match_summary["control_reuse_mean"],
            "treatment_treatment_overlaps": state.get("pair_check_summary"),
        }
        pairs = state.get("pairs")
        if pairs is not None:
            per_site = pairs["control_site_id"].value_counts()
            reuse["uses_per_control_site_histogram"] = {
                int(uses): int(count)
                for uses, count in per_site.value_counts().sort_index().items()
            }
        diagnostics["reuse"] = reuse

    skipped = state.get("skipped_checks", set())
    diagnostics["hard_checks"] = {
        check_id: _check_result_record(result, skipped)
        for check_id, result in state.get("checks", {}).items()
    }

    runtime = state.get("runtime", {})
    diagnostics["runtime"] = {
        "steps": runtime,
        "total_seconds": round(sum(entry["seconds"] for entry in runtime.values()), 3),
        "peak_mb": max((entry["peak_mb"] for entry in runtime.values()), default=None),
    }

    if "outputs" in state:
        diagnostics["outputs"] = state["outputs"]

    return diagnostics


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(HASH_BLOCK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def preserve_legacy_pairs(out_dir):
    """
    X3: before the first write, keep a pre-Issue-4 pairs file as
    control_area_pairs.pre_issue4.parquet (copy, sha256 verified). An
    existing backup is never overwritten; a file that already has the new
    schema (control_site_id) is not a legacy file and is not copied.
    """

    import pyarrow.parquet as pq
    import shutil

    target = Path(out_dir) / PAIRS_FILENAME
    backup = Path(out_dir) / LEGACY_PAIRS_BACKUP

    if backup.exists():
        return {"action": "existing_backup_kept", "path": backup.as_posix(),
                "sha256": _sha256(backup)}
    if not target.exists():
        return {"action": "no_existing_pairs_file"}
    if "control_site_id" in pq.read_schema(target).names:
        return {"action": "existing_file_is_new_schema_not_backed_up"}

    shutil.copy2(target, backup)
    source_hash, backup_hash = _sha256(target), _sha256(backup)
    if source_hash != backup_hash:
        backup.unlink()
        raise HardCheckError(
            f"legacy backup failed: sha256 of {backup} differs from {target}"
        )
    print(f"Legacy pairs backed up to {backup} (sha256 {backup_hash[:16]}...)")
    return {"action": "backed_up", "path": backup.as_posix(), "sha256": backup_hash}


def _arrow_schema_problems(path, dtypes):
    """N10: the written parquet has the expected columns and Arrow types."""

    import pyarrow.parquet as pq

    schema = pq.read_schema(path)
    problems = []
    if schema.names != list(dtypes):
        problems.append(f"{path.name}: columns differ from the schema")
    for column, dtype in dtypes.items():
        if column in schema.names:
            arrow = str(schema.field(column).type)
            if arrow not in ARROW_TYPES[dtype]:
                problems.append(f"{path.name}: {column} is {arrow}, expected {dtype}")
    return problems


def write_outputs(params, pairs, sites_out, unmatched, state):
    """
    Step 20 (N7, N10, N15): every file is written to a temporary name in
    out_dir and schema-checked; then the parquet files are renamed with
    os.replace and the diagnostics file is renamed last, as the completion
    marker. A stale match_diagnostics.failed.json is removed on success.
    On a PermissionError the files already replaced are reported and the
    remaining temporary files are kept.
    """

    out_dir = Path(params.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    state["legacy_backup"] = preserve_legacy_pairs(out_dir)

    tables = (
        (PAIRS_FILENAME, pairs, PAIRS_DTYPES),
        (SITES_FILENAME, sites_out, SITES_DTYPES),
        (UNMATCHED_FILENAME, unmatched, UNMATCHED_DTYPES),
    )
    temporary = []
    problems = []
    for name, frame, dtypes in tables:
        path = out_dir / f"{name}.tmp"
        frame.to_parquet(path, engine="pyarrow", index=False)
        problems += _arrow_schema_problems(path, dtypes)
        temporary.append((path, out_dir / name))
    if problems:
        raise HardCheckError(
            "parquet schema check failed (temporary files kept): "
            + "; ".join(problems)
        )

    state["outputs"] = {
        "out_dir": out_dir.as_posix(),
        "legacy_backup": state["legacy_backup"],
        "files": {
            final.name: {"rows": len(frame), "sha256": _sha256(path)}
            for (path, final), (_, frame, _) in zip(temporary, tables)
        },
    }
    state["status"] = "completed"
    diagnostics_path = out_dir / f"{DIAGNOSTICS_FILENAME}.tmp"
    diagnostics_path.write_text(
        diagnostics_json(build_diagnostics(state)), encoding="utf-8"
    )
    temporary.append((diagnostics_path, out_dir / DIAGNOSTICS_FILENAME))

    replaced = []
    try:
        for path, final in temporary:
            os.replace(path, final)
            replaced.append(final.name)
    except PermissionError as error:
        raise HardCheckError(
            f"could not replace {error.filename} (is it open elsewhere?); "
            f"already replaced: {replaced or 'none'}; remaining temporary "
            f"files kept in {out_dir}"
        ) from error

    failed = out_dir / FAILED_DIAGNOSTICS_FILENAME
    if failed.exists():
        failed.unlink()

    return {final.name: final for _, final in temporary}


def print_summary(state, written):
    """Step 20: attrition table, reasons and the files written."""

    print("\nAttrition (steps 0-12):")
    print(
        pd.DataFrame(state["attrition"]).to_string(
            index=False,
            formatters={"remaining": "{:,}".format, "dropped": "{:,}".format},
        )
    )
    print("\nOutputs:")
    for name, info in state["outputs"]["files"].items():
        print(f"  {name}: {info['rows']:,} rows, sha256 {info['sha256'][:16]}...")
    print(f"  {DIAGNOSTICS_FILENAME}: {written[DIAGNOSTICS_FILENAME]}")
    backup = state["outputs"]["legacy_backup"]
    print(f"  legacy pairs backup: {backup['action']}")
    print(
        f"\nStage 7 completed: {len(state['pairs']):,} pairs written to "
        f"{state['outputs']['out_dir']}"
    )


def write_failed_diagnostics(state):
    """N8: every stop writes match_diagnostics.failed.json when out_dir is known."""

    params = state.get("params")
    if params is None:
        print("No failure diagnostics: parameters were not built.", file=sys.stderr)
        return None

    out_dir = Path(params.out_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / FAILED_DIAGNOSTICS_FILENAME
        path.write_text(diagnostics_json(build_diagnostics(state)), encoding="utf-8")
    except (OSError, TypeError, ValueError) as error:
        print(f"Could not write failure diagnostics: {error}", file=sys.stderr)
        return None
    print(f"Failure diagnostics written to {path}", file=sys.stderr)
    return path


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


def _check_h15(scales):
    """H15: both SDs are finite and > 0, and there is at least 1 eligible treatment."""

    start = time.perf_counter()
    problems = []

    if scales.n_treatments < 1:
        problems.append("no eligible treatments")

    for name, value in (("sd_100", scales.sd_100), ("sd_250", scales.sd_250)):
        if not (math.isfinite(value) and value > 0):
            problems.append(f"{name} must be finite and > 0 (got {value})")

    return CheckResult(
        check_id="H15",
        passed=not problems,
        n_violations=len(problems),
        examples=problems,
        seconds=time.perf_counter() - start,
    )


def _check_result(check_id, start, problems, violations):
    return CheckResult(
        check_id=check_id,
        passed=violations == 0,
        n_violations=int(violations),
        examples=problems,
        seconds=time.perf_counter() - start,
    )


def _pair_examples(pairs, mask, limit=10):
    return ", ".join(pairs.loc[mask, "pair_id"].head(limit).astype(str).tolist())


def _check_h5(pairs, complaints, sites):
    """
    H5: every matched treatment is first-of-episode, not at an artifact
    site and has a borough. Path (approved): pairs.treatment_key ->
    complaints.unique_key -> complaint row -> complaint site_idx -> sites
    row. treatment_episode_id and treatment_site_id are validated values,
    not join keys. Uses no treatment-rule output.
    """

    start = time.perf_counter()
    problems = []
    violations = 0

    row = pd.Index(complaints["unique_key"].to_numpy()).get_indexer(
        pairs["treatment_key"].to_numpy()
    )
    found = row >= 0
    safe = np.where(found, row, 0)

    site_idx = complaints["site_idx"].to_numpy()[safe]
    tests = (
        (~found, "treatment_key not found in complaints"),
        (found & ~complaints["is_first_of_episode"].to_numpy()[safe],
         "treatment is not the first complaint of its episode"),
        (found & sites["is_artifact"].to_numpy()[site_idx],
         "treatment site is an artifact"),
        (found & sites["borough"].isna().to_numpy()[site_idx],
         "treatment site has no borough"),
        (found & (pairs["treatment_episode_id"].to_numpy()
                  != complaints["episode_id"].to_numpy()[safe]),
         "treatment_episode_id differs from the complaint's episode"),
        (found & (pairs["treatment_site_id"].to_numpy(dtype=object)
                  != sites["site_id"].to_numpy(dtype=object)[site_idx]),
         "treatment_site_id differs from the complaint's site"),
    )
    for mask, message in tests:
        count = int(mask.sum())
        if count:
            violations += count
            problems.append(f"{count:,} {message}: {_pair_examples(pairs, mask)}")

    return _check_result("H5", start, problems, violations)


def _check_h6(params, pairs, coverage):
    """
    H6: pair windows inside coverage, mirroring treatment rules 1-3
    (U4): W in crime coverage; W +/- lag in darkness coverage; B =
    [b_start, b_end) in crime coverage, B evaluated as [b_start,
    b_end - 1] (A13). Placebo runs use the shifted windows.
    """

    start = time.perf_counter()
    problems = []
    violations = 0

    lag = params.report_lag_days * SECONDS_PER_DAY
    w_start = _to_seconds(pairs["window_start"])
    w_end = _to_seconds(pairs["window_end"])
    b_start = _to_seconds(pairs["baseline_start"])
    b_end = _to_seconds(pairs["baseline_end"])

    tests = (
        (w_start < coverage.crime_min_s, "W starts before crime coverage"),
        (w_end > coverage.crime_max_s, "W ends after crime coverage"),
        (w_start - lag < coverage.dark_min_s,
         "W start - lag before darkness coverage"),
        (w_end + lag > coverage.dark_max_s,
         "W end + lag after darkness coverage"),
        (b_start < coverage.crime_min_s, "B starts before crime coverage"),
        (b_end - 1 > coverage.crime_max_s, "B ends after crime coverage"),
    )
    for mask, message in tests:
        count = int(mask.sum())
        if count:
            violations += count
            problems.append(f"{count:,} {message}: {_pair_examples(pairs, mask)}")

    return _check_result("H6", start, problems, violations)


def _check_h7(params, pairs):
    """
    H7: distance recomputed from the pair coordinates with the explicit
    formula equals distance_m exactly (U5) and lies in
    [match_band_min_m - 1e-6, match_band_max_m + 1e-6]; boroughs are
    equal and present. Zone disjointness follows from d >= 500 m.
    """

    start = time.perf_counter()
    problems = []
    violations = 0

    distance = _distance(
        pairs["treatment_x_m"].to_numpy(), pairs["treatment_y_m"].to_numpy(),
        pairs["control_x_m"].to_numpy(), pairs["control_y_m"].to_numpy(),
    )
    t_borough = pairs["treatment_borough"]
    c_borough = pairs["control_borough"]

    tests = (
        (distance != pairs["distance_m"].to_numpy(),
         "distance_m differs from the recomputed distance"),
        (distance < params.match_band_min_m - 1e-6,
         f"closer than {params.match_band_min_m:g} m"),
        (distance > params.match_band_max_m + 1e-6,
         f"farther than {params.match_band_max_m:g} m"),
        ((t_borough.isna() | c_borough.isna()).to_numpy()
         | (t_borough.fillna("") != c_borough.fillna("")).to_numpy(),
         "treatment and control boroughs differ or are missing"),
    )
    for mask, message in tests:
        count = int(mask.sum())
        if count:
            violations += count
            problems.append(f"{count:,} {message}: {_pair_examples(pairs, mask)}")

    return _check_result("H7", start, problems, violations)


def _check_h8(params, pairs, scales):
    """
    H8: |log1p(treatment) - log1p(control)| <= caliper_sd * sd for both
    baselines, in the matcher's form (O5), no tolerance.
    """

    start = time.perf_counter()
    problems = []
    violations = 0

    for radius, sd in (("100m", scales.sd_100), ("250m", scales.sd_250)):
        t = np.log1p(pairs[f"treatment_base_{radius}"].to_numpy(np.float64))
        c = np.log1p(pairs[f"control_base_{radius}"].to_numpy(np.float64))
        mask = ~(np.abs(t - c) <= params.caliper_sd * sd)
        count = int(mask.sum())
        if count:
            violations += count
            problems.append(
                f"{count:,} pairs outside the {radius} caliper: "
                f"{_pair_examples(pairs, mask)}"
            )

    return _check_result("H8", start, problems, violations)


def _check_h9(params, pairs, treatments_eligible, complaints, sites):
    """
    H9 (approved interpretation): intervals rebuilt without the
    ReuseRegistry. Control uses: the pair's control site
    (control_site_id) and closed W (window_start/window_end). Treatment
    windows: every eligible treatment's closed W at its own site, the
    site found via treatment_key -> complaints -> site_idx.

    Fails when a control interval overlaps any other interval at its
    site (A2, closed), or, under reuse_policy = never, when a control site
    appears more than once. A control_site_id not in sites, or an
    eligible treatment_key not in complaints, is one violation per row;
    such rows are left out of the overlap sweep. Treatment-treatment
    overlaps are reported, not violations (U6).

    Returns (CheckResult, treatment-overlap summary).
    """

    start = time.perf_counter()
    problems = []
    violations = 0

    site_of = pd.Index(sites["site_id"].to_numpy(dtype=object))
    c_site = site_of.get_indexer(pairs["control_site_id"].to_numpy(dtype=object))
    row = pd.Index(complaints["unique_key"].to_numpy()).get_indexer(
        treatments_eligible["treatment_key"].to_numpy()
    )

    # Unresolved rows are violations and are left out of the sweep.
    c_found = c_site >= 0
    t_found = row >= 0
    if not c_found.all():
        violations += int((~c_found).sum())
        problems.append(
            f"{int((~c_found).sum()):,} control_site_id not found in sites: "
            f"{_pair_examples(pairs, ~c_found)}"
        )
    if not t_found.all():
        missing = treatments_eligible.loc[~t_found, "treatment_key"]
        violations += int(len(missing))
        problems.append(
            f"{len(missing):,} eligible treatment_key not found in complaints: "
            f"{', '.join(missing.head(10).astype(str).tolist())}"
        )
    t_site = complaints["site_idx"].to_numpy()[row[t_found]].astype(np.int64)

    intervals = pd.DataFrame({
        "site": np.concatenate([c_site[c_found], t_site]),
        "start": np.concatenate([
            _to_seconds(pairs["window_start"])[c_found],
            treatments_eligible["w_start_s"].to_numpy()[t_found],
        ]),
        "end": np.concatenate([
            _to_seconds(pairs["window_end"])[c_found],
            treatments_eligible["w_end_s"].to_numpy()[t_found],
        ]),
        "is_control": np.concatenate([
            np.ones(int(c_found.sum()), dtype=bool),
            np.zeros(int(t_found.sum()), dtype=bool),
        ]),
        "pair_row": np.concatenate([
            np.flatnonzero(c_found), np.full(int(t_found.sum()), -1)
        ]),
    }).sort_values(["site", "start", "end"], kind="mergesort")

    # Sorted by start within a site: interval i overlaps an earlier one
    # when the running maximum end before it is >= its start, and a later
    # one when the next start is <= its end.
    by_site = intervals.groupby("site", sort=False)
    prior_end = (
        by_site["end"].cummax().groupby(intervals["site"]).shift(1).to_numpy()
    )
    next_start = by_site["start"].shift(-1).to_numpy()
    start_s = intervals["start"].to_numpy()
    end_s = intervals["end"].to_numpy()
    overlaps = (prior_end >= start_s) | (next_start <= end_s)

    bad_rows = intervals["pair_row"].to_numpy()[
        overlaps & intervals["is_control"].to_numpy()
    ]
    if len(bad_rows):
        mask = np.zeros(len(pairs), dtype=bool)
        mask[bad_rows] = True
        violations += int(mask.sum())
        problems.append(
            f"{int(mask.sum()):,} control intervals overlap another interval "
            f"at their site: {_pair_examples(pairs, mask)}"
        )

    if params.reuse_policy == "never":
        mask = pairs["control_site_id"].duplicated(keep=False).to_numpy()
        if mask.any():
            violations += int(mask.sum())
            problems.append(
                f"{int(mask.sum()):,} pairs reuse a control site under "
                f"'never': {_pair_examples(pairs, mask)}"
            )

    # Descriptive: treatment-treatment overlaps among eligible treatments.
    treatments = intervals.loc[~intervals["is_control"]]
    n_pairs_overlap = 0
    sites_overlap = 0
    treatments_overlap = 0
    for _, group in treatments.groupby("site", sort=False):
        if len(group) < 2:
            continue
        a = group["start"].to_numpy()
        b = group["end"].to_numpy()
        both = (a[:, None] <= b[None, :]) & (b[:, None] >= a[None, :])
        np.fill_diagonal(both, False)
        n = int(both.sum()) // 2
        if n:
            n_pairs_overlap += n
            sites_overlap += 1
            treatments_overlap += int(both.any(axis=1).sum())

    tt_summary = {
        "n_treatment_pairs_overlapping": n_pairs_overlap,
        "n_sites_with_overlap": sites_overlap,
        "n_treatments_in_overlap": treatments_overlap,
    }
    return _check_result("H9", start, problems, violations), tt_summary


def _check_h12(pairs, include_balance):
    """
    H12: pair_id and treatment_key unique; treatment site != control site;
    required columns without nulls; columns, order and dtypes equal to
    PAIRS_DTYPES. Phase 1 (include_balance False, after step 14) excludes
    the bal_* columns; phase 2 (after step 15) checks the full schema (U2).
    """

    start = time.perf_counter()
    problems = []
    violations = 0

    expected = [
        column for column in PAIRS_DTYPES
        if include_balance or column not in BALANCE_COLUMNS
    ]

    if list(pairs.columns) != expected:
        missing = [column for column in expected if column not in pairs.columns]
        extra = [column for column in pairs.columns if column not in expected]
        violations += 1
        problems.append(
            f"columns differ from the schema (missing {missing}, extra {extra}, "
            f"or order)"
        )

    wrong = [
        f"{column}: {pairs[column].dtype} != {PAIRS_DTYPES[column]}"
        for column in expected
        if column in pairs.columns
        and str(pairs[column].dtype) != PAIRS_DTYPES[column]
    ]
    if wrong:
        violations += len(wrong)
        problems.append(f"{len(wrong)} wrong dtypes: {wrong[:10]}")

    nulls = {
        column: int(pairs[column].isna().sum())
        for column in expected
        if column in pairs.columns and column not in PAIRS_NULLABLE_COLUMNS
        and pairs[column].isna().any()
    }
    if nulls:
        violations += sum(nulls.values())
        problems.append(f"nulls in required columns: {nulls}")

    for column in ("pair_id", "treatment_key"):
        mask = pairs[column].duplicated(keep=False).to_numpy()
        if mask.any():
            violations += int(mask.sum())
            problems.append(
                f"{int(mask.sum()):,} duplicated {column}: "
                f"{_pair_examples(pairs, mask)}"
            )

    mask = (
        pairs["treatment_site_id"].astype(object).to_numpy()
        == pairs["control_site_id"].astype(object).to_numpy()
    )
    if mask.any():
        violations += int(mask.sum())
        problems.append(
            f"{int(mask.sum()):,} pairs with treatment site == control site: "
            f"{_pair_examples(pairs, mask)}"
        )

    return _check_result("H12", start, problems, violations)


def _check_h14(pairs):
    """
    H14: band >= clean >= caliper >= 1, and 0 <= reuse_blocked <=
    caliper - 1 (U3, from the O3 counting rule).
    """

    start = time.perf_counter()
    problems = []
    violations = 0

    band = pairs["n_candidates_band"].to_numpy()
    clean = pairs["n_candidates_clean"].to_numpy()
    caliper = pairs["n_candidates_caliper"].to_numpy()
    blocked = pairs["n_candidates_reuse_blocked"].to_numpy()

    tests = (
        (band < clean, "band < clean"),
        (clean < caliper, "clean < caliper"),
        (caliper < 1, "caliper < 1"),
        (blocked < 0, "reuse_blocked < 0"),
        (blocked > caliper - 1, "reuse_blocked > caliper - 1"),
    )
    for mask, message in tests:
        count = int(mask.sum())
        if count:
            violations += count
            problems.append(f"{count:,} pairs with {message}: {_pair_examples(pairs, mask)}")

    return _check_result("H14", start, problems, violations)


def _check_h13(n_s3_rows, treatments_all, pairs, unmatched, attrition):
    """
    H13: every Stage 3 treatment is accounted for exactly once (N3, N4).
    rows in pairs + rows in unmatched = n_s3_rows (read in step 2); no key
    in both; the union equals the Stage 3 keys; the 12-step attrition list
    starts at n_s3_rows, telescopes, ends at the pair count, and each
    step's dropped count equals the unmatched rows with that reason_step.
    All 12 reason codes appear in the list (count 0 allowed).
    """

    start = time.perf_counter()
    problems = []
    violations = 0

    def add(condition, message):
        nonlocal violations
        if condition:
            violations += 1
            problems.append(message)

    pair_keys = set(pairs["treatment_key"].tolist())
    unmatched_keys = set(unmatched["treatment_key"].tolist())
    stage3_keys = set(treatments_all["treatment_key"].tolist())

    add(len(pairs) + len(unmatched) != n_s3_rows,
        f"pairs {len(pairs):,} + unmatched {len(unmatched):,} != "
        f"Stage 3 rows {n_s3_rows:,}")
    add(bool(pair_keys & unmatched_keys),
        f"{len(pair_keys & unmatched_keys):,} keys in both pairs and unmatched")
    add(pair_keys | unmatched_keys != stage3_keys,
        "pairs and unmatched keys differ from the Stage 3 keys")
    add(len(unmatched_keys) != len(unmatched), "duplicated unmatched keys")

    add(attrition[0]["remaining"] != n_s3_rows,
        "attrition does not start at the Stage 3 row count")
    for previous, entry in zip(attrition, attrition[1:]):
        add(entry["remaining"] != previous["remaining"] - entry["dropped"],
            f"attrition step {entry['step']} does not telescope")
    add(attrition[-1]["remaining"] != len(pairs),
        "attrition does not end at the pair count")

    steps = unmatched["reason_step"].value_counts().to_dict()
    listed = [entry["rule"] for entry in attrition[1:]]
    add(listed != [code for code, _ in REASON_CODES],
        "attrition does not list the 12 reason codes in order")
    for entry in attrition[1:]:
        actual = int(steps.get(entry["step"], 0))
        add(actual != entry["dropped"],
            f"step {entry['step']} ({entry['rule']}): attrition dropped "
            f"{entry['dropped']:,}, unmatched rows {actual:,}")

    return _check_result("H13", start, problems, violations)


def _check_h16_post(params, pairs, complaints):
    """
    H16 after matching (N5; H16_POST_DEFINITION). Placebo runs: pair
    dates equal the real complaint dates minus the shift; every
    complaint's darkness interval is the Stage 7 rule applied to its
    unshifted dates; no matched treatment has an S-4 pre or post hit with
    no exemption applied. Canonical runs: not applicable.
    """

    start = time.perf_counter()
    if params.placebo_shift_days == 0:
        return _check_result(
            "H16_post", start,
            ["not applicable (placebo_shift_days = 0)"], 0,
        )

    problems = []
    violations = 0
    shift_s = params.placebo_shift_days * SECONDS_PER_DAY

    row = pd.Index(complaints["unique_key"].to_numpy()).get_indexer(
        pairs["treatment_key"].to_numpy()
    )
    found = row >= 0
    safe = np.where(found, row, 0)
    real_created = _to_seconds(complaints["created_date"])
    has_closed = complaints["closed_date"].notna().to_numpy()
    real_closed = np.zeros(len(complaints), dtype=np.int64)
    real_closed[has_closed] = _to_seconds(complaints.loc[has_closed, "closed_date"])

    tests = [
        (~found, "treatment_key not in complaints"),
        (found & (_to_seconds(pairs["created_date"])
                  != real_created[safe] - shift_s),
         "created_date is not the real date minus the shift"),
        (found & (_to_seconds(pairs["closed_date"])
                  != real_closed[safe] - shift_s),
         "closed_date is not the real date minus the shift"),
    ]

    duration_h = np.full(len(complaints), np.nan)
    duration_h[has_closed] = (real_closed[has_closed] - real_created[has_closed]) / 3600
    valid = (
        has_closed
        & (duration_h >= params.valid_min_duration_h)
        & (duration_h <= params.valid_max_duration_h)
    )
    start_s = real_created - params.report_lag_days * SECONDS_PER_DAY
    end_s = np.where(
        valid, real_closed,
        real_created + int(round(params.imputed_duration_hours * 3600)),
    )
    interval_wrong = (
        (complaints["dark_start_s"].to_numpy() != start_s)
        | (complaints["dark_end_s"].to_numpy() != end_s)
    )
    count = int(interval_wrong.sum())
    if count:
        violations += count
        problems.append(f"{count:,} complaints with darkness not built from real dates")

    tree = cKDTree(complaints[["x_m", "y_m"]].to_numpy(dtype=np.float64))
    centers = pairs[["treatment_x_m", "treatment_y_m"]].to_numpy(np.float64)
    c = _to_seconds(pairs["created_date"])
    closed = _to_seconds(pairs["closed_date"])
    w_end = _to_seconds(pairs["window_end"])
    event_s = params.event_window_days * SECONDS_PER_DAY
    s = complaints["dark_start_s"].to_numpy()
    e = complaints["dark_end_s"].to_numpy()
    pre = np.zeros(len(pairs), dtype=bool)
    post = np.zeros(len(pairs), dtype=bool)
    for owner, point, _ in _ball_candidates(
        tree, centers, params.treatment_clean_radius_m, RULE_QUERY_CHUNK
    ):
        pre[owner[(s[point] <= c[owner] - 1) & (e[point] >= c[owner] - event_s)]] = True
        post[owner[(s[point] <= w_end[owner]) & (e[point] >= closed[owner] + 1)]] = True
    tests += [
        (pre, "matched treatments with an S-4 pre hit and no exemption"),
        (post, "matched treatments with an S-4 post hit and no exemption"),
    ]

    for mask, message in tests:
        count = int(np.asarray(mask).sum())
        if count:
            violations += count
            problems.append(f"{count:,} {message}: {_pair_examples(pairs, mask)}")

    return _check_result("H16_post", start, problems, violations)


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
    "darkness" (H2, H17 after step 4), "sites" (H18 after step 5),
    "episodes" (H3, H4 after step 6), "scales" (H15 after step 11) and
    "pairs" (H5, H6, H7, H8, H9, H12 phase 1, H14 after step 14; H9's
    treatment-treatment overlap counts go into tables["summary"]) and
    "balance" (H12 phase 2 on the full schema after step 15),
    "recheck" (H10, H11 from step 18; skipped ones are recorded as such)
    and "accounting" (H13, H16_post after step 19).
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
    elif stage == "scales":
        results = [_check_h15(tables["scales"])]
    elif stage == "pairs":
        pairs = tables["pairs"]
        results = [
            _check_h5(pairs, tables["complaints"], tables["sites"]),
            _check_h6(params, pairs, tables["coverage"]),
            _check_h7(params, pairs),
            _check_h8(params, pairs, tables["scales"]),
        ]
        h9, overlap_summary = _check_h9(
            params, pairs, tables["treatments_eligible"],
            tables["complaints"], tables["sites"],
        )
        tables["summary"].update(overlap_summary)
        results += [
            h9,
            _check_h12(pairs, include_balance=False),
            _check_h14(pairs),
        ]
    elif stage == "balance":
        results = [_check_h12(tables["pairs"], include_balance=True)]
    elif stage == "recheck":
        results = [tables["h10"], tables["h11"]]
    elif stage == "accounting":
        results = [
            _check_h13(
                tables["n_s3_rows"], tables["treatments_all"],
                tables["pairs"], tables["unmatched"], tables["attrition"],
            ),
            _check_h16_post(params, tables["pairs"], tables["complaints"]),
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


def _stop(error, state):
    # The violations were already printed by _record.
    state["status"] = "failed"
    state["error"] = str(error).splitlines()[0]
    sys.stdout.flush()
    write_failed_diagnostics(state)
    print(
        f"\nStage 7 stopped: {str(error).splitlines()[0]}",
        file=sys.stderr,
    )
    return EXIT_HARD_CHECK_FAILED


def main(argv=None):
    runtime = {}
    checks = {}
    state = {
        "argv": list(sys.argv[1:] if argv is None else argv),
        "runtime": runtime,
        "checks": checks,
        "skipped_checks": set(),
    }

    try:
        return _run(argv, state)
    except Exception as error:
        # Unhandled exception: failure diagnostics, then the traceback
        # (exit 1).
        state["status"] = "failed"
        state["error"] = f"unhandled {type(error).__name__}: {error}"
        write_failed_diagnostics(state)
        raise


def _run(argv, state):
    runtime = state["runtime"]
    checks = state["checks"]

    try:
        with StageTimer("parse_args", runtime):
            params = parse_args(argv, checks)
    except HardCheckError as error:
        state["params"] = getattr(error, "params", None)
        return _stop(error, state)

    state["params"] = params

    _print_run_header(params)

    print("\n--- Provenance")
    with StageTimer("provenance", runtime):
        provenance = _collect_provenance()
    state["provenance"] = provenance

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
        return _stop(error, state)

    state.update(raw_summary=raw_summary, coverage=coverage, n_s3_rows=n_s3_rows)

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
        return _stop(error, state)

    print("\n--- Step 5: sites")
    state.update(darkness_summary=darkness_summary, coverage=coverage)

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
        return _stop(error, state)

    print("\n--- Step 6: episodes")
    state["sites_universe"] = sites_universe

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
        return _stop(error, state)

    state["episode_summary"] = episode_summary

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

    print("\n--- Step 8: treatment windows")
    with StageTimer("compute_windows", runtime):
        treatments_all = compute_windows(params, treatments_all)

    print("\n--- Step 9: treatment rules")
    with StageTimer("apply_treatment_rules", runtime):
        (
            treatments_all,
            treatments_eligible,
            rejected_rules,
            attrition,
        ) = apply_treatment_rules(
            params, treatments_all, complaints, sites, indexes, coverage
        )

    print("\n--- Step 10: treatment baselines")
    state.update(treatments_eligible=treatments_eligible, attrition=attrition)

    with StageTimer("compute_treatment_baselines", runtime):
        treatments_eligible, baseline_summary = compute_treatment_baselines(
            params, treatments_eligible, indexes, crime
        )

    print("\n--- Step 11: standardisation")
    with StageTimer("standardise", runtime):
        treatments_eligible, scales = standardise(params, treatments_eligible)

    print("\n--- Hard checks: scales")
    try:
        with StageTimer("checks_scales", runtime):
            run_hard_checks(params, "scales", checks, scales=scales)
    except HardCheckError as error:
        return _stop(error, state)

    print("\n--- Step 12: reuse registry")
    with StageTimer("init_reuse_registry", runtime):
        registry = init_reuse_registry(params, treatments_eligible)

    print("\n--- Step 13: band candidates")
    with StageTimer("build_band_candidates", runtime):
        indexes, band_summary = build_band_candidates(
            params, treatments_eligible, sites, indexes
        )

    print("\n--- Step 13: matching")
    with StageTimer("match_treatments", runtime):
        match, match_summary = match_treatments(
            params, treatments_eligible, sites, indexes, scales, registry
        )

    state.update(band_summary=band_summary, match_summary=match_summary)

    # Band lists are only needed by matching.
    indexes.band_sites = indexes.band_dist = indexes.band_offsets = None

    print("\n--- Step 14: pair assembly")
    with StageTimer("assemble_pairs", runtime):
        pairs = assemble_pairs(
            params, match, treatments_eligible, sites, registry
        )

    print("\n--- Hard checks: pairs")
    pair_check_summary = {}
    try:
        with StageTimer("checks_pairs", runtime):
            run_hard_checks(
                params,
                "pairs",
                checks,
                pairs=pairs,
                treatments_eligible=treatments_eligible,
                complaints=complaints,
                sites=sites,
                coverage=coverage,
                scales=scales,
                summary=pair_check_summary,
            )
    except HardCheckError as error:
        return _stop(error, state)

    print(
        "H9 reported (not violations): treatment-treatment overlaps "
        f"{pair_check_summary['n_treatment_pairs_overlapping']:,} interval "
        f"pairs at {pair_check_summary['n_sites_with_overlap']:,} sites, "
        f"{pair_check_summary['n_treatments_in_overlap']:,} treatments"
    )

    print("\n--- Step 15: balance")
    with StageTimer("compute_balance", runtime):
        pairs, balance = compute_balance(
            params, pairs, treatments_eligible, episodes, complaints, crime,
            indexes,
        )

    print("\n--- Hard checks: balance")
    try:
        with StageTimer("checks_balance", runtime):
            run_hard_checks(params, "balance", checks, pairs=pairs)
    except HardCheckError as error:
        return _stop(error, state)

    print("\n--- Step 16: contamination")
    with StageTimer("compute_contamination", runtime):
        contamination = compute_contamination(
            params, pairs, complaints, episodes, indexes, episode_summary
        )

    state.update(
        treatments_eligible=treatments_eligible, pairs=pairs,
        pair_check_summary=pair_check_summary, balance=balance,
        contamination=contamination,
    )

    # Determinism (N6): hash of the sorted pair_id list and the order
    # hash; with --check-determinism, a second steps 12-13 pass.
    determinism = {
        "pair_list_sha256": pair_list_hash(pairs["pair_id"]),
        "order_hash": match.order_hash,
        "check_determinism": params.check_determinism,
        "second_pass": None,
    }
    print(
        f"\nPair-list hash {determinism['pair_list_sha256'][:16]}, "
        f"order hash {match.order_hash[:16]}"
    )
    if params.check_determinism:
        print("\n--- Determinism check (second steps 12-13 pass)")
        with StageTimer("check_determinism", runtime):
            determinism["second_pass"] = check_determinism(
                params, treatments_eligible, sites, indexes, scales, match
            )
    state["determinism"] = determinism
    if params.check_determinism and not determinism["second_pass"]["identical"]:
        return _stop(
            HardCheckError("determinism check failed: second pass differs"),
            state,
        )

    # The matching structures are not needed any more (EP memory plan).
    del indexes, crime, registry
    gc.collect()

    print("\n--- Step 18: independent recheck")
    with StageTimer("independent_recheck", runtime):
        if params.skip_recheck:
            h10, h11, recheck = skipped_recheck(params, pairs)
            state["skipped_checks"] |= {"H10", "H11"}
        else:
            h10, h11, recheck = independent_recheck(params, pairs)
    state["recheck"] = recheck
    gc.collect()

    print("\n--- Hard checks: recheck")
    try:
        with StageTimer("checks_recheck", runtime):
            run_hard_checks(params, "recheck", checks, h10=h10, h11=h11)
    except HardCheckError as error:
        return _stop(error, state)

    print("\n--- Step 19: unmatched treatments and site roles")
    with StageTimer("build_unmatched", runtime):
        unmatched = build_unmatched(
            treatments_all, treatments_eligible, match, pairs, sites
        )
        sites_out = assemble_sites(sites, pairs)
        attrition_all = full_attrition(attrition, match_summary)
    state.update(unmatched=unmatched, attrition=attrition_all)

    print("\n--- Hard checks: accounting")
    try:
        with StageTimer("checks_accounting", runtime):
            run_hard_checks(
                params,
                "accounting",
                checks,
                n_s3_rows=n_s3_rows,
                treatments_all=treatments_all,
                pairs=pairs,
                unmatched=unmatched,
                attrition=attrition_all,
                complaints=complaints,
            )
    except HardCheckError as error:
        return _stop(error, state)

    print("\n--- Step 20: outputs")
    try:
        with StageTimer("write_outputs", runtime):
            written = write_outputs(params, pairs, sites_out, unmatched, state)
    except HardCheckError as error:
        return _stop(error, state)

    print_summary(state, written)

    return EXIT_COMPLETED


if __name__ == "__main__":
    sys.exit(main())
