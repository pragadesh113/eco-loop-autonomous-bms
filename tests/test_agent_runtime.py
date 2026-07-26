"""Fake-only AGT-002 runtime, timing, MCP, and semantic-containment tests."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest

from bms_agent.control import (
    ObservationEnvelope,
    ObservationSnapshot,
    ValidationReasonCode,
    ValidationResult,
    ZoneSnapshot,
    choose_fallback,
)
from bms_agent.graph import (
    ActionSource,
    AgentGraphRuntime,
    AgentObservation,
    CompletionRoute,
    ExpectedGraphError,
    GatewayActionRequest,
    GatewayActionResult,
    GatewayStatus,
    GatewaySummary,
    GraphAction,
    GraphRunInput,
    GraphRunner,
    GraphStateView,
    McpGateway,
    McpGatewayError,
    ReflectionRecord,
    RunState,
    SimulationStatus,
    TrendSample,
)
from bms_agent.llm import (
    AdvisoryProvider,
    AdvisoryResult,
    AdvisoryRole,
    ComfortAssessment,
    ComfortRisk,
    ComfortState,
    EnergyEffect,
    EnergyProposal,
    ProviderStatus,
    SetpointDirection,
    SupervisorDecision,
    SupervisorDisposition,
)
from bms_agent.llm.schemas import OutputT
from bms_agent.simulation.baseline import ZONES

UTC_0 = "2026-07-26T00:00:00Z"


@dataclass
class FakeClock:
    value: float = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@dataclass
class FakeProvider:
    clock: FakeClock
    durations: list[float] = field(default_factory=lambda: list[float]())
    failing_role: AdvisoryRole | None = None
    failure_status: ProviderStatus = ProviderStatus.UNAVAILABLE
    energy: EnergyProposal = field(
        default_factory=lambda: EnergyProposal(
            proposed_setpoint_c=25.0,
            expected_energy_effect=EnergyEffect.REDUCE,
            confidence=0.9,
            reason="reduce cooling",
        )
    )
    comfort: ComfortAssessment = field(
        default_factory=lambda: ComfortAssessment(
            comfort_state=ComfortState.COLD,
            recommended_direction=SetpointDirection.RAISE,
            risk=ComfortRisk.TARGET_VIOLATION,
            reason="negative PMV is cold",
        )
    )
    supervisor_decision: SupervisorDecision = field(
        default_factory=lambda: SupervisorDecision(
            disposition=SupervisorDisposition.ACCEPT,
            proposed_setpoint_c=25.0,
            conflict=False,
            energy_evidence="model invented energy",
            comfort_evidence="model invented comfort",
        )
    )
    calls: list[tuple[AdvisoryRole, str, str, bool]] = field(
        default_factory=lambda: list[tuple[AdvisoryRole, str, str, bool]]()
    )

    def generate(
        self,
        *,
        role: AdvisoryRole,
        output_schema: type[OutputT],
        prompt: str,
        deadline_bound: bool = False,
    ) -> AdvisoryResult[OutputT]:
        self.calls.append((role, output_schema.__name__, prompt, deadline_bound))
        index = len(self.calls) - 1
        if index < len(self.durations):
            self.clock.advance(self.durations[index])
        if role is self.failing_role:
            return AdvisoryResult[OutputT](
                status=self.failure_status,
                role=role,
                schema_name=output_schema.__name__,
                model=None,
                output=None,
                attempt_count=1,
                correction_attempted=False,
                used_fallback=False,
                wall_duration_ms=1.0,
                detail="bounded fake failure",
            )
        raw = {
            AdvisoryRole.ENERGY: self.energy,
            AdvisoryRole.COMFORT: self.comfort,
            AdvisoryRole.SUPERVISOR: self.supervisor_decision,
        }[role]
        output = output_schema.model_validate(raw.model_dump())
        return AdvisoryResult[OutputT](
            status=ProviderStatus.SUCCESS,
            role=role,
            schema_name=output_schema.__name__,
            model="fake:model",
            output=output,
            attempt_count=1,
            correction_attempted=False,
            used_fallback=False,
            wall_duration_ms=1.0,
        )


@dataclass
class FakeGateway:
    observations: list[AgentObservation]
    status_value: str = "running"
    result_changes: dict[str, object] = field(default_factory=lambda: dict[str, object]())
    submit_error: bool = False
    terminal_timeout: bool = False
    start_calls: list[tuple[str, int, float]] = field(
        default_factory=lambda: list[tuple[str, int, float]]()
    )
    await_calls: int = 0
    submitted: list[GatewayActionRequest] = field(
        default_factory=lambda: list[GatewayActionRequest]()
    )
    submit_attempts: int = 0
    submit_timeouts: list[float] = field(default_factory=lambda: list[float]())
    last_observation: AgentObservation | None = None
    stop_calls: int = 0

    def start(
        self,
        *,
        run_id: str,
        max_weather_timesteps: int,
        action_wait_seconds: float,
    ) -> None:
        self.start_calls.append((run_id, max_weather_timesteps, action_wait_seconds))

    def await_observation(
        self,
        *,
        run_id: str,
        timeout_seconds: float,
    ) -> AgentObservation:
        _ = timeout_seconds
        self.await_calls += 1
        if not self.observations and self.terminal_timeout:
            raise McpGatewayError("OBSERVATION_TIMEOUT", "bounded terminal timeout")
        observation = self.observations.pop(0)
        assert observation.envelope.run_id == run_id
        self.last_observation = observation
        return observation

    def submit_action(
        self,
        request: GatewayActionRequest,
        *,
        timeout_seconds: float,
    ) -> GatewayActionResult:
        assert 0.0 < timeout_seconds <= 3.0
        self.submit_attempts += 1
        self.submit_timeouts.append(timeout_seconds)
        if self.submit_error:
            raise McpGatewayError("SUBMIT_TIMEOUT", "bounded fake timeout")
        self.submitted.append(request)
        if request.control_source == "advisory_proposal":
            authorization_reason = ValidationReasonCode.APPROVED.value
        else:
            assert self.last_observation is not None
            authorization_reason = choose_fallback(
                self.last_observation.envelope,
                last_safe_setpoint_c=(self.last_observation.envelope.snapshot.current_setpoint_c),
                trigger=request.fallback_trigger,
            ).reason_code.value
        values: dict[str, object] = {
            "run_id": request.run_id,
            "decision_id": request.decision_id,
            "observation_sequence": request.observation_sequence,
            "idempotency_key": request.idempotency_key,
            "requested_setpoint_c": request.setpoint_c,
            "authorized_setpoint_c": request.setpoint_c,
            "control_source": request.control_source,
            "authorization_reason_code": authorization_reason,
            "accepted": True,
            "cached": False,
        }
        values.update(self.result_changes)
        return GatewayActionResult.model_validate(values)

    def status(self, *, run_id: str) -> GatewayStatus:
        return GatewayStatus.model_validate({"run_id": run_id, "status": self.status_value})

    def summary(self, *, run_id: str) -> GatewaySummary:
        return GatewaySummary(
            run_id=run_id,
            status="completed",
            actions_applied=len(self.submitted),
        )

    def stop(self, *, run_id: str, timeout_seconds: float) -> None:
        _ = (run_id, timeout_seconds)
        self.stop_calls += 1


def _observation(
    run_id: str,
    sequence: int,
    *,
    pmv: float,
    hvac_j: float,
) -> AgentObservation:
    envelope = ObservationEnvelope(
        run_id=run_id,
        decision_id=f"{run_id}-decision-{sequence}",
        sequence=sequence,
        observed_at_utc=f"simulation-time:05-23T{sequence:02d}:00",
        snapshot=ObservationSnapshot(
            current_setpoint_c=24.0 if sequence == 1 else 25.0,
            zones=tuple(
                ZoneSnapshot(
                    zone_id=zone,
                    temperature_c=24.0,
                    pmv=pmv,
                    occupancy_people=1.0,
                )
                for zone in ZONES
            ),
            temperature_unit="degC",
            pmv_unit="dimensionless",
            occupancy_unit="people",
        ),
    )
    return AgentObservation(
        envelope=envelope,
        outdoor_dry_bulb_c=38.0,
        hvac_electricity_j=hvac_j,
        trend=(
            TrendSample(
                outdoor_dry_bulb_c=38.0,
                cooling_setpoint_c=envelope.snapshot.current_setpoint_c,
                hvac_electricity_j=hvac_j,
                occupied_pmv_min=pmv,
                occupied_pmv_max=pmv,
            ),
        ),
    )


def _runtime(
    *,
    run_id: str = "agt-runtime",
    clock: FakeClock | None = None,
    provider: FakeProvider | None = None,
    observations: Iterable[AgentObservation] | None = None,
    gateway_changes: dict[str, object] | None = None,
    submit_error: bool = False,
    terminal_timeout: bool = False,
) -> tuple[AgentGraphRuntime, FakeProvider, FakeGateway, FakeClock]:
    resolved_clock = clock or FakeClock()
    resolved_provider = provider or FakeProvider(resolved_clock)
    values = list(
        observations
        or (
            _observation(run_id, 1, pmv=-0.6, hvac_j=2_000_000.0),
            _observation(run_id, 2, pmv=0.0, hvac_j=1_000_000.0),
        )
    )
    gateway = FakeGateway(
        values,
        result_changes=gateway_changes or {},
        submit_error=submit_error,
        terminal_timeout=terminal_timeout,
    )
    runtime = AgentGraphRuntime(
        cast(AdvisoryProvider, resolved_provider),
        cast(McpGateway, gateway),
        monotonic=resolved_clock,
    )
    return runtime, resolved_provider, gateway, resolved_clock


def test_terminal_observation_timeout_after_final_action_finishes_cleanly() -> None:
    run_id = "terminal-completion"
    runtime, _, gateway, _ = _runtime(
        run_id=run_id,
        observations=(_observation(run_id, 1, pmv=0.0, hvac_j=1_000_000.0),),
        terminal_timeout=True,
    )
    gateway.status_value = "completed"

    state = _run(runtime, run_id=run_id)

    assert state["simulation_status"] is SimulationStatus.COMPLETED
    assert state["completed_decisions"] == 1
    assert state["error"] is None
    assert gateway.await_calls == 2
    assert len(gateway.submitted) == 1


def _run(
    runtime: AgentGraphRuntime,
    *,
    run_id: str = "agt-runtime",
    max_decisions: int = 1,
) -> dict[str, object]:
    return dict(
        GraphRunner(runtime).invoke(GraphRunInput(run_id=run_id, max_decisions=max_decisions))
    )


def test_success_maps_all_roles_binds_evidence_and_uses_mcp_once() -> None:
    runtime, provider, gateway, _ = _runtime()
    state = _run(runtime)

    assert state["simulation_status"] is SimulationStatus.COMPLETED
    assert [call[0] for call in provider.calls] == [
        AdvisoryRole.ENERGY,
        AdvisoryRole.COMFORT,
        AdvisoryRole.SUPERVISOR,
    ]
    assert all(call[3] is True for call in provider.calls)
    assert gateway.start_calls == [("agt-runtime", 672, 30.0)]
    assert len(gateway.submitted) == 1
    request = gateway.submitted[0]
    assert request.control_source == "advisory_proposal"
    assert request.energy_evidence == "E:reduce:c0.90"
    assert request.comfort_evidence == "C:target_violation:raise"
    assert "model invented" not in request.model_dump_json()
    assert request.fallback_trigger is None
    assert len(request.idempotency_key) <= 128
    assert gateway.stop_calls == 1


def test_model_rationales_never_cross_supervisor_or_control_evidence_boundary() -> None:
    energy_rationale = r"C:\private\energy SECRET raw error rationale"
    comfort_rationale = "/tmp/comfort traceback free-form rationale"
    clock = FakeClock()
    provider = FakeProvider(
        clock,
        energy=EnergyProposal(
            proposed_setpoint_c=25.0,
            expected_energy_effect=EnergyEffect.REDUCE,
            confidence=0.87,
            reason=energy_rationale,
        ),
        comfort=ComfortAssessment(
            comfort_state=ComfortState.COLD,
            recommended_direction=SetpointDirection.RAISE,
            risk=ComfortRisk.TARGET_VIOLATION,
            reason=comfort_rationale,
        ),
    )
    runtime, provider, gateway, _ = _runtime(clock=clock, provider=provider)
    runner = GraphRunner(runtime)

    events = tuple(runner.stream(GraphRunInput(run_id="agt-runtime", max_decisions=1)))

    supervisor_prompt = next(
        call[2] for call in provider.calls if call[0] is AdvisoryRole.SUPERVISOR
    )
    request = gateway.submitted[0]
    serialized_events = json.dumps(
        [event.model_dump(mode="json") for event in events],
        sort_keys=True,
    )
    serialized_control_evidence = json.dumps(
        {
            "energy": request.energy_evidence,
            "comfort": request.comfort_evidence,
        },
        sort_keys=True,
    )
    for rationale in (energy_rationale, comfort_rationale):
        assert rationale not in supervisor_prompt
        assert rationale not in request.model_dump_json()
        assert rationale not in serialized_events
        assert rationale not in serialized_control_evidence
    assert request.energy_evidence == "E:reduce:c0.87"
    assert request.comfort_evidence == "C:target_violation:raise"
    assert request.energy_evidence.startswith("E:")
    assert request.comfort_evidence.startswith("C:")


@pytest.mark.parametrize(
    "status",
    [
        ProviderStatus.TIMEOUT,
        ProviderStatus.UNAVAILABLE,
        ProviderStatus.MODEL_MISSING,
        ProviderStatus.MALFORMED,
    ],
)
@pytest.mark.parametrize(
    ("role", "expected_calls"),
    [
        (AdvisoryRole.ENERGY, 1),
        (AdvisoryRole.COMFORT, 2),
        (AdvisoryRole.SUPERVISOR, 3),
    ],
)
def test_provider_failure_at_every_role_is_one_deterministic_fallback(
    status: ProviderStatus,
    role: AdvisoryRole,
    expected_calls: int,
) -> None:
    clock = FakeClock()
    provider = FakeProvider(
        clock,
        failing_role=role,
        failure_status=status,
    )
    runtime, provider, gateway, _ = _runtime(clock=clock, provider=provider)

    state = _run(runtime)

    assert state["simulation_status"] is SimulationStatus.COMPLETED
    assert len(provider.calls) == expected_calls
    assert state["revision_count"] == 2
    validation = state["validation"]
    assert isinstance(validation, ValidationResult)
    assert validation.reason_code is ValidationReasonCode.ADVISORY_UNAVAILABLE
    assert len(gateway.submitted) == 1
    request = gateway.submitted[0]
    assert request.control_source == "deterministic_fallback"
    assert request.energy_evidence is None
    assert request.comfort_evidence is None
    assert request.fallback_trigger is ValidationReasonCode.ADVISORY_UNAVAILABLE


@pytest.mark.parametrize("durations", [[5.72, 4.37, 5.03], [5.54, 3.65, 5.03]])
def test_measured_three_role_profiles_reach_valid_advisory_path(
    durations: list[float],
) -> None:
    clock = FakeClock()
    provider = FakeProvider(clock, durations=durations)
    runtime, provider, gateway, clock = _runtime(clock=clock, provider=provider)

    state = _run(runtime)

    assert state["simulation_status"] is SimulationStatus.COMPLETED
    assert [call[0] for call in provider.calls] == [
        AdvisoryRole.ENERGY,
        AdvisoryRole.COMFORT,
        AdvisoryRole.SUPERVISOR,
    ]
    assert len(gateway.submitted) == 1
    assert gateway.submitted[0].control_source == "advisory_proposal"
    assert clock.value < 30.0


@pytest.mark.parametrize(
    ("durations", "expected_roles"),
    [
        ([10.001], [AdvisoryRole.ENERGY]),
        (
            [9.5, 9.501],
            [AdvisoryRole.ENERGY, AdvisoryRole.COMFORT],
        ),
    ],
)
def test_role_overrun_skips_next_role_and_submits_timely_fallback(
    durations: list[float],
    expected_roles: list[AdvisoryRole],
) -> None:
    clock = FakeClock()
    provider = FakeProvider(clock, durations=durations)
    runtime, provider, gateway, clock = _runtime(clock=clock, provider=provider)

    state = _run(runtime)

    assert state["simulation_status"] is SimulationStatus.COMPLETED
    assert [call[0] for call in provider.calls] == expected_roles
    assert gateway.submit_attempts == 1
    assert gateway.submitted[0].control_source == "deterministic_fallback"
    assert clock.value <= 19.001


@pytest.mark.parametrize(
    ("role", "durations"),
    [
        (AdvisoryRole.ENERGY, [8.0]),
        (AdvisoryRole.COMFORT, [5.72, 8.0]),
        (AdvisoryRole.SUPERVISOR, [5.72, 4.37, 8.0]),
    ],
)
def test_timeout_during_each_role_submits_one_timely_fallback(
    role: AdvisoryRole,
    durations: list[float],
) -> None:
    clock = FakeClock()
    provider = FakeProvider(
        clock,
        durations=durations,
        failing_role=role,
        failure_status=ProviderStatus.TIMEOUT,
    )
    runtime, provider, gateway, clock = _runtime(clock=clock, provider=provider)

    state = _run(runtime)

    assert state["simulation_status"] is SimulationStatus.COMPLETED
    assert provider.calls[-1][0] is role
    assert gateway.submit_attempts == 1
    assert gateway.submitted[0].control_source == "deterministic_fallback"
    assert clock.value <= 18.09


def test_exact_role_reserve_boundaries_allow_all_three_roles() -> None:
    clock = FakeClock()
    provider = FakeProvider(clock, durations=[9.5, 9.5, 0.0])
    runtime, provider, gateway, _ = _runtime(clock=clock, provider=provider)

    state = _run(runtime)

    assert state["simulation_status"] is SimulationStatus.COMPLETED
    assert len(provider.calls) == 3
    assert gateway.submitted[0].control_source == "advisory_proposal"


def test_exact_energy_reserve_boundary_allows_energy_role() -> None:
    clock = FakeClock()
    provider = FakeProvider(clock)
    runtime, provider, _, _ = _runtime(clock=clock, provider=provider)
    initial = GraphStateView.from_state(RunState(run_id="agt-runtime", max_decisions=1))
    runtime.initialize_run(initial)
    observation = runtime.await_observation(initial)
    clock.advance(1.0)
    state = GraphStateView.from_state(
        RunState(
            run_id="agt-runtime",
            max_decisions=1,
            observation=observation,
        )
    )

    runtime.energy_agent(state)

    assert [call[0] for call in provider.calls] == [AdvisoryRole.ENERGY]


def test_insufficient_reserve_before_energy_skips_provider_call() -> None:
    clock = FakeClock()
    provider = FakeProvider(clock)
    runtime, provider, _, _ = _runtime(clock=clock, provider=provider)
    initial = GraphStateView.from_state(RunState(run_id="agt-runtime", max_decisions=1))
    runtime.initialize_run(initial)
    observation = runtime.await_observation(initial)
    clock.advance(1.001)
    state = GraphStateView.from_state(
        RunState(
            run_id="agt-runtime",
            max_decisions=1,
            observation=observation,
        )
    )

    proposal = runtime.energy_agent(state)

    assert provider.calls == []
    assert proposal.expected_energy_effect is EnergyEffect.NEUTRAL


@pytest.mark.parametrize(
    ("durations", "expected_roles"),
    [
        ([10.001], [AdvisoryRole.ENERGY]),
        (
            [9.5, 9.501],
            [AdvisoryRole.ENERGY, AdvisoryRole.COMFORT],
        ),
    ],
)
def test_insufficient_reserve_before_comfort_or_supervisor_skips_remaining_roles(
    durations: list[float],
    expected_roles: list[AdvisoryRole],
) -> None:
    clock = FakeClock()
    provider = FakeProvider(clock, durations=durations)
    runtime, provider, gateway, _ = _runtime(clock=clock, provider=provider)

    state = _run(runtime)

    assert [call[0] for call in provider.calls] == expected_roles
    assert state["simulation_status"] is SimulationStatus.COMPLETED
    assert len(gateway.submitted) == 1
    assert gateway.submitted[0].control_source == "deterministic_fallback"


@pytest.mark.parametrize(
    ("durations", "expected_status", "expected_submit_attempts"),
    [
        ([9.0, 10.0, 8.0], SimulationStatus.COMPLETED, 1),
        ([9.0, 10.0, 8.001], SimulationStatus.FAILED, 0),
    ],
)
def test_submit_margin_exact_three_allowed_below_three_rejected(
    durations: list[float],
    expected_status: SimulationStatus,
    expected_submit_attempts: int,
) -> None:
    clock = FakeClock()
    provider = FakeProvider(clock, durations=durations)
    runtime, _, gateway, _ = _runtime(clock=clock, provider=provider)

    state = _run(runtime)

    assert state["simulation_status"] is expected_status
    assert gateway.submit_attempts == expected_submit_attempts
    if expected_submit_attempts:
        assert gateway.submit_timeouts == [3.0]
        assert gateway.submitted[0].control_source == "advisory_proposal"
    else:
        assert gateway.submitted == []
        assert state["cleanup_completed"] is True


def test_wrong_pmv_semantics_cannot_actuate_advisory_and_uses_fallback() -> None:
    run_id = "semantic-wrong"
    clock = FakeClock()
    provider = FakeProvider(
        clock,
        comfort=ComfortAssessment(
            comfort_state=ComfortState.COLD,
            recommended_direction=SetpointDirection.RAISE,
            risk=ComfortRisk.LOW,
            reason="wrongly calls hot PMV cold",
        ),
    )
    runtime, _, gateway, _ = _runtime(
        run_id=run_id,
        clock=clock,
        provider=provider,
        observations=(
            _observation(run_id, 1, pmv=0.7, hvac_j=2_000_000.0),
            _observation(run_id, 2, pmv=0.1, hvac_j=1_000_000.0),
        ),
    )

    state = _run(runtime, run_id=run_id)

    assert state["revision_count"] == 2
    assert len(gateway.submitted) == 1
    request = gateway.submitted[0]
    assert request.control_source == "deterministic_fallback"
    assert request.setpoint_c == 23.5
    assert request.fallback_trigger is ValidationReasonCode.HOT_DIRECTION_WORSENING


@pytest.mark.parametrize(
    "changes",
    [
        {"run_id": "wrong-run"},
        {"authorized_setpoint_c": 24.9},
        {"requested_setpoint_c": 24.9},
        {"decision_id": "wrong-decision"},
        {"observation_sequence": 99},
        {"control_source": "deterministic_fallback"},
        {"idempotency_key": "wrong-key"},
        {"accepted": False},
        {"authorization_reason_code": "RATE_LIMIT_EXCEEDED"},
        {"cached": True},
    ],
)
def test_mcp_result_substitution_fails_closed_without_submit_retry(
    changes: dict[str, object],
) -> None:
    runtime, _, gateway, _ = _runtime(gateway_changes=changes)

    state = _run(runtime)

    assert state["simulation_status"] is SimulationStatus.FAILED
    assert state["cleanup_completed"] is True
    assert gateway.submit_attempts == 1
    assert len(gateway.submitted) == 1
    assert gateway.stop_calls == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"authorization_reason_code": "APPROVED"},
        {"cached": True},
    ],
)
def test_fallback_authorization_metadata_mismatch_fails_closed_once(
    changes: dict[str, object],
) -> None:
    clock = FakeClock()
    provider = FakeProvider(
        clock,
        failing_role=AdvisoryRole.ENERGY,
        failure_status=ProviderStatus.UNAVAILABLE,
    )
    runtime, _, gateway, _ = _runtime(
        clock=clock,
        provider=provider,
        gateway_changes=changes,
    )

    state = _run(runtime)

    assert state["simulation_status"] is SimulationStatus.FAILED
    assert state["cleanup_completed"] is True
    assert gateway.submit_attempts == 1
    assert len(gateway.submitted) == 1
    assert gateway.stop_calls == 1


def test_mcp_submit_timeout_is_not_retried_and_aborts_safely() -> None:
    runtime, _, gateway, _ = _runtime(submit_error=True)

    state = _run(runtime)

    assert state["simulation_status"] is SimulationStatus.FAILED
    assert state["cleanup_completed"] is True
    assert gateway.submit_attempts == 1
    assert gateway.submitted == []
    assert gateway.stop_calls == 1


def test_fallback_source_without_typed_fallback_never_reaches_gateway() -> None:
    runtime, _, gateway, _ = _runtime()
    initial = GraphStateView.from_state(RunState(run_id="agt-runtime", max_decisions=1))
    runtime.initialize_run(initial)
    observation = runtime.await_observation(initial)
    state = GraphStateView.from_state(
        RunState(
            run_id="agt-runtime",
            max_decisions=1,
            observation=observation,
        )
    )
    action = GraphAction(
        run_id=observation.run_id,
        decision_id=observation.decision_id,
        observation_sequence=observation.sequence,
        setpoint_c=24.0,
        source=ActionSource.FALLBACK,
        energy_evidence="deterministic fallback",
        comfort_evidence="missing typed fallback",
        fallback=None,
    )

    with pytest.raises(ExpectedGraphError, match="typed fallback"):
        runtime.apply_action(state, action)

    assert gateway.submit_attempts == 0
    assert gateway.submitted == []


@pytest.mark.parametrize(
    ("next_pmv", "next_hvac_j", "energy_match", "comfort_match"),
    [
        (0.0, 1_000_000.0, True, False),
        (0.7, 3_000_000.0, False, True),
    ],
)
def test_reflection_explicitly_compares_predictions_and_measurements(
    next_pmv: float,
    next_hvac_j: float,
    energy_match: bool,
    comfort_match: bool,
) -> None:
    runtime, _, _, _ = _runtime(
        observations=(
            _observation("agt-runtime", 1, pmv=-0.6, hvac_j=2_000_000.0),
            _observation("agt-runtime", 2, pmv=next_pmv, hvac_j=next_hvac_j),
        )
    )

    state = _run(runtime)
    reflection = state["reflection"]

    assert isinstance(reflection, ReflectionRecord)
    assert reflection.predicted_energy_effect is EnergyEffect.REDUCE
    assert reflection.energy_prediction_matched is energy_match
    assert reflection.predicted_comfort_risk is ComfortRisk.TARGET_VIOLATION
    assert reflection.comfort_prediction_matched is comfort_match
    assert reflection.measured_occupied_pmv_compliance_percent in {0.0, 100.0}


def test_evaluation_observation_is_cached_for_next_graph_await_once() -> None:
    run_id = "two-decisions-agent"
    observations = (
        _observation(run_id, 1, pmv=-0.6, hvac_j=3_000_000.0),
        _observation(run_id, 2, pmv=-0.6, hvac_j=2_000_000.0),
        _observation(run_id, 3, pmv=0.0, hvac_j=1_000_000.0),
    )
    runtime, _, gateway, _ = _runtime(
        run_id=run_id,
        observations=observations,
    )

    state = _run(runtime, run_id=run_id, max_decisions=2)

    assert state["simulation_status"] is SimulationStatus.COMPLETED
    assert state["completed_decisions"] == 2
    assert gateway.await_calls == 3
    assert len(gateway.submitted) == 2


def test_agent_runtime_has_no_direct_server_session_or_actuator_import() -> None:
    source = Path("src/bms_agent/graph/agent_runtime.py").read_text(encoding="utf-8")
    for forbidden in (
        "SessionRegistry",
        "SimulationSession",
        "pyenergyplus",
        "set_actuator_value",
        "bms_agent.mcp_server.server",
    ):
        assert forbidden not in source


def test_gateway_action_contract_keeps_advisory_and_fallback_fields_separate() -> None:
    with pytest.raises(ValueError):
        GatewayActionRequest(
            run_id="run",
            decision_id="decision",
            observation_sequence=1,
            idempotency_key="key",
            setpoint_c=24.0,
            control_source="advisory_proposal",
        )
    with pytest.raises(ValueError):
        GatewayActionRequest(
            run_id="run",
            decision_id="decision",
            observation_sequence=1,
            idempotency_key="key",
            setpoint_c=24.0,
            control_source="deterministic_fallback",
            energy_evidence="not allowed",
        )
    with pytest.raises(ValueError):
        GatewayActionRequest(
            run_id="run",
            decision_id="decision",
            observation_sequence=1,
            idempotency_key="key",
            setpoint_c=24.0,
            control_source="deterministic_fallback",
        )


def test_runtime_continue_route_uses_gateway_status_only() -> None:
    runtime, _, gateway, _ = _runtime()
    state = GraphStateView.model_validate(
        {
            "run_id": "agt-runtime",
            "max_decisions": 1,
            "completed_decisions": 0,
            "simulation_status": SimulationStatus.INITIALIZING,
            "graph_node": "initialize_run",
            "recent_nodes": (),
            "recent_observations": (),
            "observation": None,
            "energy_proposal": None,
            "comfort_assessment": None,
            "supervisor_decision": None,
            "control_proposal": None,
            "validation": None,
            "revision_count": 0,
            "action": None,
            "applied_action": None,
            "evaluation": None,
            "reflection": None,
            "completion_route": None,
            "error": None,
            "summary": None,
            "cleanup_completed": False,
        }
    )
    runtime.initialize_run(state)
    gateway.status_value = "completed"
    assert runtime.continue_or_finish(state) is CompletionRoute.FINISH
    gateway.status_value = "failed"
    assert runtime.continue_or_finish(state) is CompletionRoute.FATAL
