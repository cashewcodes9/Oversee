from fastapi import FastAPI

from oversee import __version__
from oversee.api.routers import kpis, ports, vessels

app = FastAPI(
    title="Oversee – Port Activity Intelligence API",
    version=__version__,
    description="Vessel movement and port activity KPIs built from open AIS data.",
)

app.include_router(ports.router)
app.include_router(vessels.router)
app.include_router(kpis.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
