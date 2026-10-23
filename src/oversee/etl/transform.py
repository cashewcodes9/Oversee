"""Transform step: clean and normalise raw data. Pure functions, no database access."""

import pandas as pd

from oversee.etl.regions import BoundingBox

AIS_COLUMNS = {
    "MMSI": "mmsi",
    "BaseDateTime": "ts",
    "LAT": "latitude",
    "LON": "longitude",
    "SOG": "sog",
    "COG": "cog",
    "Heading": "heading",
    "VesselName": "name",
    "IMO": "imo",
    "CallSign": "call_sign",
    "VesselType": "vessel_type",
    "Status": "nav_status",
    "Length": "length_m",
    "Width": "width_m",
}

# Column names in the NGA "UpdatedPub150" CSV; check against the downloaded file.
WPI_COLUMNS = {
    "World Port Index Number": "wpi_number",
    "Main Port Name": "name",
    "Country Code": "country_code",
    "UN/LOCODE": "unlocode",
    "Latitude": "latitude",
    "Longitude": "longitude",
    "Harbor Size": "harbor_size",
}

# AIS "not available" sentinel values
HEADING_NOT_AVAILABLE = 511
SOG_NOT_AVAILABLE = 102.3


def clean_ais(df: pd.DataFrame, bbox: BoundingBox | None = None) -> pd.DataFrame:
    """Rename, type, validate and deduplicate raw AIS position reports."""
    out = df.rename(columns=AIS_COLUMNS)[list(AIS_COLUMNS.values())].copy()

    out["ts"] = pd.to_datetime(out["ts"], utc=True, errors="coerce")
    out = out.dropna(subset=["mmsi", "ts", "latitude", "longitude"])

    # Valid MMSIs are 9 digits; lat/lon outside range are AIS "not available"
    out = out[out["mmsi"].between(100_000_000, 999_999_999)]
    out = out[out["latitude"].between(-90, 90) & out["longitude"].between(-180, 180)]

    out.loc[out["heading"] == HEADING_NOT_AVAILABLE, "heading"] = None
    out.loc[out["sog"] >= SOG_NOT_AVAILABLE, "sog"] = None

    if bbox is not None:
        out = out[bbox.contains(out["latitude"], out["longitude"])]

    out["mmsi"] = out["mmsi"].astype("int64")
    return out.drop_duplicates(subset=["mmsi", "ts"]).reset_index(drop=True)


def split_vessels(positions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split cleaned AIS rows into a vessel dimension and a position fact table."""
    vessel_cols = ["mmsi", "imo", "name", "call_sign", "vessel_type", "length_m", "width_m"]
    vessels = (
        positions.sort_values("ts")
        .groupby("mmsi", as_index=False)[vessel_cols[1:]]
        .last()
    )
    position_cols = ["mmsi", "ts", "latitude", "longitude", "sog", "cog", "heading", "nav_status"]
    return vessels, positions[position_cols]


def clean_wpi(df: pd.DataFrame) -> pd.DataFrame:
    """Select and validate port reference rows from the World Port Index."""
    out = df.rename(columns=WPI_COLUMNS)[list(WPI_COLUMNS.values())].copy()
    out = out.dropna(subset=["wpi_number", "name", "latitude", "longitude"])
    out["wpi_number"] = out["wpi_number"].astype("int64")
    return out.drop_duplicates(subset=["wpi_number"]).reset_index(drop=True)
