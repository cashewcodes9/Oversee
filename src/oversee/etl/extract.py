"""Extract step: download and read raw source files."""

import hashlib
import logging
import shutil
import urllib.request
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pandas as pd

RAW_DIR = Path("data/raw")

# MarineCadastre publishes one zstd-compressed CSV per day (index: .../csv2/csv2025/index.html)
MARINECADASTRE_AIS_URL = (
    "https://noaaocm.blob.core.windows.net/ais/csv2/csv{year}/ais-{day}.csv.zst"
)
WPI_URL = "https://msi.nga.mil/api/publications/world-port-index?output=csv"

AIS_CHUNK_ROWS = 500_000
AIS_TEXT_COLUMNS = {"imo": "string", "call_sign": "string", "vessel_name": "string"}

logger = logging.getLogger(__name__)


def download(url: str, target: Path, refresh: bool = False) -> Path:
    """Download to target via a .part file, so an interrupted download never looks complete."""
    if target.exists() and not refresh:
        logger.info("using cached file", extra={"path": str(target)})
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    logger.info("downloading", extra={"url": url})
    with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out, length=1 << 20)
    partial.replace(target)
    logger.info("downloaded", extra={"path": str(target), "bytes": target.stat().st_size})
    return target


def download_ais_day(day: date, raw_dir: Path = RAW_DIR) -> Path:
    """Download one day of MarineCadastre AIS data (skips if already present)."""
    url = MARINECADASTRE_AIS_URL.format(year=day.year, day=day.isoformat())
    return download(url, raw_dir / f"ais-{day.isoformat()}.csv.zst")


def download_wpi(raw_dir: Path = RAW_DIR, refresh: bool = False) -> Path:
    return download(WPI_URL, raw_dir / "wpi.csv", refresh=refresh)


def read_ais_chunks(path: Path, chunk_rows: int = AIS_CHUNK_ROWS) -> Iterator[pd.DataFrame]:
    """Stream a MarineCadastre AIS CSV (plain or .zst) in chunks to keep memory flat."""
    yield from pd.read_csv(path, chunksize=chunk_rows, dtype=AIS_TEXT_COLUMNS, low_memory=False)


def read_wpi(path: Path) -> pd.DataFrame:
    """Read the World Port Index CSV export from NGA."""
    return pd.read_csv(path, dtype={"unloCode": "string"}, low_memory=False)


def file_fingerprint(path: Path) -> tuple[str, int]:
    """SHA-256 and size of a file, read in 1 MB blocks."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1 << 20):
            digest.update(block)
    return digest.hexdigest(), path.stat().st_size
