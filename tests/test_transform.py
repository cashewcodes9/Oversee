import pandas as pd
import pytest

from oversee.etl.regions import BoundingBox
from oversee.etl.transform import (
    clean_ais,
    clean_wpi,
    parse_dms,
    split_vessels,
)


def ais_row(**overrides):
    """One row in MarineCadastre's 2025 csv2 format."""
    row = {
        "mmsi": 367000001,
        "base_date_time": "2025-03-03 00:00:00",
        "longitude": -118.2,
        "latitude": 33.7,
        "sog": 12.0,
        "cog": 90.0,
        "heading": 88.0,
        "vessel_name": "TEST",
        "imo": "IMO1234567",
        "call_sign": "WABC",
        "vessel_type": 70,
        "status": 0,
        "length": 200,
        "width": 30,
        "draft": 9.5,
        "cargo": 70,
        "transceiver": "A",
    }
    return row | overrides


def test_clean_ais_drops_invalid_and_counts_each_rule():
    raw = pd.DataFrame(
        [
            ais_row(),
            ais_row(),  # duplicate
            ais_row(mmsi=123),  # invalid MMSI
            ais_row(latitude=91, base_date_time="2025-03-03 00:01:00"),  # lat not available
            ais_row(base_date_time=None),  # missing timestamp
        ]
    )
    out, counts = clean_ais(raw)
    assert len(out) == 1
    assert str(out["ts"].dt.tz) == "UTC"
    assert counts == {
        "duplicate": 1,
        "invalid_mmsi": 1,
        "invalid_position": 1,
        "missing_required": 1,
    }


def test_clean_ais_nulls_sentinels():
    out, _ = clean_ais(pd.DataFrame([ais_row(heading=511, sog=102.3)]))
    assert pd.isna(out.loc[0, "heading"])
    assert pd.isna(out.loc[0, "sog"])


def test_clean_ais_drops_impossible_speed_over_ground():
    out, counts = clean_ais(pd.DataFrame([ais_row(sog=55.0)]))
    assert out.empty
    assert counts == {"sog_over_limit": 1}


def test_clean_ais_filters_by_region():
    bbox = BoundingBox(min_lat=33, max_lat=34, min_lon=-119, max_lon=-118)
    raw = pd.DataFrame([ais_row(), ais_row(mmsi=367000002, latitude=40.0)])
    out, counts = clean_ais(raw, bbox=bbox)
    assert out["mmsi"].tolist() == [367000001]
    assert counts == {"outside_region": 1}


def test_clean_ais_drops_single_gps_spike_but_keeps_track():
    # One report per minute along the coast, with a one-off jump of ~60 nm in the middle
    raw = pd.DataFrame(
        [
            ais_row(base_date_time="2025-03-03 00:00:00", latitude=33.700),
            ais_row(base_date_time="2025-03-03 00:01:00", latitude=33.702),
            ais_row(base_date_time="2025-03-03 00:02:00", latitude=34.700),  # spike
            ais_row(base_date_time="2025-03-03 00:03:00", latitude=33.706),
            ais_row(base_date_time="2025-03-03 00:04:00", latitude=33.708),
        ]
    )
    out, counts = clean_ais(raw)
    assert counts == {"implied_speed": 1}
    assert out["latitude"].tolist() == [33.700, 33.702, 33.706, 33.708]


def test_split_vessels_keeps_latest_attributes():
    raw = pd.DataFrame(
        [
            ais_row(vessel_name="OLD"),
            ais_row(vessel_name="NEW", base_date_time="2025-03-03 01:00:00", longitude=-118.21),
        ]
    )
    vessels, positions = split_vessels(clean_ais(raw)[0])
    assert vessels.loc[0, "name"] == "NEW"
    assert len(positions) == 2


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("30°20'00\"N", 30.333333),
        ("48°17'00\"E", 48.283333),
        ("33°45'30\"S", -33.758333),
        ("118°13'00\"W", -118.216667),
        (12.5, 12.5),
        ("-118.2", -118.2),
        ("garbage", None),
        (None, None),
    ],
)
def test_parse_dms(value, expected):
    result = parse_dms(value)
    assert result == pytest.approx(expected, abs=1e-6) if expected is not None else result is None


def test_clean_wpi():
    raw = pd.DataFrame(
        [
            {
                "portNumber": 15140,
                "portName": "Long Beach",
                "countryCode": "US",
                "unloCode": "US LGB",
                "latitude": "33°45'00\"N",
                "longitude": "118°13'00\"W",
                "harborSize": "L",
                "harborType": "CN",
            },
            {
                "portNumber": 99999,
                "portName": "No position",
                "countryCode": "US",
                "unloCode": "",
                "latitude": None,
                "longitude": None,
                "harborSize": "S",
                "harborType": "CN",
            },
        ]
    )
    out, counts = clean_wpi(raw)
    assert len(out) == 1
    port = out.iloc[0]
    assert (port["name"], port["harbor_size"]) == ("Long Beach", "large")
    assert port["latitude"] == pytest.approx(33.75)
    assert port["longitude"] == pytest.approx(-118.216667, abs=1e-6)
    assert "harborType" not in out.columns
    assert counts == {"missing_required": 1}
