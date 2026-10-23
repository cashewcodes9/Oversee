"""Pipeline orchestration: one function per user-facing ETL action.

The CLI is a thin layer over these functions, so they can also be called from
tests, a scheduler or a notebook.
"""

import logging
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from oversee.db import get_engine
from oversee.etl import aggregate, extract, load, transform
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
        kept: list[pd.DataFrame] = []
        counts: Counter = Counter()
        rows_read = 0
        for chunk in extract.read_ais_chunks(path):
            rows_read += len(chunk)
            rows, chunk_counts = transform.validate_positions(transform.normalise_ais(chunk), bbox)
            kept.append(rows)
            counts.update(chunk_counts)
            logger.debug("chunk processed", extra={"rows_read": rows_read, "kept": len(rows)})

        positions, track_counts = transform.clean_tracks(pd.concat(kept, ignore_index=True))
        counts.update(track_counts)
        vessels, positions = transform.split_vessels(positions)

        run["rows_read"] = rows_read
        run["rows_loaded"] = load.load_ais(vessels, positions, run["run_id"])
        load.load_quality_log(run["run_id"], counts)
        logger.info(
            "AIS file loaded",
            extra={"vessels": len(vessels), "positions_kept": len(positions), **counts},
        )
    return run["rows_loaded"]


def ports_loaded() -> int:
    with get_engine().connect() as conn:
        return conn.execute(text("SELECT count(*) FROM ports")).scalar_one()


def rebuild_aggregates(params: aggregate.AggregateParams = aggregate.DEFAULT_PARAMS) -> dict:
    with load.source_run("aggregate") as run:
        with get_engine().begin() as conn:
            counts = aggregate.run(conn, params)
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
    """End to end: ports, then each AIS day in [start, end], then aggregates.

    Each day is downloaded, loaded and (optionally) deleted before the next, so
    disk use stays at about one daily file.
    """
    if refresh_ports or ports_loaded() == 0:
        load_ports_file(extract.download_wpi(refresh=refresh_ports))

    day = start
    while day <= end:
        path = extract.download_ais_day(day)
        load_ais_file(path, bbox)
        if delete_raw:
            path.unlink()
            logger.info("raw file deleted", extra={"path": str(path)})
        day += timedelta(days=1)

    rebuild_aggregates()
