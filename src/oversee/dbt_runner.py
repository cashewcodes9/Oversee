"""Run the dbt project in transform/ with Oversee's database settings.

dbt reads its connection from POSTGRES_* environment variables (transform/profiles.yml).
This module fills them from Settings (so from .env), points dbt at the project, and is
exposed as the `oversee-dbt` command:

    oversee-dbt build                     # models + tests
    oversee-dbt build --vars '{port_call_radius_m: 3000}'
    oversee-dbt docs generate
"""

import json
import os
import sys
from pathlib import Path

from oversee.config import Settings, get_settings


def project_dir() -> Path:
    """transform/ in the working directory (repo root, /app in Docker) or next to src/."""
    if env := os.environ.get("OVERSEE_DBT_PROJECT_DIR"):
        return Path(env)
    for candidate in (Path.cwd() / "transform", Path(__file__).resolve().parents[2] / "transform"):
        if (candidate / "dbt_project.yml").exists():
            return candidate
    raise FileNotFoundError("dbt project not found; set OVERSEE_DBT_PROJECT_DIR")


def dbt_executable() -> str:
    """The dbt next to this interpreter, so it works without an activated venv."""
    beside = Path(sys.executable).with_name("dbt.exe" if os.name == "nt" else "dbt")
    return str(beside) if beside.exists() else "dbt"


def prepare_subprocess_env(settings: Settings | None = None) -> None:
    """For tools that run dbt as a subprocess (dagster-dbt): POSTGRES_* and dbt on PATH."""
    os.environ.update(database_env(settings))
    scripts = str(Path(dbt_executable()).parent)
    if Path(dbt_executable()).is_absolute() and scripts not in os.environ["PATH"].split(os.pathsep):
        os.environ["PATH"] = scripts + os.pathsep + os.environ["PATH"]


def database_env(settings: Settings | None = None) -> dict[str, str]:
    settings = settings or get_settings()
    return {
        "POSTGRES_HOST": settings.postgres_host,
        "POSTGRES_PORT": str(settings.postgres_port),
        "POSTGRES_USER": settings.postgres_user,
        "POSTGRES_PASSWORD": settings.postgres_password,
        "POSTGRES_DB": settings.postgres_db,
    }


def with_project(args: list[str]) -> list[str]:
    """Add --project-dir/--profiles-dir unless given; dbt subcommand comes first."""
    path = str(project_dir())
    extra = []
    if "--project-dir" not in args:
        extra += ["--project-dir", path]
    if "--profiles-dir" not in args:
        extra += ["--profiles-dir", path]
    return args + extra


def invoke(args: list[str], variables: dict | None = None, settings: Settings | None = None):
    """Run dbt in-process; returns dbt's dbtRunnerResult (check .success)."""
    from dbt.cli.main import dbtRunner

    if variables:
        args = [*args, "--vars", json.dumps(variables)]
    env = database_env(settings)
    saved = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    try:
        return dbtRunner().invoke(with_project(args))
    finally:
        # Restore, so pointing dbt at another database does not leak into this process
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main() -> None:
    result = invoke(sys.argv[1:] or ["build"])
    sys.exit(0 if result.success else 1)


if __name__ == "__main__":
    main()
