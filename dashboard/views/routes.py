import plotly.graph_objects as go
import streamlit as st
from common import MAP_STYLE, explain, frame, no_data

st.title("Routes")
explain(
    "The most common trips between ports. A trip (voyage) is a vessel's move from one port "
    "call to its next port call at a different port.",
    "Each line on the map is a route, thicker for more voyages. The table lists routes from "
    "most to least travelled, with the average time from leaving one port to arriving at "
    "the next.",
    """
- **Long Beach ↔ Los Angeles** dominates because the two ports share one harbour: tugs,
  bunker vessels and ships moving between terminals count as voyages.
- Trips to ports outside the region are not seen, because positions outside it are not
  loaded. Only trips that both start and end in the region appear.
- Lines are drawn straight between ports, not along the path sailed.
""",
)

routes = frame("/kpis/routes", limit=30)
if routes.empty:
    no_data("No voyages have been detected yet.")

top = (
    st.slider("Routes to show", 5, len(routes), min(15, len(routes)))
    if len(routes) > 5
    else len(routes)
)
shown = routes.head(top)

fig = go.Figure()
widest = shown["voyages"].max()
for r in shown.itertuples():
    fig.add_trace(
        go.Scattermap(
            lat=[r.origin_latitude, r.destination_latitude],
            lon=[r.origin_longitude, r.destination_longitude],
            mode="lines+markers",
            line={"width": 1 + 9 * r.voyages / widest},
            name=f"{r.origin} → {r.destination}",
            hovertext=f"{r.origin} → {r.destination}: {r.voyages} voyages",
            hoverinfo="text",
        )
    )
fig.update_layout(
    map={"style": MAP_STYLE, "center": {"lat": 33.8, "lon": -118.6}, "zoom": 6.5},
    height=520,
    showlegend=False,
    margin={"l": 0, "r": 0, "t": 0, "b": 0},
)
st.plotly_chart(fig, width="stretch")

st.dataframe(
    shown[["origin", "destination", "voyages", "avg_duration_hours"]].rename(
        columns={
            "origin": "From",
            "destination": "To",
            "voyages": "Voyages",
            "avg_duration_hours": "Average trip (hours)",
        }
    ),
    hide_index=True,
    width="stretch",
    column_config={"Average trip (hours)": st.column_config.NumberColumn(format="%.1f")},
)
