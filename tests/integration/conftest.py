"""Fixtures for tests that need a real PostgreSQL/PostGIS server.

Throwaway databases are created on the server from .env settings, so the
development data is never touched. Tests are skipped when no server is reachable
(start one with `docker compose up -d db`), unless OVERSEE_REQUIRE_DB is set, as
in CI, where an unreachable server is an error.
"""

import os
import uuid
from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import URL, Connection, Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError

from oversee import dbt_runner, migrate
from oversee.config import Settings, get_settings


def settings_for(url: str) -> Settings:
    """Settings pointing at the database in url (for dbt, which reads POSTGRES_*)."""
    parsed = make_url(url)
    return get_settings().model_copy(
        update={
            "postgres_host": parsed.host,
            "postgres_port": parsed.port,
            "postgres_user": parsed.username,
            "postgres_password": parsed.password,
            "postgres_db": parsed.database,
        }
    )


def build_analytics(url: str) -> None:
    """Create the dbt models (analytics schema) in a migrated database."""
    result = dbt_runner.invoke(["run", "--quiet"], settings=settings_for(url))
    assert result.success, "dbt run failed; see output above"


@pytest.fixture(scope="session")
def database_factory() -> Iterator[Callable[[], str]]:
    """Returns a function that creates an empty database and returns its URL."""
    url: URL = make_url(get_settings().database_url)
    admin = create_engine(url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3})
    try:
        admin_conn = admin.connect()
    except OperationalError:
        if os.environ.get("OVERSEE_REQUIRE_DB"):
            raise
        pytest.skip("PostgreSQL not reachable; start it with `docker compose up -d db`")

    created: list[str] = []

    def create() -> str:
        name = f"oversee_test_{uuid.uuid4().hex[:8]}"
        admin_conn.execute(text(f'CREATE DATABASE "{name}"'))
        created.append(name)
        return url.set(database=name).render_as_string(hide_password=False)

    yield create

    for name in created:
        admin_conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin_conn.close()
    admin.dispose()


@pytest.fixture(scope="session")
def engine(database_factory) -> Iterator[Engine]:
    """A database migrated to the latest revision with empty dbt models, shared by the session."""
    url = database_factory()
    migrate.upgrade(url)
    build_analytics(url)
    test_engine = create_engine(url)
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def conn(engine: Engine) -> Iterator[Connection]:
    """Connection inside a transaction that is rolled back after each test."""
    with engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()
