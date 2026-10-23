"""Extract step: download and read raw source files."""

import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

RAW_DIR = Path("data/raw")

# MarineCadastre publishes one zipped CSV per day. Verify the URL pattern for
# the chosen year on https://hub.marinecadastre.gov before relying on it.
MARINECADASTRE_AIS_URL = "https://coast.noaa.gov/htdata/CMSP/AISDataHandler/{y}/AIS_{y}_{m:02d}_{d:02d}.zip"


def download_ais_day(day: date, raw_dir: Path = RAW_DIR) -> Path:
    """Download one day of MarineCadastre AIS data (skips if already present)."""
    url = MARINECADASTRE_AIS_URL.format(y=day.year, m=day.month, d=day.day)
    target = raw_dir / Path(url).name
    if not target.exists():
        raw_dir.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(url, target)
    return target


def read_ais(path: Path) -> pd.DataFrame:
    """Read a MarineCadastre AIS CSV (plain or zipped)."""
    return pd.read_csv(path, low_memory=False)


def read_wpi(path: Path) -> pd.DataFrame:
    """Read the World Port Index CSV export from NGA."""
    return pd.read_csv(path, low_memory=False)
