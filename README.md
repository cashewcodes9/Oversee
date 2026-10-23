# Oversee – Port Activity Intelligence Platform

[![CI](https://github.com/cashewcodes9/Oversee/actions/workflows/ci.yml/badge.svg)](https://github.com/cashewcodes9/Oversee/actions/workflows/ci.yml)

A small, full-stack shipping analytics product. Open AIS vessel traffic is kept as Parquet in
an S3 data lake. A Python ETL pipeline cleans one region of it, together with World Port Index
reference data, into PostgreSQL/PostGIS. A FastAPI service exposes the data as operational
KPIs, and a dashboard visualises them.

**Business questions it answers**

- Which ports are busiest, and when do activity peaks occur?
- Which routes show the most vessel movement?
- How long do vessels dwell in port?
- Where are operational hotspots or unusual patterns?

## Architecture

```
MarineCadastre AIS ─► raw lake (Parquet on S3) ─┐
                      DuckDB, SeaweedFS locally  ├─► ETL (transform → load) ─► PostgreSQL/PostGIS ─► FastAPI ─► Dashboard
World Port Index ───────────────────────────────┘     src/oversee/etl           migrations          src/oversee/api   dashboard/
```

Each AIS day is kept unfiltered as Parquet in an S3-compatible lake, and only the region
being analysed is cleaned into PostgreSQL. See [docs/architecture.md](docs/architecture.md)
for the layers, data model and pipeline details.

## Stack

| Layer      | Tech                                  |
|------------|---------------------------------------|
| Data       | PostgreSQL 16 + PostGIS, Alembic      |
| Lake       | Parquet on S3 (SeaweedFS locally), DuckDB |
| ETL        | Python, pandas, DuckDB SQL, SQLAlchemy |
| API        | FastAPI, Pydantic                     |
| Dashboard  | Streamlit, Plotly                     |
| Runtime    | Docker Compose                        |
| Tooling    | GNU Make, invoke                      |
| Quality    | pytest, ruff, pre-commit, GitHub Actions |

## Project layout

```
src/oversee/
  config.py          settings from environment / .env
  db.py              engine + session dependency
  log.py             text / JSON logging, ETL run_id on every record
  migrate.py         `oversee-db` CLI around Alembic
  migrations/        Alembic environment and numbered SQL revisions
  api/               FastAPI app, routers, response schemas
  etl/               extract, lake, transform (pandas), clean_sql (DuckDB), load, aggregate, CLI
dashboard/           Streamlit dashboard (talks only to the API)
  app.py             navigation and sidebar; run this file
  common.py          API access and page helpers
  views/             one file per page
docker/seaweedfs/    S3 credentials for the local lake service
tests/               unit tests; tests/integration/ needs PostgreSQL (and the lake for S3 tests)
Makefile             developer commands (`make help`)
tasks.py             the same commands as invoke tasks (`invoke --list`)
data/raw/            downloaded source files (git-ignored)
data/lake/           local lake when LAKE_ROOT=data/lake (git-ignored)
data/tmp/            DuckDB spill files (git-ignored)
docs/                architecture notes
```

## Running the project

### Prerequisites

- **Python 3.11+**, **Docker** (Docker Desktop on Windows, running), and **GNU Make**
  (Windows: `scoop install make` or `winget install ezwinports.make`; or use the commands
  under "Without Make" below)
- **Free ports:** 5432 (PostgreSQL), 8333 and 8888 (lake), 8000 (API), 8501 (dashboard)
- **Disk:** about 1.5 GB for the default 4-day window (see "Load data")

### 1. First-time setup

```bash
make venv                        # create .venv
make install                     # install the package with dashboard and dev extras
make env                         # create .env from .env.example
.venv/Scripts/pre-commit install # optional: lint on every commit (.venv/bin/ outside Windows)
```

`.env` works as created. Add `AISSTREAM_API_KEY` only if you want the live feed (see
[Configuration](#configuration)).

### 2. Start everything

```bash
make dev
```

This starts PostgreSQL and the S3 lake in Docker, waits until they are healthy, applies
database migrations, then runs the API and the dashboard. Leave this terminal open; Ctrl+C
stops the API and dashboard.

### 3. Load data (first run only)

The dashboard is empty until data is loaded. In a **second terminal**:

```bash
make etl
```

This loads the World Port Index, downloads AIS days 2025-03-03..06 into the lake, loads the
Los Angeles / Long Beach region into PostgreSQL and builds port calls and summaries. It takes
about 5 minutes. Each extra day adds about 1–2 minutes, 180 MB in the lake and 130 MB in
PostgreSQL. Data stays in Docker volumes, so this is a one-time step. Options:

```bash
make etl START=2025-03-03 END=2025-03-16   # a longer window; days already loaded are skipped
make stream MINUTES=10                     # live positions; needs AISSTREAM_API_KEY in .env
```

### 4. Check it works

- Dashboard: http://localhost:8501 shows headline numbers and the pages listed in the sidebar
- API docs: http://localhost:8000/docs, where you can try every endpoint
- Health check: http://localhost:8000/health returns `{"status": "ok", ...}`
- Lake files: http://localhost:8888/buckets/oversee-lake/ais/

### Day to day

| To | Run |
|---|---|
| Start working | `make dev` (data from earlier runs is still there) |
| Stop | Ctrl+C in the `make dev` terminal, then `make down` to stop the containers |
| Add more days | `make etl START=... END=...` |
| Run the tests | `make test-unit` (no containers), or `make test` with `make up` running |
| Start from scratch | `docker compose down -v` (**deletes** the database and lake volumes), then steps 2–3 |

### Run fully in Docker

No Python setup needed on the host:

```bash
cp .env.example .env
make stack                                   # or: docker compose up --build
# second terminal, to load data:
docker compose run --rm api oversee-etl run --region socal --start 2025-03-03 --end 2025-03-06
```

A one-shot `migrate` service applies migrations before the API starts. The API and dashboard
are on the same ports as above.

<details>
<summary>Without Make</summary>

```bash
cp .env.example .env
python -m venv .venv
.venv\Scripts\activate          # Windows; use `source .venv/bin/activate` elsewhere
pip install -e ".[dashboard,dev]"

docker compose up -d --wait db lake
oversee-db upgrade
oversee-etl run --region socal --start 2025-03-03 --end 2025-03-06 --delete-raw

uvicorn oversee.api.main:app --reload                 # terminal 1: http://localhost:8000/docs
streamlit run dashboard/app.py                        # terminal 2: http://localhost:8501
```

The same commands are also invoke tasks (`invoke --list`).
</details>

## Make targets

| Target | What it does |
|---|---|
| `make dev` | Start db and lake, apply migrations, run API and dashboard together |
| `make up` / `make down` | Start (and wait for) or stop the db and lake containers; data volumes are kept |
| `make migrate` | Apply database migrations |
| `make api` / `make dashboard` | Run one of the two on its own |
| `make stack` | Everything in Docker |
| `make etl` | End to end: ports, AIS days through the lake, aggregates |
| `make lake-ingest` / `make lake-load` | Download days into the lake / load them from the lake for a region |
| `make aggregate` | Rebuild port calls, routes and summaries |
| `make stream` | Collect live AIS for `MINUTES` |
| `make test` / `make test-unit` | All tests / unit tests only (no containers) |
| `make lint` / `make fmt` | Check as CI does / fix and format |

Variables: `REGION` (default `socal`), `START` and `END` (default `2025-03-03`..`2025-03-06`),
`MINUTES` (default `5`), e.g. `make lake-load START=2025-03-05 END=2025-03-05`.

## Configuration

Settings come from `.env` (see [.env.example](.env.example)) or the environment.

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | `localhost`, `5432`, `oversee` | Database connection |
| `LAKE_ROOT` | `data/lake` (`.env.example`: `s3://oversee-lake`) | Lake location: a local folder or an S3 URI |
| `S3_ENDPOINT`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_USE_SSL`, `S3_URL_STYLE`, `S3_REGION` | unset (`S3_REGION`: `us-east-1`) | S3-compatible server (the compose `lake` service). Leave unset for AWS S3 with the standard AWS credential chain |
| `DUCKDB_MEMORY_LIMIT`, `DUCKDB_TEMP_DIR` | `2GB`, `data/tmp/duckdb` | DuckDB memory cap; beyond it DuckDB spills to disk |
| `AISSTREAM_API_KEY` | unset | Live feed key from https://aisstream.io |
| `LOG_LEVEL`, `LOG_FORMAT` | `INFO`, `text` | Logging; `json` in containers |
| `API_BASE_URL` | `http://localhost:8000` | Where the dashboard finds the API |

## Data pipeline

Two feeds write to the same tables:

- **Batch** (`oversee-etl run`): MarineCadastre daily files for a date window. Each day is
  downloaded and converted by DuckDB to Parquet in the raw lake, unless it is already
  there. It is then read back filtered to the region, cleaned and loaded with `COPY`.
  Finally port calls and summaries are rebuilt. A file that was already loaded (same
  SHA-256) is skipped, so re-running a window is safe.
- **Live** (`oversee-etl stream`): an AISStream WebSocket collector that validates and
  writes buffered messages every 60 s, reconnects with backoff and flushes on Ctrl+C.

Individual steps are also available: `oversee-etl ports`, `download-ais`, `lake-ingest`,
`lake-load`, `ais` (a raw file straight into PostgreSQL, bypassing the lake), and
`aggregate` (`oversee-etl <step> --help`). Every run is recorded in `source_runs`, and
rows dropped by each cleaning rule in `data_quality_log`. Sources, licences and rules are
documented in [data/README.md](data/README.md).

The cleaning rules exist twice: in pandas (`transform.py`, used by the pipeline) and in
DuckDB SQL (`clean_sql.py`). Tests require both to give the same rows and drop counts. The
SQL version cleans a whole US day (7.8M rows) in about 20 s within the DuckDB memory cap.
It is the basis for a cleaned lake layer (see the roadmap).

The lake can be queried directly with DuckDB, for example to count reports per day:

```sql
SELECT date, count(*) FROM read_parquet('s3://oversee-lake/ais/*/*.parquet', hive_partitioning = true)
GROUP BY date ORDER BY date;
```

In Python, `oversee.etl.lake.open_lake().con` is a DuckDB connection already set up for the
lake.

## Database migrations

The schema is managed by Alembic. Revisions are hand-written SQL in
`src/oversee/migrations/versions/`, numbered `0001`, `0002`, ...

```bash
oversee-db revision -m "add vessel groups"   # new empty revision, next number
oversee-db upgrade                           # apply pending revisions
oversee-db downgrade -1                      # roll back one
oversee-db current
```

A database created before migrations existed is stamped at the baseline automatically.

## Logging

Set `LOG_FORMAT=text` (default, readable) or `LOG_FORMAT=json` (one object per line, used in
containers) and `LOG_LEVEL`. ETL log lines carry the `run_id` of the `source_runs` row they
belong to; the API logs method, path, status and duration for every request.

## API endpoints

| Method | Path                         | Description                         |
|--------|------------------------------|-------------------------------------|
| GET    | `/health`                    | Service health                      |
| GET    | `/ports`                     | Port catalogue (filter by country; `active=true` for ports with activity) |
| GET    | `/ports/{port_id}/activity`  | Daily arrivals/departures/dwell     |
| GET    | `/vessels`                   | Vessel list (filter by type)        |
| GET    | `/vessels/{mmsi}/routes`     | Voyages between ports               |
| GET    | `/kpis/overview`             | Headline counts and data coverage   |
| GET    | `/kpis/daily`                | AIS reports, active vessels, arrivals and departures per day |
| GET    | `/kpis/ports`                | Ports ranked by port calls (`start`, `end`, `limit`) |
| GET    | `/kpis/density`              | Reports and vessels per grid cell (`cell_deg`, `vessel_group`) |
| GET    | `/kpis/routes`               | Busiest origin → destination pairs with average trip time |
| GET    | `/kpis/peaks`                | Arrivals by weekday and hour (`port_id`, `tz`) |
| GET    | `/kpis/dwell`                | Time in port per vessel group, median and 90th percentile (`port_id`) |
| GET    | `/kpis/speed`                | Share of time moving and moving speed per vessel group |

## Dashboard

Six pages, listed in the sidebar. Each one starts with **what it shows**, **how to read
it**, and a **Notes and limitations** box:

| Page | Shows |
|---|---|
| **Overview** | A guide to the dashboard, headline numbers, daily traffic, and a glossary of AIS terms |
| **Busiest ports** | Ports ranked by port calls, on a map, and day-by-day activity for a chosen port |
| **Vessel density** | A heatmap of where vessels report from, filterable by vessel group and grid size |
| **Routes** | The most travelled port-to-port routes, on a map and in a table, with trip times |
| **Busiest periods** | Arrivals by weekday and hour in local time, for the region or one port |
| **Speed and dwell** | Time in port by vessel group, and how often and how fast each group moves |

The dashboard only talks to the API. Pages live in `dashboard/views/`, one file each, and
shared helpers (API access, the explanation block) are in `dashboard/common.py`.

## Tests

```bash
make test         # everything (pytest)
make test-unit    # unit tests only, no containers (pytest --ignore=tests/integration)
make lint         # ruff check + format check, as CI runs them
pytest tests/test_transform.py::test_clean_ais_nulls_sentinels   # a single test
```

Integration tests in `tests/integration/` need the database (`make up`). They
create throwaway databases, build them through the migrations and drop them afterwards, so
development data is untouched. Without a reachable server they are skipped locally; CI sets
`OVERSEE_REQUIRE_DB=1` so they fail instead. `tests/integration/test_lake_s3.py` runs against
the `lake` service when it is up and is skipped otherwise; CI does not run it. It writes
under a unique `test-*` prefix in the bucket. Lake unit tests and the pandas vs SQL parity
tests (`tests/test_clean_sql.py`) use a local folder and need no containers.
`tests/test_dashboard.py` renders every dashboard page against a fake API (with data, with
none, and with the API down). `tests/integration/test_api_kpis.py` checks the KPI endpoints
against a small known dataset.

CI (GitHub Actions) runs lint, unit tests, integration tests against a PostGIS service and a
Docker image build on every push to `main` and every pull request.

## Troubleshooting

- **Dashboard says "API not reachable":** start the API (`make api`, or use `make dev`), or
  set `API_BASE_URL`.
- **DuckDB `Out of Memory Error: Allocation failure`:** lower `DUCKDB_MEMORY_LIMIT`. DuckDB
  then spills to `DUCKDB_TEMP_DIR` instead of asking Windows for memory it does not have.
- **Lake volume much larger than its files:** SeaweedFS's defaults reserve about 1 GB per
  volume file. The compose `lake` service uses 256 MB volumes without preallocation; keep
  those flags if you change the command.
- **`pre-commit` fails with `FileNotFoundError` under `...\Packages\PythonSoftwareFoundation...`:**
  the Microsoft Store Python's cache path is too long. Set
  `VIRTUALENV_OVERRIDE_APP_DATA=%USERPROFILE%\.cache\virtualenv` and run it again.
- **`make` not found on Windows:** `scoop install make` or `winget install ezwinports.make`,
  or use the commands under "Without Make".

## Roadmap

- [x] Project scaffolding, schema, ETL for ports and AIS positions
- [x] Port call detection and `port_activity_daily` / `route_summary` aggregation
- [x] Engineering foundations: migrations, CI, pre-commit, structured logging
- [x] Real data: 2025 MarineCadastre batch window + live AISStream collector, data quality log
- [x] Raw data lake: daily Parquet on S3-compatible storage, written and filtered with DuckDB
- [x] Cleaning rules in DuckDB SQL, matching pandas on real data
- [x] Developer tooling: Makefile, dashboard picker limited to ports with activity
- [ ] Cleaned lake layer: whole US days cleaned once in SQL, regions loaded from it
- [ ] `/movement-summary` endpoint with region and date filters
- [x] Six-page dashboard: overview, busiest ports, vessel density, routes, busiest periods,
      speed and dwell, with explanations on every page
- [ ] Optional: orchestration with Dagster or dbt models

## Data sources

- [MarineCadastre AIS vessel traffic](https://hub.marinecadastre.gov/) (NOAA / BOEM), 2025 daily files
- [World Port Index](https://msi.nga.mil/Publications/WPI) (NGA)
- [AISStream.io](https://aisstream.io) live AIS feed
- Details, columns and terms: [data/README.md](data/README.md)
