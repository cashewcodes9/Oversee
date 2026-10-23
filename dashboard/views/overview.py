import pandas as pd
import plotly.express as px
import streamlit as st
from common import explain, frame, get, no_data

st.title("Port activity overview")
st.markdown(
    "Oversee turns open **AIS** vessel reports (the position messages ships broadcast "
    "every few seconds to minutes) into port and traffic statistics for the "
    "**Los Angeles / Long Beach** region. Positions come from NOAA MarineCadastre, port "
    "locations from the NGA World Port Index."
)

st.markdown(
    """
**Using this dashboard.** Pick a page in the sidebar:

| Page | Answers |
|---|---|
| **Overview** | How much data is loaded, and how busy was each day? |
| **Busiest ports** | Which ports see the most vessel visits, and how does that change day by day? |
| **Vessel density** | Where do vessels spend their time on the water? |
| **Routes** | Which port-to-port trips are most common, and how long do they take? |
| **Busiest periods** | At what hours and weekdays do vessels arrive? |
| **Speed and dwell** | How long do vessels stay in port, and how fast do they move? |
"""
)

kpis = get("/kpis/overview")
st.subheader("Headline numbers")
cols = st.columns(4)
cols[0].metric(
    "Vessels", f"{kpis['vessels']:,}", help="Distinct ships (MMSI numbers) seen in the region."
)
cols[1].metric(
    "AIS reports",
    f"{kpis['positions']:,}",
    help="Position reports kept after cleaning, about one per vessel per minute.",
)
cols[2].metric(
    "Port calls",
    f"{kpis['port_calls']:,}",
    help="Stays of a vessel at a port: at most 1 knot within 5 km, for at least 60 minutes.",
)
cols[3].metric(
    "Ports in catalogue",
    f"{kpis['ports']:,}",
    help="All World Port Index ports; only those in the region have activity.",
)

daily = frame("/kpis/daily")
if daily.empty:
    no_data("No AIS positions are loaded yet.")
daily["day"] = pd.to_datetime(daily["day"])

st.subheader("Traffic per day")
explain(
    "For each UTC day, how many distinct vessels reported a position, and how many port "
    "calls started (arrivals) and ended (departures) across all ports in the region.",
    "Higher bars mean a busier day. Compare arrivals with departures to see whether "
    "vessels were building up in port or leaving.",
    """
- **The first day overstates arrivals.** Vessels already in port when the data starts
  get their first report as arrival time, so day one counts every vessel in port.
- **The last day understates departures.** Calls still running at the end of the data
  have no departure yet.
- Days far outside the batch window come from the optional live feed
  (`make stream`), which only covers the minutes it ran.
""",
)

left, right = st.columns(2)
left.plotly_chart(
    px.bar(
        daily,
        x="day",
        y="active_vessels",
        labels={"day": "Day (UTC)", "active_vessels": "Vessels reporting"},
        title="Active vessels",
    ),
    width="stretch",
)
moves = daily.melt(id_vars="day", value_vars=["arrivals", "departures"], var_name="event")
right.plotly_chart(
    px.bar(
        moves,
        x="day",
        y="value",
        color="event",
        barmode="group",
        labels={"day": "Day (UTC)", "value": "Port calls", "event": ""},
        title="Arrivals and departures",
    ),
    width="stretch",
)

with st.expander("Glossary"):
    st.markdown(
        """
- **AIS** (Automatic Identification System): radio messages with a ship's identity,
  position, speed and course.
- **MMSI**: the 9-digit number identifying a ship's radio; used here as the vessel id.
- **Port call**: one stay of a vessel at a port, detected from slow positions near it.
- **Dwell time**: how long a port call lasted, from arrival to departure.
- **Voyage / route**: a trip between two consecutive port calls at different ports.
- **SOG** (speed over ground): the speed a vessel reports, in knots (1 knot = 1.85 km/h).
- **Vessel group**: the AIS ship type grouped into cargo, tanker, passenger, tug, fishing,
  pleasure, service, high speed and other.
"""
    )
