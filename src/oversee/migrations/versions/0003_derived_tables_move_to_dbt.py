"""Derived tables move to dbt: drop the old Python-maintained copies.

Port calls, voyages and the daily summaries are now dbt models in the analytics schema
(transform/models/marts), built by `oversee-dbt build`. Alembic keeps owning the tables
the Python ETL loads (ports, vessels, vessel_positions, lineage and quality logs).

Downgrade recreates the old tables empty; rebuild their contents with the pre-dbt code.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-24
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE route_summary, port_activity_daily, vessel_routes, port_calls")


def downgrade() -> None:
    op.execute("""
    CREATE TABLE port_calls (
        port_call_id  BIGSERIAL PRIMARY KEY,
        mmsi          BIGINT      NOT NULL REFERENCES vessels (mmsi),
        port_id       INTEGER     NOT NULL REFERENCES ports (port_id),
        arrival_ts    TIMESTAMPTZ NOT NULL,
        departure_ts  TIMESTAMPTZ,
        UNIQUE (mmsi, port_id, arrival_ts)
    );

    CREATE TABLE vessel_routes (
        route_id             BIGSERIAL PRIMARY KEY,
        mmsi                 BIGINT  NOT NULL REFERENCES vessels (mmsi),
        origin_port_id       INTEGER NOT NULL REFERENCES ports (port_id),
        destination_port_id  INTEGER NOT NULL REFERENCES ports (port_id),
        departure_ts         TIMESTAMPTZ NOT NULL,
        arrival_ts           TIMESTAMPTZ NOT NULL
    );

    CREATE TABLE port_activity_daily (
        port_id          INTEGER NOT NULL REFERENCES ports (port_id),
        activity_date    DATE    NOT NULL,
        arrivals         INTEGER NOT NULL DEFAULT 0,
        departures       INTEGER NOT NULL DEFAULT 0,
        unique_vessels   INTEGER NOT NULL DEFAULT 0,
        avg_dwell_hours  DOUBLE PRECISION,
        PRIMARY KEY (port_id, activity_date)
    );

    CREATE TABLE route_summary (
        origin_port_id       INTEGER NOT NULL REFERENCES ports (port_id),
        destination_port_id  INTEGER NOT NULL REFERENCES ports (port_id),
        summary_date         DATE    NOT NULL,
        voyages              INTEGER NOT NULL DEFAULT 0,
        avg_duration_hours   DOUBLE PRECISION,
        PRIMARY KEY (origin_port_id, destination_port_id, summary_date)
    );
    """)
