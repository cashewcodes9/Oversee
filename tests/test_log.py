import json
import logging

from fastapi.testclient import TestClient

from oversee.api.main import app
from oversee.log import JsonFormatter, RunContextFilter, TextFormatter, run_id_var


def make_record(**extra) -> logging.LogRecord:
    record = logging.LogRecord("oversee.etl", logging.INFO, __file__, 1, "run finished", None, None)
    record.__dict__.update(extra)
    RunContextFilter().filter(record)
    return record


def test_json_formatter_includes_extras_and_run_id():
    token = run_id_var.set(42)
    try:
        payload = json.loads(JsonFormatter().format(make_record(rows_loaded=10)))
    finally:
        run_id_var.reset(token)

    assert payload["message"] == "run finished"
    assert payload["level"] == "INFO"
    assert payload["run_id"] == 42
    assert payload["rows_loaded"] == 10


def test_formatters_omit_run_id_outside_a_run():
    record = make_record()
    assert "run_id" not in json.loads(JsonFormatter().format(record))
    assert "run_id" not in TextFormatter().format(record)


def test_text_formatter_appends_extras():
    line = TextFormatter().format(make_record(source="wpi", rows_loaded=3))
    assert line.endswith("oversee.etl: run finished source=wpi rows_loaded=3")


def test_api_logs_each_request(caplog):
    with caplog.at_level(logging.INFO, logger="oversee.api"):
        TestClient(app).get("/health")

    (record,) = [r for r in caplog.records if r.name == "oversee.api"]
    assert (record.method, record.path, record.status) == ("GET", "/health", 200)
