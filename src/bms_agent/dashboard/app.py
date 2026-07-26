"""Eco-Loop judging results and isolated interactive scenario lab."""

# pyright: reportCallIssue=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownLambdaType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from bms_agent.dashboard.data import (
    DashboardData,
    DashboardDataError,
    decision_reflection_trace,
    discover_completed_runs,
    latest_agent_outputs,
    load_dashboard_data,
)
from bms_agent.dashboard.lab import LabCycle, LabInputs, run_lab_cycle

st.set_page_config(page_title="Eco-Loop Building Agents", page_icon="🌿", layout="wide")


def _runs_root() -> Path:
    configured = os.environ.get("BMS_DASHBOARD_RUNS_ROOT")
    return Path(configured) if configured else Path(__file__).resolve().parents[3] / "runs"


@st.cache_data(show_spinner=False)
def _cached_data(runs_root: str, run_id: str) -> DashboardData:
    return load_dashboard_data(Path(runs_root), run_id)


def _energy_column(frame: pd.DataFrame) -> str:
    matches = [column for column in frame if "Electricity:HVAC [J](TimeStep)" in column]
    if len(matches) != 1:
        raise DashboardDataError("EnergyPlus output has no unique HVAC energy column")
    return matches[0]


def _pmv_chart(data: DashboardData) -> go.Figure:
    frame = data.observations.copy()
    frame["simulation_timestamp"] = pd.to_datetime(frame["simulation_timestamp"])
    figure = go.Figure()
    for zone_id, group in frame.groupby("zone_id", sort=True):
        figure.add_trace(
            go.Scatter(
                x=group["simulation_timestamp"],
                y=group["pmv"],
                mode="lines",
                name=str(zone_id),
                line={"width": 1.5},
            )
        )
    figure.add_hrect(y0=-0.5, y1=0.5, fillcolor="#4CAF50", opacity=0.14, line_width=0)
    figure.update_layout(
        height=390,
        margin={"l": 20, "r": 20, "t": 25, "b": 20},
        yaxis_title="Fanger PMV (dimensionless)",
        xaxis_title="Simulated time",
        legend_title="Zone",
    )
    return figure


def _temperature_chart(data: DashboardData) -> go.Figure:
    raw = data.raw_output
    x = raw.iloc[:, 0].astype(str)
    temperature_columns = [
        column
        for column in raw
        if "SPACE" in column and "Zone Mean Air Temperature [C](TimeStep)" in column
    ]
    setpoint_columns = [
        column
        for column in raw
        if "CLG-SETP-SCH:Schedule Value [](TimeStep)" in column
    ]
    if not temperature_columns or len(setpoint_columns) != 1:
        raise DashboardDataError("EnergyPlus temperature/setpoint series are incomplete")
    figure = go.Figure()
    for column in temperature_columns:
        figure.add_trace(
            go.Scatter(
                x=x,
                y=raw[column],
                mode="lines",
                name=column.split(":", 1)[0],
                line={"width": 1},
            )
        )
    figure.add_trace(
        go.Scatter(
            x=x,
            y=raw[setpoint_columns[0]],
            mode="lines",
            name="Applied cooling setpoint",
            line={"width": 3, "color": "#111827"},
        )
    )
    figure.update_layout(
        height=390,
        margin={"l": 20, "r": 20, "t": 25, "b": 20},
        yaxis_title="Temperature / setpoint (°C)",
        xaxis_title="Simulated time",
    )
    return figure


def _energy_chart(data: DashboardData) -> go.Figure:
    controlled_column = _energy_column(data.raw_output)
    baseline_column = _energy_column(data.baseline_raw_output)
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            y=data.baseline_raw_output[baseline_column].fillna(0).cumsum() / 3_600_000,
            mode="lines",
            name="Fixed baseline",
            line={"width": 3, "color": "#9CA3AF"},
        )
    )
    figure.add_trace(
        go.Scatter(
            y=data.raw_output[controlled_column].fillna(0).cumsum() / 3_600_000,
            mode="lines",
            name="Agent controlled",
            line={"width": 3, "color": "#16A34A"},
        )
    )
    figure.update_layout(
        height=350,
        margin={"l": 20, "r": 20, "t": 25, "b": 20},
        yaxis_title="Cumulative HVAC electricity (kWh)",
        xaxis_title="15-minute timestep",
    )
    return figure


def _render_results() -> None:
    st.title("Eco-Loop Building Agents")
    st.caption(
        "Physics-based, PMV-aware closed-loop HVAC optimization • New Delhi • 7 days"
    )
    runs_root = _runs_root().resolve()
    run_ids = discover_completed_runs(runs_root)
    if not run_ids:
        st.error("No completed dashboard run is available.")
        return
    run_id = st.sidebar.selectbox("Completed run", run_ids)
    st.sidebar.info("Read-only accepted EnergyPlus evidence.")
    try:
        data = _cached_data(str(runs_root), run_id)
        comparison = data.comparison
        summary = data.summary
        st.success(f"Run completed: {run_id}")

        compliance_delta = (
            comparison.controlled_compliance_percent
            - comparison.baseline_compliance_percent
        )
        columns = st.columns(6)
        columns[0].metric(
            "HVAC energy",
            f"{comparison.controlled_hvac_kwh:.2f} kWh",
            f"-{comparison.savings_percent:.2f}%",
        )
        columns[1].metric("Energy saved", f"{comparison.savings_kwh:.2f} kWh")
        columns[2].metric("Cost saved", f"₹{comparison.cost_savings_inr:.2f}")
        columns[3].metric(
            "Occupied PMV compliance",
            f"{comparison.controlled_compliance_percent:.2f}%",
            f"+{compliance_delta:.2f} pp",
        )
        columns[4].metric("Rejected actions", str(summary.decisions.rejected))
        columns[5].metric("Fallbacks", str(summary.decisions.fallbacks))

        status_left, status_right = st.columns(2)
        latest_node = str(data.graph_events.iloc[-1]["node"])
        status_left.metric("Run status", "Completed")
        status_right.metric("Latest LangGraph node", latest_node)

        st.subheader("Occupied comfort")
        st.plotly_chart(_pmv_chart(data), width="stretch", key="pmv")
        st.subheader("Zone temperature and applied setpoint")
        st.plotly_chart(_temperature_chart(data), width="stretch", key="temperature")
        st.subheader("Baseline vs controlled cumulative HVAC electricity")
        st.plotly_chart(_energy_chart(data), width="stretch", key="energy")

        left, right = st.columns([1, 2])
        with left:
            st.subheader("Latest agent cycle")
            st.caption("Safe structured outputs reconstructed from the persisted audit.")
            st.dataframe(latest_agent_outputs(data), width="stretch", hide_index=True)
        with right:
            st.subheader("Decision and reflection chronology")
            st.dataframe(
                decision_reflection_trace(data),
                width="stretch",
                hide_index=True,
                column_config={
                    "requested_setpoint_c": "Requested setpoint (°C)",
                    "actuator_value_after_write_c": "Applied setpoint (°C)",
                },
            )
    except DashboardDataError as error:
        st.error(str(error))


_PRESETS: dict[str, tuple[float, float, float, float, float]] = {
    "Normal occupied": (34.0, 4.0, 0.0, 24.0, 25.0),
    "Heatwave": (45.0, 5.0, 0.45, 24.0, 28.0),
    "Crowded warm zone": (38.0, 12.0, 0.65, 24.0, 27.0),
    "Cold morning": (16.0, 4.0, -0.65, 24.0, 21.0),
    "Unoccupied setback": (35.0, 0.0, 0.0, 24.0, 26.0),
}


def _lab_history() -> list[LabCycle]:
    if "live_lab_history" not in st.session_state:
        st.session_state.live_lab_history = []
    return cast(list[LabCycle], st.session_state.live_lab_history)


def _lab_pmv_chart(history: list[LabCycle]) -> go.Figure:
    figure = go.Figure()
    steps = [cycle.step for cycle in history]
    for zone_index, zone_name in enumerate(
        ("SPACE1", "SPACE2", "SPACE3", "SPACE4", "SPACE5")
    ):
        figure.add_trace(
            go.Scatter(
                x=steps,
                y=[cycle.post_pmvs[zone_index] for cycle in history],
                mode="lines+markers",
                name=zone_name,
            )
        )
    figure.add_hrect(y0=-0.5, y1=0.5, fillcolor="#4CAF50", opacity=0.14, line_width=0)
    figure.update_layout(
        height=350,
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
        xaxis_title="Interactive decision step",
        yaxis_title="Illustrative Fanger PMV",
    )
    return figure


def _lab_control_chart(history: list[LabCycle]) -> go.Figure:
    figure = go.Figure()
    steps = [cycle.step for cycle in history]
    figure.add_trace(
        go.Scatter(
            x=steps,
            y=[cycle.applied_setpoint_c for cycle in history],
            mode="lines+markers",
            name="Applied setpoint (°C)",
        )
    )
    figure.add_trace(
        go.Bar(
            x=steps,
            y=[cycle.illustrative_hvac_kwh for cycle in history],
            name="Illustrative HVAC (kWh)",
            yaxis="y2",
            opacity=0.35,
        )
    )
    figure.update_layout(
        height=350,
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
        xaxis_title="Interactive decision step",
        yaxis={"title": "Cooling setpoint (°C)", "range": [21.5, 28.5]},
        yaxis2={
            "title": "Illustrative HVAC (kWh)",
            "overlaying": "y",
            "side": "right",
            "rangemode": "tozero",
        },
        legend={"orientation": "h"},
    )
    return figure


def _render_live_lab() -> None:
    st.title("Live Scenario Lab")
    st.warning(
        "Interactive reduced-order simulation only. It uses the real LangGraph agent "
        "stages and safety policy, but it does not control EnergyPlus, a real building, "
        "or the accepted-run artifacts."
    )
    st.caption(
        "Change conditions and run one decision at a time to watch "
        "Energy → Comfort → Supervisor → Safety → Action → Reflection."
    )
    preset_name = st.selectbox("Scenario", tuple(_PRESETS))
    outdoor, occupancy, disturbance, setpoint, zone_temperature = _PRESETS[preset_name]
    left, middle, right = st.columns(3)
    with left:
        outdoor_value = st.slider(
            "Outdoor temperature (°C)", -10.0, 55.0, outdoor, 0.5
        )
        occupancy_value = st.slider(
            "Occupancy per zone (people)", 0.0, 20.0, occupancy, 1.0
        )
    with middle:
        disturbance_value = st.slider(
            "PMV disturbance", -1.5, 1.5, disturbance, 0.05
        )
        setpoint_value = st.slider(
            "Current cooling setpoint (°C)", 22.0, 28.0, setpoint, 0.5
        )
    with right:
        zone_temperature_value = st.slider(
            "Starting mean zone temperature (°C)",
            15.0,
            40.0,
            zone_temperature,
            0.5,
        )
        provider_mode = st.selectbox(
            "Agent provider",
            (
                "Deterministic fast demo",
                "Local Qwen 4B",
                "Simulate LLM failure",
            ),
            help=(
                "Qwen is advisory only. Any failure or unsafe proposal uses "
                "deterministic fallback."
            ),
        )

    run_column, reset_column, status_column = st.columns([1, 1, 3])
    run_clicked = run_column.button("Run one agent cycle", type="primary")
    reset_clicked = reset_column.button("Reset lab")
    history = _lab_history()
    if reset_clicked:
        history.clear()
        st.session_state.pop("live_lab_temperatures", None)
        st.rerun()
    if run_clicked:
        previous = cast(
            tuple[float, ...] | None,
            st.session_state.get("live_lab_temperatures"),
        )
        with st.spinner("Running the typed LangGraph decision..."):
            cycle = run_lab_cycle(
                step=len(history) + 1,
                inputs=LabInputs(
                    outdoor_temperature_c=outdoor_value,
                    occupancy_per_zone=occupancy_value,
                    pmv_disturbance=disturbance_value,
                    current_setpoint_c=setpoint_value,
                    zone_temperature_c=zone_temperature_value,
                    provider_mode=provider_mode,
                ),
                previous_temperatures_c=previous,
            )
        history.append(cycle)
        st.session_state.live_lab_temperatures = cycle.post_zone_temperatures_c

    status_column.info(
        f"{len(history)} decision cycle(s) in this browser session. No project files written."
    )
    if not history:
        st.info("Adjust a scenario and run the first agent cycle.")
        return

    latest = history[-1]
    metrics = st.columns(4)
    metrics[0].metric("Applied safe setpoint", f"{latest.applied_setpoint_c:.1f}°C")
    metrics[1].metric(
        "Occupied comfort", f"{latest.occupied_comfort_percent:.0f}%"
    )
    metrics[2].metric(
        "Illustrative HVAC", f"{latest.illustrative_hvac_kwh:.3f} kWh"
    )
    metrics[3].metric("Provider result", latest.provider_status)

    st.subheader("Live agent process")
    st.dataframe(
        pd.DataFrame([item.model_dump() for item in latest.trace]),
        width="stretch",
        hide_index=True,
    )
    chart_left, chart_right = st.columns(2)
    with chart_left:
        st.plotly_chart(_lab_pmv_chart(history), width="stretch", key="lab-pmv")
    with chart_right:
        st.plotly_chart(
            _lab_control_chart(history), width="stretch", key="lab-control"
        )

    energy_text: object = (
        latest.energy_proposal.model_dump()
        if latest.energy_proposal is not None
        else "Unavailable"
    )
    comfort_text: object = (
        latest.comfort_assessment.model_dump()
        if latest.comfort_assessment is not None
        else "Unavailable"
    )
    supervisor_text: object = (
        latest.supervisor_decision.model_dump()
        if latest.supervisor_decision is not None
        else "Abstained"
    )
    role_columns = st.columns(3)
    role_columns[0].markdown("**Energy agent**")
    role_columns[0].json(energy_text)
    role_columns[1].markdown("**Comfort agent**")
    role_columns[1].json(comfort_text)
    role_columns[2].markdown("**Supervisor agent**")
    role_columns[2].json(supervisor_text)
    st.success(f"Reflection: {latest.reflection}")


def render() -> None:
    view = st.sidebar.radio("View", ("Results", "Live Scenario Lab"))
    if view == "Live Scenario Lab":
        _render_live_lab()
    else:
        _render_results()


render()
