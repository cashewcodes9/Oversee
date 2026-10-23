import pandas as pd
import plotly.express as px
import streamlit as st
from common import LOCAL_TZ, WEEKDAYS, active_ports, explain, frame, no_data

st.title("Busiest periods")
explain(
    "When vessels arrive: port-call arrivals counted by weekday and hour of day, in local "
    f"time ({LOCAL_TZ}).",
    "Darker cells in the heatmap had more arrivals. The bar chart below adds up all "
    "weekdays, so its tallest bars are the peak arrival hours. Choose a port to look at "
    "one port instead of the whole region.",
    """
- Each cell only reflects the days in the loaded data. With a few days loaded, each
  weekday appears once or not at all, so a busy cell can be one busy day.
  Load a longer window (`make etl START=... END=...`) for steadier patterns.
- The first day of data counts every vessel already in port as arriving at the first
  report, which inflates its earliest hours.
""",
)

ports = active_ports()
choice = st.selectbox("Port", ["all ports"] + ports["name"].tolist())
port_id = None
if choice != "all ports":
    port_id = int(ports.loc[ports["name"] == choice, "port_id"].iloc[0])

slots = frame("/kpis/peaks", port_id=port_id, tz=LOCAL_TZ)
if slots.empty:
    no_data("No port calls for this selection.")

grid = (
    slots.pivot_table(index="weekday", columns="hour", values="arrivals", fill_value=0)
    .reindex(index=range(7), columns=range(24), fill_value=0)
    .rename(index=dict(enumerate(WEEKDAYS)))
)
st.plotly_chart(
    px.imshow(
        grid,
        aspect="auto",
        color_continuous_scale="Blues",
        labels={"x": "Hour of day (local)", "y": "", "color": "Arrivals"},
        title="Arrivals by weekday and hour",
    ),
    width="stretch",
)

by_hour = slots.groupby("hour", as_index=False)["arrivals"].sum()
by_hour = pd.DataFrame({"hour": range(24)}).merge(by_hour, how="left").fillna(0)
peak = by_hour.loc[by_hour["arrivals"].idxmax()]
st.plotly_chart(
    px.bar(
        by_hour,
        x="hour",
        y="arrivals",
        labels={"hour": "Hour of day (local)", "arrivals": "Arrivals"},
        title="Arrivals by hour, all weekdays",
    ),
    width="stretch",
)
days = ", ".join(WEEKDAYS[d] for d in sorted(slots["weekday"].unique()))
st.caption(
    f"Peak hour: {int(peak['hour']):02d}:00–{int(peak['hour']) + 1:02d}:00 with "
    f"{int(peak['arrivals'])} arrivals. Weekdays in the data: {days}."
)
