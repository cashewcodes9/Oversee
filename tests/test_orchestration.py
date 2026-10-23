"""The Dagster definitions load, and the asset graph runs from raw files to the marts.

Needs the dbt manifest, which `dbt parse` builds without a database connection.
"""

import pytest

pytest.importorskip("dagster")
pytest.importorskip("dagster_dbt")

from dagster import AssetKey  # noqa: E402

from oversee import dbt_runner  # noqa: E402


@pytest.fixture(scope="module")
def graph():
    assert dbt_runner.invoke(["parse", "--quiet"]).success
    from oversee.orchestration.definitions import defs

    return defs.resolve_asset_graph()


def parents(graph, key: str) -> set[str]:
    return {p.to_user_string() for p in graph.get(AssetKey.from_user_string(key)).parent_keys}


def test_loaded_tables_feed_dbt_staging(graph):
    assert parents(graph, "vessel_positions") == {"ais_lake_day"}
    assert parents(graph, "staging/stg_positions") == {"vessel_positions"}
    assert parents(graph, "staging/stg_vessels") == {"vessels"}
    assert parents(graph, "staging/stg_ports") == {"ports"}


def test_marts_build_on_port_calls(graph):
    assert parents(graph, "analytics/fct_port_calls") == {
        "intermediate/int_in_port_reports",
        "staging/stg_positions",
    }
    assert parents(graph, "analytics/agg_route_summary") == {"analytics/fct_voyages"}


def test_ais_assets_are_daily_partitioned(graph):
    for key in ("ais_lake_day", "vessel_positions", "vessels"):
        partitions = graph.get(AssetKey.from_user_string(key)).partitions_def
        assert partitions.get_first_partition_key() == "2025-03-03"


def test_dbt_tests_and_quality_check_are_asset_checks(graph):
    checks = {check.name for check in graph.asset_check_keys}
    assert "cleaning_drop_share" in checks
    assert "unique_fct_port_calls_port_call_id" in checks
    assert "assert_port_calls_do_not_overlap" in checks


def test_jobs_and_stopped_schedule():
    from dagster import DefaultScheduleStatus

    from oversee.orchestration.definitions import defs

    assert {job.name for job in defs.jobs} >= {"ingest_ais_day", "refresh_analytics"}
    (schedule,) = defs.schedules
    assert schedule.name == "refresh_analytics_hourly"
    assert schedule.default_status == DefaultScheduleStatus.STOPPED
