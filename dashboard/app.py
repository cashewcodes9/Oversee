"""Streamlit dashboard for port activity KPIs. Reads everything through the REST API."""

import os

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

API = os.environ.get("API_BASE_URL", "http://localhost:8000")


def get(path: str, **params):
    resp = requests.get(f"{API}{path}", params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


st.set_page_config(page_title="Oversee – Port Activity", layout="wide")
st.title("Port Activity Intelligence")

try:
    kpis = get("/kpis/overview")
except requests.RequestException as exc:
    st.error(f"API not reachable at {API}: {exc}")
    st.stop()

cols = st.columns(4)
cols[0].metric("Ports", kpis["ports"])
cols[1].metric("Vessels", kpis["vessels"])
cols[2].metric("Positions", f"{kpis['positions']:,}")
cols[3].metric("Port calls", kpis["port_calls"])

ports = pd.DataFrame(get("/ports", limit=1000))
if ports.empty:
    st.info("No ports loaded yet. Run `oversee-etl ports <wpi.csv>`.")
    st.stop()

st.subheader("Ports")
st.map(ports, latitude="latitude", longitude="longitude")

st.subheader("Daily activity")
port_name = st.selectbox("Port", ports["name"])
port_id = int(ports.loc[ports["name"] == port_name, "port_id"].iloc[0])
activity = pd.DataFrame(get(f"/ports/{port_id}/activity"))
if activity.empty:
    st.info("No activity for this port yet. Run `oversee-etl aggregate`.")
else:
    st.plotly_chart(
        px.bar(activity, x="activity_date", y=["arrivals", "departures"], barmode="group"),
        use_container_width=True,
    )

# TODO: busiest ports ranking, route summary, hourly peak heatmap
