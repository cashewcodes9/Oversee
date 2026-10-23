"""Database migrations (Alembic), exposed as the `oversee-db` command.

Examples:
    oversee-db upgrade              # apply all pending revisions
    oversee-db downgrade -1         # roll back one revision
    oversee-db current
    oversee-db revision -m "add vessel groups"
"""

import argparse
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from oversee.config import get_settings
from oversee.log import setup_logging

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
BASELINE = "0001"

logger = logging.getLogger(__name__)


def alembic_config(url: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    # configparser treats % as interpolation, so escape it (passwords may contain one)
    cfg.set_main_option("sqlalchemy.url", (url or get_settings().database_url).replace("%", "%%"))
    return cfg


def current_revision(url: str | None = None) -> str | None:
    engine = create_engine(url or get_settings().database_url)
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def _has_unversioned_schema(url: str | None) -> bool:
    """True for databases created by the old docker init script, before Alembic."""
    engine = create_engine(url or get_settings().database_url)
    try:
        with engine.connect() as conn:
            tables = inspect(conn)
            return tables.has_table("ports") and not tables.has_table("alembic_version")
    finally:
        engine.dispose()


def upgrade(url: str | None = None, revision: str = "head") -> None:
    cfg = alembic_config(url)
    if _has_unversioned_schema(url):
        logger.info(
            "existing schema has no version table, stamping baseline", extra={"revision": BASELINE}
        )
        command.stamp(cfg, BASELINE)
    command.upgrade(cfg, revision)


def downgrade(url: str | None = None, revision: str = "-1") -> None:
    command.downgrade(alembic_config(url), revision)


def new_revision(message: str) -> None:
    """Create a revision file numbered after the current head (0002, 0003, ...)."""
    cfg = alembic_config()
    head = ScriptDirectory.from_config(cfg).get_current_head()
    command.revision(cfg, message=message, rev_id=f"{int(head or 0) + 1:04d}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="oversee-db")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("upgrade", help="Apply revisions up to REVISION (default: head)")
    p.add_argument("revision", nargs="?", default="head")
    p = sub.add_parser("downgrade", help="Roll back to REVISION (e.g. -1 or base)")
    p.add_argument("revision")
    sub.add_parser("current", help="Show the database's current revision")
    sub.add_parser("history", help="List all revisions")
    p = sub.add_parser("revision", help="Create a new, empty revision file")
    p.add_argument("-m", "--message", required=True)

    args = parser.parse_args()
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_format)

    if args.cmd == "upgrade":
        upgrade(revision=args.revision)
    elif args.cmd == "downgrade":
        downgrade(revision=args.revision)
    elif args.cmd == "current":
        print(current_revision() or "(empty database)")
    elif args.cmd == "history":
        command.history(alembic_config())
    elif args.cmd == "revision":
        new_revision(args.message)


if __name__ == "__main__":
    main()
