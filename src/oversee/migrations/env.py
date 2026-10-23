"""Alembic environment. Revisions are hand-written SQL; there is no ORM metadata to autogenerate from."""

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config
url = config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    context.configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
