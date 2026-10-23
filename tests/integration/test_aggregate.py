"""The dbt models that build port calls, voyages and summaries, run for real with `dbt build`.

dbt reads committed data over its own connection, so these tests use a database of their
own and commit their inputs; every test starts from empty source tables.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, create_engine, text

from oversee import migrate
from oversee.etl import aggregate
from tests.integration.conftest import settings_for

T0 = datetime(2024, 1, 1, tzinfo=UTC)

LONG_BEACH = (33.754, -118.216)
SAN_DIEGO = (32.714, -117.173)
AT_SEA = (33.2, -118.0)


@dataclass
class Db:
    url: str
    engine: Engine

    def run(self, params: aggregate.AggregateParams = aggregate.DEFAULT_PARAMS) -> dict:
        return aggregate.run(params, settings=settings_for(self.url))

    def rows(self, sql: str, **params):
        with self.engine.connect() as conn:
            return conn.execute(text(sql), params).all()


@pytest.fixture(scope="module")
def dbt_database(database_factory):
    url = database_factory()
    migrate.upgrade(url)
    engine = create_engine(url)
    yield url, engine
    engine.dispose()


@pytest.fixture
def db(dbt_database) -> Db:
    url, engine = dbt_database
    with engine.begin() as conn:
        conn.execute(
            text("TRUNCATE vessel_positions, vessels, ports, source_runs RESTART IDENTITY CASCADE")
        )
    return Db(url, engine)


@pytest.fixture
def ports(db: Db) -> dict[str, int]:
    with db.engine.begin() as conn:
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


def add_track(db: Db, mmsi: int, track: list[tuple[float, tuple[float, float], float]]):
    """Insert positions given as (hours after T0, (lat, lon), sog)."""
    with db.engine.begin() as conn:
        conn.execute(
            text("INSERT INTO vessels (mmsi) VALUES (:m) ON CONFLICT DO NOTHING"), {"m": mmsi}
        )
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


def calls(db: Db):
    return db.rows(
        "SELECT mmsi, port_id, arrival_ts, departure_ts, dwell_hours "
        "FROM analytics.fct_port_calls ORDER BY mmsi, arrival_ts"
    )


def test_voyage_between_two_ports(db, ports):
    add_track(
        db,
        111111111,
        [
            (0, AT_SEA, 12.0),
            *stay(2, 10, LONG_BEACH),  # 02:00–12:00 at Long Beach
            (14, AT_SEA, 14.0),
            *stay(20, 5, SAN_DIEGO),  # 20:00–01:00 at San Diego
            (27, AT_SEA, 13.0),
        ],
    )

    counts = db.run()

    assert counts["port_calls"] == 2
    lb, sd = calls(db)
    assert (lb.port_id, lb.arrival_ts, lb.departure_ts) == (
        ports["Long Beach"],
        T0 + timedelta(hours=2),
        T0 + timedelta(hours=12),
    )
    assert lb.dwell_hours == pytest.approx(10)
    assert (sd.port_id, sd.arrival_ts, sd.departure_ts) == (
        ports["San Diego"],
        T0 + timedelta(hours=20),
        T0 + timedelta(hours=25),
    )

    (route,) = db.rows("SELECT origin_port_id, destination_port_id FROM analytics.fct_voyages")
    assert route == (ports["Long Beach"], ports["San Diego"])

    summary = db.rows("SELECT voyages, avg_duration_hours FROM analytics.agg_route_summary")
    assert summary == [(1, pytest.approx(8.0))]


def test_daily_activity(db, ports):
    add_track(db, 111111111, [(0, AT_SEA, 12.0), *stay(2, 10, LONG_BEACH), (14, AT_SEA, 14.0)])
    add_track(db, 222222222, [(1, AT_SEA, 10.0), *stay(4, 26, LONG_BEACH), (32, AT_SEA, 11.0)])

    db.run()

    day1, day2 = db.rows(
        "SELECT activity_date, arrivals, departures, unique_vessels, avg_dwell_hours "
        "FROM analytics.agg_port_activity_daily WHERE port_id = :p ORDER BY activity_date",
        p=ports["Long Beach"],
    )
    assert (day1.arrivals, day1.departures, day1.unique_vessels) == (2, 1, 2)
    assert day1.avg_dwell_hours == pytest.approx((10 + 26) / 2)
    assert (day2.arrivals, day2.departures, day2.unique_vessels) == (0, 1, 1)


def test_open_call_presence_ends_at_the_vessels_own_last_report(db, ports):
    # Vessel still docked when its data ends; another vessel reports 30 days later (live feed)
    add_track(db, 333333333, [(0, AT_SEA, 12.0), *stay(3, 4, LONG_BEACH)])
    add_track(db, 444444444, [(30 * 24, AT_SEA, 12.0)])

    db.run()

    ((days,),) = db.rows(
        "SELECT count(*) FROM analytics.agg_port_activity_daily WHERE port_id = :p",
        p=ports["Long Beach"],
    )
    assert days == 1


def test_call_open_at_end_of_data_has_no_departure(db, ports):
    add_track(db, 333333333, [(0, AT_SEA, 12.0), *stay(3, 4, LONG_BEACH)])

    counts = db.run()

    (call,) = calls(db)
    assert call.departure_ts is None
    assert call.dwell_hours is None
    assert counts["vessel_routes"] == 0


def test_short_stops_and_moving_vessels_are_ignored(db, ports):
    add_track(
        db,
        444444444,
        [
            (0, LONG_BEACH, 0.2),
            (0.5, LONG_BEACH, 0.2),  # 30 min stop < 60 min dwell
            (1, LONG_BEACH, 8.0),
            (2, LONG_BEACH, 9.0),  # passing through at speed
            (3, AT_SEA, 12.0),
        ],
    )

    assert db.run()["port_calls"] == 0


def test_reporting_gap_splits_calls_and_thresholds_are_dbt_vars(db, ports):
    add_track(
        db,
        555555555,
        [
            *stay(0, 3, LONG_BEACH),
            *stay(13, 3, LONG_BEACH),  # 10 h silence > 6 h max gap
            (20, AT_SEA, 12.0),
        ],
    )

    assert db.run()["port_calls"] == 2
    assert db.run(aggregate.AggregateParams(max_gap_hours=12))["port_calls"] == 1


def test_rerun_is_idempotent(db, ports):
    add_track(db, 111111111, [(0, AT_SEA, 12.0), *stay(2, 10, LONG_BEACH), (14, AT_SEA, 14.0)])

    first = db.run()
    second = db.run()

    assert first == second
    assert len(calls(db)) == 1


def test_failing_data_test_fails_the_build(db, ports):
    # A vessel group outside the accepted values breaks a dbt data test
    add_track(db, 666666666, [(0, AT_SEA, 12.0)])
    with db.engine.begin() as conn:
        conn.execute(text("UPDATE vessels SET vessel_group = 'submarine'"))

    with pytest.raises(aggregate.DbtBuildError):
        db.run()
