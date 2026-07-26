"""Synchronous graph gateway over registered FastMCP tools."""

from __future__ import annotations

import asyncio
import math
import threading
from datetime import UTC, datetime
from typing import TypeVar

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ValidationError

from bms_agent.control import ObservationEnvelope, ObservationSnapshot, ZoneSnapshot
from bms_agent.graph import (
    AgentObservation,
    GatewayActionRequest,
    GatewayActionResult,
    GatewayStatus,
    GatewaySummary,
    McpGatewayError,
    TrendSample,
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
    StartData,
    StartRequest,
    StatusData,
    StopData,
    StopRequest,
    SummaryData,
    ToolResponse,
    TrendData,
    TrendRequest,
)

DataT = TypeVar("DataT", bound=BaseModel)

_ALLOWED_TOOL_ERROR_CODES = frozenset(
    {
        "ACTIVE_RESET_REFUSED",
        "ACTIVE_SESSION_EXISTS",
        "DUPLICATE_ACTION",
        "FALLBACK_MISMATCH",
        "IDEMPOTENCY_CONFLICT",
        "IDENTITY_MISMATCH",
        "INVALID_ACTION",
        "NO_OBSERVATION",
        "NO_PENDING_ACTION",
        "NO_SESSION",
        "OBSERVATION_TIMEOUT",
        "RUN_ID_MISMATCH",
        "SAFETY_CONTEXT_UNAVAILABLE",
        "SAFETY_REJECTED",
        "STALE_ACTION",
        "STALE_OBSERVATION",
        "START_FAILED",
        "STOP_TIMEOUT",
    }
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class InProcessFastMcpGateway:
    """Use FastMCP's registered tool dispatcher without exposing session internals."""

    def __init__(self, server: FastMCP[object]) -> None:
        self._server = server
        self._runner = asyncio.Runner()
        self._lock = threading.Lock()
        self._closed = False
        self._observations: list[ObservationData] = []
        self._action_requests: list[GatewayActionRequest] = []
        self._action_results: list[GatewayActionResult] = []

    @property
    def observations(self) -> tuple[ObservationData, ...]:
        return tuple(self._observations)

    @property
    def action_requests(self) -> tuple[GatewayActionRequest, ...]:
        return tuple(self._action_requests)

    @property
    def action_results(self) -> tuple[GatewayActionResult, ...]:
        return tuple(self._action_results)

    def __enter__(self) -> InProcessFastMcpGateway:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._runner.close()
            self._closed = True

    def _call(
        self,
        name: str,
        request: BaseModel,
        response_model: type[ToolResponse[DataT]],
        *,
        timeout_seconds: float,
    ) -> DataT:
        if timeout_seconds <= 0.0:
            raise McpGatewayError("MCP_TIMEOUT", "MCP tool deadline is unavailable")
        with self._lock:
            if self._closed:
                raise McpGatewayError("MCP_GATEWAY_CLOSED", "MCP gateway is closed")
            try:
                raw: object = self._runner.run(
                    asyncio.wait_for(
                        self._server.call_tool(
                            name,
                            {"request": request.model_dump(mode="json")},
                        ),
                        timeout=timeout_seconds,
                    )
                )
                if isinstance(raw, tuple):
                    if len(raw) != 2:
                        raise ValueError("unexpected FastMCP tool tuple")
                    raw = raw[1]
                response = response_model.model_validate(raw)
            except TimeoutError as error:
                raise McpGatewayError("MCP_TIMEOUT", "MCP tool timed out") from error
            except (ValidationError, TypeError, ValueError) as error:
                raise McpGatewayError(
                    "MCP_RESPONSE_INVALID",
                    "MCP tool returned an invalid typed response",
                ) from error
            except McpGatewayError:
                raise
            except Exception as error:
                raise McpGatewayError(
                    "MCP_TRANSPORT_FAILURE",
                    "MCP tool dispatch failed",
                ) from error
        if not response.ok or response.data is None:
            code = "MCP_TOOL_FAILED"
            if (
                response.error is not None
                and response.error.code in _ALLOWED_TOOL_ERROR_CODES
            ):
                code = response.error.code
            raise McpGatewayError(code, "MCP tool rejected the request")
        return response.data

    def start(
        self,
        *,
        run_id: str,
        max_weather_timesteps: int,
        action_wait_seconds: float,
    ) -> None:
        data = self._call(
            "start_simulation",
            StartRequest(
                runId=run_id,
                maxWeatherTimesteps=max_weather_timesteps,
                actionWaitSeconds=action_wait_seconds,
            ),
            ToolResponse[StartData],
            timeout_seconds=30.0,
        )
        if data.runId != run_id or data.mode != "controlled":
            raise McpGatewayError("MCP_IDENTITY_MISMATCH", "MCP start identity mismatch")
        constraints = self._call(
            "get_control_constraints",
            RunRequest(runId=run_id),
            ToolResponse[ConstraintsData],
            timeout_seconds=3.0,
        )
        if (
            constraints.runId != run_id
            or constraints.occupiedPmvLower != -0.5
            or constraints.occupiedPmvUpper != 0.5
            or constraints.emergencyPmvLower != -1.0
            or constraints.emergencyPmvUpper != 1.0
            or constraints.coolingSetpointMinC != 22.0
            or constraints.coolingSetpointMaxC != 28.0
            or constraints.decisionIntervalMinutes != 60
            or constraints.units.temperature != "degC"
            or constraints.units.pmv != "dimensionless"
            or constraints.units.ppd != "percent"
            or constraints.units.occupancy != "people"
            or constraints.units.energy != "joule"
        ):
            raise McpGatewayError(
                "MCP_CONSTRAINTS_MISMATCH",
                "MCP control constraints differ from the locked safety contract",
            )

    def await_observation(
        self,
        *,
        run_id: str,
        timeout_seconds: float,
    ) -> AgentObservation:
        observation = self._call(
            "await_observation",
            AwaitObservationRequest(runId=run_id, timeoutSeconds=timeout_seconds),
            ToolResponse[ObservationData],
            timeout_seconds=max(0.001, timeout_seconds),
        )
        trend = self._call(
            "get_recent_trend",
            TrendRequest(runId=run_id, sampleCount=12),
            ToolResponse[TrendData],
            timeout_seconds=max(0.001, min(timeout_seconds, 3.0)),
        )
        if observation.runId != run_id or trend.runId != run_id:
            raise McpGatewayError(
                "MCP_IDENTITY_MISMATCH",
                "MCP observation identity mismatch",
            )
        self._observations.append(observation)
        return _agent_observation(observation, trend)

    def submit_action(
        self,
        request: GatewayActionRequest,
        *,
        timeout_seconds: float,
    ) -> GatewayActionResult:
        data = self._call(
            "submit_action",
            ActionRequest(
                runId=request.run_id,
                decisionId=request.decision_id,
                observationSequence=request.observation_sequence,
                idempotencyKey=request.idempotency_key,
                setpointC=request.setpoint_c,
                controlSource=request.control_source,
                energyEvidence=request.energy_evidence,
                comfortEvidence=request.comfort_evidence,
                fallbackTrigger=request.fallback_trigger,
            ),
            ToolResponse[ActionData],
            timeout_seconds=timeout_seconds,
        )
        try:
            result = GatewayActionResult(
                run_id=data.runId,
                decision_id=data.decisionId,
                observation_sequence=data.observationSequence,
                idempotency_key=data.idempotencyKey,
                requested_setpoint_c=data.requestedSetpointC,
                authorized_setpoint_c=data.authorizedSetpointC,
                control_source=data.controlSource,
                authorization_reason_code=data.authorizationReasonCode,
                accepted=data.accepted,
                cached=data.cached,
            )
        except ValidationError as error:
            raise McpGatewayError(
                "MCP_RESPONSE_INVALID",
                "MCP action response is invalid",
            ) from error
        self._action_requests.append(request)
        self._action_results.append(result)
        return result

    def status(self, *, run_id: str) -> GatewayStatus:
        data = self._call(
            "get_session_status",
            RunRequest(runId=run_id),
            ToolResponse[StatusData],
            timeout_seconds=3.0,
        )
        try:
            return GatewayStatus.model_validate(
                {"run_id": data.runId, "status": data.status}
            )
        except ValidationError as error:
            raise McpGatewayError(
                "MCP_RESPONSE_INVALID",
                "MCP status response is invalid",
            ) from error

    def summary(self, *, run_id: str) -> GatewaySummary:
        data = self._call(
            "get_run_summary",
            RunRequest(runId=run_id),
            ToolResponse[SummaryData],
            timeout_seconds=3.0,
        )
        try:
            return GatewaySummary.model_validate(
                {
                    "run_id": data.runId,
                    "status": data.status,
                    "actions_applied": data.actionsApplied,
                }
            )
        except ValidationError as error:
            raise McpGatewayError(
                "MCP_RESPONSE_INVALID",
                "MCP summary response is invalid",
            ) from error

    def stop(self, *, run_id: str, timeout_seconds: float) -> None:
        data = self._call(
            "stop_simulation",
            StopRequest(runId=run_id, timeoutSeconds=timeout_seconds),
            ToolResponse[StopData],
            timeout_seconds=max(0.001, timeout_seconds),
        )
        if data.runId != run_id:
            raise McpGatewayError("MCP_IDENTITY_MISMATCH", "MCP stop identity mismatch")

    def reset(self, *, run_id: str) -> None:
        data = self._call(
            "reset_simulation",
            ResetRequest(runId=run_id),
            ToolResponse[ResetData],
            timeout_seconds=3.0,
        )
        if data.runId != run_id or not data.reset:
            raise McpGatewayError("MCP_IDENTITY_MISMATCH", "MCP reset identity mismatch")


def _agent_observation(
    observation: ObservationData,
    trend: TrendData,
) -> AgentObservation:
    zones = tuple(
        ZoneSnapshot(
            zone_id=zone.zone,
            temperature_c=zone.temperatureC,
            pmv=zone.pmv,
            occupancy_people=zone.occupancyPeople,
        )
        for zone in observation.zones
    )
    envelope = ObservationEnvelope(
        run_id=observation.runId,
        decision_id=observation.decisionId,
        sequence=observation.sequence,
        observed_at_utc=_utc_now(),
        snapshot=ObservationSnapshot(
            current_setpoint_c=observation.coolingScheduleValueC,
            zones=zones,
            temperature_unit=observation.units.temperature,
            pmv_unit=observation.units.pmv,
            occupancy_unit=observation.units.occupancy,
        ),
    )
    samples = tuple(
        sample
        for item in trend.samples[-12:]
        if (sample := _trend_sample(item)) is not None
    )
    return AgentObservation(
        envelope=envelope,
        outdoor_dry_bulb_c=observation.outdoorDryBulbC,
        hvac_electricity_j=observation.hvacElectricityJ,
        trend=samples,
    )


def _trend_sample(observation: ObservationData) -> TrendSample | None:
    if not (
        math.isfinite(observation.coolingScheduleValueC)
        and 22.0 <= observation.coolingScheduleValueC <= 28.0
    ):
        return None
    occupied_pmv = [
        zone.pmv for zone in observation.zones if zone.occupancyPeople > 0.0
    ]
    return TrendSample(
        outdoor_dry_bulb_c=observation.outdoorDryBulbC,
        cooling_setpoint_c=observation.coolingScheduleValueC,
        hvac_electricity_j=observation.hvacElectricityJ,
        occupied_pmv_min=min(occupied_pmv) if occupied_pmv else None,
        occupied_pmv_max=max(occupied_pmv) if occupied_pmv else None,
    )


__all__ = ["InProcessFastMcpGateway"]
