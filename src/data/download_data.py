from pathlib import Path
import requests

RAW_DIR = Path("data/raw")
RAW_DIR.mkdir(parents=True, exist_ok=True)

# NYC 311 Street Light Complaints
STREETLIGHT_URL = (
    "https://data.cityofnewyork.us/resource/fhrw-4uyv.csv?"
    "$limit=500000&descriptor=Street%20Light%20Out"
)

# NYPD Complaint Data
CRIME_URL = (
    "https://data.cityofnewyork.us/resource/qgea-i56i.csv?"
    "$limit=500000"
)


def download_file(url, output_path):
    print(f"Downloading -> {output_path}")

    response = requests.get(url, stream=True)
    response.raise_for_status()

    with open(output_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    print(f"Saved -> {output_path}")


def main():
    streetlight_file = RAW_DIR / "streetlight_complaints.csv"
    crime_file = RAW_DIR / "nypd_crime.csv"

    download_file(STREETLIGHT_URL, streetlight_file)
    download_file(CRIME_URL, crime_file)

    print("\nDone.")
    print(streetlight_file)
    print(crime_file)


if __name__ == "__main__":
    main()