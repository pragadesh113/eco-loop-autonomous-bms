"""LAB-001 isolated dynamic scenario and Streamlit acceptance tests."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from bms_agent.control import FallbackReasonCode
from bms_agent.dashboard.lab import LabInputs, run_lab_cycle


def _inputs(
    *,
    provider_mode: str = "Deterministic fast demo",
    occupancy: float = 5.0,
    disturbance: float = 0.7,
) -> LabInputs:
    return LabInputs.model_validate(
        {
            "outdoor_temperature_c": 45.0,
            "occupancy_per_zone": occupancy,
            "pmv_disturbance": disturbance,
            "current_setpoint_c": 24.0,
            "zone_temperature_c": 28.0,
            "provider_mode": provider_mode,
        }
    )


def test_hot_occupied_cycle_uses_real_graph_and_bounded_fallback() -> None:
    cycle = run_lab_cycle(step=1, inputs=_inputs())

    assert tuple(item.stage for item in cycle.trace) == (
        "Observe",
        "Energy",
        "Comfort",
        "Supervisor",
        "Safety",
        "Action",
        "Sandbox",
        "Reflection",
    )
    assert cycle.validation is not None
    assert not cycle.validation.approved
    assert cycle.fallback is not None
    assert cycle.fallback.reason_code is FallbackReasonCode.CORRECT_OCCUPIED_HOT
    assert cycle.applied_setpoint_c == 23.5
    assert 22.0 <= cycle.applied_setpoint_c <= 28.0
    assert abs(cycle.applied_setpoint_c - cycle.inputs.current_setpoint_c) <= 1.0
    assert "isolated sandbox only" in cycle.trace[5].detail


def test_provider_failure_abstains_and_falls_back_deterministically() -> None:
    cycle = run_lab_cycle(
        step=1,
        inputs=_inputs(provider_mode="Simulate LLM failure"),
    )

    assert cycle.provider_status == "unavailable"
    assert cycle.energy_proposal is None
    assert cycle.comfort_assessment is None
    assert cycle.supervisor_decision is None
    assert cycle.validation is None
    assert cycle.fallback is not None
    assert cycle.fallback.reason_code is FallbackReasonCode.CORRECT_OCCUPIED_HOT
    assert cycle.applied_setpoint_c == 23.5


def test_unoccupied_cycle_moves_toward_energy_setback_within_rate_limit() -> None:
    cycle = run_lab_cycle(
        step=1,
        inputs=_inputs(occupancy=0.0, disturbance=0.0),
    )

    assert cycle.applied_setpoint_c == 25.0
    assert cycle.occupied_comfort_percent == 100.0
    assert cycle.fallback is None
    assert cycle.validation is not None and cycle.validation.approved


def test_streamlit_live_lab_step_is_stateful_and_explicitly_isolated() -> None:
    app_path = (
        Path(__file__).parents[1] / "src" / "bms_agent" / "dashboard" / "app.py"
    )
    app = AppTest.from_file(str(app_path)).run(timeout=20)
    app.radio[0].set_value("Live Scenario Lab").run(timeout=20)

    assert not app.exception
    assert "Live Scenario Lab" in app.title[0].value
    warning = " ".join(item.value for item in app.warning)
    assert "does not control EnergyPlus" in warning
    assert "accepted-run artifacts" in warning

    app.button[0].click().run(timeout=20)
    assert not app.exception
    labels = {metric.label for metric in app.metric}
    assert {
        "Applied safe setpoint",
        "Occupied comfort",
        "Illustrative HVAC",
        "Provider result",
    }.issubset(labels)
    assert "No project files written" in app.info[0].value


def test_streamlit_preset_reset_and_failure_cards_are_valid_json() -> None:
    app_path = (
        Path(__file__).parents[1] / "src" / "bms_agent" / "dashboard" / "app.py"
    )
    app = AppTest.from_file(str(app_path)).run(timeout=20)
    app.radio[0].set_value("Live Scenario Lab").run(timeout=20)
    app.button[0].click().run(timeout=20)

    app.selectbox[0].set_value("Crowded warm zone").run(timeout=20)
    assert tuple(slider.value for slider in app.slider) == (
        38.0,
        12.0,
        0.65,
        24.0,
        27.0,
    )
    assert app.info[0].value.startswith("0 decision cycle(s)")

    app.selectbox[1].set_value("Simulate LLM failure").run(timeout=20)
    app.button[0].click().run(timeout=20)

    assert not app.exception
    assert tuple(item.value for item in app.json) == (
        '{"status": "unavailable", "authority": "deterministic fallback"}',
        '{"status": "unavailable", "authority": "deterministic PMV policy"}',
        '{"status": "abstained", "authority": "deterministic safety fallback"}',
    )
    provider_metric = next(
        metric for metric in app.metric if metric.label == "Provider result"
    )
    assert provider_metric.value == "Fallback"
    assert provider_metric.delta == "LLM unavailable"
