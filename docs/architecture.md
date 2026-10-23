# Architecture

## Layers

```
MarineCadastre .csv.zst ─► data/raw/ ─► raw lake (Parquet) ─► region slice ─► PostgreSQL ──► dbt models ─► FastAPI
     (one file per day)    (cache)      all US waters,        DuckDB filter,   public:        staging,
                                        all columns           pandas rules     loaded tables  intermediate,
                                                                                              analytics marts
```

Dagster orchestrates the whole chain as assets, with daily partitions for AIS days.

| Layer | Where | Holds | Written by |
|---|---|---|---|
| Download cache | `data/raw/` | source files as published | `extract.download_ais_day` |
| Raw lake | `LAKE_ROOT`: `s3://oversee-lake` (SeaweedFS) or `data/lake/` | one Parquet file per day, every source column, no filtering | `lake.Lake.ingest_ais_file` |
| Loaded tables | PostgreSQL/PostGIS, schema `public` | one region, cleaned: ports, vessels, positions, lineage and quality logs | `load` (schema by Alembic) |
| Analytics | PostgreSQL, schemas `staging`, `intermediate`, `analytics` | port calls, voyages, daily summaries, dimensions | dbt (`transform/`) |

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
5. **Transform in SQL with dbt** (`transform/`, run by `etl/aggregate.py` or Dagster): a
   full rebuild of every model, followed by its data tests. See "Transformation with dbt".
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

   Every threshold is a dbt variable (`port_call_*` in `transform/dbt_project.yml`), also
   exposed as flags on `oversee-etl aggregate --help`.

   Known limitations:
   - Arrivals are only as precise as the AIS reporting interval.
   - Calls already under way when the data window starts show the first observed time as
     their arrival.
   - Anchorages within the radius count as port time.

Every load is recorded in `source_runs` with its status, row counts and any error. A dbt
build through `oversee-etl aggregate` is recorded too (source `dbt`).

## Transformation with dbt

The dbt project in `transform/` owns everything derived from the loaded tables. It reads the
three tables the ETL loads as **sources** and builds three layers:

| Layer | Schema | Materialized | Models |
|---|---|---|---|
| Staging | `staging` | views | `stg_ports`, `stg_vessels`, `stg_positions`: one per source, column selection only |
| Intermediate | `intermediate` | view | `int_in_port_reports`: reports at low speed near a port, nearest port wins (PostGIS) |
| Marts | `analytics` | tables | `fct_port_calls`, `fct_voyages`, `agg_port_activity_daily`, `agg_route_summary`, `dim_ports`, `dim_vessels` |

```
ports ─────────────► stg_ports ─────┬─► int_in_port_reports ─► fct_port_calls ─┬─► fct_voyages ─► agg_route_summary
vessel_positions ──► stg_positions ─┘                                          ├─► agg_port_activity_daily
vessels ───────────► stg_vessels ──────────────────────────────────────────────┴─► dim_ports, dim_vessels
```

- **Tests:** 39 data tests run on every build. They cover unique and not-null keys,
  relationships between calls, voyages, ports and vessels, accepted vessel groups, and four
  singular tests in `transform/tests/`: closed calls meet the minimum dwell, a vessel's
  calls never overlap, voyages move forward in time between different ports, and there is
  one activity row per port and day. A failing test fails the build.
- **Exposures:** the KPI API and the dashboard are declared as exposures, so the lineage
  graph in `make dbt-docs` ends at the products that use the data.
- **Running it:** `oversee-dbt <command>` (or `make dbt-build`, `make dbt-docs`) fills the
  `POSTGRES_*` connection from `.env` and points dbt at `transform/`. `oversee-etl
  aggregate` and `oversee-etl run` call `dbt build` the same way.
- **Migration from Python:** the SQL is the same as the earlier Python aggregation step.
  On the four loaded days, the dbt marts matched its tables row for row: 1,381 port calls,
  159 voyages, 52 port-days and 43 route-days. Alembic revision `0003` dropped the old
  tables.

## Orchestration with Dagster

`src/oversee/orchestration/` defines the pipeline as Dagster software-defined assets. Each
asset calls the same pipeline functions as the CLI:

| Asset | Group | Partitions | Does |
|---|---|---|---|
| `ports` | ingest | none | Load the World Port Index (skipped if already loaded) |
| `ais_lake_day` | ingest | daily, 2025-03-03..16 | Download a day into the Parquet lake |
| `vessel_positions`, `vessels` | ingest | daily | Load the day for the region from the lake (one step, two tables) |
| every dbt model | `dbt_staging`, `dbt_intermediate`, `dbt_marts` | none | `dbt build`, through `dagster-dbt` |

The dbt sources point at the ingest assets (`meta.dagster.asset_key`), so Dagster draws one
graph from the raw download to the marts. dbt tests appear as **asset checks**, alongside a
cleaning-quality check on `vessel_positions` that warns when more than 1% of in-region rows
were dropped. Each ingest run records metadata: the lake URI, row counts and rows dropped
per cleaning rule.

There are two jobs: `ingest_ais_day` (partitioned, so days can be backfilled from the UI)
and `refresh_analytics` (`dbt build`). The `refresh_analytics_hourly` schedule runs the
second one every hour. It is stopped by default and meant for when the live collector is
running.

`make dagster` starts the UI on http://localhost:3000, with run history in `.dagster/`.

## Data model

| Table | Grain | Role | Owner |
|---|---|---|---|
| `source_runs` | one pipeline run | lineage | Alembic, ETL |
| `data_quality_log` | rule × run | lineage | Alembic, ETL |
| `ports` | one WPI port | source | Alembic, ETL |
| `vessels` | one MMSI (latest attributes) | source | Alembic, ETL |
| `vessel_positions` | one AIS report `(mmsi, ts)` | source | Alembic, ETL |
| `analytics.fct_port_calls` | one vessel stay at one port | fact | dbt |
| `analytics.fct_voyages` | one trip between consecutive calls | fact | dbt |
| `analytics.agg_port_activity_daily` | port × day | aggregate | dbt |
| `analytics.agg_route_summary` | origin × destination × day | aggregate | dbt |
| `analytics.dim_ports`, `analytics.dim_vessels` | one port / vessel, with totals | dimension | dbt |

`ports.geom` and `vessel_positions.geom` are generated PostGIS geography columns with GiST
indexes. They support radius queries such as `ST_DWithin(p.geom, port.geom, 5000)` when
detecting port calls.

## Operations

- **Developer commands:** the `Makefile` wraps the common commands (`make help`). `make dev`
  starts db and lake, applies migrations, then runs API and dashboard together. `make etl`
  loads a window. `make dbt-build`, `make dbt-docs` and `make dagster` run the
  transformation and orchestration tools. `tasks.py` has the same commands as invoke tasks.
- **Lake storage:** `docker compose up -d --wait lake` (or `make up`) starts SeaweedFS (S3 API on :8333,
  file browser at http://localhost:8888/buckets/). Data lives in the `lakedata` volume.
  The development credentials in `docker/seaweedfs/s3.json` are for local use only.
- **Schema:** Alembic revisions in `src/oversee/migrations/versions/` own the loaded
  tables (`public`); dbt owns the `staging`, `intermediate` and `analytics` schemas. In
  Docker, a one-shot `migrate` service applies the revisions before the API starts;
  locally, run `oversee-db upgrade`.
- **Logging:** text locally, JSON in containers (`LOG_FORMAT`). ETL records carry the
  `run_id` of their `source_runs` row, so a log line can be traced to the run it belongs to.
  The API logs method, path, status and duration for each request.
- **CI:** on each push and pull request, GitHub Actions runs lint, format check, unit
  tests (including the Dagster asset graph), integration tests against a PostGIS service
  (including real `dbt build` runs), a migration upgrade, and a Docker build with
  `oversee-dbt parse`.

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
daily charts use UTC days, matching `analytics.agg_port_activity_daily`.
