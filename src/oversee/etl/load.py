"""Load step: write cleaned frames to PostgreSQL and record pipeline runs."""

import logging
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager

import pandas as pd
from sqlalchemy import Connection, text

from oversee.db import get_engine
from oversee.log import run_id_var

logger = logging.getLogger(__name__)


@contextmanager
def source_run(
    source: str,
    file_name: str | None = None,
    file_sha256: str | None = None,
    file_bytes: int | None = None,
) -> Iterator[dict]:
    """Record a pipeline run in source_runs; callers fill in row counts on the yielded dict.

    While the run is active, every log record carries its run_id.
    """
    with get_engine().begin() as conn:
        run_id = conn.execute(
            text(
                """
                INSERT INTO source_runs (source, file_name, file_sha256, file_bytes)
                VALUES (:source, :file_name, :sha, :bytes)
                RETURNING run_id
                """
            ),
            {"source": source, "file_name": file_name, "sha": file_sha256, "bytes": file_bytes},
        ).scalar_one()

    token = run_id_var.set(run_id)
    started = time.perf_counter()
    logger.info("run started", extra={"source": source, "file_name": file_name})
    stats = {"run_id": run_id, "rows_read": None, "rows_loaded": None}
    try:
        yield stats
    except Exception as exc:
        _finish_run(run_id, "failed", stats, error=str(exc))
        logger.exception("run failed", extra={"source": source})
        raise
    else:
        _finish_run(run_id, "success", stats)
        logger.info(
            "run finished",
            extra={
                "source": source,
                "rows_read": stats["rows_read"],
                "rows_loaded": stats["rows_loaded"],
                "duration_s": round(time.perf_counter() - started, 2),
            },
        )
    finally:
        run_id_var.reset(token)


def _finish_run(run_id: int, status: str, stats: dict, error: str | None = None) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                UPDATE source_runs
                SET finished_at = now(), status = :status, rows_read = :rows_read,
                    rows_loaded = :rows_loaded, error = :error
                WHERE run_id = :run_id
                """
            ),
            {
                "run_id": run_id,
                "status": status,
                "error": error,
                "rows_read": stats["rows_read"],
                "rows_loaded": stats["rows_loaded"],
            },
        )


def already_loaded(source: str, file_sha256: str) -> bool:
    """True if an identical file was loaded successfully before."""
    with get_engine().connect() as conn:
        return bool(
            conn.execute(
                text(
                    "SELECT 1 FROM source_runs "
                    "WHERE source = :source AND file_sha256 = :sha AND status = 'success'"
                ),
                {"source": source, "sha": file_sha256},
            ).scalar()
        )


def load_quality_log(run_id: int, counts: Counter) -> None:
    """Add dropped-row counts for a run; repeated calls for the same rule accumulate."""
    if not counts:
        return
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO data_quality_log (run_id, rule, rows_dropped)
                VALUES (:run_id, :rule, :n)
                ON CONFLICT (run_id, rule)
                DO UPDATE SET rows_dropped = data_quality_log.rows_dropped + EXCLUDED.rows_dropped
                """
            ),
            [{"run_id": run_id, "rule": rule, "n": n} for rule, n in counts.items()],
        )


def _copy_rows(table, conn, keys, data_iter) -> None:
    """pandas.to_sql insertion method using PostgreSQL COPY (much faster than INSERTs)."""
    columns = ", ".join(f'"{k}"' for k in keys)
    with conn.connection.cursor() as cur:
        with cur.copy(f'COPY "{table.name}" ({columns}) FROM STDIN') as copy:
            for row in data_iter:
                copy.write_row(row)


def _stage(conn: Connection, df: pd.DataFrame, name: str) -> None:
    df.to_sql(name, conn, if_exists="replace", index=False, method=_copy_rows)


def load_ports(ports: pd.DataFrame) -> int:
    with get_engine().begin() as conn:
        _stage(conn, ports, "stg_ports")
        result = conn.execute(
            text(
                """
                INSERT INTO ports (wpi_number, name, country_code, unlocode,
                                   latitude, longitude, harbor_size)
                SELECT wpi_number, name, country_code, unlocode,
                       latitude, longitude, harbor_size
                FROM stg_ports
                ON CONFLICT (wpi_number) DO UPDATE
                SET name = EXCLUDED.name, country_code = EXCLUDED.country_code,
                    unlocode = EXCLUDED.unlocode, latitude = EXCLUDED.latitude,
                    longitude = EXCLUDED.longitude, harbor_size = EXCLUDED.harbor_size
                """
            )
        )
        conn.execute(text("DROP TABLE stg_ports"))
        return result.rowcount


def load_ais(vessels: pd.DataFrame, positions: pd.DataFrame, run_id: int) -> int:
    """Upsert vessels and insert new positions; returns the number of positions inserted.

    Vessel attributes are only overwritten by non-null values, so position-only
    updates (live feed) never erase a known name or type. Staged columns are cast
    explicitly because an all-null column arrives with whatever type pandas guessed.
    """
    with get_engine().begin() as conn:
        _stage(conn, vessels, "stg_vessels")
        conn.execute(
            text(
                """
                INSERT INTO vessels (mmsi, imo, name, call_sign, vessel_type,
                                     length_m, width_m, vessel_group)
                SELECT s.mmsi, s.imo, s.name, s.call_sign, s.vessel_type,
                       s.length_m, s.width_m, coalesce(g.group_name, 'unknown')
                FROM (
                    SELECT CAST(mmsi AS BIGINT) AS mmsi,
                           CAST(imo AS TEXT) AS imo,
                           CAST(name AS TEXT) AS name,
                           CAST(call_sign AS TEXT) AS call_sign,
                           CAST(vessel_type AS INTEGER) AS vessel_type,
                           CAST(length_m AS DOUBLE PRECISION) AS length_m,
                           CAST(width_m AS DOUBLE PRECISION) AS width_m
                    FROM stg_vessels
                ) s
                LEFT JOIN vessel_type_groups g
                       ON s.vessel_type BETWEEN g.code_from AND g.code_to
                ON CONFLICT (mmsi) DO UPDATE
                SET imo = coalesce(EXCLUDED.imo, vessels.imo),
                    name = coalesce(EXCLUDED.name, vessels.name),
                    call_sign = coalesce(EXCLUDED.call_sign, vessels.call_sign),
                    vessel_type = coalesce(EXCLUDED.vessel_type, vessels.vessel_type),
                    length_m = coalesce(EXCLUDED.length_m, vessels.length_m),
                    width_m = coalesce(EXCLUDED.width_m, vessels.width_m),
                    vessel_group = CASE WHEN EXCLUDED.vessel_type IS NULL
                                        THEN vessels.vessel_group
                                        ELSE EXCLUDED.vessel_group END,
                    updated_at = now()
                """
            )
        )

        _stage(conn, positions, "stg_positions")
        result = conn.execute(
            text(
                """
                INSERT INTO vessel_positions (mmsi, ts, latitude, longitude, sog, cog,
                                              heading, nav_status, run_id)
                SELECT CAST(mmsi AS BIGINT), CAST(ts AS TIMESTAMPTZ),
                       CAST(latitude AS DOUBLE PRECISION), CAST(longitude AS DOUBLE PRECISION),
                       CAST(sog AS REAL), CAST(cog AS REAL), CAST(heading AS REAL),
                       CAST(nav_status AS INTEGER), :run_id
                FROM stg_positions
                ON CONFLICT (mmsi, ts) DO NOTHING
                """
            ),
            {"run_id": run_id},
        )
        conn.execute(text("DROP TABLE stg_vessels; DROP TABLE stg_positions"))
        return result.rowcount
