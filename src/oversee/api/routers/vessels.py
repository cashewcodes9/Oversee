from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from oversee.api.schemas import Vessel, VesselRoute
from oversee.db import get_session

router = APIRouter(prefix="/vessels", tags=["vessels"])


@router.get("", response_model=list[Vessel])
def list_vessels(
    vessel_type: int | None = None,
    limit: int = Query(100, le=1000),
    session: Session = Depends(get_session),
):
    rows = session.execute(
        text(
            """
            SELECT mmsi, imo, name, vessel_type, length_m
            FROM vessels
            WHERE (CAST(:vessel_type AS INTEGER) IS NULL OR vessel_type = :vessel_type)
            ORDER BY mmsi
            LIMIT :limit
            """
        ),
        {"vessel_type": vessel_type, "limit": limit},
    ).mappings()
    return [Vessel(**r) for r in rows]


@router.get("/{mmsi}/routes", response_model=list[VesselRoute])
def vessel_routes(mmsi: int, session: Session = Depends(get_session)):
    rows = session.execute(
        text(
            """
            SELECT origin_port_id, destination_port_id, departure_ts, arrival_ts
            FROM analytics.fct_voyages
            WHERE mmsi = :mmsi
            ORDER BY departure_ts
            """
        ),
        {"mmsi": mmsi},
    ).mappings()
    return [VesselRoute(**r) for r in rows]
