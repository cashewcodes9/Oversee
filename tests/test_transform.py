import pandas as pd

from oversee.etl.regions import BoundingBox
from oversee.etl.transform import clean_ais, clean_wpi, split_vessels


def ais_row(**overrides):
    row = {
        "MMSI": 367000001, "BaseDateTime": "2024-01-01T00:00:00", "LAT": 33.7, "LON": -118.2,
        "SOG": 12.0, "COG": 90.0, "Heading": 88, "VesselName": "TEST", "IMO": "IMO1234567",
        "CallSign": "WABC", "VesselType": 70, "Status": 0, "Length": 200, "Width": 30,
    }
    return row | overrides


def test_clean_ais_drops_invalid_and_duplicates():
    raw = pd.DataFrame([
        ais_row(),
        ais_row(),                       # duplicate
        ais_row(MMSI=123),               # invalid MMSI
        ais_row(LAT=91, BaseDateTime="2024-01-01T00:01:00"),  # lat not available
    ])
    out = clean_ais(raw)
    assert len(out) == 1
    assert str(out["ts"].dt.tz) == "UTC"


def test_clean_ais_nulls_sentinels():
    out = clean_ais(pd.DataFrame([ais_row(Heading=511, SOG=102.3)]))
    assert pd.isna(out.loc[0, "heading"])
    assert pd.isna(out.loc[0, "sog"])


def test_clean_ais_filters_by_region():
    bbox = BoundingBox(min_lat=33, max_lat=34, min_lon=-119, max_lon=-118)
    raw = pd.DataFrame([ais_row(), ais_row(MMSI=367000002, LAT=40.0)])
    assert clean_ais(raw, bbox=bbox)["mmsi"].tolist() == [367000001]


def test_split_vessels_keeps_latest_attributes():
    raw = pd.DataFrame([
        ais_row(VesselName="OLD"),
        ais_row(VesselName="NEW", BaseDateTime="2024-01-01T01:00:00"),
    ])
    vessels, positions = split_vessels(clean_ais(raw))
    assert vessels.loc[0, "name"] == "NEW"
    assert len(positions) == 2


def test_clean_wpi():
    raw = pd.DataFrame([{
        "World Port Index Number": 18080, "Main Port Name": "Long Beach",
        "Country Code": "US", "UN/LOCODE": "US LGB", "Latitude": 33.75,
        "Longitude": -118.2, "Harbor Size": "Large", "Extra": "ignored",
    }])
    out = clean_wpi(raw)
    assert out.loc[0, "name"] == "Long Beach"
    assert "Extra" not in out.columns
