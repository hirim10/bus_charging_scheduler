import streamlit as st
import pandas as pd
from pathlib import Path
from scheduler import Scheduler
from scenario_loader import load_all_scenarios, fmt_time

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Bus Charging Scheduler",
    page_icon="🚌",
    layout="wide"
)

st.title("🚌 Bus Charging Scheduler")
st.caption("Exponent Energy — Take Home Assignment")

# ---------------------------------------------------------------------------
# Load scenarios
# ---------------------------------------------------------------------------

SCENARIO_DIR = Path("scenarios")

scenario_map = load_all_scenarios(str(SCENARIO_DIR))

if not scenario_map:
    st.error("No scenario files found in /scenarios folder.")
    st.stop()

# ---------------------------------------------------------------------------
# Scenario picker
# ---------------------------------------------------------------------------

selected_name = st.selectbox("Select a Scenario", list(scenario_map.keys()))
scenario_path, scenario_data = scenario_map[selected_name]

st.divider()

# ---------------------------------------------------------------------------
# Section 1 — Scenario Input View
# ---------------------------------------------------------------------------

st.subheader("📋 Scenario Input")

col1, col2, col3 = st.columns(3)

with col1:
    st.markdown("**Route**")
    stops = scenario_data["route"]["stops"]
    segs = scenario_data["route"]["segments"]
    route_rows = []
    for seg in segs:
        route_rows.append({
            "From": seg["from"],
            "To": seg["to"],
            "Distance (km)": seg["distance_km"]
        })
    st.dataframe(pd.DataFrame(route_rows), hide_index=True, use_container_width=True)

with col2:
    st.markdown("**Stations & Chargers**")
    station_rows = [
        {"Station": s["id"], "Chargers": s["chargers"]}
        for s in scenario_data["stations"]
    ]
    st.dataframe(pd.DataFrame(station_rows), hide_index=True, use_container_width=True)

with col3:
    st.markdown("**Physics & Weights**")
    p = scenario_data["physics"]
    w = scenario_data["weights"]
    meta_rows = [
        {"Parameter": "Battery Range (km)", "Value": p["battery_range_km"]},
        {"Parameter": "Charge Time (min)", "Value": p["charge_time_min"]},
        {"Parameter": "Speed (km/h)", "Value": p["speed_kmh"]},
        {"Parameter": "Weight — Individual", "Value": w["individual"]},
        {"Parameter": "Weight — Operator", "Value": w["operator"]},
        {"Parameter": "Weight — Overall", "Value": w["overall"]},
    ]
    st.dataframe(pd.DataFrame(meta_rows), hide_index=True, use_container_width=True)

st.markdown("**Bus Departure Schedule**")
bus_rows = []
for b in scenario_data["buses"]:
    bus_rows.append({
        "Bus ID": b["id"],
        "Operator": b["operator"].upper(),
        "Direction": "Bengaluru → Kochi" if b["direction"] == "BK" else "Kochi → Bengaluru",
        "Departure": b["departure"]
    })
df_buses = pd.DataFrame(bus_rows)
st.dataframe(df_buses, hide_index=True, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# Run scheduler
# ---------------------------------------------------------------------------

with st.spinner("Running scheduler..."):
    scheduler = Scheduler(scenario_data)
    results = scheduler.run()

# ---------------------------------------------------------------------------
# Section 2 — Per Bus Timetable
# ---------------------------------------------------------------------------

st.subheader("🗓️ Per-Bus Timetable")
st.caption("Full timeline for each bus — charging stops, wait times, and final arrival.")

bus_table_rows = []
for r in results:
    direction_label = "BK → Kochi" if r.direction == "BK" else "KB → Bengaluru"
    stations_used = " → ".join(e.station_id for e in r.charge_events) if r.charge_events else "—"

    charge_details = []
    for e in r.charge_events:
        charge_details.append(
            f"{e.station_id}: arr {fmt_time(e.arrival_time)}, "
            f"wait {int(e.wait_time)}m, "
            f"done {fmt_time(e.charge_end)}"
        )
    charge_detail_str = " | ".join(charge_details) if charge_details else "—"

    bus_table_rows.append({
        "Bus ID": r.bus_id,
        "Operator": r.operator.upper(),
        "Direction": direction_label,
        "Departure": fmt_time(r.departure_time),
        "Stations Used": stations_used,
        "Charge Details": charge_detail_str,
        "Total Wait (min)": round(r.total_wait, 1),
        "Arrival": fmt_time(r.arrival_time)
    })

df_timetable = pd.DataFrame(bus_table_rows)

# Colour code by operator
operator_colours = {
    "KPN": "background-color: #e8f4f8",
    "FRESHBUS": "background-color: #f0f8e8",
    "FLIXBUS": "background-color: #fff8e8"
}

st.dataframe(df_timetable, hide_index=True, use_container_width=True)

# Summary stats
st.markdown("**Summary**")
col1, col2, col3, col4 = st.columns(4)
total_waits = [r.total_wait for r in results]
col1.metric("Total Buses", len(results))
col2.metric("Avg Wait (min)", round(sum(total_waits) / len(total_waits), 1))
col3.metric("Max Wait (min)", round(max(total_waits), 1))
col4.metric("Min Wait (min)", round(min(total_waits), 1))

# Per operator breakdown
st.markdown("**Per-Operator Wait Summary**")
operators = list(set(r.operator for r in results))
op_rows = []
for op in sorted(operators):
    op_results = [r for r in results if r.operator == op]
    waits = [r.total_wait for r in op_results]
    op_rows.append({
        "Operator": op.upper(),
        "Buses": len(op_results),
        "Avg Wait (min)": round(sum(waits) / len(waits), 1),
        "Max Wait (min)": round(max(waits), 1),
        "Total Wait (min)": round(sum(waits), 1)
    })
st.dataframe(pd.DataFrame(op_rows), hide_index=True, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# Section 3 — Per Station View
# ---------------------------------------------------------------------------

st.subheader("🔌 Per-Station Charging Order")
st.caption("Order in which buses charged at each station, with queue times.")

stations = [s["id"] for s in scenario_data["stations"]]

station_cols = st.columns(len(stations))

for col, station_id in zip(station_cols, stations):
    with col:
        st.markdown(f"**Station {station_id}**")

        # Collect all charge events at this station
        station_events = []
        for r in results:
            for e in r.charge_events:
                if e.station_id == station_id:
                    station_events.append({
                        "Bus": r.bus_id,
                        "Op": r.operator.upper(),
                        "Arrived": fmt_time(e.arrival_time),
                        "Started": fmt_time(e.charge_start),
                        "Done": fmt_time(e.charge_end),
                        "Wait": f"{int(e.wait_time)}m"
                    })

        if station_events:
            # Sort by charge start time
            station_events.sort(key=lambda x: x["Started"])
            st.dataframe(
                pd.DataFrame(station_events),
                hide_index=True,
                use_container_width=True
            )
            st.caption(f"{len(station_events)} buses charged here")
        else:
            st.info("No buses charged here")