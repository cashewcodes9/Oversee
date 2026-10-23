from datetime import UTC, datetime

import pandas as pd
import pytest

from oversee.etl.regions import BoundingBox
from oversee.etl.stream import batch_frames, parse_message, parse_time, subscription

BOX = BoundingBox(min_lat=32.5, max_lat=34.5, min_lon=-120.5, max_lon=-117.0)


def position_message(mmsi=367000001, time_utc="2025-03-03 18:22:32.318353417 +0000 UTC", **body):
    report = {"Latitude": 33.75, "Longitude": -118.2, "Sog": 0.1, "Cog": 90.0,
              "TrueHeading": 511, "NavigationalStatus": 5} | body  # fmt: skip
    return {
        "MessageType": "PositionReport",
        "MetaData": {"MMSI": mmsi, "ShipName": "TEST  ", "time_utc": time_utc},
        "Message": {"PositionReport": report},
    }


def static_message(mmsi=367000001):
    return {
        "MessageType": "ShipStaticData",
        "MetaData": {
            "MMSI": mmsi,
            "ShipName": "EVER TEST",
            "time_utc": "2025-03-03 18:00:00 +0000 UTC",
        },
        "Message": {
            "ShipStaticData": {
                "ImoNumber": 9811000,
                "CallSign": "H3RC ",
                "Name": "EVER TEST           ",
                "Type": 71,
                "Dimension": {"A": 300, "B": 100, "C": 30, "D": 29},
            }
        },
    }


def test_subscription_uses_region_corners_and_message_filter():
    sub = subscription("key", BOX)
    assert sub["APIKey"] == "key"
    assert sub["BoundingBoxes"] == [[[32.5, -120.5], [34.5, -117.0]]]
    assert "PositionReport" in sub["FilterMessageTypes"]
    assert "ShipStaticData" in sub["FilterMessageTypes"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2025-03-03 18:22:32.318353417 +0000 UTC", datetime(2025, 3, 3, 18, 22, 32, 318353, UTC)),
        ("2025-03-03 18:22:32 +0000 UTC", datetime(2025, 3, 3, 18, 22, 32, tzinfo=UTC)),
    ],
)
def test_parse_time_handles_nanoseconds_and_whole_seconds(value, expected):
    assert parse_time(value) == expected


def test_parse_position_message():
    kind, row = parse_message(position_message())
    assert kind == "position"
    assert row["mmsi"] == 367000001
    assert (row["latitude"], row["longitude"], row["sog"], row["nav_status"]) == (
        33.75,
        -118.2,
        0.1,
        5,
    )


def test_parse_static_message_derives_size_and_trims_text():
    kind, row = parse_message(static_message())
    assert kind == "static"
    assert row["name"] == "EVER TEST"
    assert row["call_sign"] == "H3RC"
    assert row["imo"] == "IMO9811000"
    assert (row["vessel_type"], row["length_m"], row["width_m"]) == (71, 400, 59)


def test_unknown_or_incomplete_messages_are_ignored():
    assert (
        parse_message({"MessageType": "BaseStationReport", "MetaData": {}, "Message": {}}) is None
    )
    assert parse_message({"MessageType": "PositionReport", "Message": {}}) is None


def test_batch_frames_validates_and_adds_bare_vessels_for_positions():
    positions = [
        parse_message(position_message(mmsi=367000001))[1],
        parse_message(position_message(mmsi=367000002))[1],
        parse_message(position_message(mmsi=367000003, Latitude=40.0))[1],  # outside region
    ]
    statics = [parse_message(static_message(mmsi=367000001))[1]]

    vessels, frame, counts = batch_frames(positions, statics, BOX)

    assert sorted(frame["mmsi"]) == [367000001, 367000002]
    assert counts == {"outside_region": 1}
    assert frame["heading"].isna().all()  # 511 = not available
    by_mmsi = vessels.set_index("mmsi")
    assert by_mmsi.loc[367000001, "name"] == "EVER TEST"
    assert pd.isna(by_mmsi.loc[367000002, "name"])
