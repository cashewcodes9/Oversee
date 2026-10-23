"""Transform step: clean and normalise raw data. Pure functions, no database access.

Cleaning functions return the cleaned frame plus a Counter of rows dropped per
rule, which the load step stores in data_quality_log.
"""

import re
from collections import Counter

import numpy as np
import pandas as pd

from oversee.etl.regions import BoundingBox

# MarineCadastre daily CSV (csv2 format, 2024 onwards)
AIS_COLUMNS = {
    "mmsi": "mmsi",
    "base_date_time": "ts",
    "latitude": "latitude",
    "longitude": "longitude",
    "sog": "sog",
    "cog": "cog",
    "heading": "heading",
    "vessel_name": "name",
    "imo": "imo",
    "call_sign": "call_sign",
    "vessel_type": "vessel_type",
    "status": "nav_status",
    "length": "length_m",
    "width": "width_m",
}

VESSEL_COLUMNS = ["mmsi", "imo", "name", "call_sign", "vessel_type", "length_m", "width_m"]
POSITION_COLUMNS = ["mmsi", "ts", "latitude", "longitude", "sog", "cog", "heading", "nav_status"]

# NGA World Port Index, msi.nga.mil/api/publications/world-port-index?output=csv
WPI_COLUMNS = {
    "portNumber": "wpi_number",
    "portName": "name",
    "countryCode": "country_code",
    "unloCode": "unlocode",
    "latitude": "latitude",
    "longitude": "longitude",
    "harborSize": "harbor_size",
}
HARBOR_SIZES = {"V": "very small", "S": "small", "M": "medium", "L": "large"}

# AIS "not available" sentinel values
HEADING_NOT_AVAILABLE = 511
SOG_NOT_AVAILABLE = 102.3

# Physical plausibility limits; faster readings are GPS errors
MAX_SOG_KNOTS = 50
MAX_IMPLIED_SPEED_KNOTS = 60
EARTH_RADIUS_NM = 3440.065


def _drop(df: pd.DataFrame, mask: pd.Series, rule: str, counts: Counter) -> pd.DataFrame:
    if n := int(mask.sum()):
        counts[rule] += n
    return df[~mask]


def normalise_ais(df: pd.DataFrame) -> pd.DataFrame:
    """Rename MarineCadastre columns to the internal schema and parse timestamps as UTC."""
    out = df.rename(columns=AIS_COLUMNS)[list(AIS_COLUMNS.values())].copy()
    out["ts"] = pd.to_datetime(out["ts"], utc=True, errors="coerce")
    return out


def validate_positions(
    df: pd.DataFrame, bbox: BoundingBox | None = None
) -> tuple[pd.DataFrame, Counter]:
    """Row-level rules on internally named columns. Safe to apply chunk by chunk."""
    counts: Counter = Counter()
    out = _drop(
        df,
        df[["mmsi", "ts", "latitude", "longitude"]].isna().any(axis=1),
        "missing_required",
        counts,
    )
    # Valid MMSIs are 9 digits; lat/lon outside range are AIS "not available"
    out = _drop(out, ~out["mmsi"].between(100_000_000, 999_999_999), "invalid_mmsi", counts)
    out = _drop(
        out,
        ~(out["latitude"].between(-90, 90) & out["longitude"].between(-180, 180)),
        "invalid_position",
        counts,
    )
    if bbox is not None:
        out = _drop(
            out, ~bbox.contains(out["latitude"], out["longitude"]), "outside_region", counts
        )

    out = out.copy()
    out.loc[out["heading"] == HEADING_NOT_AVAILABLE, "heading"] = None
    out.loc[out["sog"] >= SOG_NOT_AVAILABLE, "sog"] = None
    out = _drop(out, out["sog"] > MAX_SOG_KNOTS, "sog_over_limit", counts)

    out["mmsi"] = out["mmsi"].astype("int64")
    return out.reset_index(drop=True), counts


def _haversine_nm(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (
        np.sin((lat2 - lat1) / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_NM * np.arcsin(np.sqrt(a))


def clean_tracks(df: pd.DataFrame) -> tuple[pd.DataFrame, Counter]:
    """Rules that compare reports of the same vessel; apply to the whole file at once.

    A report is a GPS spike when the speed implied by the distance from the previous
    report is above MAX_IMPLIED_SPEED_KNOTS and the speed onward to the next report is
    too (or there is no next report). The first report of a track is never flagged.
    """
    counts: Counter = Counter()
    out = _drop(df, df.duplicated(subset=["mmsi", "ts"]), "duplicate", counts)
    out = out.sort_values(["mmsi", "ts"]).reset_index(drop=True)

    by_vessel = out.groupby("mmsi")
    hours = by_vessel["ts"].diff().dt.total_seconds() / 3600
    distance = _haversine_nm(
        by_vessel["latitude"].shift(), by_vessel["longitude"].shift(),
        out["latitude"], out["longitude"],
    )  # fmt: skip
    speed_in = distance / hours
    speed_out = speed_in.groupby(out["mmsi"]).shift(-1)
    spike = (speed_in > MAX_IMPLIED_SPEED_KNOTS) & (
        speed_out.isna() | (speed_out > MAX_IMPLIED_SPEED_KNOTS)
    )
    out = _drop(out, spike, "implied_speed", counts)
    return out.reset_index(drop=True), counts


def clean_ais(df: pd.DataFrame, bbox: BoundingBox | None = None) -> tuple[pd.DataFrame, Counter]:
    """All AIS rules on a raw MarineCadastre frame that fits in memory."""
    rows, counts = validate_positions(normalise_ais(df), bbox)
    tracks, track_counts = clean_tracks(rows)
    return tracks, counts + track_counts


def split_vessels(positions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split cleaned AIS rows into a vessel dimension and a position fact table."""
    vessels = positions.sort_values("ts").groupby("mmsi", as_index=False)[VESSEL_COLUMNS[1:]].last()
    return vessels, positions[POSITION_COLUMNS]


_DMS = re.compile(r"""(\d+)°\s*(\d+)'\s*(\d+(?:\.\d+)?)"?\s*([NSEW])""")


def parse_dms(value) -> float | None:
    """Convert 30°20'00"N style coordinates to decimal degrees; numbers pass through."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, int | float):
        return float(value)
    match = _DMS.search(str(value))
    if not match:
        try:
            return float(value)
        except ValueError:
            return None
    deg, minutes, seconds, hemisphere = match.groups()
    decimal = int(deg) + int(minutes) / 60 + float(seconds) / 3600
    return -decimal if hemisphere in "SW" else decimal


def clean_wpi(df: pd.DataFrame) -> tuple[pd.DataFrame, Counter]:
    """Select, type and validate port reference rows from the World Port Index."""
    counts: Counter = Counter()
    out = df.rename(columns=WPI_COLUMNS)[list(WPI_COLUMNS.values())].copy()
    out["latitude"] = out["latitude"].map(parse_dms)
    out["longitude"] = out["longitude"].map(parse_dms)
    out["harbor_size"] = out["harbor_size"].map(HARBOR_SIZES)
    out["unlocode"] = out["unlocode"].str.strip().replace("", None)

    out = _drop(
        out,
        out[["wpi_number", "name", "latitude", "longitude"]].isna().any(axis=1),
        "missing_required",
        counts,
    )
    out = _drop(out, out.duplicated(subset=["wpi_number"]), "duplicate", counts)
    out["wpi_number"] = out["wpi_number"].astype("int64")
    return out.reset_index(drop=True), counts
