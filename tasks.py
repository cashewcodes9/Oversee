"""Developer tasks, run with `invoke <task>` (list them with `invoke --list`)."""

import sys

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
