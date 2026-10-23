"""Streamlit dashboard for port activity KPIs. Reads everything through the REST API.

Run with `make dashboard` (or `make dev` for the API too). Each page lives in views/.
"""

import pandas as pd
import streamlit as st
from common import API, get

st.set_page_config(page_title="Oversee – Port Activity", page_icon="⚓", layout="wide")

# Paths are relative to this file; Streamlit resolves them against the main script
pages = [
    st.Page("views/overview.py", title="Overview", icon="🧭", default=True),
    st.Page("views/ports.py", title="Busiest ports", icon="⚓"),
    st.Page("views/density.py", title="Vessel density", icon="🗺️"),
    st.Page("views/routes.py", title="Routes", icon="🛳️"),
    st.Page("views/peaks.py", title="Busiest periods", icon="🕒"),
    st.Page("views/speed_dwell.py", title="Speed and dwell", icon="⏱️"),
]
page = st.navigation(pages)

with st.sidebar:
    st.markdown("### Oversee")
    st.caption(
        "Port activity intelligence from open AIS vessel data. Start with **Overview**, "
        "then use the pages above to drill into ports, traffic, routes and timing."
    )
    kpis = get("/kpis/overview")
    if kpis["first_position_ts"]:
        first = pd.Timestamp(kpis["first_position_ts"]).date()
        last = pd.Timestamp(kpis["last_position_ts"]).date()
        st.caption(f"**Data:** {first} to {last} · Los Angeles / Long Beach region")
    st.caption(f"**API:** {API}")

page.run()
