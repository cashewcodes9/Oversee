"""Lake tests on a local directory root; no S3 server or database needed."""

from datetime import date

import duckdb
import pandas as pd
import pytest

from oversee.etl import extract
from oversee.etl.lake import Lake
from oversee.etl.regions import REGIONS
from tests.test_transform import ais_row

DAY = date(2025, 3, 3)


@pytest.fixture
def lake(tmp_path):
    return Lake(str(tmp_path / "lake"), duckdb.connect())


def write_raw(tmp_path, rows):
    path = tmp_path / "ais-2025-03-03.csv.zst"
    pd.DataFrame(rows).to_csv(path, index=False, compression="zstd")
    return path


def test_ingest_stores_all_rows_with_source_fingerprint(lake, tmp_path):
    raw = write_raw(tmp_path, [ais_row(), ais_row(mmsi=367000003, latitude=47.6)])

    stored = lake.ingest_ais_file(raw, DAY)

    assert stored.uri.endswith("ais/date=2025-03-03/ais-2025-03-03.parquet")
    assert stored.rows == 2
    assert (stored.source_sha256, stored.source_bytes) == extract.file_fingerprint(raw)
    assert not list((tmp_path / "lake").rglob("*.part"))


def test_missing_day_is_none(lake):
    assert lake.ais_day(DAY) is None


def test_reingest_replaces_the_day(lake, tmp_path):
    lake.ingest_ais_file(write_raw(tmp_path, [ais_row(), ais_row(mmsi=367000002)]), DAY)
    stored = lake.ingest_ais_file(write_raw(tmp_path, [ais_row()]), DAY)
    assert stored.rows == 1
    assert lake.ais_day(DAY).rows == 1


def test_read_filters_to_region_and_keeps_source_columns(lake, tmp_path):
    raw = write_raw(
        tmp_path,
        [ais_row(), ais_row(mmsi=367000003, latitude=47.6, longitude=-122.3)],  # Seattle
    )
    lake.ingest_ais_file(raw, DAY)

    chunks = list(lake.read_ais_day(DAY, REGIONS["socal"]))

    out = pd.concat(chunks)
    assert out["mmsi"].tolist() == [367000001]
    assert set(ais_row()) <= set(out.columns)
    assert out["imo"].tolist() == ["IMO1234567"]


def test_read_streams_in_chunks(lake, tmp_path):
    rows = [ais_row(mmsi=367000000 + i) for i in range(5000)]
    lake.ingest_ais_file(write_raw(tmp_path, rows), DAY)
    chunks = list(lake.read_ais_day(DAY, chunk_vectors=1))  # 2048 rows each
    assert [len(c) for c in chunks] == [2048, 2048, 904]
