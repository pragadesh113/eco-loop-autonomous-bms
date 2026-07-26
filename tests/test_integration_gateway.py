"""Concrete FastMCP gateway contract tests."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import TypeVar, cast

import pytest
from pydantic import BaseModel

from bms_agent.control import ValidationReasonCode
from bms_agent.graph import GatewayActionRequest, McpGatewayError
from bms_agent.integration import (
    InProcessFastMcpGateway,
    persist_experiment_artifacts,
    run_controlled_graph,
)
from bms_agent.mcp_server.server import (
    ActionData,
    ActionRequest,
    AwaitObservationRequest,
    ConstraintsData,
    ObservationData,
    ResetData,
    ResetRequest,
    RunRequest,
    SessionRegistry,
    StartData,
    StartRequest,
    StatusData,
    StopData,
    StopRequest,
    SummaryData,
    ToolError,
    ToolResponse,
    TrendData,
    TrendRequest,
    Units,
    ZoneData,
    build_server,
)

FakeDataT = TypeVar("FakeDataT", bound=BaseModel)


def observation(run_id: str = "run", sequence: int = 1) -> ObservationData:
    return ObservationData(
        runId=run_id,
        decisionId=f"{run_id}-decision-{sequence:04d}",
        sequence=sequence,
        month=5,
        day=23,
        hour=1,
        minute=0,
        outdoorDryBulbC=35.0,
        coolingScheduleValueC=24.0,
        hvacElectricityJ=3_600_000.0,
        zones=[
            ZoneData(
                zone=f"ZONE-{index}",
                temperatureC=24.0 + index / 10,
                pmv=0.1 * index,
                ppdPercent=5.0,
                occupancyPeople=1.0,
                coolingSetpointC=24.0,
            )
            for index in range(1, 6)
        ],
        units=Units(),
    )


class FakeRegistry:
    def __init__(self) -> None:
        self.action_calls = 0
        self.override_run_id: str | None = None
        self.failure_code: str | None = None
        self.observation_count = 0
        self.trend_setpoint_override: float | None = None
        self.constraints_calls = 0
        self.reset_calls = 0
        self.constraints_setpoint_max = 28.0

    def _result(self, data: FakeDataT) -> ToolResponse[FakeDataT]:
        if self.failure_code is not None:
            return ToolResponse[FakeDataT](
                ok=False,
                error=ToolError(
                    code=self.failure_code,
                    message="bounded",
                    retryable=False,
                ),
            )
        return ToolResponse[FakeDataT](ok=True, data=data)

    def start(self, request: StartRequest) -> ToolResponse[StartData]:
        return self._result(
            StartData(
                runId=self.override_run_id or request.runId,
                mode="controlled",
                status="starting",
                runDirectory="runs/test",
            )
        )

    def await_observation(
        self, request: AwaitObservationRequest
    ) -> ToolResponse[ObservationData]:
        self.observation_count += 1
        return self._result(
            observation(
                self.override_run_id or request.runId,
                self.observation_count,
            )
        )

    def latest_observation(self, request: RunRequest) -> ToolResponse[ObservationData]:
        return self._result(observation(self.override_run_id or request.runId))

    def recent_trend(self, request: TrendRequest) -> ToolResponse[TrendData]:
        item = observation(self.override_run_id or request.runId)
        if self.trend_setpoint_override is not None:
            item = item.model_copy(
                update={"coolingScheduleValueC": self.trend_setpoint_override}
            )
        return self._result(
            TrendData(
                runId=self.override_run_id or request.runId,
                samples=[item],
            )
        )

    def constraints(self, request: RunRequest) -> ToolResponse[ConstraintsData]:
        self.constraints_calls += 1
        return self._result(
            ConstraintsData(
                runId=self.override_run_id or request.runId,
                occupiedPmvLower=-0.5,
                occupiedPmvUpper=0.5,
                emergencyPmvLower=-1.0,
                emergencyPmvUpper=1.0,
                coolingSetpointMinC=22.0,
                coolingSetpointMaxC=self.constraints_setpoint_max,
                decisionIntervalMinutes=60,
            )
        )

    def submit_action(self, request: ActionRequest) -> ToolResponse[ActionData]:
        self.action_calls += 1
        return self._result(
            ActionData(
                runId=self.override_run_id or request.runId,
                decisionId=request.decisionId,
                observationSequence=request.observationSequence,
                idempotencyKey=request.idempotencyKey,
                requestedSetpointC=request.setpointC,
                authorizedSetpointC=request.setpointC,
                controlSource=request.controlSource,
                authorizationReasonCode=(
                    "APPROVED"
                    if request.controlSource == "advisory_proposal"
                    else "LAST_SAFE_INVALID_INPUT"
                ),
                accepted=True,
                cached=False,
            )
        )

    def status(self, request: RunRequest) -> ToolResponse[StatusData]:
        return self._result(
            StatusData(
                runId=self.override_run_id or request.runId,
                status="completed",
                exitCode=0,
                weatherTimesteps=96,
                observationCount=24,
                actionCount=self.action_calls,
                failure=None,
            )
        )

    def inspect_errors(self, request: RunRequest) -> object:
        raise AssertionError(request)

    def stop(self, request: StopRequest) -> ToolResponse[StopData]:
        return self._result(
            StopData(runId=self.override_run_id or request.runId, stopped=True, status="cancelled")
        )

    def reset(self, request: ResetRequest) -> ToolResponse[ResetData]:
        self.reset_calls += 1
        return self._result(
            ResetData(runId=self.override_run_id or request.runId, reset=True)
        )

    def summary(self, request: RunRequest) -> ToolResponse[SummaryData]:
        return self._result(
            SummaryData(
                runId=self.override_run_id or request.runId,
                status="completed",
                exitCode=0,
                weatherTimesteps=96,
                hourlyObservations=24,
                actionsApplied=self.action_calls,
                latestScheduleValueC=24.0,
                latestFiveZoneSetpointsC=[24.0] * 5,
            )
        )


def gateway_fixture() -> tuple[InProcessFastMcpGateway, FakeRegistry]:
    registry = FakeRegistry()
    server = build_server(cast(SessionRegistry, registry))
    return InProcessFastMcpGateway(server), registry


def test_gateway_maps_registered_fastmcp_tools_and_units() -> None:
    gateway, registry = gateway_fixture()
    with gateway:
        gateway.start(run_id="run", max_weather_timesteps=96, action_wait_seconds=30.0)
        current = gateway.await_observation(run_id="run", timeout_seconds=3.0)
        result = gateway.submit_action(
            GatewayActionRequest(
                run_id="run",
                decision_id="decision-1",
                observation_sequence=1,
                idempotency_key="key-1",
                setpoint_c=24.5,
                control_source="advisory_proposal",
                energy_evidence="E:reduce:c0.9",
                comfort_evidence="C:within:hold",
            ),
            timeout_seconds=3.0,
        )
        status = gateway.status(run_id="run")
        summary = gateway.summary(run_id="run")
        gateway.stop(run_id="run", timeout_seconds=3.0)
        gateway.reset(run_id="run")

    assert current.envelope.run_id == "run"
    assert len(current.envelope.snapshot.zones) == 5
    assert current.envelope.snapshot.temperature_unit == "degC"
    assert math.isclose(current.hvac_electricity_j, 3_600_000.0)
    assert len(current.trend) == 1
    assert result.accepted and not result.cached
    assert result.authorization_reason_code == "APPROVED"
    assert registry.action_calls == 1
    assert registry.constraints_calls == registry.reset_calls == 1
    assert status.status == summary.status == "completed"
    assert summary.actions_applied == 1


def test_gateway_excludes_precontrol_out_of_policy_trend_setpoint() -> None:
    gateway, registry = gateway_fixture()
    registry.trend_setpoint_override = 29.4
    with gateway:
        current = gateway.await_observation(run_id="run", timeout_seconds=3.0)
    assert current.trend == ()


def test_gateway_preserves_fallback_reason_and_single_submit() -> None:
    gateway, registry = gateway_fixture()
    with gateway:
        result = gateway.submit_action(
            GatewayActionRequest(
                run_id="run",
                decision_id="decision-1",
                observation_sequence=1,
                idempotency_key="key-2",
                setpoint_c=24.0,
                control_source="deterministic_fallback",
                fallback_trigger=ValidationReasonCode.ADVISORY_UNAVAILABLE,
            ),
            timeout_seconds=3.0,
        )
    assert result.authorization_reason_code == "LAST_SAFE_INVALID_INPUT"
    assert registry.action_calls == 1


@pytest.mark.parametrize(
    "operation",
    ["start", "observe", "status"],
)
def test_gateway_normalizes_tool_failures(
    operation: str,
) -> None:
    gateway, registry = gateway_fixture()
    registry.failure_code = "SECRET_TOKEN_PAYLOAD"
    with gateway, pytest.raises(McpGatewayError) as captured:
        if operation == "start":
            gateway.start(
                run_id="run",
                max_weather_timesteps=96,
                action_wait_seconds=30.0,
            )
        elif operation == "observe":
            gateway.await_observation(run_id="run", timeout_seconds=3.0)
        else:
            gateway.status(run_id="run")
    assert captured.value.code == "MCP_TOOL_FAILED"
    assert "bounded" not in captured.value.safe_message


def test_gateway_rejects_changed_server_constraints() -> None:
    gateway, registry = gateway_fixture()
    registry.constraints_setpoint_max = 29.0
    with gateway, pytest.raises(McpGatewayError) as captured:
        gateway.start(
            run_id="run",
            max_weather_timesteps=96,
            action_wait_seconds=30.0,
        )
    assert captured.value.code == "MCP_CONSTRAINTS_MISMATCH"


def test_gateway_rejects_cross_run_response_and_closed_use() -> None:
    gateway, registry = gateway_fixture()
    registry.override_run_id = "other-run"
    with gateway, pytest.raises(McpGatewayError, match="identity"):
        gateway.start(run_id="run", max_weather_timesteps=96, action_wait_seconds=30.0)
    with pytest.raises(McpGatewayError) as captured:
        gateway.status(run_id="run")
    assert captured.value.code == "MCP_GATEWAY_CLOSED"


def test_deterministic_closed_loop_uses_registered_tools_and_finishes() -> None:
    registry = FakeRegistry()
    server = build_server(cast(SessionRegistry, registry))

    result = run_controlled_graph(
        server=server,
        run_id="closed-loop",
        max_weather_timesteps=96,
        max_decisions=1,
        deterministic_only=True,
    )

    summary = result.state.get("summary")
    assert summary is not None
    assert summary.completed_decisions == 1
    assert result.state.get("error") is None
    assert len(result.action_requests) == len(result.action_results) == 1
    assert result.action_requests[0].control_source == "deterministic_fallback"
    assert registry.action_calls == 1
    assert registry.constraints_calls == registry.reset_calls == 1


def test_typed_optimizer_closed_loop_remains_server_authorized() -> None:
    registry = FakeRegistry()
    server = build_server(cast(SessionRegistry, registry))

    result = run_controlled_graph(
        server=server,
        run_id="optimized-loop",
        max_weather_timesteps=96,
        max_decisions=1,
        deterministic_optimization=True,
    )

    summary = result.state.get("summary")
    assert summary is not None
    assert summary.completed_decisions == 1
    assert result.state.get("error") is None
    assert len(result.action_results) == 1
    assert result.action_results[0].accepted
    assert registry.action_calls == 1
    assert registry.constraints_calls == registry.reset_calls == 1


def test_artifact_repeat_refusal_does_not_mutate_existing_evidence(
    tmp_path: Path,
) -> None:
    registry = FakeRegistry()
    result = run_controlled_graph(
        server=build_server(cast(SessionRegistry, registry)),
        run_id="repeat-safe",
        max_weather_timesteps=96,
        max_decisions=1,
        deterministic_only=True,
    )
    run_dir = tmp_path / "runs" / "repeat-safe"
    run_dir.mkdir(parents=True)
    targets = {
        run_dir / "normalized-metrics.jsonl": b"trusted-metrics\n",
        run_dir / "summary.json": b'{"trusted":true}\n',
    }
    for path, content in targets.items():
        path.write_bytes(content)
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in targets
    }

    with pytest.raises(ValueError, match="already exist"):
        persist_experiment_artifacts(
            project_root=tmp_path,
            result=result,
            baseline_run_id="baseline",
        )

    after = {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in targets
    }
    assert after == before
