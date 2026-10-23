"""The SQL cleaning rules must match transform.py exactly: same rows, same drop counts."""

from datetime import date

import duckdb
import pandas as pd
import pytest

from oversee.etl import clean_sql, transform
from oversee.etl.lake import Lake
from oversee.etl.regions import REGIONS, BoundingBox
from tests.test_transform import ais_row

DAY = date(2025, 3, 3)


def both(tmp_path, rows, bbox=None):
    """Run pandas and SQL cleaning on the same lake file."""
    raw = tmp_path / "ais-2025-03-03.csv.zst"
    pd.DataFrame(rows).to_csv(raw, index=False, compression="zstd")
    lake = Lake(str(tmp_path / "lake"), duckdb.connect())
    lake.ingest_ais_file(raw, DAY)

    expected, expected_counts = transform.clean_ais(pd.concat(lake.read_ais_day(DAY)), bbox)
    counts = clean_sql.clean_positions(lake.con, lake.ais_day_uri(DAY), bbox)
    return clean_sql.fetch_clean(lake.con), counts, expected, expected_counts


def assert_same(actual, expected):
    def norm(df):
        return df.reset_index(drop=True).astype(object).where(df.notna().values, None)

    pd.testing.assert_frame_equal(norm(actual), norm(expected.reset_index(drop=True)))


SCENARIOS = {
    "valid": [ais_row()],
    "each row rule": [
        ais_row(),
        ais_row(),  # duplicate
        ais_row(mmsi=123),  # invalid MMSI
        ais_row(latitude=91, base_date_time="2025-03-03 00:01:00"),  # lat not available
        ais_row(base_date_time=None),  # missing timestamp
        ais_row(mmsi=367000009, sog=55.0),  # over speed limit
    ],
    "sentinels become null": [ais_row(heading=511, sog=102.3)],
    "duplicate keeps first in file order": [
        ais_row(vessel_name="FIRST"),
        ais_row(vessel_name="SECOND"),
    ],
    "gps spike": [
        ais_row(base_date_time="2025-03-03 00:00:00", latitude=33.700),
        ais_row(base_date_time="2025-03-03 00:01:00", latitude=33.702),
        ais_row(base_date_time="2025-03-03 00:02:00", latitude=34.700),  # spike
        ais_row(base_date_time="2025-03-03 00:03:00", latitude=33.706),
        ais_row(base_date_time="2025-03-03 00:04:00", latitude=33.708),
    ],
    "spike as last report": [
        ais_row(base_date_time="2025-03-03 00:00:00", latitude=33.700),
        ais_row(base_date_time="2025-03-03 00:01:00", latitude=34.700),
    ],
    "two vessels out of order": [
        ais_row(mmsi=367000002, base_date_time="2025-03-03 00:05:00"),
        ais_row(base_date_time="2025-03-03 00:03:00"),
        ais_row(mmsi=367000002, base_date_time="2025-03-03 00:01:00"),
        ais_row(base_date_time="2025-03-03 00:00:00", longitude=-118.21),
    ],
}


@pytest.mark.parametrize("rows", SCENARIOS.values(), ids=SCENARIOS.keys())
def test_sql_matches_pandas(tmp_path, rows):
    actual, counts, expected, expected_counts = both(tmp_path, rows)
    assert counts == expected_counts
    assert_same(actual, expected)


def test_sql_matches_pandas_with_region(tmp_path):
    rows = [ais_row(), ais_row(mmsi=367000002, latitude=40.0), ais_row(mmsi=367000003)]
    actual, counts, expected, expected_counts = both(tmp_path, rows, REGIONS["socal"])
    assert counts == expected_counts == {"outside_region": 1}
    assert_same(actual, expected)


def test_region_bounds_are_inclusive(tmp_path):
    bbox = BoundingBox(min_lat=33.7, max_lat=34, min_lon=-119, max_lon=-118.2)
    actual, counts, expected, _ = both(tmp_path, [ais_row()], bbox)  # exactly on the corner
    assert len(actual) == len(expected) == 1
