"""Aggregate step: build port calls, voyages and reporting tables with dbt.

The SQL lives in the dbt project (transform/models): staging views over the tables the
ETL loads, an in-port intermediate model, and the marts fct_port_calls, fct_voyages,
agg_port_activity_daily, agg_route_summary, dim_ports and dim_vessels in the analytics
schema. Every build is a full refresh, so re-running is idempotent, and runs dbt's data
tests.

Port call heuristic: a vessel is "in port" when it reports a speed at or below
`max_sog_knots` within `radius_m` of a port (nearest port wins). Consecutive in-port
reports at the same port form one call unless separated by more than `max_gap_hours`.
Calls shorter than `min_dwell_minutes` are discarded. A call whose last in-port report
is also the vessel's last report overall is still open, so its departure_ts stays NULL.

All dates are UTC.
"""

from dataclasses import dataclass

from sqlalchemy import create_engine, text

from oversee import dbt_runner
from oversee.config import Settings, get_settings

MART_TABLES = {
    "port_calls": "analytics.fct_port_calls",
    "vessel_routes": "analytics.fct_voyages",
    "port_activity_daily": "analytics.agg_port_activity_daily",
    "route_summary": "analytics.agg_route_summary",
}


@dataclass(frozen=True)
class AggregateParams:
    radius_m: float = 5_000
    max_sog_knots: float = 1.0
    max_gap_hours: float = 6
    min_dwell_minutes: float = 60

    def dbt_vars(self) -> dict[str, float]:
        return {
            "port_call_radius_m": self.radius_m,
            "port_call_max_sog_knots": self.max_sog_knots,
            "port_call_max_gap_hours": self.max_gap_hours,
            "port_call_min_dwell_minutes": self.min_dwell_minutes,
        }


DEFAULT_PARAMS = AggregateParams()


class DbtBuildError(RuntimeError):
    pass


def run(
    params: AggregateParams = DEFAULT_PARAMS, settings: Settings | None = None
) -> dict[str, int]:
    """`dbt build` (models and tests); returns row counts per derived table."""
    settings = settings or get_settings()
    result = dbt_runner.invoke(["build"], variables=params.dbt_vars(), settings=settings)
    if not result.success:
        raise DbtBuildError("dbt build failed; see the dbt output above") from result.exception
    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            return {
                name: conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                for name, table in MART_TABLES.items()
            }
    finally:
        engine.dispose()
