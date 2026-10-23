"""Geographic regions used to keep the MVP dataset small."""

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class BoundingBox:
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    def contains(self, lat: pd.Series, lon: pd.Series) -> pd.Series:
        return lat.between(self.min_lat, self.max_lat) & lon.between(self.min_lon, self.max_lon)


# MVP focuses on a single region; add more as the project grows.
REGIONS: dict[str, BoundingBox] = {
    # Ports of Los Angeles / Long Beach and approaches
    "socal": BoundingBox(min_lat=32.5, max_lat=34.5, min_lon=-120.5, max_lon=-117.0),
}
