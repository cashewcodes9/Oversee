# Oversee – Port Activity Intelligence Platform

[![CI](https://github.com/cashewcodes9/Oversee/actions/workflows/ci.yml/badge.svg)](https://github.com/cashewcodes9/Oversee/actions/workflows/ci.yml)

A small, full-stack shipping analytics product: open AIS vessel traffic and World Port Index
reference data go through a Python ETL pipeline into PostgreSQL/PostGIS. A FastAPI service
exposes the data as operational KPIs, and a dashboard visualises them.

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
| ETL        | Python, pandas, SQLAlchemy            |
| API        | FastAPI, Pydantic                     |
| Dashboard  | Streamlit, Plotly                     |
| Runtime    | Docker Compose                        |
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
  etl/               extract, lake, transform, load, aggregate, regions, CLI
dashboard/app.py     Streamlit dashboard (talks only to the API)
docker/seaweedfs/    S3 credentials for the local lake service
tests/               unit tests; tests/integration/ needs PostgreSQL (and the lake for S3 tests)
tasks.py             invoke tasks (`invoke --list`)
data/raw/            downloaded source files (git-ignored)
data/lake/           local lake when LAKE_ROOT=data/lake (git-ignored)
docs/                architecture notes
```

## Getting started

```bash
# 1. Configure
cp .env.example .env

# 2. Install the package locally
python -m venv .venv
.venv\Scripts\activate          # Windows; use `source .venv/bin/activate` elsewhere
pip install -e ".[dashboard,dev]"
pre-commit install              # ruff + file checks on every commit

# 3. Start the database and the S3 lake, then apply migrations
docker compose up -d --wait db lake
oversee-db upgrade

# 4. Load data: ports, 14 days of AIS for LA / Long Beach, then aggregates
oversee-etl run --region socal --start 2025-03-03 --end 2025-03-16 --delete-raw

# Optional: live positions from AISStream (free key in .env as AISSTREAM_API_KEY)
oversee-etl stream --region socal                     # Ctrl+C to stop

# 5. Run the API and dashboard
uvicorn oversee.api.main:app --reload                 # http://localhost:8000/docs
streamlit run dashboard/app.py                        # http://localhost:8501
```

Or run everything in containers with `docker compose up --build`: a one-shot `migrate` service
applies migrations before the API starts.

With GNU Make installed (Windows: `scoop install make`), `make dev` does steps 3 and 5 in
one go: it starts the containers, applies migrations, then runs the API and dashboard
together. `make etl START=2025-03-03 END=2025-03-16` loads data. `make help` lists all
targets.

Common commands are also available as invoke tasks: `invoke db`, `invoke lake`, `invoke migrate`,
`invoke test --unit`, `invoke lint`, `invoke fmt`, `invoke serve`, `invoke dashboard`,
`invoke stack`.

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
| GET    | `/ports`                     | Port catalogue (filter by country)  |
| GET    | `/ports/{port_id}/activity`  | Daily arrivals/departures/dwell     |
| GET    | `/vessels`                   | Vessel list (filter by type)        |
| GET    | `/vessels/{mmsi}/routes`     | Voyages between ports               |
| GET    | `/kpis/overview`             | Headline counts and data coverage   |

## Tests

```bash
pytest                    # everything
pytest --ignore=tests/integration   # unit tests only, no database
ruff check . && ruff format --check .
```

Integration tests in `tests/integration/` need the database (`docker compose up -d db`). They
create throwaway databases, build them through the migrations and drop them afterwards, so
development data is untouched. Without a reachable server they are skipped locally; CI sets
`OVERSEE_REQUIRE_DB=1` so they fail instead. `tests/integration/test_lake_s3.py` runs against
the `lake` service when it is up (`docker compose up -d --wait lake`) and is skipped
otherwise. It writes under a unique `test-*` prefix in the bucket.

CI (GitHub Actions) runs lint, unit tests, integration tests against a PostGIS service and a
Docker image build on every push to `main` and every pull request.

**Windows note:** if `pre-commit` fails while creating its environments with a
`FileNotFoundError` under `...\Packages\PythonSoftwareFoundation...`, the Microsoft Store
Python's cache path is too long. Set `VIRTUALENV_OVERRIDE_APP_DATA=%USERPROFILE%\.cache\virtualenv`
and run it again.

## Roadmap

- [x] Project scaffolding, schema, ETL for ports and AIS positions
- [x] Port call detection and `port_activity_daily` / `route_summary` aggregation
- [x] Engineering foundations: migrations, CI, pre-commit, structured logging
- [x] Real data: 2025 MarineCadastre batch window + live AISStream collector, data quality log
- [x] Raw data lake: daily Parquet on S3-compatible storage, written and filtered with DuckDB
- [ ] `/movement-summary` endpoint with region and date filters
- [ ] Dashboard: busiest ports, route summary, hourly peak heatmap
- [ ] Optional: orchestration with Dagster or dbt models

## Data sources

- [MarineCadastre AIS vessel traffic](https://hub.marinecadastre.gov/) (NOAA / BOEM), 2025 daily files
- [World Port Index](https://msi.nga.mil/Publications/WPI) (NGA)
- [AISStream.io](https://aisstream.io) live AIS feed
- Details, columns and terms: [data/README.md](data/README.md)
