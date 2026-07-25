"""Immutable advisory schemas shared by local and remote LLM providers."""

from __future__ import annotations

from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AdvisoryContract(BaseModel):
    """Strict immutable contract for untrusted model output."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class EnergyEffect(StrEnum):
    REDUCE = "reduce"
    NEUTRAL = "neutral"
    INCREASE = "increase"


class ComfortState(StrEnum):
    COLD = "cold"
    COMFORTABLE = "comfortable"
    WARM = "warm"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class SetpointDirection(StrEnum):
    LOWER = "lower"
    HOLD = "hold"
    RAISE = "raise"


class ComfortRisk(StrEnum):
    LOW = "low"
    TARGET_VIOLATION = "target_violation"
    EMERGENCY = "emergency"
    UNKNOWN = "unknown"


class SupervisorDisposition(StrEnum):
    ACCEPT = "accept"
    REVISE = "revise"
    ABSTAIN = "abstain"


class EnergyProposal(AdvisoryContract):
    """Energy-role proposal; always advisory until deterministic validation."""

    proposed_setpoint_c: float = Field(ge=22.0, le=28.0)
    expected_energy_effect: EnergyEffect
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=48)


class ComfortAssessment(AdvisoryContract):
    """Comfort-role interpretation of supplied PMV evidence."""

    comfort_state: ComfortState
    recommended_direction: SetpointDirection
    risk: ComfortRisk
    reason: str = Field(min_length=1, max_length=48)


class SupervisorDecision(AdvisoryContract):
    """Supervisor reconciliation; it cannot authorize or apply an action."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=False,
    )

    disposition: SupervisorDisposition = Field(
        validation_alias="decision",
        serialization_alias="decision",
    )
    proposed_setpoint_c: float | None = Field(
        ge=22.0,
        le=28.0,
        validation_alias="setpoint_c",
        serialization_alias="setpoint_c",
    )
    conflict: bool
    energy_evidence: str = Field(
        min_length=1,
        max_length=28,
        validation_alias="energy",
        serialization_alias="energy",
    )
    comfort_evidence: str = Field(
        min_length=1,
        max_length=28,
        validation_alias="comfort",
        serialization_alias="comfort",
    )

    @model_validator(mode="after")
    def require_consistent_setpoint(self) -> SupervisorDecision:
        if self.disposition is SupervisorDisposition.ABSTAIN:
            if self.proposed_setpoint_c is not None:
                raise ValueError("An abstention cannot include a proposed setpoint.")
        elif self.proposed_setpoint_c is None:
            raise ValueError("Accept and revise decisions require a proposed setpoint.")
        return self


class AdvisoryRole(StrEnum):
    ENERGY = "energy"
    COMFORT = "comfort"
    SUPERVISOR = "supervisor"


class ProviderStatus(StrEnum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    MODEL_MISSING = "model_missing"
    MALFORMED = "malformed"


OutputT = TypeVar("OutputT", bound=AdvisoryContract)


class AdvisoryResult(AdvisoryContract, Generic[OutputT]):
    """Controlled result returned by any interchangeable advisory provider."""

    status: ProviderStatus
    role: AdvisoryRole
    schema_name: str
    model: str | None
    output: OutputT | None
    attempt_count: int = Field(ge=0, le=3)
    correction_attempted: bool
    used_fallback: bool
    wall_duration_ms: float = Field(ge=0.0)
    detail: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def require_status_consistency(self) -> AdvisoryResult[OutputT]:
        if self.status is ProviderStatus.SUCCESS:
            if self.output is None or self.model is None or self.detail is not None:
                raise ValueError("Successful results require output/model and no detail.")
        elif self.output is not None:
            raise ValueError("Failed results cannot carry advisory output.")
        return self


RepresentativeOutput = EnergyProposal | ComfortAssessment | SupervisorDecision


__all__ = [
    "AdvisoryContract",
    "AdvisoryResult",
    "AdvisoryRole",
    "ComfortAssessment",
    "ComfortRisk",
    "ComfortState",
    "EnergyEffect",
    "EnergyProposal",
    "OutputT",
    "ProviderStatus",
    "RepresentativeOutput",
    "SetpointDirection",
    "SupervisorDecision",
    "SupervisorDisposition",
]
