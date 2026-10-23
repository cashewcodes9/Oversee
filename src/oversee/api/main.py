import logging
import time

from fastapi import FastAPI, Request

from oversee import __version__
from oversee.api.routers import kpis, ports, vessels
from oversee.config import get_settings
from oversee.log import setup_logging

settings = get_settings()
setup_logging(settings.log_level, settings.log_format)
logger = logging.getLogger("oversee.api")

app = FastAPI(
    title="Oversee – Port Activity Intelligence API",
    version=__version__,
    description="Vessel movement and port activity KPIs built from open AIS data.",
)

app.include_router(ports.router)
app.include_router(vessels.router)
app.include_router(kpis.router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    logger.info(
        "request",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        },
    )
    return response


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
