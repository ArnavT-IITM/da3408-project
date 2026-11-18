"""Download the public Kaggle archive once; retain only the three required CSVs."""

import json
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

DEST = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/raw")
URL = "https://www.kaggle.com/api/v1/datasets/download/rohanrao/air-quality-data-in-india"


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    files = ["station_hour.csv", "stations.csv", "city_day.csv"]
    if all((DEST / name).exists() for name in files):
        print("Raw CSVs already exist; ingestion will verify their hashes.")
        return
    request = urllib.request.Request(URL, headers={"User-Agent": "kaggle-api/1.7.4"})
    with tempfile.TemporaryFile() as archive:
        with urllib.request.urlopen(request, timeout=120) as response:
            while chunk := response.read(1024 * 1024):
                archive.write(chunk)
        archive.seek(0)
        with zipfile.ZipFile(archive) as zipped:
            for name in files:
                # Fixed member names prevent path traversal from an archive.
                with zipped.open(name) as source, (DEST / name).open("wb") as target:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
    (DEST / "source.json").write_text(
        json.dumps(
            {
                "url": URL,
                "publisher": "Rohan Rao (Vopani), CPCB-derived measurements",
                "dataset": "Air Quality Data in India (2015–2020)",
                "license": "CC0 (dataset metadata)",
                "files": files,
            },
            indent=2,
        )
    )
    print(f"Downloaded {', '.join(files)} to {DEST}")


if __name__ == "__main__":
    main()
