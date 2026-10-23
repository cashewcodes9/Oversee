"""KPI endpoints against a small, known dataset in a rolled-back transaction.

Derived rows are inserted straight into the dbt model tables, so these tests check the
API queries alone; tests/integration/test_aggregate.py checks how dbt builds them.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from oversee.api.main import app
from oversee.db import get_session

CARGO, TUG = 367000001, 367000002


@pytest.fixture
def client(conn: Connection):
    def session_on_test_connection():
        session = Session(bind=conn, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = session_on_test_connection
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def ports(conn: Connection) -> dict[str, int]:
    """2025-03-03 is a Monday; Los Angeles is UTC-8 until 2025-03-09."""
    rows = conn.execute(
        text(
            """
            INSERT INTO ports (wpi_number, name, latitude, longitude) VALUES
                (1, 'Long Beach', 33.754, -118.216),
                (2, 'San Diego', 32.714, -117.173),
                (3, 'Quiet Cove', 34.0, -119.0)
            RETURNING name, port_id
            """
        )
    ).all()
    ids = dict(rows)
    params = {"cargo": CARGO, "tug": TUG, "lb": ids["Long Beach"], "sd": ids["San Diego"]}
    for statement in (
        """
        INSERT INTO vessels (mmsi, name, vessel_type, vessel_group) VALUES
            (:cargo, 'CARGO ONE', 70, 'cargo'), (:tug, 'TUG ONE', 52, 'tug')
        """,
        """
        INSERT INTO vessel_positions (mmsi, ts, latitude, longitude, sog) VALUES
            (:cargo, '2025-03-03 10:00Z', 33.754, -118.216, 0),
            (:cargo, '2025-03-03 10:01Z', 33.754, -118.216, 0),
            (:tug, '2025-03-04 11:00Z', 33.2, -118.0, NULL),
            (:tug, '2025-03-04 12:00Z', 33.2, -118.0, 12)
        """,
        """
        INSERT INTO analytics.fct_port_calls
            (port_call_id, mmsi, port_id, arrival_ts, departure_ts, dwell_hours) VALUES
            (1, :cargo, :lb, '2025-03-03 10:00Z', '2025-03-03 20:00Z', 10),
            (2, :tug, :lb, '2025-03-03 18:00Z', '2025-03-04 00:00Z', 6),
            (3, :tug, :sd, '2025-03-04 12:00Z', NULL, NULL)
        """,
        """
        INSERT INTO analytics.agg_port_activity_daily
            (port_id, activity_date, arrivals, departures, unique_vessels) VALUES
            (:lb, '2025-03-03', 2, 1, 2), (:lb, '2025-03-04', 0, 1, 1),
            (:sd, '2025-03-04', 1, 0, 1)
        """,
        """
        INSERT INTO analytics.agg_route_summary
            (origin_port_id, destination_port_id, summary_date, voyages, avg_duration_hours)
        VALUES (:lb, :sd, '2025-03-04', 1, 12), (:lb, :sd, '2025-03-05', 3, 8),
               (:sd, :lb, '2025-03-04', 1, 10)
        """,
    ):
        conn.execute(text(statement), {k: v for k, v in params.items() if f":{k}" in statement})
    return ids


def get(client, path, **params):
    resp = client.get(path, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_daily_traffic(client, ports):
    assert get(client, "/kpis/daily") == [
        {"day": "2025-03-03", "positions": 2, "active_vessels": 1, "arrivals": 2, "departures": 1},
        {"day": "2025-03-04", "positions": 2, "active_vessels": 1, "arrivals": 1, "departures": 1},
    ]


def test_port_ranking(client, ports):
    ranking = get(client, "/kpis/ports")
    assert [(p["name"], p["port_calls"], p["unique_vessels"]) for p in ranking] == [
        ("Long Beach", 2, 2),
        ("San Diego", 1, 1),
    ]
    assert ranking[0]["avg_dwell_hours"] == pytest.approx(8.0)
    assert ranking[1]["avg_dwell_hours"] is None  # call still open

    assert [p["name"] for p in get(client, "/kpis/ports", start="2025-03-04")] == ["San Diego"]
    assert [p["name"] for p in get(client, "/kpis/ports", end="2025-03-03")] == ["Long Beach"]


def test_active_ports_filter(client, ports):
    names = [p["name"] for p in get(client, "/ports", active="true")]
    assert names == ["Long Beach", "San Diego"]
    assert "Quiet Cove" in [p["name"] for p in get(client, "/ports")]


def test_density(client, ports):
    cells = get(client, "/kpis/density", cell_deg=0.1)
    assert sum(c["positions"] for c in cells) == 4
    # Cells are keyed by their centre: 33.754, -118.216 falls in the cell centred 33.75, -118.25
    by_centre = {(round(c["latitude"], 2), round(c["longitude"], 2)): c for c in cells}
    assert by_centre[(33.75, -118.25)]["positions"] == 2
    assert by_centre[(33.25, -117.95)]["vessels"] == 1
    assert sum(c["positions"] for c in get(client, "/kpis/density", vessel_group="tug")) == 2


def test_routes_weight_duration_by_voyages(client, ports):
    routes = get(client, "/kpis/routes")
    assert [(r["origin"], r["destination"], r["voyages"]) for r in routes] == [
        ("Long Beach", "San Diego", 4),
        ("San Diego", "Long Beach", 1),
    ]
    assert routes[0]["avg_duration_hours"] == pytest.approx(9.0)  # (12 + 3 * 8) / 4


def test_peaks_in_local_time(client, ports):
    slots = get(client, "/kpis/peaks", tz="America/Los_Angeles")
    assert [(s["weekday"], s["hour"], s["arrivals"]) for s in slots] == [
        (0, 2, 1),
        (0, 10, 1),
        (1, 4, 1),
    ]
    utc = get(client, "/kpis/peaks", port_id=ports["San Diego"])
    assert [(s["weekday"], s["hour"]) for s in utc] == [(1, 12)]


def test_peaks_rejects_unknown_time_zone(client, ports):
    assert client.get("/kpis/peaks", params={"tz": "Mars/Base"}).status_code == 422


def test_dwell_counts_completed_calls_only(client, ports):
    dwell = {d["vessel_group"]: d for d in get(client, "/kpis/dwell")}
    assert dwell["cargo"]["port_calls"] == 1
    assert dwell["cargo"]["median_hours"] == pytest.approx(10.0)
    assert dwell["tug"]["median_hours"] == pytest.approx(6.0)
    assert get(client, "/kpis/dwell", port_id=ports["San Diego"]) == []


def test_speed_by_group(client, ports):
    speed = {s["vessel_group"]: s for s in get(client, "/kpis/speed")}
    assert speed["cargo"]["positions"] == 2
    assert speed["cargo"]["moving_share"] == 0
    assert speed["cargo"]["median_moving_knots"] is None
    assert speed["tug"]["positions"] == 1  # the report without speed is left out
    assert speed["tug"]["moving_share"] == 1
    assert speed["tug"]["median_moving_knots"] == pytest.approx(12.0)
