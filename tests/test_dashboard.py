"""Render every dashboard page against a fake API: with data, with none, and with the API down."""

from unittest import mock

import pytest
import requests

pytest.importorskip("streamlit")
import streamlit as st  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

PAGES = ["overview", "ports", "density", "routes", "peaks", "speed_dwell"]

LB = {"port_id": 1, "name": "Long Beach", "latitude": 33.77, "longitude": -118.18}
LA = {"port_id": 2, "name": "Los Angeles", "latitude": 33.75, "longitude": -118.25}

SAMPLE = {
    "/kpis/overview": {
        "ports": 2,
        "vessels": 3,
        "positions": 1000,
        "port_calls": 4,
        "first_position_ts": "2025-03-03T00:00:00Z",
        "last_position_ts": "2025-03-04T23:59:00Z",
    },
    "/kpis/daily": [
        {
            "day": "2025-03-03",
            "positions": 600,
            "active_vessels": 3,
            "arrivals": 3,
            "departures": 1,
        },
        {
            "day": "2025-03-04",
            "positions": 400,
            "active_vessels": 2,
            "arrivals": 1,
            "departures": 2,
        },
    ],
    "/ports": [
        {**LB, "country_code": "US", "unlocode": "USLGB"},
        {**LA, "country_code": "US", "unlocode": "USLAX"},
    ],
    "/ports/1/activity": [
        {
            "activity_date": "2025-03-03",
            "arrivals": 2,
            "departures": 1,
            "unique_vessels": 2,
            "avg_dwell_hours": 5.0,
        },
    ],
    "/ports/2/activity": [],
    "/kpis/ports": [
        {**LB, "port_calls": 3, "unique_vessels": 2, "avg_dwell_hours": 5.0},
        {**LA, "port_calls": 1, "unique_vessels": 1, "avg_dwell_hours": None},
    ],
    "/kpis/density": [
        {"latitude": 33.75, "longitude": -118.25, "positions": 900, "vessels": 2},
        {"latitude": 33.25, "longitude": -117.95, "positions": 100, "vessels": 1},
    ],
    "/kpis/routes": [
        {
            "origin_port_id": 1,
            "origin": "Long Beach",
            "origin_latitude": 33.77,
            "origin_longitude": -118.18,
            "destination_port_id": 2,
            "destination": "Los Angeles",
            "destination_latitude": 33.75,
            "destination_longitude": -118.25,
            "voyages": 3,
            "avg_duration_hours": 1.5,
        },
    ],
    "/kpis/peaks": [
        {"weekday": 0, "hour": 2, "arrivals": 2},
        {"weekday": 1, "hour": 14, "arrivals": 1},
    ],
    "/kpis/dwell": [
        {"vessel_group": "cargo", "port_calls": 2, "median_hours": 20.0, "p90_hours": 30.0},
    ],
    "/kpis/speed": [
        {
            "vessel_group": "cargo",
            "positions": 500,
            "moving_share": 0.4,
            "median_moving_knots": 11.0,
            "p90_moving_knots": 14.0,
        },
        {
            "vessel_group": "pleasure",
            "positions": 500,
            "moving_share": 0.02,
            "median_moving_knots": None,
            "p90_moving_knots": None,
        },
    ],
}

EMPTY = {
    path: ({**value, "ports": 0, "vessels": 0, "positions": 0, "port_calls": 0,
            "first_position_ts": None, "last_position_ts": None}
           if path == "/kpis/overview" else [])
    for path, value in SAMPLE.items()
}  # fmt: skip


def fake_api(responses):
    def get(url, params=None, timeout=None):
        path = url.split("8000", 1)[1]
        response = mock.Mock()
        response.json.return_value = responses[path]
        response.raise_for_status.return_value = None
        return response

    return get


def render(page, responses=None, error=None):
    st.cache_data.clear()
    side_effect = error or fake_api(responses)
    with mock.patch("requests.get", side_effect=side_effect):
        at = AppTest.from_file("../dashboard/app.py", default_timeout=30)
        at.run()
        if page != "overview":
            at.switch_page(f"views/{page}.py").run()
    return at


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_with_data(page):
    at = render(page, SAMPLE)
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.title) == 1
    assert at.get("plotly_chart")
    assert any("What this shows" in m.value for m in at.markdown)


@pytest.mark.parametrize("page", PAGES)
def test_page_explains_how_to_load_data_when_empty(page):
    at = render(page, EMPTY)
    assert not at.exception, [e.value for e in at.exception]
    assert any("make etl" in i.value for i in at.info)


def test_api_down_shows_how_to_start_it():
    at = render("overview", error=requests.ConnectionError("refused"))
    assert not at.exception
    assert "make dev" in at.error[0].value
