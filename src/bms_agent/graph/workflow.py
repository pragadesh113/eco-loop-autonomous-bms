# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
"""Explicit LangGraph state machine for the safe building-control loop."""

from __future__ import annotations

import math
import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal, TypeGuard, cast
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import StateSnapshot

from bms_agent.control import (
    ControlProposal,
    FallbackDecision,
    ObservationEnvelope,
    ValidationReasonCode,
    ValidationResult,
    validate_identity,
)
from bms_agent.graph.runtime import ExpectedGraphError, GraphRuntime
from bms_agent.graph.state import (
    ActionSource,
    AppliedAction,
    CompletionRoute,
    EvaluationRecord,
    GraphAction,
    GraphError,
    GraphEvent,
    GraphRunInput,
    GraphStateView,
    ReflectionRecord,
    RunState,
    RunSummary,
    SimulationStatus,
)
from bms_agent.llm import ComfortAssessment, EnergyProposal, SupervisorDecision

MAX_REVISIONS = 2
RECENT_NODE_LIMIT = 32
RECENT_OBSERVATION_LIMIT = 24
WORST_CASE_NODES_PER_DECISION = 16
FIXED_RUN_NODES = 4

ValidationRoute = Literal["approved", "retry", "fallback", "fatal"]
ApplyRoute = Literal["advance", "fatal"]
FinishRoute = Literal["continue", "finish", "fatal"]
FinalizeRoute = Literal["success", "fatal"]
_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$")
_EVENT_NODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_EVENT_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SENSITIVE_MARKERS = ("password", "prompt", "raw-output", "raw_output", "secret", "token")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _identity_is_safe(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        validate_identity(value)
    except ValueError:
        return False
    return True


def _safe_error_code(value: object) -> str:
    return (
        value
        if isinstance(value, str) and _ERROR_CODE_RE.fullmatch(value)
        else "EXPECTED_SERVICE_ERROR"
    )


def _safe_error_message(value: object) -> str:
    if not isinstance(value, str):
        return "expected service failure; sensitive details were redacted"
    lowered = value.lower()
    if (
        not value.strip()
        or any(ord(character) < 32 for character in value)
        or any(marker in lowered for marker in _SENSITIVE_MARKERS)
    ):
        return "expected service failure; sensitive details were redacted"
    return value[:240]


def _event_name_is_safe(
    value: object,
    pattern: re.Pattern[str],
) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and pattern.fullmatch(value) is not None
        and not any(marker in value.lower() for marker in _SENSITIVE_MARKERS)
    )


def _configured_thread_id(config: RunnableConfig) -> str | None:
    configurable = config.get("configurable")
    if not isinstance(configurable, Mapping):
        return None
    thread_id = configurable.get("thread_id")
    return thread_id if _identity_is_safe(thread_id) else None


def _current_observation_is_anchored(
    state: RunState,
    config: RunnableConfig,
) -> bool:
    """Bind mutable current identity to thread ID and retained accepted history."""

    thread_id = _configured_thread_id(config)
    observation = state.get("observation")
    history = state.get("recent_observations", ())
    if (
        thread_id is None
        or state.get("run_id") != thread_id
        or observation is None
        or not history
    ):
        return False
    accepted = history[-1]
    try:
        return (
            observation.run_id == thread_id
            and accepted.run_id == thread_id
            and observation.decision_id == accepted.decision_id
            and observation.sequence == accepted.sequence
        )
    except (AttributeError, TypeError):
        return False


def recursion_limit_for(max_decisions: int) -> int:
    """Bound graph steps from the configured decision count and worst-case route."""

    if max_decisions < 1:
        raise ValueError("max_decisions must be positive.")
    return FIXED_RUN_NODES + max_decisions * WORST_CASE_NODES_PER_DECISION


def _initial_state(run: GraphRunInput) -> RunState:
    return RunState(
        run_id=run.run_id,
        max_decisions=run.max_decisions,
        completed_decisions=0,
        simulation_status=SimulationStatus.INITIALIZING,
        graph_node="START",
        recent_nodes=(),
        recent_observations=(),
        observation=None,
        energy_proposal=None,
        comfort_assessment=None,
        supervisor_decision=None,
        control_proposal=None,
        validation=None,
        revision_count=0,
        action=None,
        applied_action=None,
        evaluation=None,
        reflection=None,
        completion_route=None,
        error=None,
        summary=None,
        cleanup_completed=False,
    )


class GraphNodeSet:
    """Named graph nodes backed by one typed runtime dependency."""

    def __init__(self, runtime: GraphRuntime) -> None:
        self.runtime = runtime

    @staticmethod
    def _entered(state: RunState, node: str) -> RunState:
        recent = (*state.get("recent_nodes", ()), node)[-RECENT_NODE_LIMIT:]
        return RunState(graph_node=node, recent_nodes=recent)

    @staticmethod
    def _fatal(state: RunState, node: str, error: ExpectedGraphError) -> RunState:
        update = GraphNodeSet._entered(state, node)
        update.update(
            error=GraphError(
                code=_safe_error_code(error.code),
                node=node,
                message=_safe_error_message(error.safe_message),
                fatal=True,
                occurred_at_utc=_utc_now(),
            ),
            simulation_status=SimulationStatus.FAILED,
            completion_route=CompletionRoute.FATAL,
        )
        return update

    @staticmethod
    def _unexpected(state: RunState, node: str) -> RunState:
        return GraphNodeSet._fatal(
            state,
            node,
            ExpectedGraphError(
                "UNEXPECTED_NODE_ERROR",
                "unexpected node failure; sensitive details were redacted",
            ),
        )

    @staticmethod
    def _fatal_contract(state: RunState, node: str, message: str) -> RunState:
        return GraphNodeSet._fatal(
            state,
            node,
            ExpectedGraphError("GRAPH_CONTRACT_ERROR", message),
        )

    def initialize_run(self, state: RunState) -> RunState:
        node = "initialize_run"
        try:
            self.runtime.initialize_run(GraphStateView.from_state(state))
        except ExpectedGraphError as error:
            return self._fatal(state, node, error)
        except Exception:
            return self._unexpected(state, node)
        update = self._entered(state, node)
        update.update(simulation_status=SimulationStatus.WAITING)
        return update

    def await_observation(self, state: RunState) -> RunState:
        node = "await_observation"
        if state.get("error") is not None:
            return self._entered(state, node)
        try:
            observation = self.runtime.await_observation(GraphStateView.from_state(state))
        except ExpectedGraphError as error:
            return self._fatal(state, node, error)
        except Exception:
            return self._unexpected(state, node)
        try:
            normalized_observation = ObservationEnvelope.model_validate(
                observation.model_dump()
            )
        except Exception:
            return self._fatal_contract(
                state,
                node,
                "observation contract is invalid; supplied values were discarded",
            )
        previous = state.get("observation")
        if (
            not _identity_is_safe(normalized_observation.run_id)
            or not _identity_is_safe(normalized_observation.decision_id)
            or normalized_observation.run_id != state.get("run_id")
        ):
            return self._fatal_contract(
                state,
                node,
                "observation identity differs from the graph contract",
            )
        if normalized_observation.sequence < 1 or (
            previous is not None
            and normalized_observation.sequence <= previous.sequence
        ):
            return self._fatal_contract(
                state,
                node,
                "observation sequence is not strictly newer",
            )
        recent = (
            *state.get("recent_observations", ()),
            normalized_observation,
        )[-RECENT_OBSERVATION_LIMIT:]
        update = self._entered(state, node)
        update.update(
            observation=normalized_observation,
            recent_observations=recent,
            energy_proposal=None,
            comfort_assessment=None,
            supervisor_decision=None,
            control_proposal=None,
            validation=None,
            revision_count=0,
            action=None,
            applied_action=None,
            evaluation=None,
            reflection=None,
            completion_route=None,
            simulation_status=SimulationStatus.RUNNING,
        )
        return update

    def energy_agent(self, state: RunState) -> RunState:
        node = "energy_agent"
        if state.get("error") is not None:
            return self._entered(state, node)
        try:
            proposal = self.runtime.energy_agent(GraphStateView.from_state(state))
        except ExpectedGraphError as error:
            return self._fatal(state, node, error)
        except Exception:
            return self._unexpected(state, node)
        try:
            normalized_proposal = EnergyProposal.model_validate(proposal.model_dump())
        except Exception:
            return self._fatal_contract(
                state,
                node,
                "energy advisory contract is invalid; supplied values were discarded",
            )
        update = self._entered(state, node)
        update.update(energy_proposal=normalized_proposal)
        return update

    def comfort_agent(self, state: RunState) -> RunState:
        node = "comfort_agent"
        if state.get("error") is not None:
            return self._entered(state, node)
        try:
            assessment = self.runtime.comfort_agent(GraphStateView.from_state(state))
        except ExpectedGraphError as error:
            return self._fatal(state, node, error)
        except Exception:
            return self._unexpected(state, node)
        try:
            normalized_assessment = ComfortAssessment.model_validate(
                assessment.model_dump()
            )
        except Exception:
            return self._fatal_contract(
                state,
                node,
                "comfort advisory contract is invalid; supplied values were discarded",
            )
        update = self._entered(state, node)
        update.update(comfort_assessment=normalized_assessment)
        return update

    def supervisor(self, state: RunState) -> RunState:
        node = "supervisor"
        if state.get("error") is not None:
            return self._entered(state, node)
        try:
            decision = self.runtime.supervisor(GraphStateView.from_state(state))
        except ExpectedGraphError as error:
            return self._fatal(state, node, error)
        except Exception:
            return self._unexpected(state, node)
        try:
            normalized_decision = SupervisorDecision.model_validate(
                decision.model_dump()
            )
        except Exception:
            return self._fatal_contract(
                state,
                node,
                "supervisor advisory contract is invalid; supplied values were discarded",
            )
        observation = state.get("observation")
        control_proposal = None
        if (
            observation is not None
            and normalized_decision.proposed_setpoint_c is not None
        ):
            try:
                control_proposal = ControlProposal(
                    run_id=observation.run_id,
                    decision_id=observation.decision_id,
                    observation_sequence=observation.sequence,
                    proposed_setpoint_c=normalized_decision.proposed_setpoint_c,
                    energy_evidence=normalized_decision.energy_evidence,
                    comfort_evidence=normalized_decision.comfort_evidence,
                )
            except Exception:
                return self._fatal_contract(
                    state,
                    node,
                    "supervisor proposal contract is invalid; supplied values were discarded",
                )
        update = self._entered(state, node)
        update.update(
            supervisor_decision=normalized_decision,
            control_proposal=control_proposal,
        )
        return update

    def validate_action(
        self,
        state: RunState,
        config: RunnableConfig,
    ) -> RunState:
        node = "validate_action"
        if state.get("error") is not None:
            return self._entered(state, node)
        if not _current_observation_is_anchored(state, config):
            return self._fatal_contract(
                state,
                node,
                "current observation identity is not anchored to thread history",
            )
        observation = state.get("observation")
        proposal = state.get("control_proposal")
        normalized_proposal = None
        if proposal is not None:
            try:
                normalized_proposal = ControlProposal.model_validate(
                    proposal.model_dump()
                )
            except Exception:
                return self._fatal_contract(
                    state,
                    node,
                    "control proposal contract is invalid; supplied values were discarded",
                )
            if (
                observation is None
                or normalized_proposal.run_id != state.get("run_id")
                or normalized_proposal.run_id != observation.run_id
                or normalized_proposal.decision_id != observation.decision_id
                or normalized_proposal.observation_sequence != observation.sequence
            ):
                return self._fatal_contract(
                    state,
                    node,
                    "proposal identity does not match the current observation",
                )
        runtime_state = dict(state)
        runtime_state["control_proposal"] = normalized_proposal
        try:
            result = self.runtime.validate_action(
                GraphStateView.from_state(cast(RunState, runtime_state)),
                normalized_proposal,
            )
        except ExpectedGraphError as error:
            return self._fatal(state, node, error)
        except Exception:
            return self._unexpected(state, node)
        try:
            normalized_result = ValidationResult.model_validate(result.model_dump())
        except Exception:
            return self._fatal_contract(
                state,
                node,
                "validation result contract is contradictory or invalid",
            )
        update = self._entered(state, node)
        update.update(
            validation=normalized_result,
            control_proposal=normalized_proposal,
        )
        if normalized_result.approved:
            if (
                normalized_result.reason_code is not ValidationReasonCode.APPROVED
                or normalized_result.emergency_observed
                or normalized_result.validated_setpoint_c is None
                or not math.isfinite(normalized_result.validated_setpoint_c)
                or not 22.0 <= normalized_result.validated_setpoint_c <= 28.0
                or not normalized_result.evidence
                or normalized_proposal is None
                or not normalized_proposal.energy_evidence.strip()
                or not normalized_proposal.comfort_evidence.strip()
                or normalized_result.validated_setpoint_c
                != normalized_proposal.proposed_setpoint_c
            ):
                return self._fatal_contract(
                    state,
                    node,
                    "approved validation did not bind one exact safe proposal",
                )
            try:
                action = GraphAction(
                    run_id=normalized_proposal.run_id,
                    decision_id=normalized_proposal.decision_id,
                    observation_sequence=normalized_proposal.observation_sequence,
                    setpoint_c=normalized_result.validated_setpoint_c,
                    source=ActionSource.ADVISORY,
                    energy_evidence=normalized_proposal.energy_evidence,
                    comfort_evidence=normalized_proposal.comfort_evidence,
                )
            except Exception:
                return self._fatal_contract(
                    state,
                    node,
                    "authorized action contract is invalid; supplied values were discarded",
                )
            update.update(action=action, control_proposal=normalized_proposal)
        return update

    @staticmethod
    def validation_route(state: RunState) -> ValidationRoute:
        if state.get("error") is not None:
            return "fatal"
        result = state.get("validation")
        if result is None:
            return "fatal"
        if result.approved:
            return "approved"
        if state.get("revision_count", 0) < MAX_REVISIONS:
            return "retry"
        return "fallback"

    def revise_decision(self, state: RunState) -> RunState:
        node = "revise_decision"
        update = self._entered(state, node)
        update.update(
            revision_count=state.get("revision_count", 0) + 1,
            supervisor_decision=None,
            control_proposal=None,
            action=None,
        )
        return update

    def fallback_action(
        self,
        state: RunState,
        config: RunnableConfig,
    ) -> RunState:
        node = "fallback_action"
        if not _current_observation_is_anchored(state, config):
            return self._fatal_contract(
                state,
                node,
                "fallback observation identity is not anchored to thread history",
            )
        try:
            fallback = self.runtime.fallback_action(GraphStateView.from_state(state))
        except ExpectedGraphError as error:
            return self._fatal(state, node, error)
        except Exception:
            return self._unexpected(state, node)
        try:
            normalized_fallback = FallbackDecision.model_validate(fallback.model_dump())
        except Exception:
            return self._fatal_contract(
                state,
                node,
                "fallback contract is invalid; supplied values were discarded",
            )
        if (
            not math.isfinite(normalized_fallback.setpoint_c)
            or not 22.0 <= normalized_fallback.setpoint_c <= 28.0
        ):
            return self._fatal_contract(
                state,
                node,
                "fallback setpoint is outside deterministic hard bounds",
            )
        observation = state.get("observation")
        if (
            observation is None
            or observation.run_id != state.get("run_id")
            or not _identity_is_safe(observation.decision_id)
        ):
            return self._fatal_contract(
                state,
                node,
                "fallback cannot be bound to the current observation",
            )
        update = self._entered(state, node)
        try:
            action = GraphAction(
                run_id=observation.run_id,
                decision_id=observation.decision_id,
                observation_sequence=observation.sequence,
                setpoint_c=normalized_fallback.setpoint_c,
                source=ActionSource.FALLBACK,
                energy_evidence="deterministic fallback",
                comfort_evidence=normalized_fallback.reason_code.value,
                fallback=normalized_fallback,
            )
        except Exception:
            return self._fatal_contract(
                state,
                node,
                "fallback action contract is invalid; supplied values were discarded",
            )
        update.update(action=action)
        return update

    def apply_action(
        self,
        state: RunState,
        config: RunnableConfig,
    ) -> RunState:
        node = "apply_action"
        if state.get("error") is not None:
            return self._entered(state, node)
        if not _current_observation_is_anchored(state, config):
            return self._fatal_contract(
                state,
                node,
                "action observation identity is not anchored to thread history",
            )
        action = state.get("action")
        if action is None:
            return self._fatal_contract(state, node, "no authorized action is available")
        try:
            normalized_action = GraphAction.model_validate(action.model_dump())
        except Exception:
            return self._fatal_contract(
                state,
                node,
                "action contract is invalid; supplied values were discarded",
            )
        observation = state.get("observation")
        if (
            observation is None
            or normalized_action.run_id != state.get("run_id")
            or normalized_action.run_id != observation.run_id
            or normalized_action.decision_id != observation.decision_id
            or normalized_action.observation_sequence != observation.sequence
        ):
            return self._fatal_contract(
                state,
                node,
                "action identity does not match the current observation",
            )
        runtime_state = dict(state)
        runtime_state["action"] = normalized_action
        try:
            applied = self.runtime.apply_action(
                GraphStateView.from_state(cast(RunState, runtime_state)),
                normalized_action,
            )
        except ExpectedGraphError as error:
            return self._fatal(state, node, error)
        except Exception:
            return self._unexpected(state, node)
        try:
            normalized_applied = AppliedAction.model_validate(applied.model_dump())
        except Exception:
            return self._fatal_contract(
                state,
                node,
                "applied action result contract is invalid",
            )
        if normalized_applied.action != normalized_action:
            return self._fatal_contract(
                state,
                node,
                "applied action result differs from the authorized action",
            )
        update = self._entered(state, node)
        update.update(
            action=normalized_action,
            applied_action=normalized_applied,
        )
        return update

    @staticmethod
    def apply_route(state: RunState) -> ApplyRoute:
        return "fatal" if state.get("error") is not None else "advance"

    def advance_and_evaluate(self, state: RunState) -> RunState:
        node = "advance_and_evaluate"
        try:
            evaluation = self.runtime.advance_and_evaluate(
                GraphStateView.from_state(state)
            )
        except ExpectedGraphError as error:
            return self._fatal(state, node, error)
        except Exception:
            return self._unexpected(state, node)
        try:
            normalized_evaluation = EvaluationRecord.model_validate(
                evaluation.model_dump()
            )
        except Exception:
            return self._fatal_contract(
                state,
                node,
                "evaluation contract is invalid; supplied values were discarded",
            )
        applied = state.get("applied_action")
        if (
            applied is None
            or normalized_evaluation.decision_id != applied.action.decision_id
        ):
            return self._fatal_contract(
                state,
                node,
                "evaluation decision identity differs from the applied action",
            )
        update = self._entered(state, node)
        update.update(
            evaluation=normalized_evaluation,
            completed_decisions=state.get("completed_decisions", 0) + 1,
        )
        return update

    def reflect(self, state: RunState) -> RunState:
        node = "reflect"
        if state.get("error") is not None:
            return self._entered(state, node)
        try:
            reflection = self.runtime.reflect(GraphStateView.from_state(state))
        except ExpectedGraphError as error:
            return self._fatal(state, node, error)
        except Exception:
            return self._unexpected(state, node)
        try:
            normalized_reflection = ReflectionRecord.model_validate(
                reflection.model_dump()
            )
        except Exception:
            return self._fatal_contract(
                state,
                node,
                "reflection contract is invalid; supplied values were discarded",
            )
        evaluation = state.get("evaluation")
        if (
            evaluation is None
            or normalized_reflection.decision_id != evaluation.decision_id
        ):
            return self._fatal_contract(
                state,
                node,
                "reflection decision identity differs from the evaluation",
            )
        update = self._entered(state, node)
        update.update(reflection=normalized_reflection)
        return update

    def continue_or_finish(self, state: RunState) -> RunState:
        node = "continue_or_finish"
        if state.get("error") is not None:
            update = self._entered(state, node)
            update.update(completion_route=CompletionRoute.FATAL)
            return update
        if state.get("completed_decisions", 0) >= state.get("max_decisions", 1):
            route = CompletionRoute.FINISH
        else:
            try:
                route = self.runtime.continue_or_finish(
                    GraphStateView.from_state(state)
                )
            except ExpectedGraphError as error:
                return self._fatal(state, node, error)
            except Exception:
                return self._unexpected(state, node)
        update = self._entered(state, node)
        update.update(completion_route=route)
        return update

    @staticmethod
    def finish_route(state: RunState) -> FinishRoute:
        if state.get("error") is not None:
            return "fatal"
        route = state.get("completion_route")
        if route is CompletionRoute.CONTINUE:
            return "continue"
        if route is CompletionRoute.FINISH:
            return "finish"
        return "fatal"

    def finalize_run(self, state: RunState) -> RunState:
        node = "finalize_run"
        try:
            summary = self.runtime.finalize_run(GraphStateView.from_state(state))
        except ExpectedGraphError as error:
            return self._fatal(state, node, error)
        except Exception:
            return self._unexpected(state, node)
        try:
            normalized_summary = RunSummary.model_validate(summary.model_dump())
        except Exception:
            return self._fatal_contract(
                state,
                node,
                "final summary contract is invalid; supplied values were discarded",
            )
        if (
            normalized_summary.run_id != state.get("run_id")
            or normalized_summary.status is not SimulationStatus.COMPLETED
            or normalized_summary.completed_decisions
            != state.get("completed_decisions", 0)
        ):
            return self._fatal_contract(
                state,
                node,
                "final summary identity, count, or status differs from graph state",
            )
        update = self._entered(state, node)
        update.update(
            summary=normalized_summary,
            simulation_status=SimulationStatus.COMPLETED,
        )
        return update

    @staticmethod
    def finalize_route(state: RunState) -> FinalizeRoute:
        return "fatal" if state.get("error") is not None else "success"

    def abort_safely(self, state: RunState) -> RunState:
        node = "abort_safely"
        error = state.get("error") or GraphError(
            code="UNSPECIFIED_FATAL_ERROR",
            node=node,
            message="graph entered the fatal route without an error record",
            fatal=True,
            occurred_at_utc=_utc_now(),
        )
        update = self._entered(state, node)
        if state.get("cleanup_completed", False):
            return update
        cleanup_completed = False
        try:
            self.runtime.abort_safely(GraphStateView.from_state(state), error)
            cleanup_completed = True
        except Exception:
            cleanup_completed = False
        update.update(
            error=error,
            cleanup_completed=cleanup_completed,
            simulation_status=SimulationStatus.FAILED,
            completion_route=CompletionRoute.FATAL,
        )
        return update


def build_state_graph(nodes: GraphNodeSet) -> StateGraph[RunState]:
    """Build the exact named process topology before compilation."""

    builder = StateGraph(RunState)
    builder.add_node("initialize_run", nodes.initialize_run)
    builder.add_node("await_observation", nodes.await_observation)
    builder.add_node("energy_agent", nodes.energy_agent)
    builder.add_node("comfort_agent", nodes.comfort_agent)
    builder.add_node("supervisor", nodes.supervisor)
    builder.add_node("validate_action", nodes.validate_action)
    builder.add_node("revise_decision", nodes.revise_decision)
    builder.add_node("fallback_action", nodes.fallback_action)
    builder.add_node("apply_action", nodes.apply_action)
    builder.add_node("advance_and_evaluate", nodes.advance_and_evaluate)
    builder.add_node("reflect", nodes.reflect)
    builder.add_node("continue_or_finish", nodes.continue_or_finish)
    builder.add_node("finalize_run", nodes.finalize_run)
    builder.add_node("abort_safely", nodes.abort_safely)

    builder.add_edge(START, "initialize_run")
    builder.add_edge("initialize_run", "await_observation")
    builder.add_edge("await_observation", "energy_agent")
    builder.add_edge("energy_agent", "comfort_agent")
    builder.add_edge("comfort_agent", "supervisor")
    builder.add_edge("supervisor", "validate_action")
    builder.add_conditional_edges(
        "validate_action",
        nodes.validation_route,
        {
            "approved": "apply_action",
            "retry": "revise_decision",
            "fallback": "fallback_action",
            "fatal": "abort_safely",
        },
    )
    builder.add_edge("revise_decision", "supervisor")
    builder.add_edge("fallback_action", "apply_action")
    builder.add_conditional_edges(
        "apply_action",
        nodes.apply_route,
        {"advance": "advance_and_evaluate", "fatal": "abort_safely"},
    )
    builder.add_edge("advance_and_evaluate", "reflect")
    builder.add_edge("reflect", "continue_or_finish")
    builder.add_conditional_edges(
        "continue_or_finish",
        nodes.finish_route,
        {
            "continue": "await_observation",
            "finish": "finalize_run",
            "fatal": "abort_safely",
        },
    )
    builder.add_conditional_edges(
        "finalize_run",
        nodes.finalize_route,
        {"success": END, "fatal": "abort_safely"},
    )
    builder.add_edge("abort_safely", END)
    return builder


class GraphRunner:
    """In-process graph runner with isolated checkpoints and redacted v2 events."""

    def __init__(
        self,
        runtime: GraphRuntime,
        *,
        checkpointer: BaseCheckpointSaver[str] | None = None,
        interrupt_after: Sequence[str] | None = None,
        recursion_limit_override: int | None = None,
    ) -> None:
        self.nodes = GraphNodeSet(runtime)
        self.runtime = runtime
        self.checkpointer = checkpointer or InMemorySaver(
            serde=JsonPlusSerializer(
                allowed_msgpack_modules=[
                    ("bms_agent.control.safety", "ControlProposal"),
                    ("bms_agent.control.safety", "FallbackDecision"),
                    ("bms_agent.control.safety", "FallbackReasonCode"),
                    ("bms_agent.control.safety", "ObservationEnvelope"),
                    ("bms_agent.control.safety", "ValidationReasonCode"),
                    ("bms_agent.control.safety", "ValidationResult"),
                    ("bms_agent.graph.state", "ActionSource"),
                    ("bms_agent.graph.state", "AppliedAction"),
                    ("bms_agent.graph.state", "CompletionRoute"),
                    ("bms_agent.graph.state", "EvaluationRecord"),
                    ("bms_agent.graph.state", "GraphAction"),
                    ("bms_agent.graph.state", "GraphError"),
                    ("bms_agent.graph.state", "ReflectionRecord"),
                    ("bms_agent.graph.state", "RunSummary"),
                    ("bms_agent.graph.state", "SimulationStatus"),
                    ("bms_agent.llm.schemas", "ComfortAssessment"),
                    ("bms_agent.llm.schemas", "ComfortRisk"),
                    ("bms_agent.llm.schemas", "ComfortState"),
                    ("bms_agent.llm.schemas", "EnergyEffect"),
                    ("bms_agent.llm.schemas", "EnergyProposal"),
                    ("bms_agent.llm.schemas", "SetpointDirection"),
                    ("bms_agent.llm.schemas", "SupervisorDecision"),
                    ("bms_agent.llm.schemas", "SupervisorDisposition"),
                ]
            )
        )
        self.recursion_limit_override = recursion_limit_override
        self.builder = build_state_graph(self.nodes)
        self.graph: CompiledStateGraph[RunState, None, RunState, RunState] = (
            self.builder.compile(
            checkpointer=self.checkpointer,
            interrupt_after=list(interrupt_after) if interrupt_after else None,
            name="eco_loop_control",
        )
        )

    @staticmethod
    def new_run_id() -> str:
        return f"bms-{uuid4()}"

    @staticmethod
    def _validated_run(run: GraphRunInput) -> GraphRunInput:
        try:
            return GraphRunInput.model_validate(run.model_dump())
        except Exception as error:
            raise ValueError("invalid graph run input") from error

    def _config(self, run_id: str, max_decisions: int) -> RunnableConfig:
        limit = self.recursion_limit_override or recursion_limit_for(max_decisions)
        return {
            "configurable": {"thread_id": run_id},
            "recursion_limit": limit,
        }

    def _existing_config(self, run_id: str) -> tuple[RunnableConfig, int]:
        if not _identity_is_safe(run_id):
            raise ValueError("invalid graph run identity")
        snapshot = self.graph.get_state(
            {"configurable": {"thread_id": run_id}}
        )
        state = cast(RunState, snapshot.values)
        max_decisions = state.get("max_decisions")
        if max_decisions is None:
            raise KeyError(f"unknown graph run: {run_id}")
        return self._config(run_id, max_decisions), max_decisions

    def invoke(self, run: GraphRunInput) -> RunState:
        normalized_run = self._validated_run(run)
        config = self._config(normalized_run.run_id, normalized_run.max_decisions)
        existing = self.graph.get_state(
            {"configurable": {"thread_id": normalized_run.run_id}}
        )
        if existing.values:
            raise ValueError("graph run_id already exists")
        try:
            result = self.graph.invoke(
                _initial_state(normalized_run),
                config,
                durability="sync",
                version="v2",
            )
        except GraphRecursionError:
            return self._abort_recursion(normalized_run.run_id, config)
        return result.value

    def resume(self, run_id: str) -> RunState:
        config, _ = self._existing_config(run_id)
        try:
            result = self.graph.invoke(
                None,
                config,
                durability="sync",
                version="v2",
            )
        except GraphRecursionError:
            return self._abort_recursion(run_id, config)
        return result.value

    def get_state(self, run_id: str) -> StateSnapshot:
        if not _identity_is_safe(run_id):
            raise ValueError("invalid graph run identity")
        return self.graph.get_state({"configurable": {"thread_id": run_id}})

    def get_state_history(
        self,
        run_id: str,
        *,
        limit: int | None = None,
    ) -> tuple[StateSnapshot, ...]:
        if not _identity_is_safe(run_id):
            raise ValueError("invalid graph run identity")
        history = self.graph.get_state_history(
            {"configurable": {"thread_id": run_id}},
            limit=limit,
        )
        return tuple(history)

    def stream(self, run: GraphRunInput) -> Iterator[GraphEvent]:
        normalized_run = self._validated_run(run)
        config = self._config(normalized_run.run_id, normalized_run.max_decisions)
        existing = self.graph.get_state(
            {"configurable": {"thread_id": normalized_run.run_id}}
        )
        if existing.values:
            raise ValueError("graph run_id already exists")
        try:
            parts = self.graph.stream(
                _initial_state(normalized_run),
                config,
                stream_mode=["updates", "tasks"],
                version="v2",
                durability="sync",
            )
            yield from self._normalize_events(parts, normalized_run.run_id)
        except GraphRecursionError:
            state = self._abort_recursion(normalized_run.run_id, config)
            error = state.get("error")
            yield GraphEvent(
                timestamp_utc=_utc_now(),
                run_id=normalized_run.run_id,
                decision_id=self._decision_id(state),
                node="abort_safely",
                phase="error",
                changed_fields=("cleanup_completed", "error", "simulation_status"),
                error=error is not None,
            )

    def _abort_recursion(
        self,
        run_id: str,
        config: RunnableConfig,
    ) -> RunState:
        snapshot = self.graph.get_state(config)
        state = cast(RunState, snapshot.values)
        error = GraphError(
            code="GRAPH_RECURSION_EXHAUSTED",
            node=(
                state.get("graph_node", "unknown")
                if _event_name_is_safe(
                    state.get("graph_node", "unknown"), _EVENT_NODE_RE
                )
                else "unknown"
            ),
            message="derived graph recursion budget was exhausted",
            fatal=True,
            occurred_at_utc=_utc_now(),
        )
        update: RunState = RunState(
            graph_node="abort_safely",
            recent_nodes=(
                *state.get("recent_nodes", ()),
                "abort_safely",
            )[-RECENT_NODE_LIMIT:],
            error=error,
            completion_route=CompletionRoute.FATAL,
            simulation_status=SimulationStatus.FAILED,
            cleanup_completed=False,
        )
        if not state.get("cleanup_completed", False):
            try:
                self.runtime.abort_safely(GraphStateView.from_state(state), error)
                update["cleanup_completed"] = True
            except Exception:
                update["cleanup_completed"] = False
        self.graph.update_state(config, update, as_node="abort_safely")
        merged = dict(state)
        merged.update(update)
        return cast(RunState, merged)

    @staticmethod
    def _decision_id(state: RunState) -> str | None:
        observation = state.get("observation")
        if observation is None or not _identity_is_safe(observation.decision_id):
            return None
        return observation.decision_id

    def _normalize_events(
        self,
        parts: Iterator[dict[str, object] | object],
        run_id: str,
    ) -> Iterator[GraphEvent]:
        for raw_part in parts:
            if not isinstance(raw_part, Mapping):
                continue
            event_type = raw_part.get("type")
            data = raw_part.get("data")
            if event_type == "updates" and isinstance(data, Mapping):
                yield from self._normalize_update(data, run_id)
            elif event_type == "tasks" and isinstance(data, Mapping):
                name = data.get("name")
                if not _event_name_is_safe(name, _EVENT_NODE_RE):
                    continue
                assert isinstance(name, str)
                is_finish = "result" in data or "error" in data
                task_error = data.get("error")
                has_error = task_error is not None
                yield GraphEvent(
                    timestamp_utc=_utc_now(),
                    run_id=run_id,
                    decision_id=self._decision_id_from_checkpoint(run_id),
                    node=name,
                    phase="error" if has_error else ("finish" if is_finish else "start"),
                    changed_fields=(),
                    error=has_error,
                )

    def _normalize_update(
        self,
        data: Mapping[object, object],
        run_id: str,
    ) -> Iterator[GraphEvent]:
        for raw_node, raw_update in data.items():
            if not _event_name_is_safe(raw_node, _EVENT_NODE_RE):
                continue
            assert isinstance(raw_node, str)
            changed_fields: tuple[str, ...] = ()
            error = False
            if isinstance(raw_update, Mapping):
                changed_fields = tuple(
                    sorted(
                        key
                        for key in raw_update
                        if _event_name_is_safe(key, _EVENT_FIELD_RE)
                    )[:32]
                )
                error = raw_update.get("error") is not None
            yield GraphEvent(
                timestamp_utc=_utc_now(),
                run_id=run_id,
                decision_id=self._decision_id_from_checkpoint(run_id),
                node=raw_node,
                phase="error" if error else "update",
                changed_fields=changed_fields,
                error=error,
            )

    def _decision_id_from_checkpoint(self, run_id: str) -> str | None:
        state = cast(RunState, self.get_state(run_id).values)
        return self._decision_id(state)


__all__ = [
    "FIXED_RUN_NODES",
    "GraphNodeSet",
    "GraphRunner",
    "MAX_REVISIONS",
    "RECENT_NODE_LIMIT",
    "RECENT_OBSERVATION_LIMIT",
    "WORST_CASE_NODES_PER_DECISION",
    "build_state_graph",
    "recursion_limit_for",
]
