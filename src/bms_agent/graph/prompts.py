"""Pure compact prompt builders over allowlisted typed agent context."""

from __future__ import annotations

from bms_agent.graph.agent_contracts import (
    ComfortAgentInput,
    EnergyAgentInput,
    SupervisorAgentInput,
)

MAX_AGENT_PROMPT_CHARS = 1_200


def _bounded(text: str) -> str:
    if not text or len(text) > MAX_AGENT_PROMPT_CHARS:
        raise ValueError("Agent prompt exceeds the fixed compact bound.")
    return text


def build_energy_prompt(context: EnergyAgentInput) -> str:
    trend = ";".join(
        (
            f"out={item.outdoor_dry_bulb_c:.1f},sp={item.cooling_setpoint_c:.1f},"
            f"hvac={item.hvac_electricity_j:.0f},"
            f"pmv={_range(item.occupied_pmv_min, item.occupied_pmv_max)}"
        )
        for item in context.trend
    )
    return _bounded(
        "Energy role. Propose one 22..28 degC cooling setpoint. "
        "Positive PMV is warm; negative PMV is cold. "
        f"current={context.current_setpoint_c:.1f} degC;"
        f"outdoor={context.outdoor_dry_bulb_c:.1f} degC;"
        f"hvac={context.hvac_electricity_j:.0f} joule;"
        f"occupied_pmv={_range(context.occupied_pmv_min, context.occupied_pmv_max)};"
        f"trend=[{trend}]. Return EnergyProposal JSON only."
    )


def build_comfort_prompt(context: ComfortAgentInput) -> str:
    zones = ";".join(
        f"t={zone.temperature_c:.1f},pmv={zone.pmv:.2f},occ={zone.occupancy_people:.1f}"
        for zone in context.zones
    )
    return _bounded(
        "Comfort role. Positive PMV is warm and needs a lower cooling setpoint; "
        "negative PMV is cold and needs a higher setpoint. "
        "Occupied target PMV=-0.50..+0.50; emergency outside -1.00..+1.00. "
        f"current={context.current_setpoint_c:.1f} degC;zones=[{zones}]. "
        "Return ComfortAssessment JSON only."
    )


def build_supervisor_prompt(context: SupervisorAgentInput) -> str:
    prior = (
        "none" if context.prior_validation_reason is None else context.prior_validation_reason.value
    )
    return _bounded(
        "Supervisor role. Reconcile separate typed evidence; advice is not authority. "
        f"current={context.current_setpoint_c:.1f} degC;"
        f"energy=(setpoint={context.energy.proposed_setpoint_c:.1f},"
        f"effect={context.energy.expected_energy_effect.value},"
        f"confidence={context.energy.confidence:.2f});"
        f"comfort=(state={context.comfort.comfort_state.value},"
        f"direction={context.comfort.recommended_direction.value},"
        f"risk={context.comfort.risk.value});"
        f"revision={context.revision_count};prior_reason={prior}. "
        "Return keys decision,setpoint_c,conflict,energy,comfort only; "
        "energy and comfort each <=28 chars."
    )


def _range(lower: float | None, upper: float | None) -> str:
    if lower is None or upper is None:
        return "none"
    return f"{lower:.2f}..{upper:.2f}"


__all__ = [
    "MAX_AGENT_PROMPT_CHARS",
    "build_comfort_prompt",
    "build_energy_prompt",
    "build_supervisor_prompt",
]
