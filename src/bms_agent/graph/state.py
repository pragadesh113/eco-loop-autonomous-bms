"""Typed immutable records and shared state for the LangGraph control process."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from bms_agent.control import (
    ControlProposal,
    EventFieldName,
    FallbackDecision,
    NonBlankEvidence,
    ObservationEnvelope,
    SafeIdentity,
    UtcTimestamp,
    ValidationResult,
)
from bms_agent.llm import ComfortAssessment, EnergyProposal, SupervisorDecision


class GraphContract(BaseModel):
    """Strict immutable contract used at graph/runtime boundaries."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class SimulationStatus(StrEnum):
    INITIALIZING = "initializing"
    WAITING = "waiting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ActionSource(StrEnum):
    ADVISORY = "advisory"
    FALLBACK = "fallback"


class CompletionRoute(StrEnum):
    CONTINUE = "continue"
    FINISH = "finish"
    FATAL = "fatal"


class GraphRunInput(GraphContract):
    run_id: SafeIdentity
    max_decisions: int = Field(ge=1, le=10_000)


class GraphAction(GraphContract):
    run_id: SafeIdentity
    decision_id: SafeIdentity
    observation_sequence: int = Field(ge=1)
    setpoint_c: float = Field(ge=22.0, le=28.0)
    source: ActionSource
    energy_evidence: NonBlankEvidence
    comfort_evidence: NonBlankEvidence
    fallback: FallbackDecision | None = None


class AppliedAction(GraphContract):
    action: GraphAction
    applied_at_utc: UtcTimestamp
    actuator_write_count: Literal[1]


class EvaluationRecord(GraphContract):
    decision_id: SafeIdentity
    evaluated_at_utc: UtcTimestamp
    energy_delta_kwh: float
    occupied_pmv_compliance_percent: float = Field(ge=0.0, le=100.0)
    safe: bool


class ReflectionRecord(GraphContract):
    decision_id: SafeIdentity
    reflected_at_utc: UtcTimestamp
    outcome: str = Field(min_length=1, max_length=160)
    recommend_continue: bool


class GraphError(GraphContract):
    code: str = Field(min_length=1, max_length=80, pattern=r"^[A-Z][A-Z0-9_]{0,79}$")
    node: EventFieldName
    message: str = Field(min_length=1, max_length=240)
    fatal: bool
    occurred_at_utc: UtcTimestamp


class RunSummary(GraphContract):
    run_id: SafeIdentity
    completed_decisions: int = Field(ge=0)
    status: SimulationStatus
    finalized_at_utc: UtcTimestamp


class GraphEvent(GraphContract):
    timestamp_utc: UtcTimestamp
    run_id: SafeIdentity
    decision_id: SafeIdentity | None
    node: EventFieldName
    phase: Literal["start", "finish", "update", "error"]
    changed_fields: tuple[EventFieldName, ...] = Field(max_length=32)
    error: bool


class RunState(TypedDict, total=False):
    """Checkpointed graph state; nodes return partial instances of this mapping."""

    run_id: str
    max_decisions: int
    completed_decisions: int
    simulation_status: SimulationStatus
    graph_node: str
    recent_nodes: tuple[str, ...]
    recent_observations: tuple[ObservationEnvelope, ...]
    observation: ObservationEnvelope | None
    energy_proposal: EnergyProposal | None
    comfort_assessment: ComfortAssessment | None
    supervisor_decision: SupervisorDecision | None
    control_proposal: ControlProposal | None
    validation: ValidationResult | None
    revision_count: int
    action: GraphAction | None
    applied_action: AppliedAction | None
    evaluation: EvaluationRecord | None
    reflection: ReflectionRecord | None
    completion_route: CompletionRoute | None
    error: GraphError | None
    summary: RunSummary | None
    cleanup_completed: bool


class GraphStateView(GraphContract):
    """Immutable snapshot passed to injected runtime services."""

    run_id: SafeIdentity
    max_decisions: int
    completed_decisions: int
    simulation_status: SimulationStatus
    graph_node: str
    recent_nodes: tuple[str, ...]
    recent_observations: tuple[ObservationEnvelope, ...]
    observation: ObservationEnvelope | None
    energy_proposal: EnergyProposal | None
    comfort_assessment: ComfortAssessment | None
    supervisor_decision: SupervisorDecision | None
    control_proposal: ControlProposal | None
    validation: ValidationResult | None
    revision_count: int
    action: GraphAction | None
    applied_action: AppliedAction | None
    evaluation: EvaluationRecord | None
    reflection: ReflectionRecord | None
    completion_route: CompletionRoute | None
    error: GraphError | None
    summary: RunSummary | None
    cleanup_completed: bool

    @classmethod
    def from_state(cls, state: RunState) -> GraphStateView:
        return cls(
            run_id=state.get("run_id", ""),
            max_decisions=state.get("max_decisions", 1),
            completed_decisions=state.get("completed_decisions", 0),
            simulation_status=state.get(
                "simulation_status", SimulationStatus.INITIALIZING
            ),
            graph_node=state.get("graph_node", "uninitialized"),
            recent_nodes=tuple(state.get("recent_nodes", ())),
            recent_observations=tuple(state.get("recent_observations", ())),
            observation=state.get("observation"),
            energy_proposal=state.get("energy_proposal"),
            comfort_assessment=state.get("comfort_assessment"),
            supervisor_decision=state.get("supervisor_decision"),
            control_proposal=state.get("control_proposal"),
            validation=state.get("validation"),
            revision_count=state.get("revision_count", 0),
            action=state.get("action"),
            applied_action=state.get("applied_action"),
            evaluation=state.get("evaluation"),
            reflection=state.get("reflection"),
            completion_route=state.get("completion_route"),
            error=state.get("error"),
            summary=state.get("summary"),
            cleanup_completed=state.get("cleanup_completed", False),
        )


__all__ = [
    "ActionSource",
    "AppliedAction",
    "CompletionRoute",
    "EvaluationRecord",
    "GraphAction",
    "GraphContract",
    "GraphError",
    "GraphEvent",
    "GraphRunInput",
    "GraphStateView",
    "ReflectionRecord",
    "RunState",
    "RunSummary",
    "SimulationStatus",
]
