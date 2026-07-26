"""Eco-Loop judging dashboard; all controls are read-only selectors."""

# pyright: reportCallIssue=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownLambdaType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false

from __future__ import annotations

import os
from pathlib import Path

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


def render() -> None:
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
    st.sidebar.info("Read-only dashboard: simulation control remains in the CLI.")
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


render()
