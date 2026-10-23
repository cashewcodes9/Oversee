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


class KpiOverview(BaseModel):
    ports: int
    vessels: int
    positions: int
    port_calls: int
    first_position_ts: datetime | None
    last_position_ts: datetime | None
