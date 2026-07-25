"""Pure contract and redaction tests for role-specific AGT-002 prompts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bms_agent.graph import (
    MAX_AGENT_PROMPT_CHARS,
    ComfortAgentInput,
    ComfortZoneInput,
    EnergyAgentInput,
    SupervisorAgentInput,
    TrendSample,
    build_comfort_prompt,
    build_energy_prompt,
    build_supervisor_prompt,
)
from bms_agent.llm import (
    ComfortAssessment,
    ComfortRisk,
    ComfortState,
    EnergyEffect,
    EnergyProposal,
    SetpointDirection,
)


def _trend() -> tuple[TrendSample, ...]:
    return (
        TrendSample(
            outdoor_dry_bulb_c=38.125,
            cooling_setpoint_c=24.0,
            hvac_electricity_j=1_234_567.8,
            occupied_pmv_min=-0.61,
            occupied_pmv_max=0.24,
        ),
    )


def test_distinct_role_prompts_are_bounded_fixed_precision_and_redacted() -> None:
    energy_rationale = r"C:\private\energy raw error"
    comfort_rationale = "/tmp/comfort traceback free-form"
    energy = EnergyAgentInput(
        current_setpoint_c=24.0,
        outdoor_dry_bulb_c=38.125,
        hvac_electricity_j=1_234_567.8,
        occupied_pmv_min=-0.61,
        occupied_pmv_max=0.24,
        trend=_trend(),
        temperature_unit="degC",
        energy_unit="joule",
        pmv_unit="dimensionless",
    )
    comfort = ComfortAgentInput(
        current_setpoint_c=24.0,
        zones=(
            ComfortZoneInput(
                temperature_c=25.126,
                pmv=-0.612,
                occupancy_people=1.0,
            ),
        ),
        target_pmv_lower=-0.5,
        target_pmv_upper=0.5,
        emergency_pmv_lower=-1.0,
        emergency_pmv_upper=1.0,
        temperature_unit="degC",
        pmv_unit="dimensionless",
        occupancy_unit="people",
    )
    supervisor = SupervisorAgentInput(
        current_setpoint_c=24.0,
        energy=EnergyProposal(
            proposed_setpoint_c=25.0,
            expected_energy_effect=EnergyEffect.REDUCE,
            confidence=0.8,
            reason=energy_rationale,
        ),
        comfort=ComfortAssessment(
            comfort_state=ComfortState.COLD,
            recommended_direction=SetpointDirection.RAISE,
            risk=ComfortRisk.TARGET_VIOLATION,
            reason=comfort_rationale,
        ),
        revision_count=1,
        prior_validation_reason=None,
    )

    prompts = (
        build_energy_prompt(energy),
        build_comfort_prompt(comfort),
        build_supervisor_prompt(supervisor),
    )

    assert len(set(prompts)) == 3
    assert all(0 < len(prompt) <= MAX_AGENT_PROMPT_CHARS for prompt in prompts)
    assert "38.1" in prompts[0] and "1234568 joule" in prompts[0]
    assert "pmv=-0.61" in prompts[1]
    assert "Positive PMV is warm" in prompts[0]
    assert "negative PMV is cold" in prompts[1]
    assert "energy=(" in prompts[2] and "comfort=(" in prompts[2]
    assert energy_rationale not in prompts[2]
    assert comfort_rationale not in prompts[2]
    assert "effect=reduce" in prompts[2] and "confidence=0.80" in prompts[2]
    assert "risk=target_violation" in prompts[2] and "direction=raise" in prompts[2]
    serialized = "\n".join(prompts)
    for forbidden in (
        "run-SECRET",
        "decision-SECRET",
        "2026-07-26T00:00:00Z",
        "C:\\",
        "raw error",
        "free-form prior evidence",
    ):
        assert forbidden not in serialized


def test_role_contexts_reject_more_than_twelve_trends_or_five_zones() -> None:
    with pytest.raises(ValidationError):
        EnergyAgentInput(
            current_setpoint_c=24.0,
            outdoor_dry_bulb_c=38.0,
            hvac_electricity_j=1.0,
            occupied_pmv_min=None,
            occupied_pmv_max=None,
            trend=_trend() * 13,
            temperature_unit="degC",
            energy_unit="joule",
            pmv_unit="dimensionless",
        )
    zone = ComfortZoneInput(
        temperature_c=24.0,
        pmv=0.0,
        occupancy_people=1.0,
    )
    with pytest.raises(ValidationError):
        ComfortAgentInput(
            current_setpoint_c=24.0,
            zones=(zone,) * 6,
            target_pmv_lower=-0.5,
            target_pmv_upper=0.5,
            emergency_pmv_lower=-1.0,
            emergency_pmv_upper=1.0,
            temperature_unit="degC",
            pmv_unit="dimensionless",
            occupancy_unit="people",
        )
