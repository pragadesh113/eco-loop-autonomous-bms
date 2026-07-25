"""Fake-driven acceptance tests for the typed LangGraph process."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

import pytest
from pydantic import ValidationError

import bms_agent.graph.workflow as graph_workflow
from bms_agent.control import (
    ControlProposal,
    FallbackDecision,
    FallbackReasonCode,
    ObservationEnvelope,
    ObservationSnapshot,
    ValidationReasonCode,
    ValidationResult,
    ZoneSnapshot,
)
from bms_agent.graph import (
    ActionSource,
    AppliedAction,
    CompletionRoute,
    EvaluationRecord,
    ExpectedGraphError,
    GraphAction,
    GraphError,
    GraphEvent,
    GraphRunInput,
    GraphRunner,
    GraphStateView,
    ReflectionRecord,
    RunState,
    RunSummary,
    SimulationStatus,
    recursion_limit_for,
)
from bms_agent.llm import (
    ComfortAssessment,
    ComfortRisk,
    ComfortState,
    EnergyEffect,
    EnergyProposal,
    SetpointDirection,
    SupervisorDecision,
    SupervisorDisposition,
)
from bms_agent.simulation.baseline import ZONES

UTC_0 = "2026-07-26T00:00:00Z"
UTC_1 = "2026-07-26T01:00:00Z"


def _no_decision_id(_run_id: str) -> None:
    return None


class ContractExplosion(Exception):
    """Non-standard contract exception used to prove generic containment."""


class ExplodingContract:
    def model_dump(self) -> dict[str, object]:
        raise ContractExplosion("SECRET_MARKER must never escape")


@dataclass
class FakeRuntime:
    rejected_validations: int = 0
    fatal_node: str | None = None
    unexpected_node: str | None = None
    observation_run_override: str | None = None
    observation_decision_override: str | None = None
    repeated_sequence: bool = False
    energy_mode: str = "normal"
    comfort_mode: str = "normal"
    supervisor_mode: str = "normal"
    supervisor_setpoint_c: float = 24.0
    supervisor_energy_evidence: str = "hold avoids extra cooling"
    supervisor_comfort_evidence: str = "occupied PMV safe"
    validation_mode: str = "normal"
    applied_mismatch: bool = False
    evaluation_mismatch: bool = False
    reflection_mismatch: bool = False
    summary_mismatch: bool = False
    initialize_calls: int = 0
    observation_calls: int = 0
    supervisor_calls: int = 0
    validation_calls: int = 0
    fallback_calls: int = 0
    apply_calls: int = 0
    abort_calls: int = 0
    finalize_calls: int = 0
    applied_actions: list[GraphAction] = field(default_factory=lambda: [])

    def _failure(self, node: str) -> None:
        if self.fatal_node == node:
            raise ExpectedGraphError("FAKE_FATAL", f"safe {node} failure")
        if self.unexpected_node == node:
            raise RuntimeError("SECRET raw prompt and traceback detail")

    def initialize_run(self, state: GraphStateView) -> None:
        self.initialize_calls += 1
        self._failure("initialize_run")

    def await_observation(self, state: GraphStateView) -> ObservationEnvelope:
        self.observation_calls += 1
        self._failure("await_observation")
        sequence = 1 if self.repeated_sequence else self.observation_calls
        observation = _observation(
            run_id=self.observation_run_override or state.run_id,
            sequence=sequence,
        )
        if self.observation_decision_override is not None:
            observation = observation.model_copy(
                update={"decision_id": self.observation_decision_override}
            )
        return observation

    def energy_agent(self, state: GraphStateView) -> EnergyProposal:
        self._failure("energy_agent")
        if self.energy_mode == "empty":
            return EnergyProposal.model_construct()
        if self.energy_mode == "foreign":
            return cast(EnergyProposal, object())
        if self.energy_mode == "exploding":
            return cast(EnergyProposal, ExplodingContract())
        return EnergyProposal(
            proposed_setpoint_c=24.0,
            expected_energy_effect=EnergyEffect.NEUTRAL,
            confidence=0.9,
            reason="bounded hold",
        )

    def comfort_agent(self, state: GraphStateView) -> ComfortAssessment:
        self._failure("comfort_agent")
        if self.comfort_mode == "empty":
            return ComfortAssessment.model_construct()
        if self.comfort_mode == "foreign":
            return cast(ComfortAssessment, object())
        if self.comfort_mode == "exploding":
            return cast(ComfortAssessment, ExplodingContract())
        return ComfortAssessment(
            comfort_state=ComfortState.COMFORTABLE,
            recommended_direction=SetpointDirection.HOLD,
            risk=ComfortRisk.LOW,
            reason="occupied PMV is safe",
        )

    def supervisor(self, state: GraphStateView) -> SupervisorDecision:
        self.supervisor_calls += 1
        self._failure("supervisor")
        if self.supervisor_mode == "empty":
            return SupervisorDecision.model_construct()
        if self.supervisor_mode == "foreign":
            return cast(SupervisorDecision, object())
        if self.supervisor_mode == "exploding":
            return cast(SupervisorDecision, ExplodingContract())
        values: dict[str, object] = {
            "disposition": SupervisorDisposition.ACCEPT,
            "proposed_setpoint_c": self.supervisor_setpoint_c,
            "conflict": False,
            "energy_evidence": self.supervisor_energy_evidence,
            "comfort_evidence": self.supervisor_comfort_evidence,
        }
        if (
            not 22.0 <= self.supervisor_setpoint_c <= 28.0
            or not self.supervisor_energy_evidence.strip()
            or not self.supervisor_comfort_evidence.strip()
        ):
            return SupervisorDecision.model_construct(
                **values  # pyright: ignore[reportArgumentType]
            )
        return SupervisorDecision(
            disposition=SupervisorDisposition.ACCEPT,
            proposed_setpoint_c=self.supervisor_setpoint_c,
            conflict=False,
            energy_evidence=self.supervisor_energy_evidence,
            comfort_evidence=self.supervisor_comfort_evidence,
        )

    def validate_action(
        self,
        state: GraphStateView,
        proposal: ControlProposal | None,
    ) -> ValidationResult:
        self.validation_calls += 1
        self._failure("validate_action")
        if self.validation_calls <= self.rejected_validations:
            return ValidationResult(
                approved=False,
                reason_code=ValidationReasonCode.RATE_LIMIT_EXCEEDED,
                validated_setpoint_c=None,
                emergency_observed=False,
                evidence=("fake rejection",),
            )
        assert proposal is not None
        if self.validation_mode != "normal":
            values: dict[str, object] = {
                "approved": True,
                "reason_code": ValidationReasonCode.APPROVED,
                "validated_setpoint_c": proposal.proposed_setpoint_c,
                "emergency_observed": False,
                "evidence": ("fake approval",),
            }
            if self.validation_mode == "contradictory_reason":
                values["reason_code"] = ValidationReasonCode.RATE_LIMIT_EXCEEDED
            elif self.validation_mode == "emergency":
                values["emergency_observed"] = True
            elif self.validation_mode == "missing_setpoint":
                values["validated_setpoint_c"] = None
            elif self.validation_mode == "nonfinite_setpoint":
                values["validated_setpoint_c"] = float("inf")
            elif self.validation_mode == "mismatched_setpoint":
                values["validated_setpoint_c"] = proposal.proposed_setpoint_c + 0.5
            elif self.validation_mode == "empty_validation_evidence":
                values["evidence"] = ()
            elif self.validation_mode == "rejected_approved_reason":
                values["approved"] = False
            return ValidationResult.model_construct(
                **values  # pyright: ignore[reportArgumentType]
            )
        return ValidationResult(
            approved=True,
            reason_code=ValidationReasonCode.APPROVED,
            validated_setpoint_c=proposal.proposed_setpoint_c,
            emergency_observed=False,
            evidence=("fake approval",),
        )

    def fallback_action(self, state: GraphStateView) -> FallbackDecision:
        self.fallback_calls += 1
        self._failure("fallback_action")
        return FallbackDecision(
            setpoint_c=24.0,
            reason_code=FallbackReasonCode.HOLD_OCCUPIED_COMFORTABLE,
            emergency_observed=False,
            used_default_reference=False,
            evidence=("deterministic hold",),
        )

    def apply_action(
        self,
        state: GraphStateView,
        action: GraphAction,
    ) -> AppliedAction:
        self.apply_calls += 1
        self._failure("apply_action")
        returned_action = action
        if self.applied_mismatch:
            returned_action = action.model_copy(update={"setpoint_c": 25.0})
        self.applied_actions.append(returned_action)
        return AppliedAction(
            action=returned_action,
            applied_at_utc=UTC_0,
            actuator_write_count=1,
        )

    def advance_and_evaluate(self, state: GraphStateView) -> EvaluationRecord:
        self._failure("advance_and_evaluate")
        assert state.applied_action is not None
        decision_id = (
            "wrong-decision"
            if self.evaluation_mismatch
            else state.applied_action.action.decision_id
        )
        return EvaluationRecord(
            decision_id=decision_id,
            evaluated_at_utc=UTC_1,
            energy_delta_kwh=-0.1,
            occupied_pmv_compliance_percent=100.0,
            safe=True,
        )

    def reflect(self, state: GraphStateView) -> ReflectionRecord:
        self._failure("reflect")
        assert state.evaluation is not None
        decision_id = (
            "wrong-decision"
            if self.reflection_mismatch
            else state.evaluation.decision_id
        )
        return ReflectionRecord(
            decision_id=decision_id,
            reflected_at_utc=UTC_1,
            predicted_energy_effect=EnergyEffect.NEUTRAL,
            measured_energy_delta_kwh=state.evaluation.energy_delta_kwh,
            energy_prediction_matched=True,
            predicted_comfort_risk=ComfortRisk.LOW,
            measured_occupied_pmv_compliance_percent=(
                state.evaluation.occupied_pmv_compliance_percent
            ),
            comfort_prediction_matched=True,
            outcome="safe measured outcome",
            recommend_continue=True,
        )

    def continue_or_finish(self, state: GraphStateView) -> CompletionRoute:
        self._failure("continue_or_finish")
        return CompletionRoute.CONTINUE

    def finalize_run(self, state: GraphStateView) -> RunSummary:
        self.finalize_calls += 1
        self._failure("finalize_run")
        return RunSummary(
            run_id="wrong-run" if self.summary_mismatch else state.run_id,
            completed_decisions=state.completed_decisions,
            status=SimulationStatus.COMPLETED,
            finalized_at_utc=UTC_1,
        )

    def abort_safely(self, state: GraphStateView, error: GraphError) -> None:
        self.abort_calls += 1


def _observation(*, run_id: str, sequence: int) -> ObservationEnvelope:
    return ObservationEnvelope(
        run_id=run_id,
        decision_id=f"decision-{sequence}",
        sequence=sequence,
        observed_at_utc=UTC_0,
        snapshot=ObservationSnapshot(
            current_setpoint_c=24.0,
            zones=tuple(
                ZoneSnapshot(
                    zone_id=zone,
                    temperature_c=24.0,
                    pmv=0.1,
                    occupancy_people=1.0,
                )
                for zone in ZONES
            ),
            temperature_unit="degC",
            pmv_unit="dimensionless",
            occupancy_unit="people",
        ),
    )


def _run(
    runtime: FakeRuntime,
    *,
    run_id: str = "graph-test",
    max_decisions: int = 1,
    recursion_limit_override: int | None = None,
) -> tuple[GraphRunner, RunState]:
    runner = GraphRunner(
        runtime,
        recursion_limit_override=recursion_limit_override,
    )
    state = runner.invoke(GraphRunInput(run_id=run_id, max_decisions=max_decisions))
    return runner, state


def test_exact_named_topology_and_no_retry_policy_on_apply() -> None:
    runner = GraphRunner(FakeRuntime())
    drawable = runner.graph.get_graph()
    nodes = set(drawable.nodes) - {"__start__", "__end__"}
    assert nodes == {
        "initialize_run",
        "await_observation",
        "energy_agent",
        "comfort_agent",
        "supervisor",
        "validate_action",
        "revise_decision",
        "fallback_action",
        "apply_action",
        "advance_and_evaluate",
        "reflect",
        "continue_or_finish",
        "finalize_run",
        "abort_safely",
    }
    edges = {(edge.source, edge.target) for edge in drawable.edges}
    assert {
        ("__start__", "initialize_run"),
        ("initialize_run", "await_observation"),
        ("await_observation", "energy_agent"),
        ("energy_agent", "comfort_agent"),
        ("comfort_agent", "supervisor"),
        ("supervisor", "validate_action"),
        ("validate_action", "apply_action"),
        ("validate_action", "revise_decision"),
        ("validate_action", "fallback_action"),
        ("validate_action", "abort_safely"),
        ("revise_decision", "supervisor"),
        ("fallback_action", "apply_action"),
        ("apply_action", "advance_and_evaluate"),
        ("apply_action", "abort_safely"),
        ("advance_and_evaluate", "reflect"),
        ("reflect", "continue_or_finish"),
        ("continue_or_finish", "await_observation"),
        ("continue_or_finish", "finalize_run"),
        ("continue_or_finish", "abort_safely"),
        ("finalize_run", "__end__"),
        ("finalize_run", "abort_safely"),
        ("abort_safely", "__end__"),
    } == edges
    assert all(
        node.retry_policy is None
        for node in runner.builder.nodes.values()
    )


def test_approved_path_applies_once_and_finishes() -> None:
    runtime = FakeRuntime()
    _, state = _run(runtime)
    assert state.get("simulation_status") is SimulationStatus.COMPLETED
    assert state.get("completed_decisions") == 1
    assert runtime.apply_calls == 1
    assert runtime.abort_calls == 0
    assert runtime.finalize_calls == 1
    assert runtime.applied_actions[0].source is ActionSource.ADVISORY
    assert state.get("recent_nodes") == (
        "initialize_run",
        "await_observation",
        "energy_agent",
        "comfort_agent",
        "supervisor",
        "validate_action",
        "apply_action",
        "advance_and_evaluate",
        "reflect",
        "continue_or_finish",
        "finalize_run",
    )


@pytest.mark.parametrize("rejections", [1, 2])
def test_one_or_two_semantic_revisions_then_approval(rejections: int) -> None:
    runtime = FakeRuntime(rejected_validations=rejections)
    _, state = _run(runtime, run_id=f"revision-{rejections}")
    assert state.get("simulation_status") is SimulationStatus.COMPLETED
    assert state.get("revision_count") == rejections
    assert runtime.supervisor_calls == rejections + 1
    assert runtime.validation_calls == rejections + 1
    assert runtime.apply_calls == 1


def test_retry_exhaustion_uses_deterministic_fallback() -> None:
    runtime = FakeRuntime(rejected_validations=99)
    _, state = _run(runtime, run_id="fallback")
    assert state.get("simulation_status") is SimulationStatus.COMPLETED
    assert state.get("revision_count") == 2
    assert runtime.validation_calls == 3
    assert runtime.supervisor_calls == 3
    assert runtime.fallback_calls == 1
    assert runtime.apply_calls == 1
    assert runtime.applied_actions[0].source is ActionSource.FALLBACK


@pytest.mark.parametrize("fatal_node", ["energy_agent", "validate_action"])
def test_provider_or_validation_fatal_aborts_without_actuation(
    fatal_node: str,
) -> None:
    runtime = FakeRuntime(fatal_node=fatal_node)
    _, state = _run(runtime, run_id=f"fatal-{fatal_node}")
    assert state.get("simulation_status") is SimulationStatus.FAILED
    assert state.get("cleanup_completed") is True
    assert runtime.abort_calls == 1
    assert runtime.apply_calls == 0
    error = state.get("error")
    assert isinstance(error, GraphError)
    assert error.code == "FAKE_FATAL"


def test_apply_failure_is_fatal_and_never_retried() -> None:
    runtime = FakeRuntime(fatal_node="apply_action")
    _, state = _run(runtime, run_id="apply-fatal")
    assert state.get("simulation_status") is SimulationStatus.FAILED
    assert runtime.apply_calls == 1
    assert runtime.abort_calls == 1
    assert state.get("completed_decisions") == 0


@pytest.mark.parametrize(
    ("fatal_node", "unexpected", "summary_mismatch"),
    [
        ("finalize_run", False, False),
        (None, True, False),
        (None, False, True),
    ],
)
def test_every_finalization_failure_routes_through_abort_exactly_once(
    fatal_node: str | None,
    unexpected: bool,
    summary_mismatch: bool,
) -> None:
    runtime = FakeRuntime(
        fatal_node=fatal_node,
        unexpected_node="finalize_run" if unexpected else None,
        summary_mismatch=summary_mismatch,
    )
    _, state = _run(
        runtime,
        run_id=f"finalize-failure-{fatal_node or unexpected or summary_mismatch}",
    )
    assert state.get("simulation_status") is SimulationStatus.FAILED
    assert state.get("cleanup_completed") is True
    assert runtime.finalize_calls == 1
    assert runtime.abort_calls == 1
    assert runtime.apply_calls == 1
    recent = state.get("recent_nodes", ())
    assert recent[-2:] == ("finalize_run", "abort_safely")


@pytest.mark.parametrize(
    "validation_mode",
    [
        "contradictory_reason",
        "emergency",
        "missing_setpoint",
        "nonfinite_setpoint",
        "mismatched_setpoint",
        "empty_validation_evidence",
        "rejected_approved_reason",
    ],
)
def test_contradictory_validation_never_authorizes(
    validation_mode: str,
) -> None:
    runtime = FakeRuntime(validation_mode=validation_mode)
    _, state = _run(runtime, run_id=f"invalid-validation-{validation_mode}")
    assert state.get("simulation_status") is SimulationStatus.FAILED
    assert state.get("cleanup_completed") is True
    assert runtime.apply_calls == 0
    assert runtime.abort_calls == 1
    error = state.get("error")
    assert isinstance(error, GraphError)
    assert error.code == "GRAPH_CONTRACT_ERROR"
    assert "ValidationError" not in error.message


@pytest.mark.parametrize(
    ("setpoint_c", "energy_evidence", "comfort_evidence"),
    [
        (float("inf"), "energy", "comfort"),
        (21.0, "energy", "comfort"),
        (29.0, "energy", "comfort"),
        (24.0, " ", "comfort"),
        (24.0, "energy", ""),
    ],
)
def test_malformed_internal_proposal_construction_is_controlled_and_redacted(
    setpoint_c: float,
    energy_evidence: str,
    comfort_evidence: str,
) -> None:
    runtime = FakeRuntime(
        supervisor_setpoint_c=setpoint_c,
        supervisor_energy_evidence=energy_evidence,
        supervisor_comfort_evidence=comfort_evidence,
        validation_mode="forged_exact",
    )
    _, state = _run(runtime, run_id="malformed-internal-proposal")
    assert state.get("simulation_status") is SimulationStatus.FAILED
    assert state.get("cleanup_completed") is True
    assert runtime.apply_calls == 0
    assert runtime.abort_calls == 1
    error = state.get("error")
    assert isinstance(error, GraphError)
    assert error.code == "GRAPH_CONTRACT_ERROR"
    serialized = error.model_dump_json()
    assert "ValidationError" not in serialized
    assert "input_value" not in serialized
    assert str(setpoint_c) not in serialized


@pytest.mark.parametrize("role", ["energy", "comfort", "supervisor"])
@pytest.mark.parametrize("mode", ["empty", "foreign", "exploding"])
def test_every_advisory_return_is_normalized_before_use_or_checkpoint(
    role: str,
    mode: str,
) -> None:
    runtime = FakeRuntime()
    setattr(runtime, f"{role}_mode", mode)
    _, state = _run(runtime, run_id=f"advisory-contract-{role}-{mode}")
    assert state.get("simulation_status") is SimulationStatus.FAILED
    assert state.get("cleanup_completed") is True
    assert runtime.apply_calls == 0
    assert runtime.abort_calls == 1
    error = state.get("error")
    assert isinstance(error, GraphError)
    assert error.code == "GRAPH_CONTRACT_ERROR"
    serialized = error.model_dump_json()
    assert "SECRET_MARKER" not in serialized
    assert "AttributeError" not in serialized
    assert "ContractExplosion" not in serialized
    if role == "energy":
        assert state.get("energy_proposal") is None
    if role == "comfort":
        assert state.get("comfort_assessment") is None
    if role == "supervisor":
        assert state.get("supervisor_decision") is None


@pytest.mark.parametrize("contract_name", ["ControlProposal", "GraphAction"])
def test_nonstandard_internal_constructor_exception_is_contained(
    contract_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode_contract(**values: object) -> object:
        raise ContractExplosion("SECRET_MARKER constructor value")

    monkeypatch.setattr(graph_workflow, contract_name, explode_contract)
    runtime = FakeRuntime()
    _, state = _run(runtime, run_id=f"constructor-exception-{contract_name}")
    assert state.get("simulation_status") is SimulationStatus.FAILED
    assert state.get("cleanup_completed") is True
    assert runtime.apply_calls == 0
    assert runtime.abort_calls == 1
    error = state.get("error")
    assert isinstance(error, GraphError)
    assert error.code == "GRAPH_CONTRACT_ERROR"
    assert "SECRET_MARKER" not in error.model_dump_json()


def test_continue_then_finish_at_deterministic_decision_cap() -> None:
    runtime = FakeRuntime()
    _, state = _run(runtime, run_id="two-decisions", max_decisions=2)
    assert state.get("simulation_status") is SimulationStatus.COMPLETED
    assert state.get("completed_decisions") == 2
    assert runtime.observation_calls == 2
    assert runtime.apply_calls == 2
    observations = state.get("recent_observations")
    assert isinstance(observations, tuple)
    assert [item.sequence for item in observations] == [1, 2]


def test_recursion_exhaustion_aborts_once_and_returns_controlled_state() -> None:
    runtime = FakeRuntime()
    _, state = _run(
        runtime,
        run_id="recursion",
        recursion_limit_override=1,
    )
    assert state.get("simulation_status") is SimulationStatus.FAILED
    assert state.get("cleanup_completed") is True
    assert runtime.abort_calls == 1
    error = state.get("error")
    assert isinstance(error, GraphError)
    assert error.code == "GRAPH_RECURSION_EXHAUSTED"


def test_checkpoint_isolation_resume_and_history() -> None:
    runtime = FakeRuntime()
    runner = GraphRunner(runtime, interrupt_after=["validate_action"])
    first = runner.invoke(GraphRunInput(run_id="checkpoint-a", max_decisions=1))
    second = runner.invoke(GraphRunInput(run_id="checkpoint-b", max_decisions=1))
    assert first.get("graph_node") == "validate_action"
    assert second.get("graph_node") == "validate_action"
    assert runner.get_state("checkpoint-a").next == ("apply_action",)
    assert runner.get_state("checkpoint-b").next == ("apply_action",)
    resumed = runner.resume("checkpoint-a")
    assert resumed.get("simulation_status") is SimulationStatus.COMPLETED
    assert runner.get_state("checkpoint-b").next == ("apply_action",)
    history = runner.get_state_history("checkpoint-a")
    assert len(history) >= 3
    assert all(
        snapshot.config.get("configurable", {}).get("thread_id") == "checkpoint-a"
        for snapshot in history
    )


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("run_id", "wrong-run"),
        ("decision_id", "wrong-decision"),
        ("observation_sequence", 99),
    ],
)
def test_resumed_proposal_identity_is_rebound_before_validation(
    field_name: str,
    bad_value: str | int,
) -> None:
    runtime = FakeRuntime()
    runner = GraphRunner(runtime, interrupt_after=["supervisor"])
    run_id = f"proposal-binding-{field_name}"
    interrupted = runner.invoke(GraphRunInput(run_id=run_id, max_decisions=1))
    proposal = interrupted.get("control_proposal")
    assert isinstance(proposal, ControlProposal)
    corrupted = proposal.model_copy(update={field_name: bad_value})
    runner.graph.update_state(
        {"configurable": {"thread_id": run_id}},
        {"control_proposal": corrupted},
        as_node="supervisor",
    )
    resumed = runner.resume(run_id)
    assert resumed.get("simulation_status") is SimulationStatus.FAILED
    assert resumed.get("cleanup_completed") is True
    assert runtime.validation_calls == 0
    assert runtime.apply_calls == 0
    assert runtime.abort_calls == 1


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("run_id", "wrong-run"),
        ("decision_id", "wrong-decision"),
        ("observation_sequence", 99),
    ],
)
def test_direct_validation_node_rejects_each_proposal_identity_mutation(
    field_name: str,
    bad_value: str | int,
) -> None:
    runtime = FakeRuntime()
    runner = GraphRunner(runtime)
    observation = _observation(run_id="direct-binding", sequence=1)
    proposal = ControlProposal(
        run_id=observation.run_id,
        decision_id=observation.decision_id,
        observation_sequence=observation.sequence,
        proposed_setpoint_c=24.0,
        energy_evidence="energy",
        comfort_evidence="comfort",
    ).model_copy(update={field_name: bad_value})
    state = RunState(
        run_id="direct-binding",
        max_decisions=1,
        completed_decisions=0,
        simulation_status=SimulationStatus.RUNNING,
        graph_node="supervisor",
        observation=observation,
        recent_observations=(observation,),
        control_proposal=proposal,
    )
    failed = runner.nodes.validate_action(
        state,
        {"configurable": {"thread_id": "direct-binding"}},
    )
    merged = state.copy()
    merged.update(failed)
    cleaned = runner.nodes.abort_safely(merged)
    assert cleaned.get("cleanup_completed") is True
    assert runtime.validation_calls == 0
    assert runtime.apply_calls == 0
    assert runtime.abort_calls == 1


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("run_id", "wrong-run"),
        ("decision_id", "wrong-decision"),
        ("observation_sequence", 99),
    ],
)
def test_resumed_action_identity_is_rebound_before_apply(
    field_name: str,
    bad_value: str | int,
) -> None:
    runtime = FakeRuntime()
    runner = GraphRunner(runtime, interrupt_after=["validate_action"])
    run_id = f"action-binding-{field_name}"
    interrupted = runner.invoke(GraphRunInput(run_id=run_id, max_decisions=1))
    action = interrupted.get("action")
    assert isinstance(action, GraphAction)
    corrupted = action.model_copy(update={field_name: bad_value})
    runner.graph.update_state(
        {"configurable": {"thread_id": run_id}},
        {"action": corrupted},
        as_node="validate_action",
    )
    resumed = runner.resume(run_id)
    assert resumed.get("simulation_status") is SimulationStatus.FAILED
    assert resumed.get("cleanup_completed") is True
    assert runtime.apply_calls == 0
    assert runtime.abort_calls == 1


@pytest.mark.parametrize("identity_axis", ["run_id", "decision_id", "sequence"])
@pytest.mark.parametrize("checkpoint_node", ["supervisor", "validate_action"])
def test_coordinated_current_identity_mutation_cannot_redefine_checkpoint_anchor(
    identity_axis: str,
    checkpoint_node: str,
) -> None:
    runtime = FakeRuntime()
    runner = GraphRunner(runtime, interrupt_after=[checkpoint_node])
    run_id = f"coordinated-{checkpoint_node}-{identity_axis}"
    interrupted = runner.invoke(GraphRunInput(run_id=run_id, max_decisions=1))
    observation = interrupted.get("observation")
    proposal = interrupted.get("control_proposal")
    assert isinstance(observation, ObservationEnvelope)
    assert isinstance(proposal, ControlProposal)
    updates: dict[str, object] = {}
    if identity_axis == "run_id":
        bad_run_id = "coordinated-wrong-run"
        updates["run_id"] = bad_run_id
        updates["observation"] = observation.model_copy(update={"run_id": bad_run_id})
        updates["control_proposal"] = proposal.model_copy(
            update={"run_id": bad_run_id}
        )
    elif identity_axis == "decision_id":
        bad_decision_id = "coordinated-wrong-decision"
        updates["observation"] = observation.model_copy(
            update={"decision_id": bad_decision_id}
        )
        updates["control_proposal"] = proposal.model_copy(
            update={"decision_id": bad_decision_id}
        )
    else:
        updates["observation"] = observation.model_copy(update={"sequence": 99})
        updates["control_proposal"] = proposal.model_copy(
            update={"observation_sequence": 99}
        )
    action = interrupted.get("action")
    if checkpoint_node == "validate_action":
        assert isinstance(action, GraphAction)
        if identity_axis == "run_id":
            updates["action"] = action.model_copy(
                update={"run_id": "coordinated-wrong-run"}
            )
        elif identity_axis == "decision_id":
            updates["action"] = action.model_copy(
                update={"decision_id": "coordinated-wrong-decision"}
            )
        else:
            updates["action"] = action.model_copy(
                update={"observation_sequence": 99}
            )
    runner.graph.update_state(
        {"configurable": {"thread_id": run_id}},
        updates,
        as_node=checkpoint_node,
    )
    resumed = runner.resume(run_id)
    assert resumed.get("simulation_status") is SimulationStatus.FAILED
    assert resumed.get("cleanup_completed") is True
    assert runtime.apply_calls == 0
    assert runtime.abort_calls == 1
    error = resumed.get("error")
    assert isinstance(error, GraphError)
    assert error.code == "GRAPH_CONTRACT_ERROR"


def test_v2_stream_events_are_consumable_and_redacted() -> None:
    runtime = FakeRuntime()
    runner = GraphRunner(runtime)
    events = list(
        runner.stream(GraphRunInput(run_id="stream-redacted", max_decisions=1))
    )
    assert events
    assert {event.phase for event in events} >= {"start", "finish", "update"}
    assert any(event.node == "apply_action" for event in events)
    assert all(event.run_id == "stream-redacted" for event in events)
    serialized = "\n".join(event.model_dump_json() for event in events)
    for secret in (
        "hold avoids extra cooling",
        "occupied PMV safe",
        "raw prompt",
        "proposed_setpoint_c\":24",
    ):
        assert secret not in serialized
    assert "energy_proposal" in serialized


@pytest.mark.parametrize("field_count", [0, 1, 32, 33, 40])
def test_event_normalizer_bounds_sorted_valid_changed_fields(
    field_count: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = GraphRunner(FakeRuntime())
    monkeypatch.setattr(runner, "_decision_id_from_checkpoint", _no_decision_id)
    field_names = [f"field_{index:03d}" for index in range(field_count)]
    raw_update = {name: index for index, name in enumerate(reversed(field_names))}

    events = list(
        runner._normalize_update(  # pyright: ignore[reportPrivateUsage]
            {"validate_action": raw_update},
            "normalizer-bounds",
        )
    )

    assert len(events) == 1
    assert events[0].changed_fields == tuple(sorted(field_names)[:32])


def test_event_normalizer_filters_invalid_fields_before_stable_slice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = GraphRunner(FakeRuntime())
    monkeypatch.setattr(runner, "_decision_id_from_checkpoint", _no_decision_id)
    valid_fields = [f"valid_{index:03d}" for index in range(40)]
    raw_update: dict[object, object] = {
        **{name: index for index, name in enumerate(reversed(valid_fields))},
        "bad/field": True,
        "secret_field": True,
        "raw_output_payload": True,
        7: True,
    }

    events = list(
        runner._normalize_update(  # pyright: ignore[reportPrivateUsage]
            {"validate_action": raw_update},
            "normalizer-filtering",
        )
    )

    assert len(events) == 1
    assert events[0].changed_fields == tuple(sorted(valid_fields)[:32])


@pytest.mark.parametrize(
    "hostile_decision_id",
    [
        "x" * 129,
        "decision\ncontrol",
        "   ",
        "decision-SECRET_MARKER",
    ],
)
def test_hostile_observation_identity_is_discarded_before_events(
    hostile_decision_id: str,
) -> None:
    runtime = FakeRuntime(observation_decision_override=hostile_decision_id)
    runner = GraphRunner(runtime)
    events = list(
        runner.stream(GraphRunInput(run_id="hostile-observation", max_decisions=1))
    )
    state = runner.get_state("hostile-observation").values
    assert state.get("simulation_status") is SimulationStatus.FAILED
    assert state.get("cleanup_completed") is True
    assert runtime.apply_calls == 0
    assert runtime.abort_calls == 1
    serialized = "\n".join(event.model_dump_json() for event in events)
    assert hostile_decision_id not in serialized
    assert "SECRET_MARKER" not in serialized
    error = state.get("error")
    assert isinstance(error, GraphError)
    assert "SECRET_MARKER" not in error.model_dump_json()
    assert max(len(event.model_dump_json()) for event in events) < 1024


@pytest.mark.parametrize(
    ("attribute", "expected_node"),
    [
        ("applied_mismatch", "apply_action"),
        ("evaluation_mismatch", "advance_and_evaluate"),
        ("reflection_mismatch", "reflect"),
        ("summary_mismatch", "finalize_run"),
    ],
)
def test_runtime_result_identity_mismatch_fails_closed(
    attribute: str,
    expected_node: str,
) -> None:
    runtime = FakeRuntime()
    setattr(runtime, attribute, True)
    _, state = _run(runtime, run_id=f"mismatch-{attribute}")
    assert state.get("simulation_status") is SimulationStatus.FAILED
    error = state.get("error")
    assert isinstance(error, GraphError)
    assert error.code == "GRAPH_CONTRACT_ERROR"
    assert error.node == expected_node


@pytest.mark.parametrize(
    "runtime",
    [
        FakeRuntime(observation_run_override="wrong-run"),
        FakeRuntime(repeated_sequence=True),
    ],
)
def test_observation_identity_and_freshness_fail_closed(runtime: FakeRuntime) -> None:
    max_decisions = 1 if runtime.observation_run_override else 2
    _, state = _run(
        runtime,
        run_id=f"observation-contract-{max_decisions}",
        max_decisions=max_decisions,
    )
    assert state.get("simulation_status") is SimulationStatus.FAILED
    assert runtime.abort_calls == 1
    if runtime.repeated_sequence:
        assert runtime.apply_calls == 1


def test_unexpected_exception_is_redacted_and_safely_aborted() -> None:
    runtime = FakeRuntime(unexpected_node="energy_agent")
    _, state = _run(runtime, run_id="unexpected")
    error = state.get("error")
    assert isinstance(error, GraphError)
    assert error.code == "UNEXPECTED_NODE_ERROR"
    assert "SECRET" not in error.message
    assert "traceback" not in error.message
    assert runtime.abort_calls == 1


def test_contracts_reject_non_finite_values_and_unbounded_evidence() -> None:
    with pytest.raises(ValidationError):
        EvaluationRecord(
            decision_id="d",
            evaluated_at_utc=UTC_1,
            energy_delta_kwh=float("nan"),
            occupied_pmv_compliance_percent=100.0,
            safe=True,
        )
    with pytest.raises(ValidationError):
        GraphAction(
            run_id="r",
            decision_id="d",
            observation_sequence=1,
            setpoint_c=24.0,
            source=ActionSource.ADVISORY,
            energy_evidence="x" * 513,
            comfort_evidence="safe",
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "approved": True,
            "reason_code": ValidationReasonCode.RATE_LIMIT_EXCEEDED,
            "validated_setpoint_c": 24.0,
            "emergency_observed": False,
            "evidence": ("contradiction",),
        },
        {
            "approved": True,
            "reason_code": ValidationReasonCode.APPROVED,
            "validated_setpoint_c": 24.0,
            "emergency_observed": True,
            "evidence": ("contradiction",),
        },
        {
            "approved": False,
            "reason_code": ValidationReasonCode.APPROVED,
            "validated_setpoint_c": None,
            "emergency_observed": False,
            "evidence": ("contradiction",),
        },
        {
            "approved": False,
            "reason_code": ValidationReasonCode.RATE_LIMIT_EXCEEDED,
            "validated_setpoint_c": 24.0,
            "emergency_observed": False,
            "evidence": ("contradiction",),
        },
    ],
)
def test_validation_result_contract_rejects_contradictions(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ValidationResult.model_validate(payload)


@pytest.mark.parametrize(
    "invalid_identity",
    [
        "",
        " ",
        "a" * 129,
        "bad/id",
        "bad\nid",
        "SECRET_MARKER",
    ],
)
def test_identity_contracts_reject_hostile_values(invalid_identity: str) -> None:
    with pytest.raises(ValidationError):
        GraphRunInput(run_id=invalid_identity, max_decisions=1)
    observation = _observation(run_id="safe-run", sequence=1)
    with pytest.raises(ValidationError):
        ObservationEnvelope.model_validate(
            {**observation.model_dump(), "decision_id": invalid_identity}
        )
    with pytest.raises(ValidationError):
        GraphAction(
            run_id="safe-run",
            decision_id=invalid_identity,
            observation_sequence=1,
            setpoint_c=24.0,
            source=ActionSource.ADVISORY,
            energy_evidence="energy",
            comfort_evidence="comfort",
        )


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("timestamp_utc", "not-utc"),
        ("timestamp_utc", "2" * 1000),
        ("run_id", " "),
        ("run_id", "SECRET_MARKER"),
        ("decision_id", "decision/control"),
        ("node", "UPPERCASE"),
        ("node", "n" * 65),
        ("node", "secret_node"),
        ("node", "raw_output_node"),
        ("node", "prompt_node"),
        ("changed_fields", ("valid_field", "bad/field")),
        ("changed_fields", ("secret_field",)),
        ("changed_fields", ("raw_output_payload",)),
        ("changed_fields", ("prompt_value",)),
        ("changed_fields", tuple(f"field_{index}" for index in range(33))),
    ],
)
def test_graph_event_contract_bounds_every_string_surface(
    field_name: str,
    invalid_value: object,
) -> None:
    payload: dict[str, object] = {
        "timestamp_utc": UTC_0,
        "run_id": "event-run",
        "decision_id": "decision-1",
        "node": "validate_action",
        "phase": "update",
        "changed_fields": ("validation",),
        "error": False,
    }
    payload[field_name] = invalid_value
    with pytest.raises(ValidationError):
        GraphEvent.model_validate(payload)


@pytest.mark.parametrize("field_name", ["energy_evidence", "comfort_evidence"])
def test_control_proposal_rejects_blank_or_unbounded_evidence(
    field_name: str,
) -> None:
    values: dict[str, object] = {
        "run_id": "proposal-run",
        "decision_id": "decision-1",
        "observation_sequence": 1,
        "proposed_setpoint_c": 24.0,
        "energy_evidence": "energy",
        "comfort_evidence": "comfort",
    }
    values[field_name] = " "
    with pytest.raises(ValidationError):
        ControlProposal.model_validate(values)
    values[field_name] = "x" * 513
    with pytest.raises(ValidationError):
        ControlProposal.model_validate(values)


def test_derived_recursion_limit_is_not_langgraph_default() -> None:
    assert recursion_limit_for(1) == 20
    assert recursion_limit_for(2) == 36
    assert recursion_limit_for(1) != 25
    with pytest.raises(ValueError):
        recursion_limit_for(0)


def test_run_ids_are_unique_and_cannot_be_reused() -> None:
    assert GraphRunner.new_run_id() != GraphRunner.new_run_id()
    runner = GraphRunner(FakeRuntime())
    request = GraphRunInput(run_id="no-reuse", max_decisions=1)
    runner.invoke(request)
    with pytest.raises(ValueError, match="already exists"):
        runner.invoke(request)
    with pytest.raises(KeyError, match="unknown graph run"):
        runner.resume("missing-run")
