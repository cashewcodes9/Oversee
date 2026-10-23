"""Load small generated files through the real pipeline into a fresh, migrated database."""

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from oversee import migrate
from oversee.etl import load, pipeline
from oversee.etl.regions import REGIONS
from oversee.etl.stream import batch_frames, parse_message
from tests.test_stream import position_message, static_message
from tests.test_transform import ais_row


@pytest.fixture
def etl_engine(database_factory, monkeypatch):
    """A fresh migrated database that the ETL modules write to (they commit their own work)."""
    url = database_factory()
    migrate.upgrade(url)
    engine = create_engine(url)
    for module in (load, pipeline):
        monkeypatch.setattr(module, "get_engine", lambda: engine)
    yield engine
    engine.dispose()


def scalar(engine, sql, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


def write_ais(tmp_path, rows, name="ais-2025-03-03.csv.zst"):
    path = tmp_path / name
    pd.DataFrame(rows).to_csv(path, index=False, compression="zstd")
    return path


def test_ais_file_loads_with_quality_log_and_vessel_group(etl_engine, tmp_path):
    path = write_ais(
        tmp_path,
        [
            ais_row(base_date_time="2025-03-03 00:00:00"),
            ais_row(base_date_time="2025-03-03 00:01:00", longitude=-118.21),
            ais_row(mmsi=367000002, vessel_type=80, base_date_time="2025-03-03 00:00:00"),
            ais_row(mmsi=123, base_date_time="2025-03-03 00:00:00"),  # invalid MMSI
            ais_row(mmsi=367000003, latitude=47.6, longitude=-122.3),  # Seattle, outside socal
        ],
    )

    inserted = pipeline.load_ais_file(path, REGIONS["socal"])

    assert inserted == 3
    assert scalar(etl_engine, "SELECT count(*) FROM vessel_positions") == 3
    assert scalar(etl_engine, "SELECT vessel_group FROM vessels WHERE mmsi = 367000001") == "cargo"
    assert scalar(etl_engine, "SELECT vessel_group FROM vessels WHERE mmsi = 367000002") == "tanker"
    with etl_engine.connect() as conn:
        quality = dict(conn.execute(text("SELECT rule, rows_dropped FROM data_quality_log")).all())
        run = conn.execute(
            text("SELECT status, rows_read, rows_loaded, file_sha256 FROM source_runs")
        ).one()
    assert quality == {"invalid_mmsi": 1, "outside_region": 1}
    assert (run.status, run.rows_read, run.rows_loaded) == ("success", 5, 3)
    assert len(run.file_sha256) == 64


def test_identical_file_is_skipped_unless_forced(etl_engine, tmp_path):
    path = write_ais(tmp_path, [ais_row()])

    assert pipeline.load_ais_file(path, REGIONS["socal"]) == 1
    assert pipeline.load_ais_file(path, REGIONS["socal"]) is None
    assert pipeline.load_ais_file(path, REGIONS["socal"], force=True) == 0  # rows already there
    assert scalar(etl_engine, "SELECT count(*) FROM source_runs") == 2


def test_ports_file_with_dms_coordinates_loads(etl_engine, tmp_path):
    path = tmp_path / "wpi.csv"
    pd.DataFrame(
        [
            {"portNumber": 15140, "portName": "Long Beach", "countryCode": "US",
             "unloCode": "US LGB", "latitude": "33°45'00\"N", "longitude": "118°13'00\"W",
             "harborSize": "L"},
        ]
    ).to_csv(path, index=False)  # fmt: skip

    assert pipeline.load_ports_file(path) == 1
    with etl_engine.connect() as conn:
        port = conn.execute(text("SELECT name, latitude, longitude, harbor_size FROM ports")).one()
    assert port.name == "Long Beach"
    assert (round(port.latitude, 4), round(port.longitude, 4)) == (33.75, -118.2167)
    assert port.harbor_size == "large"


def test_live_batches_never_erase_known_vessel_attributes(etl_engine):
    with load.source_run("aisstream") as run:
        # First batch: static data names the vessel
        vessels, frame, _ = batch_frames(
            [parse_message(position_message())[1]], [parse_message(static_message())[1]],
            REGIONS["socal"],
        )  # fmt: skip
        load.load_ais(vessels, frame, run["run_id"])

        # Second batch: a position only, so the vessel row arrives with null attributes
        later = position_message(time_utc="2025-03-03 18:30:00 +0000 UTC")
        vessels, frame, _ = batch_frames([parse_message(later)[1]], [], REGIONS["socal"])
        load.load_ais(vessels, frame, run["run_id"])

    with etl_engine.connect() as conn:
        vessel = conn.execute(text("SELECT name, vessel_type, vessel_group FROM vessels")).one()
    assert tuple(vessel) == ("EVER TEST", 71, "cargo")
    assert scalar(etl_engine, "SELECT count(*) FROM vessel_positions") == 2
