FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[dashboard]" \
    && python -c "import duckdb; duckdb.connect().install_extension('httpfs')"

COPY dashboard ./dashboard

EXPOSE 8000 8501
