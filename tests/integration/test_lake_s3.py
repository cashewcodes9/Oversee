"""Lake round trip against the SeaweedFS `lake` service (docker compose up -d --wait lake).

Skipped when the service is not reachable. Each run writes under a unique prefix in
s3://oversee-lake/test-*, so it never touches lake data used by the pipeline.
"""

import socket
import uuid
from datetime import date

import pandas as pd
import pytest

from oversee.config import Settings
from oversee.etl.lake import open_lake
from oversee.etl.regions import REGIONS
from tests.test_transform import ais_row

ENDPOINT = ("localhost", 8333)


def s3_settings(lake_root: str) -> Settings:
    return Settings(
        _env_file=None,
        lake_root=lake_root,
        s3_endpoint=f"{ENDPOINT[0]}:{ENDPOINT[1]}",
        s3_access_key_id="oversee",
        s3_secret_access_key="oversee-secret",
        s3_use_ssl=False,
        s3_url_style="path",
    )


@pytest.fixture
def s3_reachable():
    try:
        socket.create_connection(ENDPOINT, timeout=1).close()
    except OSError:
        pytest.skip("S3 lake not reachable; start it with `docker compose up -d --wait lake`")


@pytest.fixture
def s3_lake(s3_reachable):
    return open_lake(s3_settings(f"s3://oversee-lake/test-{uuid.uuid4().hex[:8]}"))


def test_missing_bucket_means_day_not_stored(s3_reachable):
    lake = open_lake(s3_settings(f"s3://oversee-missing-{uuid.uuid4().hex[:8]}"))
    assert lake.ais_day(date(2025, 3, 3)) is None


def test_s3_round_trip(s3_lake, tmp_path):
    day = date(2025, 3, 3)
    raw = tmp_path / "ais-2025-03-03.csv.zst"
    pd.DataFrame([ais_row(), ais_row(mmsi=367000003, latitude=47.6)]).to_csv(
        raw, index=False, compression="zstd"
    )

    assert s3_lake.ais_day(day) is None
    stored = s3_lake.ingest_ais_file(raw, day)

    assert stored.uri.startswith("s3://oversee-lake/test-")
    assert s3_lake.ais_day(day).rows == 2
    rows = pd.concat(s3_lake.read_ais_day(day, REGIONS["socal"]))
    assert rows["mmsi"].tolist() == [367000001]
