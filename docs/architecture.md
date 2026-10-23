# Architecture

## Pipeline

1. **Extract** (`etl/extract.py`): download daily MarineCadastre AIS zips and read the WPI CSV.
2. **Transform** (`etl/transform.py`): pure pandas functions that
   - rename source columns to the internal schema
   - parse timestamps as UTC
   - drop invalid MMSIs and out-of-range coordinates
   - null AIS "not available" sentinels (heading 511, SOG ≥ 102.3)
   - filter to the MVP region bounding box
   - deduplicate on `(mmsi, ts)`
   - split rows into a vessel dimension and a position fact table
3. **Load** (`etl/load.py`): stage each frame into a temporary table and upsert it into the
   target table. Re-running the same file is idempotent.
4. **Aggregate** (TODO): detect port calls from positions near a port at low speed, derive
   voyages, and rebuild the daily summary tables.

Every load is recorded in `source_runs` with its status, row counts and any error.

## Data model

| Table                 | Grain                                  | Role       |
|-----------------------|----------------------------------------|------------|
| `source_runs`         | one pipeline run                       | lineage    |
| `ports`               | one WPI port                           | dimension  |
| `vessels`             | one MMSI (latest attributes)           | dimension  |
| `vessel_positions`    | one AIS report `(mmsi, ts)`            | fact       |
| `port_calls`          | one vessel stay at one port            | derived    |
| `vessel_routes`       | one voyage between consecutive calls   | derived    |
| `port_activity_daily` | port × day                             | aggregate  |
| `route_summary`       | origin × destination × day             | aggregate  |

`ports.geom` and `vessel_positions.geom` are generated PostGIS geography columns with GiST
indexes. They support radius queries such as `ST_DWithin(p.geom, port.geom, 5000)` when
detecting port calls.

## MVP scope

- one region (`socal`: Los Angeles / Long Beach)
- 1–2 weeks of AIS data
- 4–6 API endpoints
- a dashboard with 3–5 charts
