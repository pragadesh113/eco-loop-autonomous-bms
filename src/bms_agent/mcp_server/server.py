"""Typed, local-only FastMCP tools for one active EnergyPlus session."""

from __future__ import annotations

import argparse
import json
import threading
from collections import deque
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Generic, Literal, TypeVar

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from bms_agent.cli import project_root
from bms_agent.simulation.baseline import (
    COMFORT_LOWER,
    COMFORT_UPPER,
    EMERGENCY_LOWER,
    EMERGENCY_UPPER,
)
from bms_agent.simulation.session import (
    SETPOINT_MAX_C,
    SETPOINT_MIN_C,
    ActionRejected,
    ControlObservation,
    SessionError,
    SessionStatus,
    SimulationSession,
    default_session_config,
)

DataT = TypeVar("DataT", bound=BaseModel)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolError(Contract):
    code: str
    message: str
    retryable: bool
    details: dict[str, object] = Field(default_factory=dict)


class ToolResponse(Contract, Generic[DataT]):
    ok: bool
    data: DataT | None = None
    error: ToolError | None = None


class StartRequest(Contract):
    runId: str
    mode: Literal["controlled"] = "controlled"
    maxWeatherTimesteps: int = Field(default=672, ge=1, le=672)
    actionWaitSeconds: float = Field(default=10.0, ge=0.01, le=30.0)


class RunRequest(Contract):
    runId: str


class AwaitObservationRequest(RunRequest):
    timeoutSeconds: float = Field(default=10.0, ge=0.0, le=30.0)


class TrendRequest(RunRequest):
    sampleCount: int = Field(default=12, ge=1, le=96)


class ActionRequest(RunRequest):
    decisionId: str
    observationSequence: int = Field(ge=1)
    idempotencyKey: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    setpointC: float


class StopRequest(RunRequest):
    timeoutSeconds: float = Field(default=5.0, ge=0.0, le=30.0)


class ResetRequest(RunRequest):
    pass


class StartData(Contract):
    runId: str
    mode: Literal["controlled"]
    status: str
    runDirectory: str


class Units(Contract):
    temperature: Literal["degC"] = "degC"
    pmv: Literal["dimensionless"] = "dimensionless"
    ppd: Literal["percent"] = "percent"
    occupancy: Literal["people"] = "people"
    energy: Literal["joule"] = "joule"


class ZoneData(Contract):
    zone: str
    temperatureC: float
    pmv: float
    ppdPercent: float
    occupancyPeople: float
    coolingSetpointC: float


class ObservationData(Contract):
    runId: str
    decisionId: str
    sequence: int
    month: int
    day: int
    hour: int
    minute: int
    outdoorDryBulbC: float
    coolingScheduleValueC: float
    hvacElectricityJ: float
    zones: list[ZoneData]
    units: Units = Field(default_factory=Units)


class TrendData(Contract):
    runId: str
    samples: list[ObservationData]
    units: Units = Field(default_factory=Units)


class ConstraintsData(Contract):
    runId: str
    occupiedPmvLower: float
    occupiedPmvUpper: float
    emergencyPmvLower: float
    emergencyPmvUpper: float
    coolingSetpointMinC: float
    coolingSetpointMaxC: float
    decisionIntervalMinutes: int
    units: Units = Field(default_factory=Units)


class ActionData(Contract):
    runId: str
    decisionId: str
    observationSequence: int
    idempotencyKey: str
    requestedSetpointC: float
    accepted: bool
    cached: bool
    units: Units = Field(default_factory=Units)


class StatusData(Contract):
    runId: str
    status: str
    exitCode: int | None
    weatherTimesteps: int
    observationCount: int
    actionCount: int
    failure: str | None


class ErrorInspectionData(Contract):
    runId: str
    status: str
    failure: str | None
    warnings: list[str]
    severeOrFatal: list[str]


class StopData(Contract):
    runId: str
    stopped: bool
    status: str


class ResetData(Contract):
    runId: str
    reset: bool


class SummaryData(Contract):
    runId: str
    status: str
    exitCode: int | None
    weatherTimesteps: int
    hourlyObservations: int
    actionsApplied: int
    latestScheduleValueC: float | None
    latestFiveZoneSetpointsC: list[float]
    units: Units = Field(default_factory=Units)


def _observation_data(observation: ControlObservation) -> ObservationData:
    return ObservationData(
        runId=observation.run_id,
        decisionId=observation.decision_id,
        sequence=observation.sequence,
        month=observation.month,
        day=observation.day,
        hour=observation.hour,
        minute=observation.minute,
        outdoorDryBulbC=observation.outdoor_dry_bulb_c,
        coolingScheduleValueC=observation.cooling_schedule_value_c,
        hvacElectricityJ=observation.hvac_electricity_j,
        zones=[
            ZoneData(
                zone=zone.zone,
                temperatureC=zone.temperature_c,
                pmv=zone.pmv,
                ppdPercent=zone.ppd_percent,
                occupancyPeople=zone.occupancy_people,
                coolingSetpointC=zone.cooling_setpoint_c,
            )
            for zone in observation.zones
        ],
    )


class SessionRegistry:
    """One-active-session registry with idempotent writes and append-only tool audit."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.RLock()
        self._session: SimulationSession | None = None
        self._latest: ControlObservation | None = None
        self._trend: deque[ControlObservation] = deque(maxlen=96)
        self._idempotency: dict[str, tuple[tuple[str, int, float], ActionData]] = {}
        self._audit_path = root / "runs" / "mcp-tool-audit.jsonl"

    def _audit(
        self,
        tool: str,
        request: BaseModel,
        *,
        ok: bool,
        error_code: str | None = None,
    ) -> None:
        record = {
            "schemaVersion": "1.0",
            "timestampUtc": datetime.now(UTC).isoformat(),
            "tool": tool,
            "request": request.model_dump(mode="json"),
            "ok": ok,
            "errorCode": error_code,
        }
        with self._lock:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self._audit_path.open("a", encoding="utf-8", newline="\n") as file_handle:
                file_handle.write(json.dumps(record, sort_keys=True) + "\n")

    def _make_error(
        self,
        tool: str,
        request: BaseModel,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, object] | None = None,
    ) -> ToolError:
        self._audit(tool, request, ok=False, error_code=code)
        return ToolError(
            code=code,
            message=message,
            retryable=retryable,
            details=details or {},
        )

    def _required_session(
        self, tool: str, request: RunRequest
    ) -> tuple[SimulationSession | None, ToolError | None]:
        session = self._session
        if session is None:
            return None, self._make_error(
                tool, request, "NO_SESSION", "No session is registered."
            )
        if session.run_id != request.runId:
            return None, self._make_error(
                tool,
                request,
                "RUN_ID_MISMATCH",
                "Requested runId does not match the registered session.",
            )
        return session, None

    def start(self, request: StartRequest) -> ToolResponse[StartData]:
        tool = "start_simulation"
        with self._lock:
            if self._session is not None and self._session.status not in {
                SessionStatus.COMPLETED,
                SessionStatus.CANCELLED,
                SessionStatus.FAILED,
            }:
                return ToolResponse(
                    ok=False,
                    error=self._make_error(
                        tool,
                        request,
                        "ACTIVE_SESSION_EXISTS",
                        "Only one active building session is allowed per process.",
                    ),
                )
            try:
                config = default_session_config(self.root)
                config = replace(
                    config,
                    max_weather_timesteps=request.maxWeatherTimesteps,
                    action_wait_seconds=request.actionWaitSeconds,
                )
                session = SimulationSession(config, request.runId)
                session.start()
            except (SessionError, OSError, ValueError) as error:
                return ToolResponse(
                    ok=False,
                    error=self._make_error(
                        tool, request, "START_FAILED", str(error)
                    ),
                )
            self._session = session
            self._latest = None
            self._trend.clear()
            self._idempotency.clear()
            data = StartData(
                runId=request.runId,
                mode="controlled",
                status=session.status.value,
                runDirectory=str(session.run_dir),
            )
            self._audit(tool, request, ok=True)
            return ToolResponse(ok=True, data=data)

    def await_observation(
        self, request: AwaitObservationRequest
    ) -> ToolResponse[ObservationData]:
        tool = "await_observation"
        with self._lock:
            session, error = self._required_session(tool, request)
        if error is not None:
            return ToolResponse(ok=False, error=error)
        assert session is not None
        observation = session.await_observation(request.timeoutSeconds)
        if observation is None:
            return ToolResponse(
                ok=False,
                error=self._make_error(
                    tool,
                    request,
                    "OBSERVATION_TIMEOUT",
                    "No observation became available within the bounded timeout.",
                    retryable=True,
                ),
            )
        with self._lock:
            self._latest = observation
            self._trend.append(observation)
            data = _observation_data(observation)
            self._audit(tool, request, ok=True)
            return ToolResponse(ok=True, data=data)

    def latest_observation(self, request: RunRequest) -> ToolResponse[ObservationData]:
        tool = "latest_observation"
        with self._lock:
            _, error = self._required_session(tool, request)
            if error is not None:
                return ToolResponse(ok=False, error=error)
            if self._latest is None:
                return ToolResponse(
                    ok=False,
                    error=self._make_error(
                        tool,
                        request,
                        "NO_OBSERVATION",
                        "No observation has been received yet.",
                        retryable=True,
                    ),
                )
            data = _observation_data(self._latest)
            self._audit(tool, request, ok=True)
            return ToolResponse(ok=True, data=data)

    def recent_trend(self, request: TrendRequest) -> ToolResponse[TrendData]:
        tool = "get_recent_trend"
        with self._lock:
            _, error = self._required_session(tool, request)
            if error is not None:
                return ToolResponse(ok=False, error=error)
            samples = list(self._trend)[-request.sampleCount :]
            data = TrendData(
                runId=request.runId,
                samples=[_observation_data(item) for item in samples],
            )
            self._audit(tool, request, ok=True)
            return ToolResponse(ok=True, data=data)

    def constraints(self, request: RunRequest) -> ToolResponse[ConstraintsData]:
        tool = "get_control_constraints"
        with self._lock:
            _, error = self._required_session(tool, request)
            if error is not None:
                return ToolResponse(ok=False, error=error)
            data = ConstraintsData(
                runId=request.runId,
                occupiedPmvLower=COMFORT_LOWER,
                occupiedPmvUpper=COMFORT_UPPER,
                emergencyPmvLower=EMERGENCY_LOWER,
                emergencyPmvUpper=EMERGENCY_UPPER,
                coolingSetpointMinC=SETPOINT_MIN_C,
                coolingSetpointMaxC=SETPOINT_MAX_C,
                decisionIntervalMinutes=60,
            )
            self._audit(tool, request, ok=True)
            return ToolResponse(ok=True, data=data)

    def submit_action(self, request: ActionRequest) -> ToolResponse[ActionData]:
        tool = "submit_action"
        payload = (request.decisionId, request.observationSequence, request.setpointC)
        with self._lock:
            session, error = self._required_session(tool, request)
            if error is not None:
                return ToolResponse(ok=False, error=error)
            cached = self._idempotency.get(request.idempotencyKey)
            if cached is not None:
                cached_payload, cached_data = cached
                if cached_payload != payload:
                    return ToolResponse(
                        ok=False,
                        error=self._make_error(
                            tool,
                            request,
                            "IDEMPOTENCY_CONFLICT",
                            "Idempotency key was already used with a different action payload.",
                        ),
                    )
                data = cached_data.model_copy(update={"cached": True})
                self._audit(tool, request, ok=True)
                return ToolResponse(ok=True, data=data)
            assert session is not None
            try:
                session.submit_action(
                    decision_id=request.decisionId,
                    observation_sequence=request.observationSequence,
                    setpoint_c=request.setpointC,
                )
            except ActionRejected as rejected:
                message = str(rejected)
                code = (
                    "STALE_ACTION"
                    if "stale" in message
                    else "DUPLICATE_ACTION"
                    if "already" in message
                    else "NO_PENDING_ACTION"
                    if "No decision" in message
                    else "INVALID_ACTION"
                )
                return ToolResponse(
                    ok=False,
                    error=self._make_error(
                        tool,
                        request,
                        code,
                        message,
                        retryable=code in {"STALE_ACTION", "NO_PENDING_ACTION"},
                    ),
                )
            data = ActionData(
                runId=request.runId,
                decisionId=request.decisionId,
                observationSequence=request.observationSequence,
                idempotencyKey=request.idempotencyKey,
                requestedSetpointC=request.setpointC,
                accepted=True,
                cached=False,
            )
            self._idempotency[request.idempotencyKey] = (payload, data)
            self._audit(tool, request, ok=True)
            return ToolResponse(ok=True, data=data)

    def status(self, request: RunRequest) -> ToolResponse[StatusData]:
        tool = "get_session_status"
        with self._lock:
            session, error = self._required_session(tool, request)
            if error is not None:
                return ToolResponse(ok=False, error=error)
            assert session is not None
            result = session.result()
            data = StatusData(
                runId=request.runId,
                status=result.status.value,
                exitCode=result.exit_code,
                weatherTimesteps=result.weather_timesteps,
                observationCount=result.observation_count,
                actionCount=result.action_count,
                failure=result.failure,
            )
            self._audit(tool, request, ok=True)
            return ToolResponse(ok=True, data=data)

    def inspect_errors(self, request: RunRequest) -> ToolResponse[ErrorInspectionData]:
        tool = "inspect_simulation_errors"
        with self._lock:
            session, error = self._required_session(tool, request)
            if error is not None:
                return ToolResponse(ok=False, error=error)
            assert session is not None
            error_path = session.run_dir / "eplusout.err"
            warnings: list[str] = []
            severe: list[str] = []
            if error_path.is_file():
                for line in error_path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines():
                    stripped = line.strip()
                    if "** Warning **" in line:
                        warnings.append(stripped)
                    if "** Severe" in line or "** Fatal" in line:
                        severe.append(stripped)
            result = session.result()
            data = ErrorInspectionData(
                runId=request.runId,
                status=result.status.value,
                failure=result.failure,
                warnings=warnings,
                severeOrFatal=severe,
            )
            self._audit(tool, request, ok=True)
            return ToolResponse(ok=True, data=data)

    def stop(self, request: StopRequest) -> ToolResponse[StopData]:
        tool = "stop_simulation"
        with self._lock:
            session, error = self._required_session(tool, request)
            if error is not None:
                return ToolResponse(ok=False, error=error)
            assert session is not None
            stopped = session.shutdown(request.timeoutSeconds)
            if not stopped:
                return ToolResponse(
                    ok=False,
                    error=self._make_error(
                        tool,
                        request,
                        "STOP_TIMEOUT",
                        "Simulation did not stop within the bounded timeout.",
                        retryable=True,
                    ),
                )
            data = StopData(
                runId=request.runId,
                stopped=stopped,
                status=session.status.value,
            )
            self._audit(tool, request, ok=True)
            return ToolResponse(ok=True, data=data)

    def reset(self, request: ResetRequest) -> ToolResponse[ResetData]:
        tool = "reset_simulation"
        with self._lock:
            session, error = self._required_session(tool, request)
            if error is not None:
                return ToolResponse(ok=False, error=error)
            assert session is not None
            if session.status not in {
                SessionStatus.COMPLETED,
                SessionStatus.CANCELLED,
                SessionStatus.FAILED,
            }:
                return ToolResponse(
                    ok=False,
                    error=self._make_error(
                        tool,
                        request,
                        "ACTIVE_RESET_REFUSED",
                        "Active session must be stopped before reset.",
                    ),
                )
            self._session = None
            self._latest = None
            self._trend.clear()
            self._idempotency.clear()
            data = ResetData(runId=request.runId, reset=True)
            self._audit(tool, request, ok=True)
            return ToolResponse(ok=True, data=data)

    def summary(self, request: RunRequest) -> ToolResponse[SummaryData]:
        tool = "get_run_summary"
        with self._lock:
            session, error = self._required_session(tool, request)
            if error is not None:
                return ToolResponse(ok=False, error=error)
            assert session is not None
            result = session.result()
            audits = session.action_audits
            latest = audits[-1] if audits else None
            data = SummaryData(
                runId=request.runId,
                status=result.status.value,
                exitCode=result.exit_code,
                weatherTimesteps=result.weather_timesteps,
                hourlyObservations=result.observation_count,
                actionsApplied=result.action_count,
                latestScheduleValueC=(
                    latest.observed_schedule_value_c if latest is not None else None
                ),
                latestFiveZoneSetpointsC=(
                    list(latest.observed_zone_setpoints_c) if latest is not None else []
                ),
            )
            self._audit(tool, request, ok=True)
            return ToolResponse(ok=True, data=data)


def build_server(registry: SessionRegistry | None = None) -> FastMCP[object]:
    registry = registry or SessionRegistry(project_root())
    mcp: FastMCP[object] = FastMCP(
        "Eco-Loop Building Agents",
        instructions="Local typed EnergyPlus control tools. One active session only.",
        host="127.0.0.1",
        port=8000,
    )

    @mcp.tool(structured_output=True)
    def start_simulation(request: StartRequest) -> ToolResponse[StartData]:
        return registry.start(request)

    @mcp.tool(structured_output=True)
    def await_observation(
        request: AwaitObservationRequest,
    ) -> ToolResponse[ObservationData]:
        return registry.await_observation(request)

    @mcp.tool(structured_output=True)
    def latest_observation(request: RunRequest) -> ToolResponse[ObservationData]:
        return registry.latest_observation(request)

    @mcp.tool(structured_output=True)
    def get_recent_trend(request: TrendRequest) -> ToolResponse[TrendData]:
        return registry.recent_trend(request)

    @mcp.tool(structured_output=True)
    def get_control_constraints(request: RunRequest) -> ToolResponse[ConstraintsData]:
        return registry.constraints(request)

    @mcp.tool(structured_output=True)
    def submit_action(request: ActionRequest) -> ToolResponse[ActionData]:
        return registry.submit_action(request)

    @mcp.tool(structured_output=True)
    def get_session_status(request: RunRequest) -> ToolResponse[StatusData]:
        return registry.status(request)

    @mcp.tool(structured_output=True)
    def inspect_simulation_errors(
        request: RunRequest,
    ) -> ToolResponse[ErrorInspectionData]:
        return registry.inspect_errors(request)

    @mcp.tool(structured_output=True)
    def stop_simulation(request: StopRequest) -> ToolResponse[StopData]:
        return registry.stop(request)

    @mcp.tool(structured_output=True)
    def reset_simulation(request: ResetRequest) -> ToolResponse[ResetData]:
        return registry.reset(request)

    @mcp.tool(structured_output=True)
    def get_run_summary(request: RunRequest) -> ToolResponse[SummaryData]:
        return registry.summary(request)

    _registered_tools = (
        start_simulation,
        await_observation,
        latest_observation,
        get_recent_trend,
        get_control_constraints,
        submit_action,
        get_session_status,
        inspect_simulation_errors,
        stop_simulation,
        reset_simulation,
        get_run_summary,
    )
    _ = _registered_tools
    return mcp


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local Eco-Loop FastMCP server.")
    parser.add_argument(
        "--transport",
        choices=("stdio", "sse", "streamable-http"),
        default="stdio",
    )
    arguments = parser.parse_args()
    build_server().run(arguments.transport)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
