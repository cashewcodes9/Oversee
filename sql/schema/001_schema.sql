-- Initial schema for the Port Activity Intelligence Platform.
-- Runs automatically on first start of the PostGIS container.

CREATE EXTENSION IF NOT EXISTS postgis;

-- ---------------------------------------------------------------------------
-- Pipeline bookkeeping
-- ---------------------------------------------------------------------------
CREATE TABLE source_runs (
    run_id       SERIAL PRIMARY KEY,
    source       TEXT        NOT NULL,          -- 'marinecadastre_ais', 'wpi', ...
    file_name    TEXT,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    status       TEXT        NOT NULL DEFAULT 'running',  -- running | success | failed
    rows_read    INTEGER,
    rows_loaded  INTEGER,
    error        TEXT
);

-- ---------------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------------
CREATE TABLE ports (
    port_id       SERIAL PRIMARY KEY,
    wpi_number    INTEGER UNIQUE,
    name          TEXT             NOT NULL,
    country_code  TEXT,
    unlocode      TEXT,
    latitude      DOUBLE PRECISION NOT NULL,
    longitude     DOUBLE PRECISION NOT NULL,
    harbor_size   TEXT,
    geom          GEOGRAPHY(Point, 4326)
        GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography) STORED
);
CREATE INDEX ports_geom_idx ON ports USING GIST (geom);

CREATE TABLE vessels (
    mmsi         BIGINT PRIMARY KEY,
    imo          TEXT,
    name         TEXT,
    call_sign    TEXT,
    vessel_type  INTEGER,
    length_m     DOUBLE PRECISION,
    width_m      DOUBLE PRECISION,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Movement data
-- ---------------------------------------------------------------------------
CREATE TABLE vessel_positions (
    position_id  BIGSERIAL PRIMARY KEY,
    mmsi         BIGINT           NOT NULL REFERENCES vessels (mmsi),
    ts           TIMESTAMPTZ      NOT NULL,
    latitude     DOUBLE PRECISION NOT NULL,
    longitude    DOUBLE PRECISION NOT NULL,
    sog          REAL,            -- speed over ground, knots
    cog          REAL,            -- course over ground, degrees
    heading      REAL,
    nav_status   INTEGER,
    run_id       INTEGER REFERENCES source_runs (run_id),
    geom         GEOGRAPHY(Point, 4326)
        GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography) STORED,
    UNIQUE (mmsi, ts)
);
CREATE INDEX vessel_positions_ts_idx ON vessel_positions (ts);
CREATE INDEX vessel_positions_geom_idx ON vessel_positions USING GIST (geom);

-- A vessel's stay inside a port's radius (derived from positions)
CREATE TABLE port_calls (
    port_call_id  BIGSERIAL PRIMARY KEY,
    mmsi          BIGINT      NOT NULL REFERENCES vessels (mmsi),
    port_id       INTEGER     NOT NULL REFERENCES ports (port_id),
    arrival_ts    TIMESTAMPTZ NOT NULL,
    departure_ts  TIMESTAMPTZ,
    UNIQUE (mmsi, port_id, arrival_ts)
);

-- A voyage between two consecutive port calls of the same vessel
CREATE TABLE vessel_routes (
    route_id             BIGSERIAL PRIMARY KEY,
    mmsi                 BIGINT  NOT NULL REFERENCES vessels (mmsi),
    origin_port_id       INTEGER NOT NULL REFERENCES ports (port_id),
    destination_port_id  INTEGER NOT NULL REFERENCES ports (port_id),
    departure_ts         TIMESTAMPTZ NOT NULL,
    arrival_ts           TIMESTAMPTZ NOT NULL
);

-- ---------------------------------------------------------------------------
-- Aggregates (reporting layer)
-- ---------------------------------------------------------------------------
CREATE TABLE port_activity_daily (
    port_id          INTEGER NOT NULL REFERENCES ports (port_id),
    activity_date    DATE    NOT NULL,
    arrivals         INTEGER NOT NULL DEFAULT 0,
    departures       INTEGER NOT NULL DEFAULT 0,
    unique_vessels   INTEGER NOT NULL DEFAULT 0,
    avg_dwell_hours  DOUBLE PRECISION,
    PRIMARY KEY (port_id, activity_date)
);

CREATE TABLE route_summary (
    origin_port_id       INTEGER NOT NULL REFERENCES ports (port_id),
    destination_port_id  INTEGER NOT NULL REFERENCES ports (port_id),
    summary_date         DATE    NOT NULL,
    voyages              INTEGER NOT NULL DEFAULT 0,
    avg_duration_hours   DOUBLE PRECISION,
    PRIMARY KEY (origin_port_id, destination_port_id, summary_date)
);
