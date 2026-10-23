import plotly.express as px
import streamlit as st
from common import active_ports, explain, frame, no_data

st.title("Speed and dwell")

st.subheader("Time in port")
explain(
    "How long vessels stay in port, by vessel group. The median is the typical stay; "
    "the 90th percentile shows the long stays (only 1 in 10 calls lasts longer).",
    "Longer bars mean longer stays. A large gap between median and 90th percentile means "
    "most calls are short but some vessels stay much longer, for example while waiting "
    "for a berth or under repair.",
    """
- Only calls that ended inside the data window are counted, so the longest stays
  (vessels still in port at the end) are missing and long stays are underestimated.
- Calls shorter than 60 minutes are not counted as port calls.
""",
)
ports = active_ports()
choice = st.selectbox("Port", ["all ports"] + ports["name"].tolist(), key="dwell_port")
port_id = None
if choice != "all ports":
    port_id = int(ports.loc[ports["name"] == choice, "port_id"].iloc[0])

dwell = frame("/kpis/dwell", port_id=port_id)
if dwell.empty:
    st.info("No completed port calls for this selection.")
else:
    long = dwell.melt(
        id_vars=["vessel_group", "port_calls"],
        value_vars=["median_hours", "p90_hours"],
        var_name="measure",
        value_name="hours",
    )
    long["measure"] = long["measure"].map(
        {"median_hours": "median", "p90_hours": "90th percentile"}
    )
    st.plotly_chart(
        px.bar(
            long,
            x="hours",
            y="vessel_group",
            color="measure",
            barmode="group",
            orientation="h",
            hover_data={"port_calls": True},
            labels={
                "hours": "Hours in port",
                "vessel_group": "",
                "measure": "",
                "port_calls": "Port calls",
            },
        ),
        width="stretch",
    )

st.subheader("Speed on the water")
explain(
    "How much of the time each vessel group is moving, and how fast it goes when it does. "
    "A report counts as moving above 1 knot.",
    "The left chart shows the share of reports that were moving: low values mean the group "
    "mostly sits at a berth, mooring or anchor. The right chart shows typical (median) and "
    "fast (90th percentile) speeds while moving.",
    """
- Speeds are what vessels report (speed over ground), in knots; 10 knots ≈ 18.5 km/h.
- Reports above 50 knots were removed during cleaning as GPS or transmission errors.
""",
)
speed = frame("/kpis/speed")
if speed.empty:
    no_data("No AIS positions are loaded yet.")

left, right = st.columns(2)
left.plotly_chart(
    px.bar(
        speed.sort_values("moving_share"),
        x="moving_share",
        y="vessel_group",
        orientation="h",
        hover_data={"positions": True},
        labels={
            "moving_share": "Share of reports moving",
            "vessel_group": "",
            "positions": "AIS reports",
        },
        title="Time spent moving",
    ).update_xaxes(tickformat=".0%"),
    width="stretch",
)
moving = speed.dropna(subset=["median_moving_knots"]).melt(
    id_vars="vessel_group",
    value_vars=["median_moving_knots", "p90_moving_knots"],
    var_name="measure",
    value_name="knots",
)
moving["measure"] = moving["measure"].map(
    {"median_moving_knots": "median", "p90_moving_knots": "90th percentile"}
)
right.plotly_chart(
    px.bar(
        moving,
        x="knots",
        y="vessel_group",
        color="measure",
        barmode="group",
        orientation="h",
        labels={"knots": "Speed while moving (knots)", "vessel_group": "", "measure": ""},
        title="Speed while moving",
    ),
    width="stretch",
)
