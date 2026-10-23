from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from oversee.api.schemas import KpiOverview
from oversee.db import get_session

router = APIRouter(prefix="/kpis", tags=["kpis"])


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
                (SELECT count(*) FROM port_calls)       AS port_calls,
                (SELECT min(ts) FROM vessel_positions)  AS first_position_ts,
                (SELECT max(ts) FROM vessel_positions)  AS last_position_ts
            """
            )
        )
        .mappings()
        .one()
    )
    return KpiOverview(**row)


# TODO: GET /movement-summary?region=...&date=... once region filtering is defined
