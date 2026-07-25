"""Dependency-injected service contract for graph nodes."""

from __future__ import annotations

from typing import Protocol

from bms_agent.control import (
    ControlProposal,
    FallbackDecision,
    ObservationEnvelope,
    ValidationResult,
)
from bms_agent.graph.state import (
    AppliedAction,
    CompletionRoute,
    EvaluationRecord,
    GraphAction,
    GraphError,
    GraphStateView,
    ReflectionRecord,
    RunSummary,
)
from bms_agent.llm import ComfortAssessment, EnergyProposal, SupervisorDecision


class ExpectedGraphError(RuntimeError):
    """Expected service failure safe to normalize into checkpointed state."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


class GraphRuntime(Protocol):
    """External operations required by the state machine.

    Implementations may use MCP, an LLM provider, or deterministic fakes. The graph
    never imports or owns those implementations.
    """

    def initialize_run(self, state: GraphStateView) -> None: ...

    def await_observation(self, state: GraphStateView) -> ObservationEnvelope: ...

    def energy_agent(self, state: GraphStateView) -> EnergyProposal: ...

    def comfort_agent(self, state: GraphStateView) -> ComfortAssessment: ...

    def supervisor(self, state: GraphStateView) -> SupervisorDecision: ...

    def validate_action(
        self,
        state: GraphStateView,
        proposal: ControlProposal | None,
    ) -> ValidationResult: ...

    def fallback_action(self, state: GraphStateView) -> FallbackDecision: ...

    def apply_action(
        self,
        state: GraphStateView,
        action: GraphAction,
    ) -> AppliedAction: ...

    def advance_and_evaluate(self, state: GraphStateView) -> EvaluationRecord: ...

    def reflect(self, state: GraphStateView) -> ReflectionRecord: ...

    def continue_or_finish(self, state: GraphStateView) -> CompletionRoute: ...

    def finalize_run(self, state: GraphStateView) -> RunSummary: ...

    def abort_safely(self, state: GraphStateView, error: GraphError) -> None: ...


__all__ = ["ExpectedGraphError", "GraphRuntime"]
