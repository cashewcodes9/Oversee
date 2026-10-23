"""Logging setup shared by the ETL, migrations and API.

Two formats: `text` for local development and `json` (one object per line) for
containers. Fields passed via `extra={...}` are included in both. While an ETL run
is active, its `run_id` is attached to every record automatically.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

run_id_var: ContextVar[int | None] = ContextVar("run_id", default=None)

_STANDARD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "taskName",
}


def _extras(record: logging.LogRecord) -> dict:
    return {
        k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS and v is not None
    }


class RunContextFilter(logging.Filter):
    """Attach the active ETL run_id (if any) to each record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = run_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **_extras(record),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")

    def formatMessage(self, record: logging.LogRecord) -> str:
        line = super().formatMessage(record)
        return line + "".join(f" {k}={v}" for k, v in _extras(record).items())


def setup_logging(level: str = "INFO", fmt: str = "text") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    handler.addFilter(RunContextFilter())

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())

    # Route uvicorn through the same handler; requests are logged by our middleware
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
