"""Developer tasks, run with `invoke <task>` (list them with `invoke --list`)."""

import sys
from pathlib import Path

from invoke import task

# Run tools with the interpreter invoke runs under, so tasks work without activating the venv
PY = f'"{sys.executable}" -m'


@task
def db(c):
    """Start PostGIS and wait until it is healthy."""
    c.run("docker compose up -d --wait db")


@task
def lake(c):
    """Start the SeaweedFS S3 lake and wait until it is healthy."""
    c.run("docker compose up -d --wait lake")


@task(pre=[db])
def migrate(c):
    """Apply pending database migrations."""
    c.run(f"{PY} oversee.migrate upgrade")


@task
def lint(c):
    """Lint and check formatting (what CI runs)."""
    c.run(f"{PY} ruff check .")
    c.run(f"{PY} ruff format --check .")


@task
def fmt(c):
    """Auto-fix lint issues and format the code."""
    c.run(f"{PY} ruff check --fix .")
    c.run(f"{PY} ruff format .")


@task(help={"unit": "Skip integration tests (no database needed)"})
def test(c, unit=False):
    """Run the test suite."""
    c.run(f"{PY} pytest tests --ignore=tests/integration" if unit else f"{PY} pytest")


@task(pre=[migrate])
def serve(c):
    """Run the API with auto-reload on http://localhost:8000."""
    c.run(f"{PY} uvicorn oversee.api.main:app --reload")


@task
def dashboard(c):
    """Run the Streamlit dashboard on http://localhost:8501."""
    c.run(f"{PY} streamlit run dashboard/app.py")


@task
def stack(c):
    """Build and run the full stack (db, migrate, api, dashboard) in Docker."""
    c.run("docker compose up --build")


@task(pre=[migrate], help={"args": 'Extra dbt arguments, e.g. "--select marts"'})
def dbt(c, args=""):
    """Build the dbt models and run their data tests."""
    c.run(f"{PY} oversee.dbt_runner build {args}")


@task
def dbt_docs(c):
    """Generate dbt docs and serve them on http://localhost:8080."""
    c.run(f"{PY} oversee.dbt_runner docs generate")
    c.run(f"{PY} oversee.dbt_runner docs serve --port 8080")


@task(pre=[migrate])
def dagster(c):
    """Dagster UI on http://localhost:3000 (run history in .dagster/)."""
    home = Path(".dagster").resolve()
    home.mkdir(exist_ok=True)
    c.run(
        f"{PY} dagster dev -m oversee.orchestration.definitions -p 3000",
        env={"DAGSTER_HOME": str(home)},
    )
