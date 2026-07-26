"""Fake-first GraphRuntime adapter for advisory roles and typed MCP tools."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from bms_agent.control import (
    ControlProposal,
    FallbackDecision,
    ObservationEnvelope,
    ValidationReasonCode,
    ValidationResult,
    choose_fallback,
    validate_proposal,
)
from bms_agent.graph.agent_contracts import (
    AgentObservation,
    ComfortAgentInput,
    ComfortZoneInput,
    EnergyAgentInput,
    GatewayActionRequest,
    McpGateway,
    McpGatewayError,
    SupervisorAgentInput,
)
from bms_agent.graph.prompts import (
    build_comfort_prompt,
    build_energy_prompt,
    build_supervisor_prompt,
)
from bms_agent.graph.runtime import ExpectedGraphError
from bms_agent.graph.state import (
    ActionSource,
    AppliedAction,
    CompletionRoute,
    EvaluationRecord,
    GraphAction,
    GraphError,
    GraphStateView,
    ReflectionRecord,
    RunSummary,
    SimulationStatus,
)
from bms_agent.llm import (
    AdvisoryProvider,
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

MonotonicClock = Callable[[], float]
ACTION_WINDOW_SECONDS = 30.0
ENERGY_ROLE_RESERVE_SECONDS = 29.0
COMFORT_ROLE_RESERVE_SECONDS = 20.0
SUPERVISOR_ROLE_RESERVE_SECONDS = 11.0
SUBMIT_MARGIN_SECONDS = 3.0
ENERGY_MATCH_TOLERANCE_KWH = 1e-6


class AgentRuntimeConfig(BaseModel):
    """Fixed safety and latency bounds for the graph adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    action_window_seconds: float = Field(default=30.0, ge=30.0, le=30.0)
    energy_role_reserve_seconds: float = Field(default=29.0, ge=29.0, le=29.0)
    comfort_role_reserve_seconds: float = Field(default=20.0, ge=20.0, le=20.0)
    supervisor_role_reserve_seconds: float = Field(default=11.0, ge=11.0, le=11.0)
    submit_margin_seconds: float = Field(default=3.0, ge=3.0, le=3.0)
    observation_timeout_seconds: float = Field(default=30.0, gt=0.0, le=30.0)
    stop_timeout_seconds: float = Field(default=5.0, gt=0.0, le=30.0)
    max_weather_timesteps: int = Field(default=672, ge=1, le=672)


@dataclass(slots=True)
class _DecisionContext:
    current: AgentObservation | None = None
    staged: AgentObservation | None = None
    staged_deadline: float | None = None
    deadline: float | None = None
    advisory_failure: ValidationReasonCode | None = None
    last_accepted_sequence: int | None = None
    stopped: bool = False


class AgentGraphRuntime:
    """Compose untrusted advisory output with deterministic policy and MCP authority."""

    def __init__(
        self,
        provider: AdvisoryProvider,
        gateway: McpGateway,
        *,
        config: AgentRuntimeConfig | None = None,
        monotonic: MonotonicClock = time.monotonic,
    ) -> None:
        self.provider = provider
        self.gateway = gateway
        self.config = config or AgentRuntimeConfig()
        self._monotonic = monotonic
        self._contexts: dict[str, _DecisionContext] = {}

    def initialize_run(self, state: GraphStateView) -> None:
        try:
            self.gateway.start(
                run_id=state.run_id,
                max_weather_timesteps=self.config.max_weather_timesteps,
                action_wait_seconds=self.config.action_window_seconds,
            )
        except McpGatewayError as error:
            raise _gateway_error(error) from None
        self._contexts[state.run_id] = _DecisionContext()

    def await_observation(self, state: GraphStateView) -> ObservationEnvelope:
        context = self._context(state.run_id)
        if context.staged is not None:
            observation = context.staged
            deadline = context.staged_deadline
            context.staged = None
            context.staged_deadline = None
        else:
            observation = self._await_gateway_observation(state.run_id)
            deadline = self._monotonic() + self.config.action_window_seconds
        context.current = AgentObservation.model_validate(observation.model_dump())
        context.deadline = deadline
        context.advisory_failure = None
        context.stopped = False
        return context.current.envelope

    def energy_agent(self, state: GraphStateView) -> EnergyProposal:
        context = self._current(state)
        current = _require_current(context)
        current_setpoint = current.envelope.snapshot.current_setpoint_c
        if not (
            math.isfinite(current_setpoint)
            and 22.0 <= current_setpoint <= 28.0
        ):
            context.advisory_failure = ValidationReasonCode.INVALID_OBSERVATION
            return _energy_placeholder(current)
        if not self._can_infer(
            context,
            self.config.energy_role_reserve_seconds,
        ):
            return _energy_placeholder(current)
        occupied = _occupied_pmvs(current.envelope)
        agent_input = EnergyAgentInput(
            current_setpoint_c=current.envelope.snapshot.current_setpoint_c,
            outdoor_dry_bulb_c=current.outdoor_dry_bulb_c,
            hvac_electricity_j=current.hvac_electricity_j,
            occupied_pmv_min=min(occupied) if occupied else None,
            occupied_pmv_max=max(occupied) if occupied else None,
            trend=current.trend,
            temperature_unit="degC",
            energy_unit="joule",
            pmv_unit="dimensionless",
        )
        try:
            result = self.provider.generate(
                role=AdvisoryRole.ENERGY,
                output_schema=EnergyProposal,
                prompt=build_energy_prompt(agent_input),
                deadline_bound=True,
            )
        except Exception as error:
            raise ExpectedGraphError(
                "ADVISORY_PROVIDER_ERROR",
                "advisory provider raised an unexpected bounded failure",
            ) from error
        if not self._accept_result(context, result.status):
            return _energy_placeholder(current)
        assert result.output is not None
        return result.output

    def comfort_agent(self, state: GraphStateView) -> ComfortAssessment:
        context = self._current(state)
        current = _require_current(context)
        if not self._can_infer(
            context,
            self.config.comfort_role_reserve_seconds,
        ):
            return _comfort_placeholder()
        zones: list[ComfortZoneInput] = []
        for zone in current.envelope.snapshot.zones:
            if (
                zone.temperature_c is None
                or zone.pmv is None
                or zone.occupancy_people is None
                or not all(
                    math.isfinite(value)
                    for value in (
                        zone.temperature_c,
                        zone.pmv,
                        zone.occupancy_people,
                    )
                )
            ):
                context.advisory_failure = ValidationReasonCode.ADVISORY_UNAVAILABLE
                return _comfort_placeholder()
            zones.append(
                ComfortZoneInput(
                    temperature_c=zone.temperature_c,
                    pmv=zone.pmv,
                    occupancy_people=zone.occupancy_people,
                )
            )
        agent_input = ComfortAgentInput(
            current_setpoint_c=current.envelope.snapshot.current_setpoint_c,
            zones=tuple(zones),
            target_pmv_lower=-0.5,
            target_pmv_upper=0.5,
            emergency_pmv_lower=-1.0,
            emergency_pmv_upper=1.0,
            temperature_unit="degC",
            pmv_unit="dimensionless",
            occupancy_unit="people",
        )
        try:
            result = self.provider.generate(
                role=AdvisoryRole.COMFORT,
                output_schema=ComfortAssessment,
                prompt=build_comfort_prompt(agent_input),
                deadline_bound=True,
            )
        except Exception as error:
            raise ExpectedGraphError(
                "ADVISORY_PROVIDER_ERROR",
                "advisory provider raised an unexpected bounded failure",
            ) from error
        if not self._accept_result(context, result.status):
            return _comfort_placeholder()
        assert result.output is not None
        return result.output

    def supervisor(self, state: GraphStateView) -> SupervisorDecision:
        context = self._current(state)
        current = _require_current(context)
        energy = state.energy_proposal or _energy_placeholder(current)
        comfort = state.comfort_assessment or _comfort_placeholder()
        energy_evidence = _energy_evidence(energy)
        comfort_evidence = _comfort_evidence(comfort)
        if not self._can_infer(
            context,
            self.config.supervisor_role_reserve_seconds,
        ):
            return _supervisor_placeholder(energy_evidence, comfort_evidence)
        agent_input = SupervisorAgentInput(
            current_setpoint_c=current.envelope.snapshot.current_setpoint_c,
            energy=energy,
            comfort=comfort,
            revision_count=state.revision_count,
            prior_validation_reason=(
                None if state.validation is None else state.validation.reason_code
            ),
        )
        try:
            result = self.provider.generate(
                role=AdvisoryRole.SUPERVISOR,
                output_schema=SupervisorDecision,
                prompt=build_supervisor_prompt(agent_input),
                deadline_bound=True,
            )
        except Exception as error:
            raise ExpectedGraphError(
                "ADVISORY_PROVIDER_ERROR",
                "advisory provider raised an unexpected bounded failure",
            ) from error
        if not self._accept_result(context, result.status):
            return _supervisor_placeholder(energy_evidence, comfort_evidence)
        assert result.output is not None
        return SupervisorDecision(
            disposition=result.output.disposition,
            proposed_setpoint_c=result.output.proposed_setpoint_c,
            conflict=result.output.conflict,
            energy_evidence=energy_evidence,
            comfort_evidence=comfort_evidence,
        )

    def validate_action(
        self,
        state: GraphStateView,
        proposal: ControlProposal | None,
    ) -> ValidationResult:
        context = self._current(state)
        current = _require_current(context)
        remaining = self._remaining(context)
        if remaining < self.config.submit_margin_seconds:
            context.advisory_failure = ValidationReasonCode.ADVISORY_DEADLINE_EXHAUSTED
        reason = context.advisory_failure
        if reason is not None:
            return _rejected(reason, "advisory path cannot authorize this decision")
        if proposal is None:
            return _rejected(
                ValidationReasonCode.ADVISORY_ABSTAINED,
                "supervisor abstained from an advisory proposal",
            )
        return validate_proposal(
            current.envelope,
            proposal,
            last_accepted_sequence=context.last_accepted_sequence,
        )

    def fallback_action(self, state: GraphStateView) -> FallbackDecision:
        context = self._current(state)
        current = _require_current(context)
        trigger = None if state.validation is None else state.validation.reason_code
        return choose_fallback(
            current.envelope,
            last_safe_setpoint_c=(current.envelope.snapshot.current_setpoint_c),
            trigger=trigger,
        )

    def apply_action(
        self,
        state: GraphStateView,
        action: GraphAction,
    ) -> AppliedAction:
        context = self._current(state)
        remaining = self._remaining(context)
        if remaining < self.config.submit_margin_seconds:
            raise ExpectedGraphError(
                "ACTION_SUBMIT_MARGIN_EXHAUSTED",
                "action submission margin is unavailable",
            )
        validation_reason = None if state.validation is None else state.validation.reason_code
        if action.source is ActionSource.ADVISORY:
            if action.fallback is not None:
                raise ExpectedGraphError(
                    "ACTION_SOURCE_MISMATCH",
                    "advisory action cannot carry deterministic fallback",
                )
            request = GatewayActionRequest(
                run_id=action.run_id,
                decision_id=action.decision_id,
                observation_sequence=action.observation_sequence,
                idempotency_key=_idempotency_key(action, validation_reason),
                setpoint_c=action.setpoint_c,
                control_source="advisory_proposal",
                energy_evidence=action.energy_evidence,
                comfort_evidence=action.comfort_evidence,
            )
            expected_authorization_reason = ValidationReasonCode.APPROVED.value
        else:
            if action.fallback is None or action.fallback.setpoint_c != action.setpoint_c:
                raise ExpectedGraphError(
                    "ACTION_FALLBACK_MISSING",
                    "fallback action requires its exact typed fallback decision",
                )
            request = GatewayActionRequest(
                run_id=action.run_id,
                decision_id=action.decision_id,
                observation_sequence=action.observation_sequence,
                idempotency_key=_idempotency_key(action, validation_reason),
                setpoint_c=action.setpoint_c,
                control_source="deterministic_fallback",
                fallback_trigger=validation_reason,
            )
            expected_authorization_reason = action.fallback.reason_code.value
        try:
            result = self.gateway.submit_action(
                request,
                timeout_seconds=self.config.submit_margin_seconds,
            )
        except McpGatewayError as error:
            raise _gateway_error(error) from None
        if (
            not result.accepted
            or result.run_id != request.run_id
            or result.decision_id != request.decision_id
            or result.observation_sequence != request.observation_sequence
            or result.idempotency_key != request.idempotency_key
            or result.requested_setpoint_c != request.setpoint_c
            or result.authorized_setpoint_c != request.setpoint_c
            or result.control_source != request.control_source
            or result.authorization_reason_code != expected_authorization_reason
            or result.cached
        ):
            raise ExpectedGraphError(
                "MCP_ACTION_MISMATCH",
                "MCP action result did not match the exact authorized request",
            )
        context.last_accepted_sequence = action.observation_sequence
        return AppliedAction(
            action=action,
            applied_at_utc=_utc_now(),
            actuator_write_count=1,
        )

    def advance_and_evaluate(self, state: GraphStateView) -> EvaluationRecord:
        context = self._current(state)
        current = _require_current(context)
        if state.applied_action is None:
            raise ExpectedGraphError(
                "EVALUATION_CONTEXT_MISSING",
                "evaluation requires one applied action",
            )
        try:
            next_observation = self.gateway.await_observation(
                run_id=state.run_id,
                timeout_seconds=self.config.observation_timeout_seconds,
            )
        except McpGatewayError as error:
            if error.code != "OBSERVATION_TIMEOUT":
                raise _gateway_error(error) from None
            try:
                terminal_status = self.gateway.status(run_id=state.run_id)
            except McpGatewayError as status_error:
                raise _gateway_error(status_error) from None
            if (
                terminal_status.run_id != state.run_id
                or terminal_status.status != "completed"
            ):
                raise _gateway_error(error) from None
            occupied = _occupied_pmvs(current.envelope)
            compliant = sum(-0.5 <= value <= 0.5 for value in occupied)
            compliance = 100.0 if not occupied else compliant / len(occupied) * 100.0
            return EvaluationRecord(
                decision_id=state.applied_action.action.decision_id,
                evaluated_at_utc=_utc_now(),
                energy_delta_kwh=0.0,
                occupied_pmv_compliance_percent=compliance,
                safe=all(-1.0 <= value <= 1.0 for value in occupied),
            )
        try:
            next_observation = AgentObservation.model_validate(
                next_observation.model_dump()
            )
        except Exception as error:
            raise ExpectedGraphError(
                "MCP_OBSERVATION_INVALID",
                "MCP observation failed the bounded graph context contract",
            ) from error
        if (
            next_observation.envelope.run_id != state.run_id
            or next_observation.envelope.sequence <= current.envelope.sequence
        ):
            raise ExpectedGraphError(
                "EVALUATION_OBSERVATION_MISMATCH",
                "evaluation observation identity or sequence is invalid",
            )
        context.staged = next_observation
        context.staged_deadline = self._monotonic() + self.config.action_window_seconds
        energy_delta_kwh = (
            next_observation.hvac_electricity_j - current.hvac_electricity_j
        ) / 3_600_000.0
        occupied = _occupied_pmvs(next_observation.envelope)
        compliant = sum(-0.5 <= value <= 0.5 for value in occupied)
        compliance = 100.0 if not occupied else compliant / len(occupied) * 100.0
        safe = all(-1.0 <= value <= 1.0 for value in occupied)
        return EvaluationRecord(
            decision_id=state.applied_action.action.decision_id,
            evaluated_at_utc=_utc_now(),
            energy_delta_kwh=energy_delta_kwh,
            occupied_pmv_compliance_percent=compliance,
            safe=safe,
        )

    def reflect(self, state: GraphStateView) -> ReflectionRecord:
        if (
            state.evaluation is None
            or state.energy_proposal is None
            or state.comfort_assessment is None
        ):
            raise ExpectedGraphError(
                "REFLECTION_CONTEXT_MISSING",
                "reflection requires typed predictions and measurement",
            )
        evaluation = state.evaluation
        energy_match = _energy_matches(
            state.energy_proposal.expected_energy_effect,
            evaluation.energy_delta_kwh,
        )
        comfort_match = _comfort_matches(
            state.comfort_assessment.risk,
            evaluation.occupied_pmv_compliance_percent,
            evaluation.safe,
        )
        outcome = (
            f"energy {state.energy_proposal.expected_energy_effect.value}/"
            f"{evaluation.energy_delta_kwh:+.4f}kWh match={str(energy_match).lower()};"
            f"comfort {state.comfort_assessment.risk.value}/"
            f"{evaluation.occupied_pmv_compliance_percent:.1f}% "
            f"match={str(comfort_match).lower()}"
        )
        return ReflectionRecord(
            decision_id=evaluation.decision_id,
            reflected_at_utc=_utc_now(),
            predicted_energy_effect=state.energy_proposal.expected_energy_effect,
            measured_energy_delta_kwh=evaluation.energy_delta_kwh,
            energy_prediction_matched=energy_match,
            predicted_comfort_risk=state.comfort_assessment.risk,
            measured_occupied_pmv_compliance_percent=(evaluation.occupied_pmv_compliance_percent),
            comfort_prediction_matched=comfort_match,
            outcome=outcome,
            recommend_continue=evaluation.safe,
        )

    def continue_or_finish(self, state: GraphStateView) -> CompletionRoute:
        try:
            status = self.gateway.status(run_id=state.run_id)
        except McpGatewayError as error:
            raise _gateway_error(error) from None
        if status.run_id != state.run_id or status.status == "failed":
            return CompletionRoute.FATAL
        if status.status in {"completed", "cancelled"}:
            return CompletionRoute.FINISH
        return CompletionRoute.CONTINUE

    def finalize_run(self, state: GraphStateView) -> RunSummary:
        context = self._context(state.run_id)
        if not context.stopped:
            try:
                self.gateway.stop(
                    run_id=state.run_id,
                    timeout_seconds=self.config.stop_timeout_seconds,
                )
            except McpGatewayError as error:
                raise _gateway_error(error) from None
            context.stopped = True
        try:
            summary = self.gateway.summary(run_id=state.run_id)
        except McpGatewayError as error:
            raise _gateway_error(error) from None
        if (
            summary.run_id != state.run_id
            or summary.status == "failed"
            or summary.actions_applied != state.completed_decisions
        ):
            raise ExpectedGraphError(
                "MCP_SUMMARY_MISMATCH",
                "MCP summary differs from the completed graph run",
            )
        return RunSummary(
            run_id=state.run_id,
            completed_decisions=state.completed_decisions,
            status=SimulationStatus.COMPLETED,
            finalized_at_utc=_utc_now(),
        )

    def abort_safely(self, state: GraphStateView, error: GraphError) -> None:
        _ = error
        context = self._contexts.get(state.run_id)
        if context is not None and context.stopped:
            return
        try:
            self.gateway.stop(
                run_id=state.run_id,
                timeout_seconds=self.config.stop_timeout_seconds,
            )
        except McpGatewayError as gateway_error:
            raise _gateway_error(gateway_error) from None
        if context is not None:
            context.stopped = True

    def _await_gateway_observation(self, run_id: str) -> AgentObservation:
        try:
            observation = self.gateway.await_observation(
                run_id=run_id,
                timeout_seconds=self.config.observation_timeout_seconds,
            )
        except McpGatewayError as error:
            raise _gateway_error(error) from None
        try:
            return AgentObservation.model_validate(observation.model_dump())
        except Exception as error:
            raise ExpectedGraphError(
                "MCP_OBSERVATION_INVALID",
                "MCP observation failed the bounded graph context contract",
            ) from error

    def _context(self, run_id: str) -> _DecisionContext:
        context = self._contexts.get(run_id)
        if context is None:
            raise ExpectedGraphError(
                "RUNTIME_CONTEXT_MISSING",
                "agent runtime has no initialized run context",
            )
        return context

    def _current(self, state: GraphStateView) -> _DecisionContext:
        context = self._context(state.run_id)
        if (
            context.current is None
            or state.observation is None
            or context.current.envelope != state.observation
        ):
            raise ExpectedGraphError(
                "RUNTIME_OBSERVATION_MISMATCH",
                "agent runtime observation differs from graph state",
            )
        return context

    def _remaining(self, context: _DecisionContext) -> float:
        if context.deadline is None:
            return 0.0
        return context.deadline - self._monotonic()

    def _can_infer(
        self,
        context: _DecisionContext,
        required_seconds: float,
    ) -> bool:
        if context.advisory_failure is not None:
            return False
        if self._remaining(context) < required_seconds:
            context.advisory_failure = ValidationReasonCode.ADVISORY_DEADLINE_EXHAUSTED
            return False
        return True

    def _accept_result(
        self,
        context: _DecisionContext,
        status: ProviderStatus,
    ) -> bool:
        if self._remaining(context) < self.config.submit_margin_seconds:
            context.advisory_failure = ValidationReasonCode.ADVISORY_DEADLINE_EXHAUSTED
            return False
        if status is not ProviderStatus.SUCCESS:
            context.advisory_failure = ValidationReasonCode.ADVISORY_UNAVAILABLE
            return False
        return True


def _gateway_error(error: McpGatewayError) -> ExpectedGraphError:
    code = error.code if error.code.startswith("MCP_") else f"MCP_{error.code}"
    return ExpectedGraphError(code[:80], "MCP gateway returned a bounded service failure")


def _require_current(context: _DecisionContext) -> AgentObservation:
    if context.current is None:
        raise ExpectedGraphError(
            "RUNTIME_CONTEXT_MISSING",
            "agent runtime has no current observation",
        )
    return context.current


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _occupied_pmvs(observation: ObservationEnvelope) -> tuple[float, ...]:
    return tuple(
        zone.pmv
        for zone in observation.snapshot.zones
        if zone.pmv is not None
        and math.isfinite(zone.pmv)
        and zone.occupancy_people is not None
        and math.isfinite(zone.occupancy_people)
        and zone.occupancy_people > 0
    )


def _energy_placeholder(observation: AgentObservation) -> EnergyProposal:
    observed = observation.envelope.snapshot.current_setpoint_c
    safe_placeholder = 24.0
    if math.isfinite(observed):
        safe_placeholder = min(28.0, max(22.0, observed))
    return EnergyProposal(
        proposed_setpoint_c=safe_placeholder,
        expected_energy_effect=EnergyEffect.NEUTRAL,
        confidence=0.0,
        reason="advisory unavailable",
    )


def _comfort_placeholder() -> ComfortAssessment:
    return ComfortAssessment(
        comfort_state=ComfortState.UNKNOWN,
        recommended_direction=SetpointDirection.HOLD,
        risk=ComfortRisk.UNKNOWN,
        reason="advisory unavailable",
    )


def _energy_evidence(value: EnergyProposal) -> str:
    return f"E:{value.expected_energy_effect.value}:c{value.confidence:.2f}"


def _comfort_evidence(value: ComfortAssessment) -> str:
    return f"C:{value.risk.value}:{value.recommended_direction.value}"


def _supervisor_placeholder(
    energy_evidence: str,
    comfort_evidence: str,
) -> SupervisorDecision:
    return SupervisorDecision(
        disposition=SupervisorDisposition.ABSTAIN,
        proposed_setpoint_c=None,
        conflict=True,
        energy_evidence=energy_evidence,
        comfort_evidence=comfort_evidence,
    )


def _rejected(reason: ValidationReasonCode, evidence: str) -> ValidationResult:
    return ValidationResult(
        approved=False,
        reason_code=reason,
        validated_setpoint_c=None,
        emergency_observed=False,
        evidence=(evidence,),
    )


def _idempotency_key(
    action: GraphAction,
    trigger: ValidationReasonCode | None,
) -> str:
    payload = {
        "run": action.run_id,
        "decision": action.decision_id,
        "sequence": action.observation_sequence,
        "setpoint": action.setpoint_c,
        "source": action.source.value,
        "energy": action.energy_evidence,
        "comfort": action.comfort_evidence,
        "trigger": None if trigger is None else trigger.value,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"agt002-{digest[:48]}"


def _energy_matches(predicted: EnergyEffect, measured_delta_kwh: float) -> bool:
    if predicted is EnergyEffect.REDUCE:
        return measured_delta_kwh < -ENERGY_MATCH_TOLERANCE_KWH
    if predicted is EnergyEffect.INCREASE:
        return measured_delta_kwh > ENERGY_MATCH_TOLERANCE_KWH
    return abs(measured_delta_kwh) <= ENERGY_MATCH_TOLERANCE_KWH


def _comfort_matches(
    predicted: ComfortRisk,
    compliance_percent: float,
    safe: bool,
) -> bool:
    if predicted is ComfortRisk.LOW:
        return safe and compliance_percent == 100.0
    if predicted is ComfortRisk.TARGET_VIOLATION:
        return safe and compliance_percent < 100.0
    if predicted is ComfortRisk.EMERGENCY:
        return not safe
    return False


__all__ = [
    "ACTION_WINDOW_SECONDS",
    "AgentGraphRuntime",
    "AgentRuntimeConfig",
    "COMFORT_ROLE_RESERVE_SECONDS",
    "ENERGY_ROLE_RESERVE_SECONDS",
    "ENERGY_MATCH_TOLERANCE_KWH",
    "SUBMIT_MARGIN_SECONDS",
    "SUPERVISOR_ROLE_RESERVE_SECONDS",
]
