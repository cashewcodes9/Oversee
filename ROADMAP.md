# Roadmap

Oversee is an **MVP**. It shows the full path from raw maritime data to a dashboard: lake,
ETL, warehouse tables, API and visualisation. It was built under deliberate resource
constraints, and those constraints set its current scope.

## MVP constraints

| Constraint | What it means for the MVP |
|---|---|
| **Budget: free and open data only** | History comes from NOAA MarineCadastre daily files, published after the fact. Live data comes from the free AISStream feed, with no uptime guarantee or replay. No paid AIS providers. |
| **One development laptop** | 16 GB RAM shared with other work, a few GB of free disk. DuckDB is capped at 2 GB of memory and spills to disk beyond that. Postgres and the lake run in local Docker. |
| **Small data window** | One region (Los Angeles / Long Beach) and a few days of AIS data, about 1.8M positions. Each extra day adds about 180 MB to the lake and 130 MB to PostgreSQL. |
| **Local infrastructure** | Object storage is a SeaweedFS container standing in for S3. Dagster runs locally (`make dagster`), with no always-on daemon, cloud deployment, authentication or monitoring. |
| **One developer** | Scope favours a complete, tested slice over breadth. |

The design keeps the way out of each constraint open. The lake code talks S3, so it can
point at AWS S3. Cleaning rules exist in DuckDB SQL ready for larger volumes. Every step is
an idempotent CLI command that a scheduler can call.

## Done

- [x] Schema, migrations (Alembic), ETL for ports and AIS positions
- [x] Port call detection and daily port / route aggregates
- [x] Engineering foundations: CI, pre-commit, structured logging, data quality log
- [x] Real data: 2025 MarineCadastre batch window, plus a live AISStream collector
      (`make stream`, runs on demand)
- [x] Raw data lake: one Parquet file per AIS day on S3-compatible storage, via DuckDB
- [x] Cleaning rules in DuckDB SQL, matching the pandas rules row for row on real data
- [x] Six-page dashboard with explanations on every page, backed by `/kpis/*` endpoints
- [x] Developer tooling: Makefile, run guide, Docker-only path
- [x] dbt transformation layer: staging, intermediate and mart models in PostgreSQL, 39 data
      tests, docs with lineage. It replaced the Python aggregation step with row-for-row
      identical results.
- [x] Dagster orchestration: the pipeline as software-defined assets, daily partitions for
      AIS days, dbt models and tests as assets and checks, a cleaning-quality check, and a
      (stopped) hourly refresh schedule

## Next: real-time data

The pipeline works end to end on historical data. Live data can be collected, but only
on demand, and nothing downstream refreshes. This phase makes Oversee show what is
happening now, alongside the history.

| Step | What | Why |
|---|---|---|
| 1. Always-on collector | A `collector` service in docker-compose runs `oversee-etl stream` permanently and restarts on failure | Live data today stops when the terminal closes |
| 2. Live archive in the lake | Raw live messages also written as daily Parquet (`ais_live/date=…`) | AISStream cannot replay, so the lake keeps the only copy for reprocessing |
| 3. Scheduled refresh | Turn on the Dagster `refresh_analytics` schedule, every 10–15 minutes instead of hourly, with the Dagster daemon running | The dbt models only change when something runs `dbt build`; a full build takes about 20 s on the current data |
| 4. Live API | `/live/vessels` (latest position per vessel in the last N minutes) and `/live/status` (last message time, message rate) | The dashboard needs "now", and a way to tell the feed is healthy |
| 5. Live dashboard page | Map of vessels in the region now, refreshing every 30 s, with a freshness indicator and the usual explanation block | The visible real-time view |
| 6. History / live split | A data-window selector on the history pages | Live days currently stretch the daily charts beyond the batch window |
| 7. Make targets | `make live` to run the collector; optionally part of `make dev` | One command, like the rest |

**Expected limits of the free feed:** coverage depends on volunteer receivers. A 3-minute
test over the region gave about one report per vessel, far sparser than the batch files'
one per minute. Messages sent while the collector is down are lost. Live port calls and
dwell times will therefore be rougher than historical ones. A paid feed (Spire,
Kpler/MarineTraffic) is the fix when data quality matters more than cost.

## Later

**Cleaned lake layer**
- Clean whole US days once with the SQL rules (`clean_sql.py`) into `ais_clean/date=…`, and
  load regions from there instead of cleaning per region in pandas
- Retire the pandas rules once the SQL path is used by the pipeline

**Broader analytics**
- `/movement-summary` endpoint with region and date filters
- More regions (each is one bounding box in `etl/regions.py`) and longer history windows
- Anomaly views: unusual dwell, vessels loitering outside ports, traffic spikes

**Production path**
- Dagster in production: an always-on daemon, alerts on failed runs and asset checks, and
  sensors that start the region load when a new lake day lands
- Incremental dbt models (by day) instead of full rebuilds once history grows
- Cloud deployment: AWS S3 for the lake, a managed Postgres, and Athena or Snowflake to
  query the lake at full scale
- API authentication, rate limiting, and monitoring and alerting for the collector and
  pipeline runs
- Continuous deployment from CI

## Known limitations of the MVP

- Port calls use one fixed rule (at most 1 knot within 5 km for at least 60 minutes).
  Anchorages inside the radius count as port time, and adjacent ports such as Los Angeles
  and Long Beach split calls by distance.
- The first day of a window overstates arrivals, and the last day understates departures.
- Trips to or from ports outside the loaded region are not seen.
- Live and batch data share tables, so live days appear in the daily charts (see step 6
  above).
