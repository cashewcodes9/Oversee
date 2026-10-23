"""Load step: write cleaned frames to PostgreSQL and record pipeline runs."""

from collections.abc import Iterator
from contextlib import contextmanager

import pandas as pd
from sqlalchemy import Connection, text

from oversee.db import get_engine


@contextmanager
def source_run(source: str, file_name: str | None = None) -> Iterator[dict]:
    """Record a pipeline run in source_runs; callers fill in row counts on the yielded dict."""
    engine = get_engine()
    with engine.begin() as conn:
        run_id = conn.execute(
            text("INSERT INTO source_runs (source, file_name) VALUES (:s, :f) RETURNING run_id"),
            {"s": source, "f": file_name},
        ).scalar_one()

    stats = {"run_id": run_id, "rows_read": None, "rows_loaded": None}
    try:
        yield stats
    except Exception as exc:
        _finish_run(run_id, "failed", stats, error=str(exc))
        raise
    _finish_run(run_id, "success", stats)


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
            {"run_id": run_id, "status": status, "error": error,
             "rows_read": stats["rows_read"], "rows_loaded": stats["rows_loaded"]},
        )


def _stage(conn: Connection, df: pd.DataFrame, name: str) -> None:
    df.to_sql(name, conn, if_exists="replace", index=False)


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
    with get_engine().begin() as conn:
        _stage(conn, vessels, "stg_vessels")
        conn.execute(
            text(
                """
                INSERT INTO vessels (mmsi, imo, name, call_sign, vessel_type, length_m, width_m)
                SELECT mmsi, imo, name, call_sign, vessel_type, length_m, width_m
                FROM stg_vessels
                ON CONFLICT (mmsi) DO UPDATE
                SET imo = EXCLUDED.imo, name = EXCLUDED.name, call_sign = EXCLUDED.call_sign,
                    vessel_type = EXCLUDED.vessel_type, length_m = EXCLUDED.length_m,
                    width_m = EXCLUDED.width_m, updated_at = now()
                """
            )
        )

        _stage(conn, positions, "stg_positions")
        result = conn.execute(
            text(
                """
                INSERT INTO vessel_positions (mmsi, ts, latitude, longitude, sog, cog,
                                              heading, nav_status, run_id)
                SELECT mmsi, ts, latitude, longitude, sog, cog, heading, nav_status, :run_id
                FROM stg_positions
                ON CONFLICT (mmsi, ts) DO NOTHING
                """
            ),
            {"run_id": run_id},
        )
        conn.execute(text("DROP TABLE stg_vessels; DROP TABLE stg_positions"))
        return result.rowcount
