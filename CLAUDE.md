# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Oversee is a port activity analytics platform: MarineCadastre AIS vessel traffic is kept as daily Parquet in an S3 lake (SeaweedFS locally, DuckDB to read and write). A Python ETL cleans one region of it, plus the NGA World Port Index, into PostgreSQL/PostGIS. A FastAPI service exposes KPIs, and a Streamlit dashboard visualises them. MVP scope is one region (`socal`, LA/Long Beach), 1–2 weeks of AIS data. See [README.md](README.md) for the roadmap and [docs/architecture.md](docs/architecture.md) for the data model.

## Commands

```bash
pip install -e ".[dashboard,dev]"        # local install (venv at .venv/, Windows: .venv\Scripts\activate)
cp .env.example .env
docker compose up -d --wait db lake       # PostGIS + SeaweedFS S3 lake (:8333, browse :8888/buckets/)
oversee-db upgrade                        # apply Alembic migrations (compose's `migrate` service does this in Docker)
docker compose up --build                 # full stack: db, api :8000, dashboard :8501

pytest                                    # unit tests; tests/integration/ skips unless the db is up
pytest tests/test_transform.py::test_clean_ais_nulls_sentinels   # single test
ruff check .                              # lint (line length 100, rules E/F/I/B/UP)

uvicorn oversee.api.main:app --reload     # API, docs at /docs
streamlit run dashboard/app.py

oversee-etl run --region socal --start 2025-03-03 --end 2025-03-16 --delete-raw   # end to end
oversee-etl stream --region socal --minutes 5   # live AISStream feed; needs AISSTREAM_API_KEY in .env
oversee-etl ports                         # downloads the WPI CSV when no path is given
oversee-etl lake-ingest 2025-03-03 2025-03-09   # download + DuckDB → Parquet in the lake
oversee-etl lake-load 2025-03-03 2025-03-09 --region socal [--force]
oversee-etl ais data/raw/ais-2025-03-03.csv.zst --region socal [--force]   # raw file straight to Postgres, no lake
oversee-etl aggregate                     # rebuild port calls, routes and summaries (see --help for thresholds)

make help                                 # Makefile targets: dev (containers + migrate + API + dashboard), etl, lake-*, test, lint, fmt ...
invoke --list                             # same commands as invoke tasks: db, lake, migrate, lint, fmt, test [--unit], serve, dashboard, stack
pre-commit install                        # ruff check/format + file checks on commit
```

Schema changes: never edit an applied revision. Create a new one with `oversee-db revision -m "..."` (numbered after the head, e.g. `0002_...py`), write SQL in `op.execute()` for both `upgrade()` and `downgrade()`, then `oversee-db upgrade`. `tests/integration/test_migrations.py` checks upgrade/downgrade round-trips.

CI (`.github/workflows/ci.yml`) runs ruff check + format check, unit tests, integration tests against a PostGIS service with `OVERSEE_REQUIRE_DB=1` (missing DB fails instead of skipping), and a Docker build. Keep `ruff` in CI pinned to the pre-commit `rev`.

## Architecture

- **Config** ([src/oversee/config.py](src/oversee/config.py)): `pydantic-settings` reads `POSTGRES_*`, `LAKE_ROOT`, `S3_*`, `LOG_LEVEL`, `LOG_FORMAT` from env/`.env`; `get_settings()` and `get_engine()` ([src/oversee/db.py](src/oversee/db.py)) are `lru_cache`d singletons.
- **Logging** ([src/oversee/log.py](src/oversee/log.py)): every entry point (ETL CLI, `oversee-db`, API module) calls `setup_logging()`. Log with `logger.info("msg", extra={...})`; extras become JSON fields or `key=value` text. `load.source_run` sets `run_id_var`, so every record during a run carries `run_id`. Use logging, not `print`, except for direct CLI answers like `oversee-db current`.
- **ETL** ([src/oversee/etl/](src/oversee/etl/)): `extract` → `lake` → `transform` → `load`, orchestrated in `pipeline.py` (`load_ports_file`, `load_ais_file`, `ingest_ais_day`, `load_ais_day`, `rebuild_aggregates`, `run_window`); `cli.py` is a thin argparse layer over `pipeline` and `stream`. Source formats and cleaning rules are documented in [data/README.md](data/README.md).
  - `lake.py` is the raw data lake: one unfiltered Parquet file per AIS day (`<LAKE_ROOT>/ais/date=YYYY-MM-DD/...`), written and read by DuckDB. `LAKE_ROOT` is `s3://oversee-lake` (compose `lake` service, SeaweedFS, since MinIO images are no longer published) or a local dir (default `data/lake`; tests use `Lake(str(tmp_path), duckdb.connect())`). The source file's SHA-256 is stored in Parquet KV metadata, so `already_loaded` treats lake loads and direct `ais` loads of the same file as one. The region bbox is applied in DuckDB, and those rows are counted as `outside_region`. `run_window` goes through the lake (`ingest_ais_day` skips the download when the day is already stored, then `load_ais_day`). DuckDB takes no parameters in `COPY`/`CREATE SECRET`, so those use `_literal()`. On S3, `glob()` of an exact missing key raises instead of matching nothing, so `ais_day` globs the folder.
  - `transform.py` holds pure pandas functions and is the unit-tested layer; keep DB access out of it. Cleaning functions return `(frame, Counter)` where the Counter holds rows dropped per rule. `validate_positions` (row-level rules + region filter) is safe per chunk; `clean_tracks` (dedupe, GPS-spike rule) needs a vessel's whole track, so it runs after chunks are combined. Rules work on internal column names; `normalise_ais` maps MarineCadastre's csv2 columns.
  - `load.py` stages each DataFrame with `COPY` into a `stg_*` table, then upserts with explicit `CAST`s (an all-null staged column has whatever type pandas guessed). Vessel upserts use `coalesce(EXCLUDED.x, vessels.x)` so position-only data never erases known attributes, and assign `vessel_group` from `vessel_type_groups`. `source_run(...)` records lineage, status, row counts, file SHA-256/size and errors in `source_runs`; `already_loaded()` makes file loads skip identical files unless `--force`; `load_quality_log()` accumulates per-rule counts.
  - `clean_sql.py` ports `transform.py`'s AIS rules to DuckDB SQL over a lake file (`clean_positions` → temp table + Counter, `fetch_clean` → DataFrame). Not used by the pipeline yet. Thresholds are imported from `transform.py`; when a rule changes, change both. `tests/test_clean_sql.py` asserts identical rows and counts. `open_lake` caps DuckDB at `DUCKDB_MEMORY_LIMIT` (default 2GB) with spill to `DUCKDB_TEMP_DIR`, because the default (80% of RAM) failed with `Allocation failure` on a 16 GB machine with little RAM free.
  - `stream.py` is the AISStream collector: pure `parse_message`/`batch_frames` (unit-tested) plus an asyncio loop that flushes every `flush_seconds` and in a `finally` on exit. One session = one `source_runs` row with source `aisstream`.
  - Regions are `BoundingBox` entries in `etl/regions.py` (`REGIONS`), which also feed the `--region` CLI choices.
  - `aggregate.py` rebuilds every derived table from `vessel_positions` + `ports` in SQL (truncate and re-insert in one transaction). `run(conn, params)` takes a `Connection`, so tests can run it inside a rolled-back transaction. Port-call thresholds live in `AggregateParams`.
- **Tests**: `tests/` holds pure unit tests. `tests/integration/conftest.py` has `database_factory` (creates throwaway `oversee_test_*` databases, dropped at session end) and `engine` (one such database migrated to head); the `conn` fixture wraps each test in a rolled-back transaction. `tests/integration/test_lake_s3.py` skips unless the `lake` service is on localhost:8333 (not run in CI). ETL code commits through `get_engine()`, so `tests/integration/test_pipeline.py` uses a per-test migrated database and monkeypatches `get_engine` in `load` and `pipeline`.
- **Database** (Alembic revisions in [src/oversee/migrations/versions/](src/oversee/migrations/versions/), baseline `0001`; [src/oversee/migrate.py](src/oversee/migrate.py) wraps Alembic and stamps pre-Alembic databases at `0001`): dimensions `ports` and `vessels`, fact `vessel_positions`, derived `port_calls` and `vessel_routes`, aggregates `port_activity_daily` and `route_summary`. `geom` columns are generated PostGIS geography columns with GiST indexes, meant for `ST_DWithin` port-call detection. The derived and aggregate tables stay empty until `oversee-etl aggregate` runs.
- **API** ([src/oversee/api/](src/oversee/api/)): routers in `routers/` use raw SQL through `sqlalchemy.text()` with a `Session` from the `get_session` dependency (no ORM models). Pydantic response models live in `schemas.py`. Optional filters follow the pattern `(CAST(:x AS TEXT) IS NULL OR col = :x)`. New routers must be registered in `api/main.py`. `tests/test_api.py` asserts the expected paths in the OpenAPI spec.
- **Dashboard** ([dashboard/](dashboard/)): talks only to the REST API (`API_BASE_URL`) and never to the database. `app.py` holds `st.navigation` with six pages in `views/`. Page paths are relative strings (`"views/x.py"`), not `Path(__file__)`, because `AppTest` runs the script from elsewhere. Every page starts with `common.explain(what, how, notes)` and calls `common.no_data()` when its data is empty; new pages should do the same. Each page's data comes from a `/kpis/*` endpoint in `api/routers/kpis.py`. Port pickers use `common.active_ports()` (`/ports?active=true`, columns kept when empty), because the plain list is alphabetical and capped at 1000. `tests/test_dashboard.py` renders every page via `AppTest` with `requests.get` mocked (data, empty, API down), so CI installs the `dashboard` extra.
- **Makefile**: recipes are plain commands so they run under cmd.exe (Scoop's GNU Make from PowerShell) as well as sh. Avoid shell syntax (`&&` chains with `cd`, `$$VAR`, `if [ ]`), and call tools via `$(PY) -m ...`. Keep it in sync with `tasks.py` and the README "Make targets" table.

`research/` is git-ignored planning material, not code.
