"""Pipeline assets. Each one wraps an existing pipeline function, so the CLI and Dagster
run the same code; Dagster adds partitions, lineage, run history and metadata.

    ports ─────────────────────────────────────────────┐
    ais_lake_day (daily) ─► vessel_positions, vessels ─┴─► dbt models (staging ─► marts)

Asset keys for the loaded tables match their names, and the dbt sources point at them
(meta.dagster.asset_key in transform/models/staging/_sources.yml).
"""

from datetime import date

from dagster import (
    AssetCheckResult,
    AssetCheckSeverity,
    AssetCheckSpec,
    AssetExecutionContext,
    AssetSpec,
    Config,
    DailyPartitionsDefinition,
    MaterializeResult,
    MetadataValue,
    asset,
    multi_asset,
)
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, DbtProject, dbt_assets
from sqlalchemy import text

from oversee import dbt_runner
from oversee.db import get_engine
from oversee.etl import extract, pipeline
from oversee.etl.lake import open_lake
from oversee.etl.regions import REGIONS

# The MarineCadastre window used throughout the docs: 2025-03-03..16 (end is exclusive)
AIS_DAYS = DailyPartitionsDefinition(start_date="2025-03-03", end_date="2025-03-17")

# Rows dropped by cleaning (other than outside the region), as a share of rows in region
MAX_DROP_SHARE = 0.01


class RegionConfig(Config):
    region: str = "socal"


@asset(
    group_name="ingest",
    kinds={"python", "postgres"},
    description="World Port Index loaded into public.ports (skipped if this file was loaded).",
)
def ports() -> MaterializeResult:
    path = extract.download_wpi()
    loaded = pipeline.load_ports_file(path)
    return MaterializeResult(
        metadata={
            "ports_in_table": pipeline.ports_loaded(),
            "loaded_this_run": loaded or 0,
            "skipped_as_already_loaded": loaded is None,
        }
    )


@asset(
    partitions_def=AIS_DAYS,
    group_name="ingest",
    kinds={"duckdb", "s3", "parquet"},
    description=(
        "One MarineCadastre AIS day as Parquet in the raw lake (all US waters, every column). "
        "Days already in the lake are not downloaded again; the raw file is deleted after."
    ),
)
def ais_lake_day(context: AssetExecutionContext) -> MaterializeResult:
    day = date.fromisoformat(context.partition_key)
    stored = pipeline.ingest_ais_day(open_lake(), day, delete_raw=True)
    return MaterializeResult(
        metadata={
            "uri": MetadataValue.path(stored.uri),
            "rows": stored.rows,
            "source_bytes": stored.source_bytes,
            "source_sha256": stored.source_sha256,
        }
    )


@multi_asset(
    partitions_def=AIS_DAYS,
    specs=[
        AssetSpec(
            "vessel_positions",
            deps=[ais_lake_day],
            group_name="ingest",
            kinds={"python", "postgres"},
            description=(
                "Cleaned AIS positions for the region in public.vessel_positions, read from "
                "the lake with the region filter applied in DuckDB."
            ),
        ),
        AssetSpec(
            "vessels",
            deps=[ais_lake_day],
            group_name="ingest",
            kinds={"python", "postgres"},
            description="Vessels seen in the region, upserted into public.vessels.",
        ),
    ],
    check_specs=[AssetCheckSpec("cleaning_drop_share", asset="vessel_positions")],
)
def ais_region_load(context: AssetExecutionContext, config: RegionConfig):
    """One day from the lake into PostgreSQL: positions and the vessels that sent them."""
    day = date.fromisoformat(context.partition_key)
    inserted = pipeline.load_ais_day(open_lake(), day, REGIONS[config.region])

    run, quality = _latest_run(f"ais-{day.isoformat()}.parquet")
    in_region = (run["rows_read"] or 0) - quality.get("outside_region", 0)
    dropped = sum(n for rule, n in quality.items() if rule != "outside_region")
    share = dropped / in_region if in_region else 0.0
    with get_engine().connect() as conn:
        vessel_count = conn.execute(text("SELECT count(*) FROM vessels")).scalar_one()
    yield MaterializeResult(asset_key="vessels", metadata={"vessels_in_table": vessel_count})
    yield MaterializeResult(
        asset_key="vessel_positions",
        metadata={
            "region": config.region,
            "inserted_this_run": inserted or 0,
            "skipped_as_already_loaded": inserted is None,
            "rows_in_source_day": run["rows_read"] or 0,
            "rows_loaded": run["rows_loaded"] or 0,
            "dropped_by_rule": MetadataValue.json(quality),
        },
        check_results=[
            AssetCheckResult(
                check_name="cleaning_drop_share",
                passed=share <= MAX_DROP_SHARE,
                severity=AssetCheckSeverity.WARN,
                description=f"At most {MAX_DROP_SHARE:.0%} of in-region rows dropped by cleaning",
                metadata={"drop_share": round(share, 5), "dropped": dropped},
            )
        ],
    )


def _latest_run(file_name: str) -> tuple[dict, dict[str, int]]:
    """The last successful load of this file and its rows dropped per cleaning rule."""
    with get_engine().connect() as conn:
        run = (
            conn.execute(
                text(
                    "SELECT run_id, rows_read, rows_loaded FROM source_runs "
                    "WHERE file_name = :f AND status = 'success' ORDER BY run_id DESC LIMIT 1"
                ),
                {"f": file_name},
            )
            .mappings()
            .first()
        )
        if run is None:
            return {"rows_read": 0, "rows_loaded": 0}, {}
        quality = dict(
            conn.execute(
                text("SELECT rule, rows_dropped FROM data_quality_log WHERE run_id = :r"),
                {"r": run["run_id"]},
            ).all()
        )
    return dict(run), quality


# dagster-dbt runs dbt as a subprocess (also when preparing the project below), so it needs
# the connection variables and the venv's dbt on PATH even without an activated venv
dbt_runner.prepare_subprocess_env()
DBT_PROJECT = DbtProject(project_dir=dbt_runner.project_dir())
DBT_PROJECT.prepare_if_dev()  # `dagster dev` parses the project, so models update live


class LayerTranslator(DagsterDbtTranslator):
    """Group dbt models by layer folder (staging, intermediate, marts)."""

    def get_group_name(self, dbt_resource_props) -> str | None:
        if dbt_resource_props["resource_type"] == "model":
            return "dbt_" + dbt_resource_props["fqn"][1]
        return super().get_group_name(dbt_resource_props)


@dbt_assets(
    manifest=DBT_PROJECT.manifest_path,
    project=DBT_PROJECT,
    dagster_dbt_translator=LayerTranslator(),
)
def oversee_dbt_models(context: AssetExecutionContext, dbt: DbtCliResource):
    """`dbt build`: every model plus its data tests, which Dagster shows as asset checks."""
    yield from dbt.cli(["build"], context=context).stream()
