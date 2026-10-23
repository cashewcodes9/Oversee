# Developer commands: `make help` lists them.
# Recipes are plain commands so they run under cmd.exe (Windows) and sh alike.

ifeq ($(OS),Windows_NT)
PY := "$(CURDIR)/.venv/Scripts/python.exe"
else
PY := "$(CURDIR)/.venv/bin/python"
endif

# Defaults for the ETL targets; override like `make etl START=2025-03-03 END=2025-03-16`
REGION ?= socal
START ?= 2025-03-03
END ?= 2025-03-06
MINUTES ?= 5

.DEFAULT_GOAL := help
.PHONY: help venv install env up down migrate api dashboard dev stack etl lake-ingest \
	lake-load aggregate stream test test-unit lint fmt

help: ## List the commands
	@$(PY) -c "import re; [print(f'  make {t:<12} {d}') for t, d in re.findall(r'^([a-z-]+):.*?## (.*)$$', open('Makefile').read(), re.M)]"

# --- setup ---------------------------------------------------------------------

venv: ## Create the .venv virtual environment
	python -m venv .venv

install: ## Install the package with dashboard and dev extras
	$(PY) -m pip install -e ".[dashboard,dev]"

env: ## Create .env from .env.example (keeps an existing .env)
	@$(PY) -c "import pathlib, shutil; p = pathlib.Path('.env'); print('.env exists, left as is') if p.exists() else (shutil.copy('.env.example', p), print('created .env'))"

# --- services ------------------------------------------------------------------

up: ## Start PostGIS and the S3 lake, wait until healthy
	docker compose up -d --wait db lake

down: ## Stop all containers (data volumes are kept)
	docker compose down

migrate: up ## Apply database migrations
	$(PY) -m oversee.migrate upgrade

api: ## Run the API with auto-reload on http://localhost:8000/docs
	$(PY) -m uvicorn oversee.api.main:app --reload

dashboard: ## Run the dashboard on http://localhost:8501
	$(PY) -m streamlit run dashboard/app.py

dev: migrate ## Start everything: containers, migrations, API and dashboard (Ctrl+C stops)
	$(MAKE) -j2 api dashboard

stack: ## Build and run db, lake, migrate, API and dashboard in Docker
	docker compose up --build

# --- data ----------------------------------------------------------------------

etl: migrate ## End-to-end load for REGION, START..END, then aggregates
	$(PY) -m oversee.etl.cli run --region $(REGION) --start $(START) --end $(END) --delete-raw

lake-ingest: up ## Download AIS days START..END into the lake
	$(PY) -m oversee.etl.cli lake-ingest $(START) $(END) --delete-raw

lake-load: migrate ## Load AIS days START..END from the lake for REGION
	$(PY) -m oversee.etl.cli lake-load $(START) $(END) --region $(REGION)

aggregate: ## Rebuild port calls, routes and summaries
	$(PY) -m oversee.etl.cli aggregate

stream: ## Collect live AIS for REGION for MINUTES (needs AISSTREAM_API_KEY)
	$(PY) -m oversee.etl.cli stream --region $(REGION) --minutes $(MINUTES)

# --- quality -------------------------------------------------------------------

test: ## Run all tests (integration tests need `make up`)
	$(PY) -m pytest

test-unit: ## Run unit tests only, no containers needed
	$(PY) -m pytest tests --ignore=tests/integration

lint: ## Lint and check formatting, as CI does
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

fmt: ## Fix lint issues and format the code
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .
