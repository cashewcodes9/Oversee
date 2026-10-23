import pandas as pd
import plotly.express as px
import streamlit as st
from common import MAP_STYLE, active_ports, explain, frame, no_data

st.title("Busiest ports")
explain(
    "Ports ranked by the number of port calls (vessel visits) over the whole data window, "
    "with how many different vessels called and how long they stayed on average.",
    "The longest bar is the busiest port. On the map, bigger circles mean more port calls. "
    "Pick a port below the ranking to see its activity day by day.",
    """
- A port call is a stay of at least 60 minutes at up to 1 knot within 5 km of a port.
  When two ports are within range (Los Angeles and Long Beach share one harbour), the
  call goes to the nearer one.
- Anchorages inside the 5 km radius count as time in port.
- Average stay only uses calls that ended inside the data window.
""",
)

ranking = frame("/kpis/ports", limit=30)
if ranking.empty:
    no_data("No port calls have been detected yet.")

left, right = st.columns([3, 2])
left.plotly_chart(
    px.bar(
        ranking.sort_values("port_calls"),
        x="port_calls",
        y="name",
        orientation="h",
        hover_data={"unique_vessels": True, "avg_dwell_hours": ":.1f"},
        labels={
            "port_calls": "Port calls",
            "name": "",
            "unique_vessels": "Different vessels",
            "avg_dwell_hours": "Average stay (h)",
        },
        title="Port calls per port",
    ),
    width="stretch",
)
right.plotly_chart(
    px.scatter_map(
        ranking,
        lat="latitude",
        lon="longitude",
        size="port_calls",
        hover_name="name",
        hover_data={"port_calls": True, "latitude": False, "longitude": False},
        zoom=6.5,
        map_style=MAP_STYLE,
        title="Where they are",
    ),
    width="stretch",
)

st.dataframe(
    ranking[["name", "port_calls", "unique_vessels", "avg_dwell_hours"]].rename(
        columns={
            "name": "Port",
            "port_calls": "Port calls",
            "unique_vessels": "Different vessels",
            "avg_dwell_hours": "Average stay (hours)",
        }
    ),
    hide_index=True,
    width="stretch",
    column_config={"Average stay (hours)": st.column_config.NumberColumn(format="%.1f")},
)

st.subheader("Daily activity for one port")
st.markdown(
    "Arrivals and departures are port calls starting and ending that day (UTC); "
    "*vessels present* counts every vessel in port at some point that day."
)
ports = active_ports()
busiest_first = ranking["name"].tolist() + sorted(set(ports["name"]) - set(ranking["name"]))
port_name = st.selectbox("Port", busiest_first, help="Ordered from busiest to quietest.")
port_id = int(ports.loc[ports["name"] == port_name, "port_id"].iloc[0])
activity = frame(f"/ports/{port_id}/activity")
if activity.empty:
    st.info("No activity for this port in the loaded data.")
else:
    activity["activity_date"] = pd.to_datetime(activity["activity_date"])
    long = activity.melt(
        id_vars="activity_date",
        value_vars=["arrivals", "departures", "unique_vessels"],
        var_name="measure",
    )
    long["measure"] = long["measure"].replace({"unique_vessels": "vessels present"})
    st.plotly_chart(
        px.bar(
            long,
            x="activity_date",
            y="value",
            color="measure",
            barmode="group",
            labels={"activity_date": "Day (UTC)", "value": "Count", "measure": ""},
        ),
        width="stretch",
    )
