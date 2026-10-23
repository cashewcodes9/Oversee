"""Pipeline orchestration: one function per user-facing ETL action.

The CLI is a thin layer over these functions, so they can also be called from
tests, a scheduler or a notebook.
"""

import logging
from collections import Counter
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from oversee.db import get_engine
from oversee.etl import aggregate, extract, load, transform
from oversee.etl.lake import Lake, LakeFile, open_lake
from oversee.etl.regions import BoundingBox

AIS_SOURCE = "marinecadastre_ais"
WPI_SOURCE = "wpi"

logger = logging.getLogger(__name__)


def load_ports_file(path: Path, force: bool = False) -> int | None:
    """Load a World Port Index CSV; returns ports loaded, or None if this file was loaded before."""
    sha, size = extract.file_fingerprint(path)
    if not force and load.already_loaded(WPI_SOURCE, sha):
        logger.info("file already loaded, skipping", extra={"path": str(path)})
        return None
    with load.source_run(WPI_SOURCE, path.name, sha, size) as run:
        raw = extract.read_wpi(path)
        ports, counts = transform.clean_wpi(raw)
        run["rows_read"] = len(raw)
        run["rows_loaded"] = load.load_ports(ports)
        load.load_quality_log(run["run_id"], counts)
    return run["rows_loaded"]


def load_ais_file(path: Path, bbox: BoundingBox | None, force: bool = False) -> int | None:
    """Load one MarineCadastre AIS file in chunks.

    Row-level rules and the region filter run per chunk, so only rows inside the
    region are held in memory; rules that compare reports of one vessel then run on
    the filtered rows. Returns positions inserted, or None if the file was loaded before.
    """
    sha, size = extract.file_fingerprint(path)
    if not force and load.already_loaded(AIS_SOURCE, sha):
        logger.info("file already loaded, skipping", extra={"path": str(path)})
        return None

    with load.source_run(AIS_SOURCE, path.name, sha, size) as run:
        _load_ais_chunks(run, extract.read_ais_chunks(path), bbox)
    return run["rows_loaded"]


def _load_ais_chunks(
    run: dict,
    chunks: Iterable[pd.DataFrame],
    bbox: BoundingBox | None,
    rows_read: int | None = None,
    pre_filtered: int = 0,
) -> None:
    """Clean and load AIS chunks inside an open source_run.

    rows_read and pre_filtered are for sources that already dropped rows outside the
    region (the lake); those rows are counted as outside_region.
    """
    kept: list[pd.DataFrame] = []
    counts: Counter = Counter()
    if pre_filtered:
        counts["outside_region"] = pre_filtered
    seen = 0
    for chunk in chunks:
        seen += len(chunk)
        rows, chunk_counts = transform.validate_positions(transform.normalise_ais(chunk), bbox)
        kept.append(rows)
        counts.update(chunk_counts)
        logger.debug("chunk processed", extra={"rows_read": seen, "kept": len(rows)})

    if kept:
        positions, track_counts = transform.clean_tracks(pd.concat(kept, ignore_index=True))
        counts.update(track_counts)
    else:
        columns = dict.fromkeys(transform.POSITION_COLUMNS + transform.VESSEL_COLUMNS)
        positions = pd.DataFrame(columns=list(columns))
    vessels, positions = transform.split_vessels(positions)

    run["rows_read"] = rows_read if rows_read is not None else seen
    run["rows_loaded"] = load.load_ais(vessels, positions, run["run_id"])
    load.load_quality_log(run["run_id"], counts)
    logger.info(
        "AIS file loaded",
        extra={"vessels": len(vessels), "positions_kept": len(positions), **counts},
    )


def ingest_ais_day(lake: Lake, day: date, delete_raw: bool = False) -> LakeFile:
    """Make sure one AIS day is in the lake: download and convert it unless it is there.

    A day already in the lake is not downloaded again.
    """
    if stored := lake.ais_day(day):
        logger.info("AIS day already in lake", extra={"uri": stored.uri})
        return stored
    path = extract.download_ais_day(day)
    stored = lake.ingest_ais_file(path, day)
    if delete_raw:
        path.unlink()
        logger.info("raw file deleted", extra={"path": str(path)})
    return stored


def load_ais_day(
    lake: Lake, day: date, bbox: BoundingBox | None, force: bool = False
) -> int | None:
    """Load one AIS day from the lake into PostgreSQL, filtering to bbox in DuckDB.

    Returns positions inserted, or None if the source file was loaded before (from
    the lake or directly with load_ais_file).
    """
    stored = lake.ais_day(day)
    if stored is None:
        raise FileNotFoundError(f"AIS day {day} is not in the lake; run lake-ingest first")
    if not force and load.already_loaded(AIS_SOURCE, stored.source_sha256):
        logger.info("file already loaded, skipping", extra={"uri": stored.uri})
        return None

    with load.source_run(
        AIS_SOURCE, Path(stored.uri).name, stored.source_sha256, stored.source_bytes
    ) as run:
        chunks = list(lake.read_ais_day(day, bbox))
        in_region = sum(len(c) for c in chunks)
        _load_ais_chunks(
            run, chunks, bbox, rows_read=stored.rows, pre_filtered=stored.rows - in_region
        )
    return run["rows_loaded"]


def ports_loaded() -> int:
    with get_engine().connect() as conn:
        return conn.execute(text("SELECT count(*) FROM ports")).scalar_one()


def rebuild_aggregates(params: aggregate.AggregateParams = aggregate.DEFAULT_PARAMS) -> dict:
    """Rebuild the derived tables with `dbt build`, recorded as one source_runs row."""
    with load.source_run("dbt") as run:
        counts = aggregate.run(params)
        run["rows_loaded"] = counts["port_calls"]
        logger.info("derived tables rebuilt", extra=counts)
    return counts


def run_window(
    start: date,
    end: date,
    bbox: BoundingBox | None,
    refresh_ports: bool = False,
    delete_raw: bool = False,
) -> None:
    """End to end: ports, then each AIS day in [start, end] through the lake, then aggregates.

    Each day is downloaded, written to the lake, (optionally) deleted from data/raw,
    then loaded from the lake for the region, before the next day starts.
    """
    if refresh_ports or ports_loaded() == 0:
        load_ports_file(extract.download_wpi(refresh=refresh_ports))

    lake = open_lake()
    day = start
    while day <= end:
        ingest_ais_day(lake, day, delete_raw=delete_raw)
        load_ais_day(lake, day, bbox)
        day += timedelta(days=1)

    rebuild_aggregates()
