"""
Dataset profile: which data the pipeline reads, where it writes, and the
region-specific constants (bounding box, projected CRS, analysis start).

Select with the environment variable LIGHTSAFE_PROFILE:

    nyc                  (default) the real NYC 311 / NYPD pipeline, unchanged paths
    karnataka_synthetic  the synthetic Karnataka demonstration dataset; it reads
                         data/synthetic/raw and writes data/synthetic/processed
                         and outputs/synthetic, so NYC artifacts are never touched

Every path is relative to the repository root (the pipeline is run from there).
"""
import os

NAME = os.getenv("LIGHTSAFE_PROFILE", "nyc").strip().lower()

_PROFILES = {
    "nyc": dict(
        raw_dir="data/raw",
        processed_dir="data/processed",
        outputs_dir="outputs",
        crime_raw_file="nypd_crime.csv",
        bbox=(40.49, 40.92, -74.26, -73.69),           # lat_min, lat_max, lon_min, lon_max
        crs="EPSG:32118",                              # NAD83 / New York Long Island, metres
        analysis_start="2019-11-01",
        tau_effect="net_post",                         # Stage 11 estimate that feeds the Stage 12 priority index
        baseline_days=365,                             # Stage 7 pre-treatment crime baseline window
        region_label="New York City",
        synthetic=False,
    ),
    "karnataka_synthetic": dict(
        raw_dir="data/synthetic/raw",
        processed_dir="data/synthetic/processed",
        outputs_dir="outputs/synthetic",
        crime_raw_file="nypd_crime.csv",               # same schema; file is synthetic_crime.csv renamed
        bbox=(11.5, 18.5, 74.0, 78.6),                 # Karnataka box used by the generator's validation
        crs="EPSG:32643",                              # WGS 84 / UTM 43N, metres; covers all 7 cities
        analysis_start="2025-01-01",
        tau_effect="direct_during",                    # effect of the dark period itself (what a repair removes)
        baseline_days=90,                              # only one year of data, so a shorter baseline
        region_label="Karnataka (synthetic)",
        synthetic=True,
    ),
}

if NAME not in _PROFILES:
    raise ValueError(f"LIGHTSAFE_PROFILE={NAME!r}; expected one of {sorted(_PROFILES)}")

_P = _PROFILES[NAME]
RAW_DIR = _P["raw_dir"]
PROCESSED_DIR = _P["processed_dir"]
OUTPUTS_DIR = _P["outputs_dir"]
CRIME_RAW_FILE = _P["crime_raw_file"]
BBOX = _P["bbox"]
CRS = _P["crs"]
ANALYSIS_START = _P["analysis_start"]
BASELINE_DAYS = _P["baseline_days"]
TAU_EFFECT = _P["tau_effect"]
REGION_LABEL = _P["region_label"]
IS_SYNTHETIC = _P["synthetic"]
