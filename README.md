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
MarineCadastre AIS ─┐
                    ├─► ETL (extract → transform → load) ─► PostgreSQL/PostGIS ─► FastAPI ─► Dashboard
World Port Index ───┘        src/oversee/etl                  migrations          src/oversee/api   dashboard/
```

See [docs/architecture.md](docs/architecture.md) for the data model and pipeline details.

## Stack

| Layer      | Tech                                  |
|------------|---------------------------------------|
| Data       | PostgreSQL 16 + PostGIS, Alembic      |
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
  etl/               extract, transform, load, aggregate, regions, CLI
dashboard/app.py     Streamlit dashboard (talks only to the API)
tests/               unit tests; tests/integration/ needs PostgreSQL
tasks.py             invoke tasks (`invoke --list`)
data/raw/            downloaded source files (git-ignored)
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

# 3. Start the database and apply migrations
docker compose up -d --wait db
oversee-db upgrade

# 4. Load data
oversee-etl ports data/raw/UpdatedPub150.csv          # World Port Index export from NGA
oversee-etl download-ais 2024-01-01 2024-01-07
oversee-etl ais data/raw/AIS_2024_01_01.zip --region socal
oversee-etl aggregate                                 # port calls, routes, daily summaries

# 5. Run the API and dashboard
uvicorn oversee.api.main:app --reload                 # http://localhost:8000/docs
streamlit run dashboard/app.py                        # http://localhost:8501
```

Or run everything in containers with `docker compose up --build`: a one-shot `migrate` service
applies migrations before the API starts.

Common commands are also available as invoke tasks: `invoke db`, `invoke migrate`,
`invoke test --unit`, `invoke lint`, `invoke fmt`, `invoke serve`, `invoke dashboard`,
`invoke stack`.

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
`OVERSEE_REQUIRE_DB=1` so they fail instead.

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
- [ ] `/movement-summary` endpoint with region and date filters
- [ ] Dashboard: busiest ports, route summary, hourly peak heatmap
- [ ] Optional: orchestration with Dagster or dbt models

## Data sources

- [MarineCadastre AIS vessel traffic](https://hub.marinecadastre.gov/) (NOAA / BOEM)
- [World Port Index](https://msi.nga.mil/Publications/WPI) (NGA)
- Optional enrichment: UN/LOCODE (UNECE)
