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
4. **Aggregate** (`etl/aggregate.py`): a full refresh of all derived tables in one
   transaction, with SQL doing the work.
   - **Port calls:** positions at ≤ 1 kn within 5 km of a port count as in port. When several
     ports are in range, the nearest one wins. Consecutive in-port reports at the same port
     form one call (gaps-and-islands with window functions). A reporting gap over 6 h splits
     a call, and calls shorter than 60 min are dropped. A call still in progress at the end of
     the data keeps `departure_ts` NULL.
   - **Voyages:** consecutive calls of one vessel at different ports.
   - **Daily activity:** for each port and UTC day, the arrivals, departures and distinct
     vessels present, plus the average dwell of calls arriving that day.
   - **Route summary:** voyages per origin, destination and departure day, with average
     duration.

   Every threshold can be tuned with `oversee-etl aggregate --help`.

   Known limitations:
   - Arrivals are only as precise as the AIS reporting interval.
   - Calls already under way when the data window starts show the first observed time as
     their arrival.
   - Anchorages within the radius count as port time.

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
