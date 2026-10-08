"""
Prepare the raw inputs of the 'karnataka_synthetic' profile.

Copies the v2 synthetic CSVs into data/synthetic/raw under the file names the
pipeline expects (streetlight_complaints.csv, nypd_crime.csv) and adds the one
NYC-schema column the matching stage reads but the synthetic data does not
carry, `police_precinct`, set to the ward id (the ward is the synthetic stand-in
for an administrative zone). Nothing else is altered.

    python scripts/synthetic/prepare_raw.py
"""
import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.features.ward_context import assign_ward, load_wards  # noqa: E402

SRC = ROOT / "LightSafe_Synthetic_Karnataka_Data" / "v2" / "data"
DST = ROOT / "data" / "synthetic" / "raw"


def main() -> int:
    DST.mkdir(parents=True, exist_ok=True)
    wards = load_wards(SRC / "synthetic_wards.csv")
    sl = pd.read_csv(SRC / "synthetic_streetlights.csv", dtype={"unique_key": str})
    sl["police_precinct"] = assign_ward(wards, sl["latitude"], sl["longitude"], sl["borough"])
    sl.to_csv(DST / "streetlight_complaints.csv", index=False, lineterminator="\n")
    shutil.copyfile(SRC / "synthetic_crime.csv", DST / "nypd_crime.csv")
    shutil.copyfile(SRC / "synthetic_wards.csv", DST / "synthetic_wards.csv")
    print(f"streetlight_complaints.csv: {len(sl)} rows; nypd_crime.csv copied; wards: {len(wards)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
