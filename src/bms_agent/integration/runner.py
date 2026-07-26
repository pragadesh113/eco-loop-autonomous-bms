"""Closed-loop LangGraph runner over the concrete FastMCP gateway."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TypeVar, cast

from mcp.server.fastmcp import FastMCP

from bms_agent.graph import (
    AgentGraphRuntime,
    AgentRuntimeConfig,
    GatewayActionRequest,
    GatewayActionResult,
    GraphEvent,
    GraphRunInput,
    GraphRunner,
    RunState,
)
from bms_agent.integration.gateway import InProcessFastMcpGateway
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
    build_local_provider,
)
from bms_agent.llm.schemas import AdvisoryContract
from bms_agent.mcp_server.server import ObservationData

OutputT = TypeVar("OutputT", bound=AdvisoryContract)


class DeterministicFallbackProvider:
    """Explicit no-inference provider used for the first live safety smoke."""

    def generate(
        self,
        *,
        role: AdvisoryRole,
        output_schema: type[OutputT],
        prompt: str,
        deadline_bound: bool = False,
    ) -> AdvisoryResult[OutputT]:
        _ = prompt, deadline_bound
        return AdvisoryResult[OutputT](
            status=ProviderStatus.UNAVAILABLE,
            role=role,
            schema_name=output_schema.__name__,
            model=None,
            output=None,
            attempt_count=0,
            correction_attempted=False,
            used_fallback=False,
            wall_duration_ms=0.0,
            detail="deterministic-only controlled smoke",
        )


class DeterministicOptimizationProvider:
    """Typed three-role optimizer used when reproducibility outranks local-LLM latency."""

    target_setpoint_c = 25.5

    def generate(
        self,
        *,
        role: AdvisoryRole,
        output_schema: type[OutputT],
        prompt: str,
        deadline_bound: bool = False,
    ) -> AdvisoryResult[OutputT]:
        _ = deadline_bound
        output: AdvisoryContract
        if role is AdvisoryRole.ENERGY:
            current = _prompt_number(prompt, r"current=(-?\d+(?:\.\d+)?)")
            proposed = min(
                current + 1.0,
                self.target_setpoint_c,
            ) if current < self.target_setpoint_c else max(
                current - 1.0,
                self.target_setpoint_c,
            )
            output = EnergyProposal(
                proposed_setpoint_c=proposed,
                expected_energy_effect=(
                    EnergyEffect.REDUCE if proposed > current else EnergyEffect.NEUTRAL
                ),
                confidence=1.0,
                reason="deterministic energy target",
            )
        elif role is AdvisoryRole.COMFORT:
            pmvs = tuple(
                float(value)
                for value in re.findall(r"pmv=(-?\d+(?:\.\d+)?)", prompt)
            )
            hot = bool(pmvs) and max(pmvs) > 0.5
            cold = bool(pmvs) and min(pmvs) < -0.5
            emergency = bool(pmvs) and any(abs(value) > 1.0 for value in pmvs)
            if hot and cold:
                state, direction = ComfortState.MIXED, SetpointDirection.HOLD
            elif hot:
                state, direction = ComfortState.WARM, SetpointDirection.LOWER
            elif cold:
                state, direction = ComfortState.COLD, SetpointDirection.RAISE
            else:
                state, direction = ComfortState.COMFORTABLE, SetpointDirection.HOLD
            output = ComfortAssessment(
                comfort_state=state,
                recommended_direction=direction,
                risk=(
                    ComfortRisk.EMERGENCY
                    if emergency
                    else ComfortRisk.TARGET_VIOLATION
                    if hot or cold
                    else ComfortRisk.LOW
                ),
                reason="deterministic PMV assessment",
            )
        else:
            proposed = _prompt_number(
                prompt,
                r"energy=\(setpoint=(-?\d+(?:\.\d+)?)",
            )
            output = SupervisorDecision(
                disposition=SupervisorDisposition.ACCEPT,
                proposed_setpoint_c=proposed,
                conflict=False,
                energy_evidence="deterministic energy",
                comfort_evidence="deterministic comfort",
            )
        return AdvisoryResult[OutputT](
            status=ProviderStatus.SUCCESS,
            role=role,
            schema_name=output_schema.__name__,
            model="deterministic-optimizer-v1",
            output=cast(OutputT, output),
            attempt_count=1,
            correction_attempted=False,
            used_fallback=False,
            wall_duration_ms=0.0,
            detail=None,
        )


def _prompt_number(prompt: str, pattern: str) -> float:
    match = re.search(pattern, prompt)
    if match is None:
        raise ValueError("typed optimizer prompt is missing required numeric context")
    return float(match.group(1))


@dataclass(frozen=True, slots=True)
class ControlledGraphResult:
    state: RunState
    events: tuple[GraphEvent, ...]
    observations: tuple[ObservationData, ...]
    action_requests: tuple[GatewayActionRequest, ...]
    action_results: tuple[GatewayActionResult, ...]


def run_controlled_graph(
    *,
    server: FastMCP[object],
    run_id: str,
    max_weather_timesteps: int,
    max_decisions: int,
    provider: AdvisoryProvider | None = None,
    deterministic_only: bool = False,
    deterministic_optimization: bool = False,
) -> ControlledGraphResult:
    """Execute one bounded closed loop and retain normalized integration evidence."""

    selected_modes = sum(
        (provider is not None, deterministic_only, deterministic_optimization)
    )
    if selected_modes > 1:
        raise ValueError("select exactly one provider mode")
    selected_provider: AdvisoryProvider
    if provider is not None:
        selected_provider = provider
    elif deterministic_only:
        selected_provider = DeterministicFallbackProvider()
    elif deterministic_optimization:
        selected_provider = DeterministicOptimizationProvider()
    else:
        selected_provider = build_local_provider()

    gateway = InProcessFastMcpGateway(server)
    with gateway:
        runtime = AgentGraphRuntime(
            selected_provider,
            gateway,
            config=AgentRuntimeConfig(
                max_weather_timesteps=max_weather_timesteps,
                action_window_seconds=30.0,
            ),
        )
        runner = GraphRunner(runtime)
        events = tuple(
            runner.stream(
                GraphRunInput(run_id=run_id, max_decisions=max_decisions)
            )
        )
        state = cast(RunState, runner.get_state(run_id).values)
        gateway.reset(run_id=run_id)
    return ControlledGraphResult(
        state=state,
        events=events,
        observations=gateway.observations,
        action_requests=gateway.action_requests,
        action_results=gateway.action_results,
    )


__all__ = [
    "ControlledGraphResult",
    "DeterministicFallbackProvider",
    "DeterministicOptimizationProvider",
    "run_controlled_graph",
]
