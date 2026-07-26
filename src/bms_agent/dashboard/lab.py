# pyright: reportAttributeAccessIssue=false
# pyright: reportTypedDictNotRequiredAccess=false
# pyright: reportUnknownMemberType=false
"""Isolated reduced-order LangGraph scenario lab for dashboard demonstrations."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from bms_agent.control import (
    ControlProposal,
    FallbackDecision,
    ObservationEnvelope,
    ObservationSnapshot,
    ValidationReasonCode,
    ValidationResult,
    ZoneSnapshot,
    choose_fallback,
    validate_proposal,
)
from bms_agent.graph import (
    ComfortAgentInput,
    ComfortZoneInput,
    EnergyAgentInput,
    SupervisorAgentInput,
)
from bms_agent.graph.prompts import (
    build_comfort_prompt,
    build_energy_prompt,
    build_supervisor_prompt,
)
from bms_agent.integration import (
    DeterministicFallbackProvider,
    DeterministicOptimizationProvider,
)
from bms_agent.llm import (
    AdvisoryProvider,
    AdvisoryRole,
    ComfortAssessment,
    EnergyProposal,
    ProviderConfig,
    ProviderStatus,
    SupervisorDecision,
    SupervisorDisposition,
    build_local_provider,
)
from bms_agent.simulation.baseline import ZONES

ProviderMode = Literal["Deterministic fast demo", "Local Qwen 4B", "Simulate LLM failure"]


class LabContract(BaseModel):
    """Strict immutable lab contract."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class LabInputs(LabContract):
    outdoor_temperature_c: float = Field(ge=-10.0, le=55.0)
    occupancy_per_zone: float = Field(ge=0.0, le=20.0)
    pmv_disturbance: float = Field(ge=-1.5, le=1.5)
    current_setpoint_c: float = Field(ge=22.0, le=28.0)
    zone_temperature_c: float = Field(ge=15.0, le=40.0)
    provider_mode: ProviderMode


class LabTrace(LabContract):
    stage: str = Field(min_length=1, max_length=32)
    status: Literal["completed", "fallback", "approved", "rejected"]
    detail: str = Field(min_length=1, max_length=180)


class LabCycle(LabContract):
    step: int = Field(ge=1)
    observed_at_utc: str
    inputs: LabInputs
    pre_zone_temperatures_c: tuple[float, ...] = Field(min_length=5, max_length=5)
    pre_pmvs: tuple[float, ...] = Field(min_length=5, max_length=5)
    energy_proposal: EnergyProposal | None
    comfort_assessment: ComfortAssessment | None
    supervisor_decision: SupervisorDecision | None
    provider_status: str
    validation: ValidationResult | None
    fallback: FallbackDecision | None
    applied_setpoint_c: float = Field(ge=22.0, le=28.0)
    post_zone_temperatures_c: tuple[float, ...] = Field(min_length=5, max_length=5)
    post_pmvs: tuple[float, ...] = Field(min_length=5, max_length=5)
    illustrative_hvac_kwh: float = Field(ge=0.0)
    occupied_comfort_percent: float = Field(ge=0.0, le=100.0)
    reflection: str = Field(min_length=1, max_length=180)
    trace: tuple[LabTrace, ...] = Field(min_length=8, max_length=8)


class LabGraphState(TypedDict, total=False):
    step: int
    inputs: LabInputs
    pre_temperatures: tuple[float, ...]
    observation: ObservationEnvelope
    pre_pmvs: tuple[float, ...]
    energy: EnergyProposal | None
    comfort: ComfortAssessment | None
    supervisor: SupervisorDecision | None
    provider_status: str
    validation: ValidationResult | None
    fallback: FallbackDecision | None
    applied_setpoint: float
    post_temperatures: tuple[float, ...]
    post_pmvs: tuple[float, ...]
    energy_kwh: float
    comfort_percent: float
    reflection: str
    trace: tuple[LabTrace, ...]


ProviderFactory = Callable[[], AdvisoryProvider]
_ZONE_OFFSETS = (-0.08, -0.04, 0.0, 0.04, 0.08)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _provider_factory(mode: ProviderMode) -> ProviderFactory:
    if mode == "Local Qwen 4B":
        return _build_ephemeral_local_provider
    if mode == "Simulate LLM failure":
        return DeterministicFallbackProvider
    return DeterministicOptimizationProvider


def _build_ephemeral_local_provider() -> AdvisoryProvider:
    """Build local Qwen without writing dashboard-session audit data into the project."""

    configured = ProviderConfig.from_environment()
    lab_config = configured.model_copy(
        update={
            "audit_path": Path(os.devnull),
            "timeout_seconds": max(12.0, configured.timeout_seconds),
        }
    )
    return build_local_provider(lab_config)


def _pmvs(
    temperatures: tuple[float, ...],
    disturbance: float,
) -> tuple[float, ...]:
    return tuple(
        round(0.30 * (temperature - 24.0) + disturbance + offset, 3)
        for temperature, offset in zip(temperatures, _ZONE_OFFSETS, strict=True)
    )


def _trace(
    state: LabGraphState,
    stage: str,
    status: Literal["completed", "fallback", "approved", "rejected"],
    detail: str,
) -> tuple[LabTrace, ...]:
    return (
        *state.get("trace", ()),
        LabTrace(stage=stage, status=status, detail=detail),
    )


class LabNodes:
    """Seven explicit nodes for one isolated demonstration decision."""

    def __init__(self, provider_factory: ProviderFactory) -> None:
        self.provider_factory = provider_factory
        self.provider: AdvisoryProvider | None = None

    def observe(self, state: LabGraphState) -> LabGraphState:
        inputs = state["inputs"]
        temperatures = state["pre_temperatures"]
        pmvs = _pmvs(temperatures, inputs.pmv_disturbance)
        step = state["step"]
        observation = ObservationEnvelope(
            run_id="live-scenario-lab",
            decision_id=f"lab-step-{step:04d}",
            sequence=step,
            observed_at_utc=_utc_now(),
            snapshot=ObservationSnapshot(
                current_setpoint_c=inputs.current_setpoint_c,
                zones=tuple(
                    ZoneSnapshot(
                        zone_id=zone,
                        temperature_c=temperature,
                        pmv=pmv,
                        occupancy_people=inputs.occupancy_per_zone,
                    )
                    for zone, temperature, pmv in zip(
                        ZONES, temperatures, pmvs, strict=True
                    )
                ),
                temperature_unit="degC",
                pmv_unit="dimensionless",
                occupancy_unit="people",
            ),
        )
        self.provider = self.provider_factory()
        return {
            "observation": observation,
            "pre_pmvs": pmvs,
            "provider_status": "ready",
            "trace": _trace(
                state,
                "Observe",
                "completed",
                f"Read five zones; occupied PMV {min(pmvs):+.2f} to {max(pmvs):+.2f}.",
            ),
        }

    def energy_agent(self, state: LabGraphState) -> LabGraphState:
        inputs = state["inputs"]
        pmvs = state["pre_pmvs"]
        provider = self.provider
        assert provider is not None
        result = provider.generate(
            role=AdvisoryRole.ENERGY,
            output_schema=EnergyProposal,
            prompt=build_energy_prompt(
                EnergyAgentInput(
                    current_setpoint_c=inputs.current_setpoint_c,
                    outdoor_dry_bulb_c=inputs.outdoor_temperature_c,
                    hvac_electricity_j=0.0,
                    occupied_pmv_min=min(pmvs) if inputs.occupancy_per_zone > 0 else None,
                    occupied_pmv_max=max(pmvs) if inputs.occupancy_per_zone > 0 else None,
                    trend=(),
                    temperature_unit="degC",
                    energy_unit="joule",
                    pmv_unit="dimensionless",
                )
            ),
            deadline_bound=True,
        )
        output = result.output if result.status is ProviderStatus.SUCCESS else None
        detail = (
            f"{result.model}: proposed {output.proposed_setpoint_c:.1f}°C."
            if output is not None
            else f"Advisory unavailable ({result.status.value}); fallback will own control."
        )
        return {
            "energy": output,
            "provider_status": result.status.value,
            "trace": _trace(state, "Energy", "completed" if output else "fallback", detail),
        }

    def comfort_agent(self, state: LabGraphState) -> LabGraphState:
        provider = self.provider
        assert provider is not None
        observation = state["observation"]
        inputs = state["inputs"]
        if state.get("energy") is None:
            return {
                "comfort": None,
                "trace": _trace(
                    state,
                    "Comfort",
                    "fallback",
                    "Skipped untrusted inference; deterministic PMV policy remains active.",
                ),
            }
        result = provider.generate(
            role=AdvisoryRole.COMFORT,
            output_schema=ComfortAssessment,
            prompt=build_comfort_prompt(
                ComfortAgentInput(
                    current_setpoint_c=inputs.current_setpoint_c,
                    zones=tuple(
                        ComfortZoneInput(
                            temperature_c=cast(float, zone.temperature_c),
                            pmv=cast(float, zone.pmv),
                            occupancy_people=cast(float, zone.occupancy_people),
                        )
                        for zone in observation.snapshot.zones
                    ),
                    target_pmv_lower=-0.5,
                    target_pmv_upper=0.5,
                    emergency_pmv_lower=-1.0,
                    emergency_pmv_upper=1.0,
                    temperature_unit="degC",
                    pmv_unit="dimensionless",
                    occupancy_unit="people",
                )
            ),
            deadline_bound=True,
        )
        output = result.output if result.status is ProviderStatus.SUCCESS else None
        detail = (
            f"{result.model}: {output.comfort_state.value}, "
            f"{output.recommended_direction.value} setpoint."
            if output is not None
            else f"Advisory unavailable ({result.status.value}); fallback will own control."
        )
        return {
            "comfort": output,
            "provider_status": result.status.value,
            "trace": _trace(
                state, "Comfort", "completed" if output else "fallback", detail
            ),
        }

    def supervisor(self, state: LabGraphState) -> LabGraphState:
        provider = self.provider
        assert provider is not None
        energy = state.get("energy")
        comfort = state.get("comfort")
        inputs = state["inputs"]
        if energy is None or comfort is None:
            return {
                "supervisor": None,
                "trace": _trace(
                    state,
                    "Supervisor",
                    "fallback",
                    "No complete typed advisory pair; abstained from proposing an action.",
                ),
            }
        result = provider.generate(
            role=AdvisoryRole.SUPERVISOR,
            output_schema=SupervisorDecision,
            prompt=build_supervisor_prompt(
                SupervisorAgentInput(
                    current_setpoint_c=inputs.current_setpoint_c,
                    energy=energy,
                    comfort=comfort,
                    revision_count=0,
                    prior_validation_reason=None,
                )
            ),
            deadline_bound=True,
        )
        output = result.output if result.status is ProviderStatus.SUCCESS else None
        detail = (
            f"{result.model}: {output.disposition.value} "
            f"{output.proposed_setpoint_c if output.proposed_setpoint_c is not None else 'none'}°C."
            if output is not None
            else f"Advisory unavailable ({result.status.value}); deterministic fallback selected."
        )
        return {
            "supervisor": output,
            "provider_status": result.status.value,
            "trace": _trace(
                state, "Supervisor", "completed" if output else "fallback", detail
            ),
        }

    def safety(self, state: LabGraphState) -> LabGraphState:
        observation = state["observation"]
        supervisor = state.get("supervisor")
        validation: ValidationResult | None = None
        trigger = ValidationReasonCode.ADVISORY_UNAVAILABLE
        if (
            supervisor is not None
            and supervisor.disposition is not SupervisorDisposition.ABSTAIN
            and supervisor.proposed_setpoint_c is not None
        ):
            proposal = ControlProposal(
                run_id=observation.run_id,
                decision_id=observation.decision_id,
                observation_sequence=observation.sequence,
                proposed_setpoint_c=supervisor.proposed_setpoint_c,
                energy_evidence=supervisor.energy_evidence,
                comfort_evidence=supervisor.comfort_evidence,
            )
            validation = validate_proposal(observation, proposal)
            trigger = validation.reason_code
        if validation is not None and validation.approved:
            assert validation.validated_setpoint_c is not None
            return {
                "validation": validation,
                "fallback": None,
                "applied_setpoint": validation.validated_setpoint_c,
                "trace": _trace(
                    state,
                    "Safety",
                    "approved",
                    f"Approved exact {validation.validated_setpoint_c:.1f}°C action.",
                ),
            }
        fallback = choose_fallback(
            observation,
            last_safe_setpoint_c=observation.snapshot.current_setpoint_c,
            trigger=trigger,
        )
        reason = validation.reason_code.value if validation is not None else trigger.value
        return {
            "validation": validation,
            "fallback": fallback,
            "applied_setpoint": fallback.setpoint_c,
            "trace": _trace(
                state,
                "Safety",
                "rejected" if validation is not None else "fallback",
                (
                    f"{reason}; safe fallback {fallback.reason_code.value} "
                    f"→ {fallback.setpoint_c:.1f}°C."
                ),
            ),
        }

    def apply_action(self, state: LabGraphState) -> LabGraphState:
        applied = state["applied_setpoint"]
        return {
            "trace": _trace(
                state,
                "Action",
                "completed",
                f"Applied {applied:.1f}°C to the isolated sandbox only.",
            )
        }

    def simulate(self, state: LabGraphState) -> LabGraphState:
        inputs = state["inputs"]
        applied = state["applied_setpoint"]
        next_temperatures = tuple(
            round(
                temperature
                + 0.08 * (inputs.outdoor_temperature_c - temperature)
                - 0.38 * max(0.0, temperature - applied),
                3,
            )
            for temperature in state["pre_temperatures"]
        )
        post_pmvs = _pmvs(next_temperatures, inputs.pmv_disturbance)
        if inputs.occupancy_per_zone > 0:
            comfortable = sum(-0.5 <= value <= 0.5 for value in post_pmvs)
            comfort_percent = 100.0 * comfortable / len(post_pmvs)
        else:
            comfort_percent = 100.0
        energy_kwh = round(
            max(0.0, inputs.outdoor_temperature_c - applied) * 0.085
            + max(0.0, 24.0 - applied) * 0.18
            + inputs.occupancy_per_zone * 0.012,
            4,
        )
        return {
            "post_temperatures": next_temperatures,
            "post_pmvs": post_pmvs,
            "energy_kwh": energy_kwh,
            "comfort_percent": comfort_percent,
            "trace": _trace(
                state,
                "Sandbox",
                "completed",
                f"Advanced reduced-order physics; illustrative HVAC {energy_kwh:.3f} kWh.",
            ),
        }

    def reflect(self, state: LabGraphState) -> LabGraphState:
        pre_pmvs = state["pre_pmvs"]
        post_pmvs = state["post_pmvs"]
        pre_peak = max(abs(value) for value in pre_pmvs)
        post_peak = max(abs(value) for value in post_pmvs)
        direction = "improved" if post_peak < pre_peak else "held/worsened"
        reflection = (
            f"Peak absolute PMV {direction} from {pre_peak:.2f} to {post_peak:.2f}; "
            f"comfort compliance is {state['comfort_percent']:.0f}%."
        )
        return {
            "reflection": reflection,
            "trace": _trace(state, "Reflection", "completed", reflection),
        }


def _build_graph(provider_factory: ProviderFactory) -> object:
    nodes = LabNodes(provider_factory)
    builder = StateGraph(LabGraphState)
    builder.add_node("observe", nodes.observe)
    builder.add_node("energy_agent", nodes.energy_agent)
    builder.add_node("comfort_agent", nodes.comfort_agent)
    builder.add_node("supervisor", nodes.supervisor)
    builder.add_node("safety", nodes.safety)
    builder.add_node("apply_action", nodes.apply_action)
    builder.add_node("simulate", nodes.simulate)
    builder.add_node("reflect", nodes.reflect)
    builder.add_edge(START, "observe")
    builder.add_edge("observe", "energy_agent")
    builder.add_edge("energy_agent", "comfort_agent")
    builder.add_edge("comfort_agent", "supervisor")
    builder.add_edge("supervisor", "safety")
    builder.add_edge("safety", "apply_action")
    builder.add_edge("apply_action", "simulate")
    builder.add_edge("simulate", "reflect")
    builder.add_edge("reflect", END)
    return builder.compile()


def run_lab_cycle(
    *,
    step: int,
    inputs: LabInputs,
    previous_temperatures_c: tuple[float, ...] | None = None,
) -> LabCycle:
    """Run one visible LangGraph cycle without accessing EnergyPlus or MCP."""

    temperatures = previous_temperatures_c or tuple(
        inputs.zone_temperature_c + offset for offset in _ZONE_OFFSETS
    )
    if len(temperatures) != len(ZONES):
        raise ValueError("live lab requires exactly five zone temperatures")
    graph = _build_graph(_provider_factory(inputs.provider_mode))
    raw = cast(
        LabGraphState,
        graph.invoke(
            LabGraphState(
                step=step,
                inputs=inputs,
                pre_temperatures=temperatures,
                trace=(),
            )
        ),
    )
    return LabCycle(
        step=step,
        observed_at_utc=raw["observation"].observed_at_utc,
        inputs=inputs,
        pre_zone_temperatures_c=temperatures,
        pre_pmvs=raw["pre_pmvs"],
        energy_proposal=raw.get("energy"),
        comfort_assessment=raw.get("comfort"),
        supervisor_decision=raw.get("supervisor"),
        provider_status=raw["provider_status"],
        validation=raw.get("validation"),
        fallback=raw.get("fallback"),
        applied_setpoint_c=raw["applied_setpoint"],
        post_zone_temperatures_c=raw["post_temperatures"],
        post_pmvs=raw["post_pmvs"],
        illustrative_hvac_kwh=raw["energy_kwh"],
        occupied_comfort_percent=raw["comfort_percent"],
        reflection=raw["reflection"],
        trace=raw["trace"],
    )


__all__ = [
    "LabCycle",
    "LabInputs",
    "LabTrace",
    "ProviderMode",
    "run_lab_cycle",
]
