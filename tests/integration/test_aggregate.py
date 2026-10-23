from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Connection, text

from oversee.etl import aggregate

T0 = datetime(2024, 1, 1, tzinfo=UTC)

LONG_BEACH = (33.754, -118.216)
SAN_DIEGO = (32.714, -117.173)
AT_SEA = (33.2, -118.0)


@pytest.fixture
def ports(conn: Connection) -> dict[str, int]:
    rows = conn.execute(
        text(
            """
            INSERT INTO ports (wpi_number, name, latitude, longitude) VALUES
                (1, 'Long Beach', :lb_lat, :lb_lon),
                (2, 'San Diego',  :sd_lat, :sd_lon)
            RETURNING name, port_id
            """
        ),
        {
            "lb_lat": LONG_BEACH[0],
            "lb_lon": LONG_BEACH[1],
            "sd_lat": SAN_DIEGO[0],
            "sd_lon": SAN_DIEGO[1],
        },
    ).all()
    return dict(rows)


def add_track(conn: Connection, mmsi: int, track: list[tuple[float, tuple[float, float], float]]):
    """Insert positions given as (hours after T0, (lat, lon), sog)."""
    conn.execute(text("INSERT INTO vessels (mmsi) VALUES (:m) ON CONFLICT DO NOTHING"), {"m": mmsi})
    conn.execute(
        text(
            "INSERT INTO vessel_positions (mmsi, ts, latitude, longitude, sog) "
            "VALUES (:mmsi, :ts, :lat, :lon, :sog)"
        ),
        [
            {"mmsi": mmsi, "ts": T0 + timedelta(hours=h), "lat": lat, "lon": lon, "sog": sog}
            for h, (lat, lon), sog in track
        ],
    )


def stay(start: float, hours: int, where: tuple[float, float]):
    return [(start + h, where, 0.1) for h in range(hours + 1)]


def calls(conn: Connection):
    return conn.execute(
        text(
            "SELECT mmsi, port_id, arrival_ts, departure_ts FROM port_calls "
            "ORDER BY mmsi, arrival_ts"
        )
    ).all()


def test_voyage_between_two_ports(conn, ports):
    add_track(
        conn,
        111111111,
        [
            (0, AT_SEA, 12.0),
            *stay(2, 10, LONG_BEACH),  # 02:00–12:00 at Long Beach
            (14, AT_SEA, 14.0),
            *stay(20, 5, SAN_DIEGO),  # 20:00–01:00 at San Diego
            (27, AT_SEA, 13.0),
        ],
    )

    counts = aggregate.run(conn)

    assert counts["port_calls"] == 2
    lb, sd = calls(conn)
    assert (lb.port_id, lb.arrival_ts, lb.departure_ts) == (
        ports["Long Beach"],
        T0 + timedelta(hours=2),
        T0 + timedelta(hours=12),
    )
    assert (sd.port_id, sd.arrival_ts, sd.departure_ts) == (
        ports["San Diego"],
        T0 + timedelta(hours=20),
        T0 + timedelta(hours=25),
    )

    route = conn.execute(text("SELECT * FROM vessel_routes")).mappings().one()
    assert route["origin_port_id"] == ports["Long Beach"]
    assert route["destination_port_id"] == ports["San Diego"]

    summary = conn.execute(text("SELECT voyages, avg_duration_hours FROM route_summary")).one()
    assert summary == (1, pytest.approx(8.0))


def test_daily_activity(conn, ports):
    add_track(conn, 111111111, [(0, AT_SEA, 12.0), *stay(2, 10, LONG_BEACH), (14, AT_SEA, 14.0)])
    add_track(conn, 222222222, [(1, AT_SEA, 10.0), *stay(4, 26, LONG_BEACH), (32, AT_SEA, 11.0)])

    aggregate.run(conn)

    rows = conn.execute(
        text(
            "SELECT activity_date, arrivals, departures, unique_vessels, avg_dwell_hours "
            "FROM port_activity_daily WHERE port_id = :p ORDER BY activity_date"
        ),
        {"p": ports["Long Beach"]},
    ).all()
    day1, day2 = rows
    assert (day1.arrivals, day1.departures, day1.unique_vessels) == (2, 1, 2)
    assert day1.avg_dwell_hours == pytest.approx((10 + 26) / 2)
    assert (day2.arrivals, day2.departures, day2.unique_vessels) == (0, 1, 1)


def test_open_call_presence_ends_at_the_vessels_own_last_report(conn, ports):
    # Vessel still docked when its data ends; another vessel reports 30 days later (live feed)
    add_track(conn, 333333333, [(0, AT_SEA, 12.0), *stay(3, 4, LONG_BEACH)])
    add_track(conn, 444444444, [(30 * 24, AT_SEA, 12.0)])

    aggregate.run(conn)

    days = conn.execute(
        text("SELECT count(*) FROM port_activity_daily WHERE port_id = :p"),
        {"p": ports["Long Beach"]},
    ).scalar()
    assert days == 1


def test_call_open_at_end_of_data_has_no_departure(conn, ports):
    add_track(conn, 333333333, [(0, AT_SEA, 12.0), *stay(3, 4, LONG_BEACH)])

    aggregate.run(conn)

    (call,) = calls(conn)
    assert call.departure_ts is None
    assert conn.execute(text("SELECT count(*) FROM vessel_routes")).scalar() == 0


def test_short_stops_and_moving_vessels_are_ignored(conn, ports):
    add_track(
        conn,
        444444444,
        [
            (0, LONG_BEACH, 0.2),
            (0.5, LONG_BEACH, 0.2),  # 30 min stop < 60 min dwell
            (1, LONG_BEACH, 8.0),
            (2, LONG_BEACH, 9.0),  # passing through at speed
            (3, AT_SEA, 12.0),
        ],
    )

    assert aggregate.run(conn)["port_calls"] == 0


def test_reporting_gap_splits_calls(conn, ports):
    add_track(
        conn,
        555555555,
        [
            *stay(0, 3, LONG_BEACH),
            *stay(13, 3, LONG_BEACH),  # 10 h silence > 6 h max gap
            (20, AT_SEA, 12.0),
        ],
    )

    assert aggregate.run(conn)["port_calls"] == 2
    assert aggregate.run(conn, aggregate.AggregateParams(max_gap_hours=12))["port_calls"] == 1


def test_rerun_is_idempotent(conn, ports):
    add_track(conn, 111111111, [(0, AT_SEA, 12.0), *stay(2, 10, LONG_BEACH), (14, AT_SEA, 14.0)])

    first = aggregate.run(conn)
    second = aggregate.run(conn)

    assert first == second
    assert len(calls(conn)) == 1
