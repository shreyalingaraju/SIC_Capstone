import argparse
import csv
import io
import os
import time
from pathlib import Path

import requests

RAW_DIR = Path("data/raw")
RAW_DIR.mkdir(parents=True, exist_ok=True)

SODA_BASE = "https://data.cityofnewyork.us/resource"

PAGE_SIZE = 50_000
REQUEST_TIMEOUT = 300
MAX_RETRIES = 5

# Optional Socrata app token to avoid anonymous throttling.
APP_TOKEN = os.environ.get("SODA_APP_TOKEN")

# Earliest crime date required by the analysis.
# Earliest outage is 2020-01-01; the event study looks back 35 days
# and the causal panel 14 days. Keep in sync with
# src/data/clean_crime.py ANALYSIS_START.
ANALYSIS_START = "2019-11-01T00:00:00"

# NYC 311 Street Light Complaints
STREETLIGHT_DATASET = "fhrw-4uyv"
STREETLIGHT_WHERE = "descriptor = 'Street Light Out'"

# NYPD Complaint Data.
# Historic covers complaints reported through the previous year;
# Current (Year To Date) covers the current year.
CRIME_SOURCES = [
    ("historic", "qgea-i56i"),
    ("current_ytd", "5uac-w243"),
]

CRIME_WHERE = f"cmplnt_fr_dt >= '{ANALYSIS_START}'"

# Only the columns used anywhere in the pipeline or notebooks.
CRIME_COLUMNS = [
    "cmplnt_num",
    "cmplnt_fr_dt",
    "cmplnt_fr_tm",
    "ky_cd",
    "ofns_desc",
    "law_cat_cd",
    "latitude",
    "longitude",
]


def soda_get(dataset, params):
    """GET a Socrata endpoint with retries and exponential backoff."""

    url = f"{SODA_BASE}/{dataset}.csv"
    headers = {"X-App-Token": APP_TOKEN} if APP_TOKEN else {}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            response.encoding = "utf-8"
            return response.text

        except requests.RequestException as error:
            if attempt == MAX_RETRIES:
                raise

            wait = 2 ** attempt
            print(f"  Request failed ({error}); retry {attempt} in {wait}s")
            time.sleep(wait)


def soda_count(dataset, where):
    """Number of records matching the filter on the server."""

    text = soda_get(
        dataset,
        {"$select": "count(*) AS n", "$where": where},
    )

    rows = list(csv.reader(io.StringIO(text)))

    return int(rows[1][0])


def download_paged(dataset, where, writer, write_header, select=None):
    """
    Download every record matching `where` in pages of PAGE_SIZE.

    Pages are parsed with the csv module so quoted newlines are
    handled and the row count is exact. Returns the number of data
    rows written.
    """

    offset = 0
    total = 0

    while True:
        params = {
            "$where": where,
            "$order": ":id",
            "$limit": PAGE_SIZE,
            "$offset": offset,
        }

        if select:
            params["$select"] = ",".join(select)

        rows = list(csv.reader(io.StringIO(soda_get(dataset, params))))

        header, data = rows[0], rows[1:]

        if write_header:
            writer.writerow(header)
            write_header = False

        writer.writerows(data)

        total += len(data)
        offset += PAGE_SIZE

        print(f"  {dataset}: {total:,} rows")

        if len(data) < PAGE_SIZE:
            return total


def download_dataset(sources, where, output_path, select=None):
    """
    Download one or more Socrata datasets into a single CSV.

    Each source is verified against the server-side count. The file
    is written to a temporary path and only replaces `output_path`
    once every source has downloaded completely.
    """

    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")

    print(f"Downloading -> {output_path}")

    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        write_header = True

        for name, dataset in sources:
            expected = soda_count(dataset, where)

            print(f"{name} ({dataset}): {expected:,} records expected")

            received = download_paged(
                dataset,
                where,
                writer,
                write_header,
                select=select,
            )

            write_header = False

            if received != expected:
                raise RuntimeError(
                    f"{name} ({dataset}): downloaded {received:,} rows "
                    f"but the server reports {expected:,}"
                )

    os.replace(tmp_path, output_path)

    print(f"Saved -> {output_path}")


def download_crime():
    download_dataset(
        CRIME_SOURCES,
        CRIME_WHERE,
        RAW_DIR / "nypd_crime.csv",
        select=CRIME_COLUMNS,
    )


def download_streetlights():
    download_dataset(
        [("streetlights", STREETLIGHT_DATASET)],
        STREETLIGHT_WHERE,
        RAW_DIR / "streetlight_complaints.csv",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        choices=["crime", "streetlights", "all"],
        default="crime",
        help="Streetlights are only refreshed when requested explicitly.",
    )
    args = parser.parse_args()

    if args.dataset in ("streetlights", "all"):
        download_streetlights()

    if args.dataset in ("crime", "all"):
        download_crime()

    print("\nDone.")


if __name__ == "__main__":
    main()
