from datetime import date, datetime

from pydantic import BaseModel


class Port(BaseModel):
    port_id: int
    name: str
    country_code: str | None
    unlocode: str | None
    latitude: float
    longitude: float


class PortActivity(BaseModel):
    activity_date: date
    arrivals: int
    departures: int
    unique_vessels: int
    avg_dwell_hours: float | None


class Vessel(BaseModel):
    mmsi: int
    imo: str | None
    name: str | None
    vessel_type: int | None
    length_m: float | None


class VesselRoute(BaseModel):
    origin_port_id: int
    destination_port_id: int
    departure_ts: datetime
    arrival_ts: datetime


class DailyTraffic(BaseModel):
    day: date
    positions: int
    active_vessels: int
    arrivals: int
    departures: int


class PortRanking(BaseModel):
    port_id: int
    name: str
    latitude: float
    longitude: float
    port_calls: int
    unique_vessels: int
    avg_dwell_hours: float | None


class DensityCell(BaseModel):
    latitude: float
    longitude: float
    positions: int
    vessels: int


class RouteTotal(BaseModel):
    origin_port_id: int
    origin: str
    origin_latitude: float
    origin_longitude: float
    destination_port_id: int
    destination: str
    destination_latitude: float
    destination_longitude: float
    voyages: int
    avg_duration_hours: float | None


class PeakSlot(BaseModel):
    weekday: int  # 0 = Monday
    hour: int
    arrivals: int


class DwellByGroup(BaseModel):
    vessel_group: str
    port_calls: int
    median_hours: float
    p90_hours: float


class SpeedByGroup(BaseModel):
    vessel_group: str
    positions: int
    moving_share: float  # share of reports above 1 knot
    median_moving_knots: float | None
    p90_moving_knots: float | None


class KpiOverview(BaseModel):
    ports: int
    vessels: int
    positions: int
    port_calls: int
    first_position_ts: datetime | None
    last_position_ts: datetime | None
