"""Strict contracts for agent prompts and the injected MCP gateway."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import Field, model_validator

from bms_agent.control import (
    ObservationEnvelope,
    SafeIdentity,
    ValidationReasonCode,
)
from bms_agent.graph.state import GraphContract
from bms_agent.llm import ComfortAssessment, EnergyProposal


class TrendSample(GraphContract):
    outdoor_dry_bulb_c: float
    cooling_setpoint_c: float = Field(ge=22.0, le=28.0)
    hvac_electricity_j: float = Field(ge=0.0)
    occupied_pmv_min: float | None
    occupied_pmv_max: float | None


class AgentObservation(GraphContract):
    envelope: ObservationEnvelope
    outdoor_dry_bulb_c: float
    hvac_electricity_j: float = Field(ge=0.0)
    trend: tuple[TrendSample, ...] = Field(max_length=12)

    @model_validator(mode="after")
    def require_five_or_fewer_zones(self) -> AgentObservation:
        if len(self.envelope.snapshot.zones) > 5:
            raise ValueError("Agent context accepts at most five zones.")
        return self


class EnergyAgentInput(GraphContract):
    current_setpoint_c: float = Field(ge=22.0, le=28.0)
    outdoor_dry_bulb_c: float
    hvac_electricity_j: float = Field(ge=0.0)
    occupied_pmv_min: float | None
    occupied_pmv_max: float | None
    trend: tuple[TrendSample, ...] = Field(max_length=12)
    temperature_unit: Literal["degC"]
    energy_unit: Literal["joule"]
    pmv_unit: Literal["dimensionless"]


class ComfortZoneInput(GraphContract):
    temperature_c: float
    pmv: float
    occupancy_people: float = Field(ge=0.0)


class ComfortAgentInput(GraphContract):
    current_setpoint_c: float = Field(ge=22.0, le=28.0)
    zones: tuple[ComfortZoneInput, ...] = Field(max_length=5)
    target_pmv_lower: float = Field(ge=-0.5, le=-0.5)
    target_pmv_upper: float = Field(ge=0.5, le=0.5)
    emergency_pmv_lower: float = Field(ge=-1.0, le=-1.0)
    emergency_pmv_upper: float = Field(ge=1.0, le=1.0)
    temperature_unit: Literal["degC"]
    pmv_unit: Literal["dimensionless"]
    occupancy_unit: Literal["people"]


class SupervisorAgentInput(GraphContract):
    current_setpoint_c: float = Field(ge=22.0, le=28.0)
    energy: EnergyProposal
    comfort: ComfortAssessment
    revision_count: int = Field(ge=0, le=2)
    prior_validation_reason: ValidationReasonCode | None


class GatewayActionRequest(GraphContract):
    run_id: SafeIdentity
    decision_id: SafeIdentity
    observation_sequence: int = Field(ge=1)
    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    setpoint_c: float = Field(ge=22.0, le=28.0)
    control_source: Literal["advisory_proposal", "deterministic_fallback"]
    energy_evidence: str | None = Field(default=None, min_length=1, max_length=512)
    comfort_evidence: str | None = Field(default=None, min_length=1, max_length=512)
    fallback_trigger: ValidationReasonCode | None = None

    @model_validator(mode="after")
    def require_source_fields(self) -> GatewayActionRequest:
        if self.control_source == "advisory_proposal":
            if self.energy_evidence is None or self.comfort_evidence is None:
                raise ValueError("Advisory action requires separate evidence.")
            if self.fallback_trigger is not None:
                raise ValueError("Advisory action cannot carry a fallback trigger.")
        elif self.energy_evidence is not None or self.comfort_evidence is not None:
            raise ValueError("Fallback action cannot carry advisory evidence.")
        elif self.fallback_trigger is None:
            raise ValueError("Fallback action requires a typed validation trigger.")
        elif self.fallback_trigger is ValidationReasonCode.APPROVED:
            raise ValueError("APPROVED cannot trigger fallback.")
        return self


class GatewayActionResult(GraphContract):
    run_id: SafeIdentity
    decision_id: SafeIdentity
    observation_sequence: int = Field(ge=1)
    idempotency_key: str
    requested_setpoint_c: float = Field(ge=22.0, le=28.0)
    authorized_setpoint_c: float = Field(ge=22.0, le=28.0)
    control_source: Literal["advisory_proposal", "deterministic_fallback"]
    authorization_reason_code: str = Field(min_length=1, max_length=80)
    accepted: bool
    cached: bool


class GatewayStatus(GraphContract):
    run_id: SafeIdentity
    status: Literal[
        "created",
        "starting",
        "running",
        "waiting",
        "stopping",
        "completed",
        "cancelled",
        "failed",
    ]


class GatewaySummary(GraphContract):
    run_id: SafeIdentity
    status: Literal["completed", "cancelled", "failed"]
    actions_applied: int = Field(ge=0)


class McpGatewayError(RuntimeError):
    """Safe normalized MCP failure without raw transport details."""

    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class McpGateway(Protocol):
    """Only service boundary through which the graph can reach MCP tools."""

    def start(
        self,
        *,
        run_id: str,
        max_weather_timesteps: int,
        action_wait_seconds: float,
    ) -> None: ...

    def await_observation(
        self,
        *,
        run_id: str,
        timeout_seconds: float,
    ) -> AgentObservation: ...

    def submit_action(
        self,
        request: GatewayActionRequest,
        *,
        timeout_seconds: float,
    ) -> GatewayActionResult: ...

    def status(self, *, run_id: str) -> GatewayStatus: ...

    def summary(self, *, run_id: str) -> GatewaySummary: ...

    def stop(self, *, run_id: str, timeout_seconds: float) -> None: ...


__all__ = [
    "AgentObservation",
    "ComfortAgentInput",
    "ComfortZoneInput",
    "EnergyAgentInput",
    "GatewayActionRequest",
    "GatewayActionResult",
    "GatewayStatus",
    "GatewaySummary",
    "McpGateway",
    "McpGatewayError",
    "SupervisorAgentInput",
    "TrendSample",
]
