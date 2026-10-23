"""Dagster entry point: `dagster dev -m oversee.orchestration.definitions` (or `make dagster`).

Jobs:
- ingest_ais_day: lake + region load for one or more days (backfill from the UI)
- refresh_analytics: `dbt build` over everything

refresh_analytics also has an hourly schedule, stopped by default; turn it on while the
live collector (`make stream`) is running to keep the dashboard tables current.
"""

from dagster import (
    AssetSelection,
    DefaultScheduleStatus,
    Definitions,
    ScheduleDefinition,
    define_asset_job,
)
from dagster_dbt import DbtCliResource

from oversee import dbt_runner
from oversee.config import get_settings
from oversee.log import setup_logging
from oversee.orchestration.assets import (
    AIS_DAYS,
    DBT_PROJECT,
    ais_lake_day,
    ais_region_load,
    oversee_dbt_models,
    ports,
)

settings = get_settings()
setup_logging(settings.log_level, settings.log_format)

ingest_ais_day = define_asset_job(
    "ingest_ais_day",
    selection=AssetSelection.assets(ais_lake_day, ais_region_load),
    partitions_def=AIS_DAYS,
    description="Download a day into the lake and load the region into PostgreSQL.",
)

refresh_analytics = define_asset_job(
    "refresh_analytics",
    selection=AssetSelection.assets(oversee_dbt_models),
    description="Rebuild port calls, voyages and summaries with dbt, including data tests.",
)

refresh_analytics_hourly = ScheduleDefinition(
    name="refresh_analytics_hourly",
    job=refresh_analytics,
    cron_schedule="0 * * * *",
    default_status=DefaultScheduleStatus.STOPPED,
    description="Keep the dashboard tables current while live data is being collected.",
)

defs = Definitions(
    assets=[ports, ais_lake_day, ais_region_load, oversee_dbt_models],
    jobs=[ingest_ais_day, refresh_analytics],
    schedules=[refresh_analytics_hourly],
    resources={
        "dbt": DbtCliResource(
            project_dir=DBT_PROJECT,
            profiles_dir=str(dbt_runner.project_dir()),
            dbt_executable=dbt_runner.dbt_executable(),
        )
    },
)
