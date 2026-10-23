"""The AIS cleaning rules of transform.py, run as DuckDB SQL over a lake file.

transform.py stays the reference implementation: tests/test_clean_sql.py runs both on
the same input and requires the same rows and the same drop counts. Thresholds are
imported from transform.py, so the two cannot drift apart.

Rules run in the same order as in pandas. Row-level rules mark each row with the first
rule it breaks; track rules (duplicates, GPS spikes) then run on the remaining rows with
window functions over each vessel's track ordered by time.
"""

from collections import Counter

import duckdb
import pandas as pd

from oversee.etl import transform as t
from oversee.etl.regions import BoundingBox

CLEAN_TABLE = "clean_positions"


def _columns() -> str:
    """Source columns under internal names, with sentinels already set to NULL."""
    exprs = []
    for source, internal in t.AIS_COLUMNS.items():
        if internal == "ts":
            expr = f"TRY_CAST({source} AS TIMESTAMP)"
        elif internal == "heading":
            expr = f"CASE WHEN {source} = {t.HEADING_NOT_AVAILABLE} THEN NULL ELSE {source} END"
        elif internal == "sog":
            expr = f"CASE WHEN {source} >= {t.SOG_NOT_AVAILABLE} THEN NULL ELSE {source} END"
        else:
            expr = source
        exprs.append(f"{expr} AS {internal}")
    return ", ".join(exprs)


def _outside(bbox: BoundingBox | None) -> str:
    if bbox is None:
        return "false"
    return (
        f"NOT (latitude BETWEEN {float(bbox.min_lat)} AND {float(bbox.max_lat)} "
        f"AND longitude BETWEEN {float(bbox.min_lon)} AND {float(bbox.max_lon)})"
    )


def clean_positions(
    con: duckdb.DuckDBPyConnection, parquet_uri: str, bbox: BoundingBox | None = None
) -> Counter:
    """Clean one lake file into the temp table clean_positions; returns rows dropped per rule.

    The table has the columns of transform.normalise_ais, in no particular order. Read
    it with fetch_clean, or COPY it to Parquet to build a cleaned lake layer.
    """
    con.execute(
        f"""
        CREATE OR REPLACE TEMP MACRO haversine_nm(lat1, lon1, lat2, lon2) AS
            2 * {t.EARTH_RADIUS_NM} * asin(sqrt(
                pow(sin((radians(lat2) - radians(lat1)) / 2), 2)
                + cos(radians(lat1)) * cos(radians(lat2))
                  * pow(sin((radians(lon2) - radians(lon1)) / 2), 2)
            ))
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE ais_rows AS
        WITH src AS (
            SELECT file_row_number AS row_no, {_columns()}
            FROM read_parquet(?, file_row_number = true)
        )
        SELECT *,
            CASE
                WHEN mmsi IS NULL OR ts IS NULL OR latitude IS NULL OR longitude IS NULL
                    THEN 'missing_required'
                WHEN mmsi NOT BETWEEN 100000000 AND 999999999 THEN 'invalid_mmsi'
                WHEN NOT (latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)
                    THEN 'invalid_position'
                WHEN {_outside(bbox)} THEN 'outside_region'
                WHEN sog > {t.MAX_SOG_KNOTS} THEN 'sog_over_limit'
            END AS drop_rule
        FROM src
        """,
        [parquet_uri],
    )
    counts: Counter = Counter(
        dict(
            con.execute(
                "SELECT drop_rule, count(*) FROM ais_rows WHERE drop_rule IS NOT NULL "
                "GROUP BY drop_rule"
            ).fetchall()
        )
    )
    valid = con.execute("SELECT count(*) FROM ais_rows WHERE drop_rule IS NULL").fetchone()[0]

    # duplicate keeps the first report in file order, like DataFrame.duplicated
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE ais_tracks AS
        WITH ranked AS (
            SELECT * EXCLUDE (drop_rule),
                row_number() OVER (PARTITION BY mmsi, ts ORDER BY row_no) > 1 AS duplicate
            FROM ais_rows
            WHERE drop_rule IS NULL
        ),
        legs AS (
            SELECT *,
                haversine_nm(lag(latitude) OVER w, lag(longitude) OVER w, latitude, longitude)
                    / (date_diff('microsecond', lag(ts) OVER w, ts) / 3.6e9) AS speed_in
            FROM ranked
            WHERE NOT duplicate
            WINDOW w AS (PARTITION BY mmsi ORDER BY ts)
        )
        SELECT * EXCLUDE (speed_in),
            coalesce(
                speed_in > {t.MAX_IMPLIED_SPEED_KNOTS}
                AND coalesce(
                    lead(speed_in) OVER (PARTITION BY mmsi ORDER BY ts)
                        > {t.MAX_IMPLIED_SPEED_KNOTS},
                    true
                ),
                false
            ) AS implied_speed
        FROM legs
        """
    )
    # Each intermediate table is dropped once counted, so at most two are held at a time
    duplicates = valid - con.execute("SELECT count(*) FROM ais_tracks").fetchone()[0]
    con.execute("DROP TABLE ais_rows")

    spikes = con.execute("SELECT count(*) FROM ais_tracks WHERE implied_speed").fetchone()[0]
    columns = ", ".join(t.AIS_COLUMNS.values())
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE {CLEAN_TABLE} AS
        SELECT {columns} FROM ais_tracks WHERE NOT implied_speed
        """
    )
    con.execute("DROP TABLE ais_tracks")

    for rule, n in (("duplicate", duplicates), ("implied_speed", spikes)):
        if n:
            counts[rule] = n
    return counts


def fetch_clean(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """clean_positions as a DataFrame shaped like transform.clean_ais output."""
    out = con.execute(f"SELECT * FROM {CLEAN_TABLE} ORDER BY mmsi, ts").df()
    out["ts"] = out["ts"].dt.tz_localize("UTC")
    out["mmsi"] = out["mmsi"].astype("int64")
    return out
