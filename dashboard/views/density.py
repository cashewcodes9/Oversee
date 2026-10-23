import plotly.express as px
import streamlit as st
from common import MAP_STYLE, explain, frame, no_data

st.title("Vessel density")
explain(
    "Where vessels spend their time: the region is divided into a grid, and each cell is "
    "coloured by how many AIS reports came from inside it.",
    "Brighter areas had more reports. Since a vessel reports about once a minute whether "
    "moving or not, bright spots are places where vessels **stay**: harbours, marinas and "
    "anchorages. Thin bright lines are busy shipping lanes. Use the filters to show one "
    "vessel group or change the grid size.",
    """
- Counts are reports, not vessels: one ship at anchor all day adds about 1,440 reports.
  Hover over a cell for the number of different vessels as well.
- Most reports in the region come from moored pleasure craft. Filter to *cargo* or
  *tanker* to see commercial traffic.
- Positions outside the region's bounding box were dropped during loading.
""",
)

groups = frame("/kpis/speed")
if groups.empty:
    no_data("No AIS positions are loaded yet.")

left, right = st.columns(2)
group = left.selectbox(
    "Vessel group",
    ["all vessels"] + sorted(groups["vessel_group"]),
    help="Groups come from the AIS ship type each vessel broadcasts.",
)
cell = right.select_slider(
    "Grid cell size (degrees)",
    options=[0.01, 0.02, 0.05, 0.1, 0.2],
    value=0.02,
    help="0.01° is about 1 km. Smaller cells show more detail but take longer to load.",
)

cells = frame(
    "/kpis/density", cell_deg=cell, vessel_group=None if group == "all vessels" else group
)
if cells.empty:
    no_data("No positions for this vessel group.")

st.plotly_chart(
    px.density_map(
        cells,
        lat="latitude",
        lon="longitude",
        z="positions",
        radius=12,
        hover_data={"positions": True, "vessels": True, "latitude": False, "longitude": False},
        center={"lat": 33.6, "lon": -118.6},
        zoom=6.5,
        height=620,
        map_style=MAP_STYLE,
        labels={"positions": "AIS reports", "vessels": "Different vessels"},
    ),
    width="stretch",
)
st.caption(f"{int(cells['positions'].sum()):,} reports in {len(cells):,} grid cells for {group}.")
