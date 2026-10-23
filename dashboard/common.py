"""Helpers shared by the dashboard pages: API access and the explanation blocks."""

import os

import pandas as pd
import requests
import streamlit as st

API = os.environ.get("API_BASE_URL", "http://localhost:8000")

# The loaded region is Los Angeles / Long Beach, so times are shown in its local time
LOCAL_TZ = "America/Los_Angeles"
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MAP_STYLE = "carto-positron"


@st.cache_data(ttl=60, show_spinner=False)
def _fetch(path: str, params: tuple) -> object:
    resp = requests.get(f"{API}{path}", params=dict(params), timeout=30)
    resp.raise_for_status()
    return resp.json()


def get(path: str, **params) -> object:
    """GET an API path; stops the page with a readable message if the API is down."""
    try:
        return _fetch(path, tuple(sorted((k, v) for k, v in params.items() if v is not None)))
    except requests.RequestException as exc:
        st.error(
            f"The API is not reachable at {API} ({exc}). Start it with `make dev` "
            "(or `make api`), then reload this page."
        )
        st.stop()


def frame(path: str, **params) -> pd.DataFrame:
    return pd.DataFrame(get(path, **params))


def explain(what: str, how: str, notes: str | None = None) -> None:
    """The standard intro under each page title: what the page shows and how to read it."""
    st.markdown(f"**What this shows.** {what}")
    st.markdown(f"**How to read it.** {how}")
    if notes:
        with st.expander("Notes and limitations"):
            st.markdown(notes)


def no_data(message: str = "No data yet.") -> None:
    st.info(
        f"{message} Load data with `make etl` (ports, AIS days and aggregates), "
        "then reload this page."
    )
    st.stop()


def active_ports() -> pd.DataFrame:
    """Ports with activity; keeps its columns when empty so pickers still work."""
    columns = ["port_id", "name", "country_code", "unlocode", "latitude", "longitude"]
    return pd.DataFrame(get("/ports", active=True, limit=1000), columns=columns)
