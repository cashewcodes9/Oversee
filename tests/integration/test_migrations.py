from sqlalchemy import create_engine, inspect, text

from oversee import migrate

TABLES = {
    "source_runs",
    "ports",
    "vessels",
    "vessel_positions",
    "port_calls",
    "vessel_routes",
    "port_activity_daily",
    "route_summary",
}


def table_names(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return set(inspect(conn).get_table_names())
    finally:
        engine.dispose()


def test_upgrade_then_downgrade_to_base(database_factory):
    url = database_factory()

    migrate.upgrade(url)
    assert TABLES <= table_names(url)
    assert migrate.current_revision(url) is not None

    migrate.downgrade(url, "base")
    assert not TABLES & table_names(url)
    assert migrate.current_revision(url) is None


def test_upgrade_is_idempotent(database_factory):
    url = database_factory()
    migrate.upgrade(url)
    head = migrate.current_revision(url)

    migrate.upgrade(url)
    assert migrate.current_revision(url) == head


def test_schema_from_old_init_script_is_stamped_not_recreated(database_factory):
    url = database_factory()
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE ports (port_id INTEGER)"))
    engine.dispose()

    migrate.upgrade(url)

    assert migrate.current_revision(url) == migrate.BASELINE
