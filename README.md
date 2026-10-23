# Oversee – Port Activity Intelligence Platform

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
World Port Index ───┘        src/oversee/etl                  sql/schema          src/oversee/api   dashboard/
```

See [docs/architecture.md](docs/architecture.md) for the data model and pipeline details.

## Stack

| Layer      | Tech                                  |
|------------|---------------------------------------|
| Data       | PostgreSQL 16 + PostGIS               |
| ETL        | Python, pandas, SQLAlchemy            |
| API        | FastAPI, Pydantic                     |
| Dashboard  | Streamlit, Plotly                     |
| Runtime    | Docker Compose                        |

## Project layout

```
src/oversee/
  config.py          settings from environment / .env
  db.py              engine + session dependency
  api/               FastAPI app, routers, response schemas
  etl/               extract, transform, load, regions, CLI
sql/schema/          DDL, applied automatically on first DB start
dashboard/app.py     Streamlit dashboard (talks only to the API)
tests/               unit tests (no database needed)
data/raw/            downloaded source files (git-ignored)
docs/                architecture notes
```

## Getting started

```bash
# 1. Configure
cp .env.example .env

# 2. Start the database (schema is created on first start)
docker compose up -d db

# 3. Install the package locally
python -m venv .venv
.venv\Scripts\activate          # Windows; use `source .venv/bin/activate` elsewhere
pip install -e ".[dashboard,dev]"

# 4. Load data
oversee-etl ports data/raw/UpdatedPub150.csv          # World Port Index export from NGA
oversee-etl download-ais 2024-01-01 2024-01-07
oversee-etl ais data/raw/AIS_2024_01_01.zip --region socal

# 5. Run the API and dashboard
uvicorn oversee.api.main:app --reload                 # http://localhost:8000/docs
streamlit run dashboard/app.py                        # http://localhost:8501
```

Or run everything in containers with `docker compose up --build`.

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
pytest
ruff check .
```

## Roadmap

- [x] Project scaffolding, schema, ETL for ports and AIS positions
- [ ] Port call detection and `port_activity_daily` / `route_summary` aggregation
- [ ] `/movement-summary` endpoint with region and date filters
- [ ] Dashboard: busiest ports, route summary, hourly peak heatmap
- [ ] Optional: orchestration with Dagster or dbt models

## Data sources

- [MarineCadastre AIS vessel traffic](https://hub.marinecadastre.gov/) (NOAA / BOEM)
- [World Port Index](https://msi.nga.mil/Publications/WPI) (NGA)
- Optional enrichment: UN/LOCODE (UNECE)
