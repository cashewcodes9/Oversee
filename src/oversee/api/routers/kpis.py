from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from oversee.api.schemas import (
    DailyTraffic,
    DensityCell,
    DwellByGroup,
    KpiOverview,
    PeakSlot,
    PortRanking,
    RouteTotal,
    SpeedByGroup,
)
from oversee.db import get_session

router = APIRouter(prefix="/kpis", tags=["kpis"])

# Reports at or below this speed count as stationary (same idea as port-call detection)
MOVING_KNOTS = 1.0


@router.get("/overview", response_model=KpiOverview)
def overview(session: Session = Depends(get_session)):
    row = (
        session.execute(
            text(
                """
            SELECT
                (SELECT count(*) FROM ports)            AS ports,
                (SELECT count(*) FROM vessels)          AS vessels,
                (SELECT count(*) FROM vessel_positions) AS positions,
                (SELECT count(*) FROM analytics.fct_port_calls)       AS port_calls,
                (SELECT min(ts) FROM vessel_positions)  AS first_position_ts,
                (SELECT max(ts) FROM vessel_positions)  AS last_position_ts
            """
            )
        )
        .mappings()
        .one()
    )
    return KpiOverview(**row)


@router.get("/daily", response_model=list[DailyTraffic])
def daily_traffic(session: Session = Depends(get_session)):
    """AIS reports, distinct vessels, arrivals and departures per UTC day, all ports."""
    rows = session.execute(
        text(
            """
            WITH positions AS (
                SELECT (ts AT TIME ZONE 'UTC')::date AS day,
                       count(*) AS positions, count(DISTINCT mmsi) AS active_vessels
                FROM vessel_positions
                GROUP BY 1
            ),
            activity AS (
                SELECT activity_date AS day, sum(arrivals) AS arrivals,
                       sum(departures) AS departures
                FROM analytics.agg_port_activity_daily
                GROUP BY 1
            )
            SELECT p.day, p.positions, p.active_vessels,
                   coalesce(a.arrivals, 0) AS arrivals, coalesce(a.departures, 0) AS departures
            FROM positions p
            LEFT JOIN activity a USING (day)
            ORDER BY p.day
            """
        )
    ).mappings()
    return [DailyTraffic(**r) for r in rows]


@router.get("/ports", response_model=list[PortRanking])
def port_ranking(
    start: date | None = None,
    end: date | None = None,
    limit: int = Query(20, le=200),
    session: Session = Depends(get_session),
):
    """Ports ranked by port calls arriving in [start, end]."""
    rows = session.execute(
        text(
            """
            SELECT p.port_id, p.name, p.latitude, p.longitude,
                   count(*) AS port_calls,
                   count(DISTINCT c.mmsi) AS unique_vessels,
                   avg(extract(epoch FROM c.departure_ts - c.arrival_ts) / 3600)
                       AS avg_dwell_hours
            FROM analytics.fct_port_calls c
            JOIN ports p USING (port_id)
            WHERE (CAST(:start AS DATE) IS NULL OR c.arrival_ts >= CAST(:start AS DATE))
              AND (CAST(:end AS DATE) IS NULL OR c.arrival_ts < CAST(:end AS DATE) + 1)
            GROUP BY p.port_id
            ORDER BY port_calls DESC, p.name
            LIMIT :limit
            """
        ),
        {"start": start, "end": end, "limit": limit},
    ).mappings()
    return [PortRanking(**r) for r in rows]


@router.get("/density", response_model=list[DensityCell])
def density(
    cell_deg: float = Query(0.05, ge=0.01, le=1.0, description="Grid cell size in degrees"),
    vessel_group: str | None = None,
    session: Session = Depends(get_session),
):
    """AIS reports and distinct vessels per grid cell; latitude/longitude is the cell centre."""
    rows = session.execute(
        text(
            """
            SELECT (floor(p.latitude / :cell) + 0.5) * :cell AS latitude,
                   (floor(p.longitude / :cell) + 0.5) * :cell AS longitude,
                   count(*) AS positions, count(DISTINCT p.mmsi) AS vessels
            FROM vessel_positions p
            JOIN vessels v USING (mmsi)
            WHERE (CAST(:vessel_group AS TEXT) IS NULL OR v.vessel_group = :vessel_group)
            GROUP BY 1, 2
            """
        ),
        {"cell": cell_deg, "vessel_group": vessel_group},
    ).mappings()
    return [DensityCell(**r) for r in rows]


@router.get("/routes", response_model=list[RouteTotal])
def routes(limit: int = Query(20, le=200), session: Session = Depends(get_session)):
    """Busiest origin → destination pairs over the whole data window."""
    rows = session.execute(
        text(
            """
            SELECT r.origin_port_id, o.name AS origin,
                   o.latitude AS origin_latitude, o.longitude AS origin_longitude,
                   r.destination_port_id, d.name AS destination,
                   d.latitude AS destination_latitude, d.longitude AS destination_longitude,
                   sum(r.voyages) AS voyages,
                   sum(r.avg_duration_hours * r.voyages) / sum(r.voyages) AS avg_duration_hours
            FROM analytics.agg_route_summary r
            JOIN ports o ON o.port_id = r.origin_port_id
            JOIN ports d ON d.port_id = r.destination_port_id
            GROUP BY r.origin_port_id, o.port_id, r.destination_port_id, d.port_id
            ORDER BY voyages DESC, origin, destination
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).mappings()
    return [RouteTotal(**r) for r in rows]


@router.get("/peaks", response_model=list[PeakSlot])
def peaks(
    port_id: int | None = None,
    tz: str = Query("UTC", description="IANA time zone for weekday and hour"),
    session: Session = Depends(get_session),
):
    """Port-call arrivals by local weekday and hour."""
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=422, detail=f"Unknown time zone: {tz}") from None
    rows = session.execute(
        text(
            """
            SELECT (extract(isodow FROM arrival_ts AT TIME ZONE :tz) - 1)::int AS weekday,
                   extract(hour FROM arrival_ts AT TIME ZONE :tz)::int AS hour,
                   count(*) AS arrivals
            FROM analytics.fct_port_calls
            WHERE (CAST(:port_id AS INTEGER) IS NULL OR port_id = :port_id)
            GROUP BY 1, 2
            ORDER BY 1, 2
            """
        ),
        {"port_id": port_id, "tz": tz},
    ).mappings()
    return [PeakSlot(**r) for r in rows]


@router.get("/dwell", response_model=list[DwellByGroup])
def dwell(port_id: int | None = None, session: Session = Depends(get_session)):
    """Time in port per vessel group, from completed port calls."""
    rows = session.execute(
        text(
            """
            WITH calls AS (
                SELECT v.vessel_group,
                       extract(epoch FROM c.departure_ts - c.arrival_ts) / 3600 AS hours
                FROM analytics.fct_port_calls c
                JOIN vessels v USING (mmsi)
                WHERE c.departure_ts IS NOT NULL
                  AND (CAST(:port_id AS INTEGER) IS NULL OR c.port_id = :port_id)
            )
            SELECT vessel_group, count(*) AS port_calls,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY hours) AS median_hours,
                   percentile_cont(0.9) WITHIN GROUP (ORDER BY hours) AS p90_hours
            FROM calls
            GROUP BY vessel_group
            ORDER BY port_calls DESC
            """
        ),
        {"port_id": port_id},
    ).mappings()
    return [DwellByGroup(**r) for r in rows]


@router.get("/speed", response_model=list[SpeedByGroup])
def speed(session: Session = Depends(get_session)):
    """Speed over ground per vessel group; percentiles cover moving reports only."""
    rows = session.execute(
        text(
            """
            SELECT v.vessel_group, count(*) AS positions,
                   avg((p.sog > :moving)::int) AS moving_share,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY p.sog)
                       FILTER (WHERE p.sog > :moving) AS median_moving_knots,
                   percentile_cont(0.9) WITHIN GROUP (ORDER BY p.sog)
                       FILTER (WHERE p.sog > :moving) AS p90_moving_knots
            FROM vessel_positions p
            JOIN vessels v USING (mmsi)
            WHERE p.sog IS NOT NULL
            GROUP BY v.vessel_group
            ORDER BY positions DESC
            """
        ),
        {"moving": MOVING_KNOTS},
    ).mappings()
    return [SpeedByGroup(**r) for r in rows]


# TODO: GET /movement-summary?region=...&date=... once region filtering is defined
