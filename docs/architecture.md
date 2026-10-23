# Architecture

## Layers

```
MarineCadastre .csv.zst ─► data/raw/ ─► raw lake (Parquet) ─► region slice ─► PostgreSQL/PostGIS ─► FastAPI
     (one file per day)    (cache)      all US waters,        DuckDB filter,   cleaned positions,
                                        all columns           pandas rules     derived tables
```

| Layer | Where | Holds | Written by |
|---|---|---|---|
| Download cache | `data/raw/` | source files as published | `extract.download_ais_day` |
| Raw lake | `LAKE_ROOT`: `s3://oversee-lake` (SeaweedFS) or `data/lake/` | one Parquet file per day, every source column, no filtering | `lake.Lake.ingest_ais_file` |
| Serving database | PostgreSQL/PostGIS | one region, cleaned, plus port calls and summaries | `load`, `aggregate` |

The lake keeps every day for all US waters in a compact, typed, queryable form, so a new
region or column means re-reading Parquet, not downloading again. PostgreSQL only holds what
the API serves.

**Lake layout:** `<root>/ais/date=YYYY-MM-DD/ais-YYYY-MM-DD.parquet`. The `date=` folder is a
Hive partition, so DuckDB, Athena, Spark or Trino read the whole lake as one table:

```sql
SELECT date, count(*) FROM read_parquet('s3://oversee-lake/ais/*/*.parquet', hive_partitioning = true)
GROUP BY date;
```

**Lineage:** the SHA-256 and size of the source file are stored in each Parquet file's
key-value metadata. Loading a day from the lake records the same fingerprint in
`source_runs`, so `already_loaded` treats a day loaded from the lake and the same file loaded
directly (`oversee-etl ais`) as one.

**Object storage:** locally the compose `lake` service runs SeaweedFS, an Apache-2.0
S3-compatible server (MinIO no longer publishes community images). DuckDB reaches it through
its `httpfs` extension with the `S3_*` settings. With `LAKE_ROOT=s3://<bucket>` and no
`S3_*` settings the same code uses AWS S3 and the standard AWS credential chain. With
`LAKE_ROOT=data/lake` no server is needed.

## Pipeline

1. **Extract** (`etl/extract.py`): download daily MarineCadastre AIS files (cached in
   `data/raw/`) and read the WPI CSV.
2. **Lake** (`etl/lake.py`): DuckDB converts each day's `.csv.zst` to Parquet in the lake
   (about 5 s for 7.6M rows). Days already in the lake are not downloaded again. Loading
   reads a day back with the region bounding box applied in DuckDB and streams the
   remaining rows to pandas in chunks. Rows outside the region are counted as
   `outside_region` in the quality log.
3. **Transform** (`etl/transform.py`): pure pandas functions that
   - rename source columns to the internal schema
   - parse timestamps as UTC
   - drop invalid MMSIs and out-of-range coordinates
   - null AIS "not available" sentinels (heading 511, SOG ≥ 102.3)
   - filter to the MVP region bounding box
   - deduplicate on `(mmsi, ts)`
   - split rows into a vessel dimension and a position fact table
   `etl/clean_sql.py` runs the same rules as DuckDB SQL directly on a lake file, using
   window functions (`LAG`/`LEAD` over each vessel's track) for duplicates and GPS spikes.
   The pipeline still uses pandas. `tests/test_clean_sql.py` requires identical rows and
   drop counts from both. On the four real days 2025-03-03..06 (1.83M rows in `socal`)
   they matched exactly. The SQL version is the one that scales: it cleans a whole US day
   (7.8M rows) in about 20 s within `DUCKDB_MEMORY_LIMIT` (2 GB by default), spilling to
   `DUCKDB_TEMP_DIR` beyond that. That makes it the basis for a cleaned lake layer.
4. **Load** (`etl/load.py`): stage each frame into a temporary table and upsert it into the
   target table. Re-running the same file is idempotent.
5. **Aggregate** (`etl/aggregate.py`): a full refresh of all derived tables in one
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

## Operations

- **Developer commands:** the `Makefile` wraps the common commands (`make help`). `make dev`
  starts db and lake, applies migrations, then runs API and dashboard together. `make etl`
  loads a window. `tasks.py` has the same commands as invoke tasks.
- **Lake storage:** `docker compose up -d --wait lake` (or `make up`) starts SeaweedFS (S3 API on :8333,
  file browser at http://localhost:8888/buckets/). Data lives in the `lakedata` volume.
  The development credentials in `docker/seaweedfs/s3.json` are for local use only.
- **Schema:** Alembic revisions in `src/oversee/migrations/versions/` are the single source
  of the schema. In Docker, a one-shot `migrate` service applies them before the API starts;
  locally, run `oversee-db upgrade`.
- **Logging:** text locally, JSON in containers (`LOG_FORMAT`). ETL records carry the
  `run_id` of their `source_runs` row, so a log line can be traced to the run it belongs to.
  The API logs method, path, status and duration for each request.
- **CI:** GitHub Actions runs lint, format check, unit tests, integration tests against a
  PostGIS service, a migration upgrade and a Docker build on each push and pull request.

## MVP scope

- one region (`socal`: Los Angeles / Long Beach)
- 1–2 weeks of AIS data
- 4–6 API endpoints
- a six-page dashboard (see below)

## Dashboard

Streamlit, reading only from the API. `dashboard/app.py` sets up navigation and the sidebar.
Each page in `dashboard/views/` follows the same pattern: a title, an explanation block
(`common.explain`: what it shows, how to read it, notes and limitations), then charts.

| Page | API endpoints | Answers |
|---|---|---|
| Overview | `/kpis/overview`, `/kpis/daily` | How much data is loaded; how busy each day was |
| Busiest ports | `/kpis/ports`, `/ports?active=true`, `/ports/{id}/activity` | Which ports get most visits |
| Vessel density | `/kpis/density` | Where vessels spend time on the water |
| Routes | `/kpis/routes` | Most common port-to-port trips and their duration |
| Busiest periods | `/kpis/peaks` | Arrival peaks by local weekday and hour |
| Speed and dwell | `/kpis/dwell`, `/kpis/speed` | Time in port; how often and fast vessel groups move |

Times on the busiest-periods page are local (`America/Los_Angeles`, the loaded region);
daily charts use UTC days, matching `port_activity_daily`.
