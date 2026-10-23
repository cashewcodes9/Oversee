"""Aggregate step: derive port calls, voyages and reporting tables from positions.

Full refresh: all derived tables are rebuilt from vessel_positions and ports in a
single transaction, so re-running is idempotent.

Port call heuristic: a vessel is "in port" when it reports a speed at or below
`max_sog_knots` within `radius_m` of a port (nearest port wins). Consecutive
in-port reports at the same port form one call unless separated by more than
`max_gap_hours`. Calls shorter than `min_dwell_minutes` are discarded. A call
whose last in-port report is also the vessel's last report overall is still
open, so its departure_ts stays NULL.

All dates are UTC.
"""

from dataclasses import asdict, dataclass

from sqlalchemy import Connection, text


@dataclass(frozen=True)
class AggregateParams:
    radius_m: float = 5_000
    max_sog_knots: float = 1.0
    max_gap_hours: float = 6
    min_dwell_minutes: float = 60


DEFAULT_PARAMS = AggregateParams()

PORT_CALLS_SQL = """
INSERT INTO port_calls (mmsi, port_id, arrival_ts, departure_ts)
WITH in_port AS (
    SELECT p.mmsi, p.ts, nearest.port_id
    FROM vessel_positions p
    CROSS JOIN LATERAL (
        SELECT pt.port_id
        FROM ports pt
        WHERE ST_DWithin(pt.geom, p.geom, CAST(:radius_m AS DOUBLE PRECISION))
        ORDER BY ST_Distance(pt.geom, p.geom)
        LIMIT 1
    ) nearest
    WHERE p.sog <= CAST(:max_sog_knots AS REAL)
),
flagged AS (
    SELECT mmsi, ts, port_id,
           CASE
               WHEN lag(port_id) OVER w IS DISTINCT FROM port_id
                 OR ts - lag(ts) OVER w
                    > CAST(:max_gap_hours AS DOUBLE PRECISION) * INTERVAL '1 hour'
               THEN 1 ELSE 0
           END AS new_call
    FROM in_port
    WINDOW w AS (PARTITION BY mmsi ORDER BY ts)
),
numbered AS (
    SELECT mmsi, ts, port_id,
           sum(new_call) OVER (PARTITION BY mmsi ORDER BY ts) AS call_no
    FROM flagged
),
calls AS (
    SELECT mmsi, port_id, min(ts) AS arrival_ts, max(ts) AS last_in_port_ts
    FROM numbered
    GROUP BY mmsi, port_id, call_no
),
last_seen AS (
    SELECT mmsi, max(ts) AS last_ts FROM vessel_positions GROUP BY mmsi
)
SELECT c.mmsi, c.port_id, c.arrival_ts,
       CASE WHEN c.last_in_port_ts < s.last_ts THEN c.last_in_port_ts END
FROM calls c
JOIN last_seen s USING (mmsi)
WHERE c.last_in_port_ts - c.arrival_ts
      >= CAST(:min_dwell_minutes AS DOUBLE PRECISION) * INTERVAL '1 minute'
"""

VESSEL_ROUTES_SQL = """
INSERT INTO vessel_routes (mmsi, origin_port_id, destination_port_id, departure_ts, arrival_ts)
SELECT mmsi, prev_port_id, port_id, prev_departure_ts, arrival_ts
FROM (
    SELECT mmsi, port_id, arrival_ts,
           lag(port_id)      OVER w AS prev_port_id,
           lag(departure_ts) OVER w AS prev_departure_ts
    FROM port_calls
    WINDOW w AS (PARTITION BY mmsi ORDER BY arrival_ts)
) consecutive
WHERE prev_port_id IS NOT NULL
  AND prev_port_id <> port_id
  AND prev_departure_ts IS NOT NULL
"""

# unique_vessels counts every vessel present on a day; open calls run to the end of the data.
PORT_ACTIVITY_DAILY_SQL = """
INSERT INTO port_activity_daily
    (port_id, activity_date, arrivals, departures, unique_vessels, avg_dwell_hours)
WITH data_end AS (
    SELECT max(ts) AS ts FROM vessel_positions
),
presence AS (
    SELECT c.port_id, CAST(d AS DATE) AS activity_date, count(DISTINCT c.mmsi) AS unique_vessels
    FROM port_calls c
    CROSS JOIN data_end e
    CROSS JOIN LATERAL generate_series(
        date_trunc('day', c.arrival_ts AT TIME ZONE 'UTC'),
        date_trunc('day', coalesce(c.departure_ts, e.ts) AT TIME ZONE 'UTC'),
        INTERVAL '1 day'
    ) d
    GROUP BY 1, 2
),
arrivals AS (
    SELECT port_id, CAST(arrival_ts AT TIME ZONE 'UTC' AS DATE) AS activity_date,
           count(*) AS n,
           avg(extract(epoch FROM departure_ts - arrival_ts) / 3600) AS avg_dwell_hours
    FROM port_calls
    GROUP BY 1, 2
),
departures AS (
    SELECT port_id, CAST(departure_ts AT TIME ZONE 'UTC' AS DATE) AS activity_date, count(*) AS n
    FROM port_calls
    WHERE departure_ts IS NOT NULL
    GROUP BY 1, 2
)
SELECT p.port_id, p.activity_date,
       coalesce(a.n, 0), coalesce(d.n, 0), p.unique_vessels, a.avg_dwell_hours
FROM presence p
LEFT JOIN arrivals a USING (port_id, activity_date)
LEFT JOIN departures d USING (port_id, activity_date)
"""

ROUTE_SUMMARY_SQL = """
INSERT INTO route_summary
    (origin_port_id, destination_port_id, summary_date, voyages, avg_duration_hours)
SELECT origin_port_id, destination_port_id,
       CAST(departure_ts AT TIME ZONE 'UTC' AS DATE),
       count(*),
       avg(extract(epoch FROM arrival_ts - departure_ts) / 3600)
FROM vessel_routes
GROUP BY 1, 2, 3
"""

DERIVED_TABLES = ["port_activity_daily", "route_summary", "vessel_routes", "port_calls"]


def run(conn: Connection, params: AggregateParams = DEFAULT_PARAMS) -> dict[str, int]:
    """Rebuild all derived tables; returns row counts per table."""
    conn.execute(text(f"TRUNCATE {', '.join(DERIVED_TABLES)} RESTART IDENTITY"))
    return {
        "port_calls": conn.execute(text(PORT_CALLS_SQL), asdict(params)).rowcount,
        "vessel_routes": conn.execute(text(VESSEL_ROUTES_SQL)).rowcount,
        "port_activity_daily": conn.execute(text(PORT_ACTIVITY_DAILY_SQL)).rowcount,
        "route_summary": conn.execute(text(ROUTE_SUMMARY_SQL)).rowcount,
    }
