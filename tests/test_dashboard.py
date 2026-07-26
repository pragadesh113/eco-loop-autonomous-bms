"""UI-001 read-only data and headless Streamlit smoke tests."""

# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from bms_agent.dashboard.data import (
    DashboardDataError,
    decision_reflection_trace,
    discover_completed_runs,
    latest_agent_outputs,
    load_dashboard_data,
)


def _write_fixture(root: Path) -> Path:
    runs = root / "runs"
    run = runs / "dashboard-run"
    baseline = runs / "baseline-run"
    run.mkdir(parents=True)
    baseline.mkdir()
    summary = {
        "record_type": "run_summary",
        "schema_version": "1.0",
        "run_id": "dashboard-run",
        "timestamp_utc": "2026-07-26T00:00:00Z",
        "mode": "controlled",
        "energy": {
            "hvac_kwh": 8.0,
            "cost_inr": 64.0,
            "tariff_inr_per_kwh": 8.0,
        },
        "comfort": {
            "occupied_samples": 1,
            "compliant_samples": 1,
            "occupied_pmv_compliance_percent": 100.0,
            "emergency_violations": 0,
            "comfort_violation_minutes": 0.0,
            "emergency_violation_minutes": 0.0,
            "mean_abs_pmv": 0.1,
            "max_abs_pmv": 0.1,
            "mean_ppd_percent": 5.0,
        },
        "decisions": {
            "proposed": 1,
            "applied": 1,
            "rejected": 0,
            "revisions": 0,
            "fallbacks": 0,
            "mean_llm_latency_seconds": 1.0,
            "max_llm_latency_seconds": 1.0,
            "mean_mcp_latency_seconds": 0.1,
            "max_mcp_latency_seconds": 0.1,
        },
        "reliability": {
            "errors": 0,
            "recoveries": 0,
            "autonomy_percent": 100.0,
            "reliability_percent": 100.0,
            "longest_without_approved_action_minutes": 0.0,
        },
    }
    (run / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    comparison_header = (
        "schema_version,run_id,timestamp_utc,baseline_run_id,baseline_hvac_kwh,"
        "controlled_hvac_kwh,savings_kwh,savings_percent,baseline_cost_inr,"
        "controlled_cost_inr,cost_savings_inr,baseline_compliance_percent,"
        "controlled_compliance_percent,baseline_emergency_violations,"
        "controlled_emergency_violations\n"
    )
    comparison_row = (
        "1.0,dashboard-run,2026-07-26T00:00:00Z,baseline-run,10,8,2,20,80,64,"
        "16,90,100,0,0\n"
    )
    (run / "comparison.csv").write_text(
        comparison_header + comparison_row, encoding="utf-8"
    )
    (run / "observations.csv").write_text(
        "simulation_timestamp,sequence,zone_id,pmv,occupancy_people,"
        "hvac_electricity_j\n"
        "2011-05-23T00:15:00+05:30,1,SPACE1-1,0.1,1,3600000\n",
        encoding="utf-8",
    )
    raw = (
        "Date/Time,SPACE1-1:Zone Mean Air Temperature [C](TimeStep),"
        "CLG-SETP-SCH:Schedule Value [](TimeStep),Electricity:HVAC [J](TimeStep)\n"
        "05/23 00:15:00,24.1,24.0,3600000\n"
    )
    (run / "eplusout.csv").write_text(raw, encoding="utf-8")
    (baseline / "eplusout.csv").write_text(raw, encoding="utf-8")
    decision = {
        "record_type": "decision",
        "schema_version": "1.0",
        "run_id": "dashboard-run",
        "timestamp_utc": "2026-07-26T00:00:00Z",
        "decision_id": "decision-1",
        "observation_sequence": 1,
        "phase": "proposed",
        "revision_index": 0,
        "llm_latency_seconds": 1.0,
        "mcp_latency_seconds": 0.1,
    }
    (run / "normalized-decisions.jsonl").write_text(
        json.dumps(decision) + "\n", encoding="utf-8"
    )
    graph_events = [
        {
            "record_type": "graph_event",
            "schema_version": "1.0",
            "run_id": "dashboard-run",
            "timestamp_utc": "2026-07-26T00:00:00Z",
            "decision_id": "decision-1",
            "node": node,
            "phase": "finish",
            "error": False,
        }
        for node in ("energy_agent", "comfort_agent", "supervisor", "reflect", "finalize_run")
    ]
    (run / "normalized-graph-events.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in graph_events),
        encoding="utf-8",
    )
    (run / "actions.jsonl").write_text(
        json.dumps(
            {
                "decision_id": "decision-1",
                "observation_sequence": 1,
                "requested_setpoint_c": 24.0,
                "actuator_value_after_write_c": 24.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return runs


def test_dashboard_loader_is_contained_typed_and_read_only(tmp_path: Path) -> None:
    runs = _write_fixture(tmp_path)
    before = {path: path.read_bytes() for path in runs.rglob("*") if path.is_file()}
    assert discover_completed_runs(runs) == ("dashboard-run",)
    data = load_dashboard_data(runs, "dashboard-run")
    assert data.comparison.savings_percent == 20.0
    assert data.summary.comfort.occupied_pmv_compliance_percent == 100.0
    outputs = latest_agent_outputs(data)
    assert tuple(outputs["role"]) == ("Energy", "Comfort", "Supervisor", "Reflection")
    assert "authorized_setpoint=24.00°C" in str(
        outputs.loc[outputs["role"] == "Supervisor", "structured_output"].iloc[0]
    )
    trace = decision_reflection_trace(data)
    assert trace.iloc[0]["reflection_status"] == "completed"
    assert trace.iloc[0]["reflection_outcome"] == "safe_finish"
    assert trace.iloc[0]["reflection_timestamp_utc"] == "2026-07-26T00:00:00Z"
    assert {path: path.read_bytes() for path in runs.rglob("*") if path.is_file()} == before
    with pytest.raises(DashboardDataError):
        load_dashboard_data(runs, "../dashboard-run")


def test_dashboard_loader_rejects_incomplete_artifacts(tmp_path: Path) -> None:
    assert discover_completed_runs(tmp_path / "missing") == ()
    invalid_root = tmp_path / "invalid"
    invalid_root.mkdir()
    invalid_child = invalid_root / "unsafe name"
    invalid_child.mkdir()
    (invalid_child / "summary.json").touch()
    (invalid_child / "comparison.csv").touch()
    assert discover_completed_runs(invalid_root) == ()

    invalid_summary_runs = _write_fixture(tmp_path / "summary")
    (invalid_summary_runs / "dashboard-run" / "summary.json").write_text(
        "{}", encoding="utf-8"
    )
    with pytest.raises(DashboardDataError, match="summary.json"):
        load_dashboard_data(invalid_summary_runs, "dashboard-run")

    incomplete_runs = _write_fixture(tmp_path / "observations")
    (incomplete_runs / "dashboard-run" / "observations.csv").write_text(
        "unexpected\n1\n", encoding="utf-8"
    )
    with pytest.raises(DashboardDataError, match="required columns"):
        load_dashboard_data(incomplete_runs, "dashboard-run")


def test_streamlit_dashboard_smoke(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs = _write_fixture(tmp_path)
    monkeypatch.setenv("BMS_DASHBOARD_RUNS_ROOT", str(runs))
    app_path = (
        Path(__file__).parents[1] / "src" / "bms_agent" / "dashboard" / "app.py"
    )
    app = AppTest.from_file(str(app_path)).run(timeout=20)
    assert not app.exception
    metric_labels = {metric.label for metric in app.metric}
    assert {
        "HVAC energy",
        "Energy saved",
        "Occupied PMV compliance",
        "Rejected actions",
        "Fallbacks",
        "Latest LangGraph node",
    }.issubset(metric_labels)
    assert app.success[0].value == "Run completed: dashboard-run"

    import bms_agent.dashboard.app as dashboard_app

    importlib.reload(dashboard_app)
