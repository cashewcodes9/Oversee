"""Fixtures for tests that need a real PostgreSQL/PostGIS server.

A throwaway database is created on the server from .env settings, so the
development data is never touched. Tests are skipped when no server is reachable
(start one with `docker compose up -d db`).
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Connection, Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError

from oversee.config import get_settings

SCHEMA_SQL = Path(__file__).parents[2] / "sql" / "schema" / "001_schema.sql"


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    url = make_url(get_settings().database_url)
    admin = create_engine(url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3})
    try:
        admin_conn = admin.connect()
    except OperationalError:
        pytest.skip("PostgreSQL not reachable; start it with `docker compose up -d db`")

    name = f"oversee_test_{uuid.uuid4().hex[:8]}"
    admin_conn.execute(text(f'CREATE DATABASE "{name}"'))
    test_engine = create_engine(url.set(database=name))
    with test_engine.begin() as conn:
        conn.exec_driver_sql(SCHEMA_SQL.read_text(encoding="utf-8"))

    yield test_engine

    test_engine.dispose()
    admin_conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin_conn.close()
    admin.dispose()


@pytest.fixture
def conn(engine: Engine) -> Iterator[Connection]:
    """Connection inside a transaction that is rolled back after each test."""
    with engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()
