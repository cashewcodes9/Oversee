from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from oversee.api.schemas import Port, PortActivity
from oversee.db import get_session

router = APIRouter(prefix="/ports", tags=["ports"])


@router.get("", response_model=list[Port])
def list_ports(
    country_code: str | None = None,
    active: bool = Query(False, description="Only ports with rows in port_activity_daily"),
    limit: int = Query(100, le=1000),
    session: Session = Depends(get_session),
):
    rows = session.execute(
        text(
            """
            SELECT port_id, name, country_code, unlocode, latitude, longitude
            FROM ports p
            WHERE (CAST(:country_code AS TEXT) IS NULL OR country_code = :country_code)
              AND (NOT :active OR EXISTS (
                  SELECT 1 FROM port_activity_daily a WHERE a.port_id = p.port_id
              ))
            ORDER BY name
            LIMIT :limit
            """
        ),
        {"country_code": country_code, "active": active, "limit": limit},
    ).mappings()
    return [Port(**r) for r in rows]


@router.get("/{port_id}/activity", response_model=list[PortActivity])
def port_activity(
    port_id: int,
    start: date | None = None,
    end: date | None = None,
    session: Session = Depends(get_session),
):
    exists = session.execute(
        text("SELECT 1 FROM ports WHERE port_id = :id"), {"id": port_id}
    ).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail="Port not found")

    rows = session.execute(
        text(
            """
            SELECT activity_date, arrivals, departures, unique_vessels, avg_dwell_hours
            FROM port_activity_daily
            WHERE port_id = :port_id
              AND (CAST(:start AS DATE) IS NULL OR activity_date >= :start)
              AND (CAST(:end AS DATE) IS NULL OR activity_date <= :end)
            ORDER BY activity_date
            """
        ),
        {"port_id": port_id, "start": start, "end": end},
    ).mappings()
    return [PortActivity(**r) for r in rows]
