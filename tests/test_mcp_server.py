from __future__ import annotations

import asyncio
import uuid
from typing import Protocol, TypeVar, cast

import pytest
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ValidationError

from bms_agent.cli import project_root
from bms_agent.control.safety import ValidationReasonCode
from bms_agent.mcp_server.server import (
    ActionData,
    ActionRequest,
    AwaitObservationRequest,
    ConstraintsData,
    ErrorInspectionData,
    ObservationData,
    ResetData,
    RunRequest,
    SessionRegistry,
    StartData,
    StartRequest,
    StatusData,
    StopData,
    StopRequest,
    SummaryData,
    ToolResponse,
    TrendData,
    TrendRequest,
    build_server,
)

DataT = TypeVar("DataT", bound=BaseModel)


class ListedTool(Protocol):
    name: str
    inputSchema: dict[str, object]
    outputSchema: dict[str, object] | None


class ActionAuditRequest(BaseModel):
    runId: str
    controlSource: str
    energyEvidence: str | None = None
    fallbackTrigger: str | None = None


class ActionAuditRecord(BaseModel):
    tool: str
    request: ActionAuditRequest


async def _call(
    server: FastMCP[object],
    name: str,
    request: BaseModel,
    response_model: type[ToolResponse[DataT]],
) -> ToolResponse[DataT]:
    raw: object = await server.call_tool(
        name,
        {"request": request.model_dump(mode="json")},
    )
    if isinstance(raw, tuple):
        assert len(raw) == 2
        raw = raw[1]
    return response_model.model_validate(raw)


def test_registry_returns_structured_errors_without_a_session() -> None:
    registry = SessionRegistry(project_root())

    response = registry.status(RunRequest(runId="missing"))

    assert response.ok is False
    assert response.data is None
    assert response.error is not None
    assert response.error.code == "NO_SESSION"
    assert response.error.retryable is False
    assert "Traceback" not in response.error.message


def test_action_source_contract_is_typed_and_bounded() -> None:
    common: dict[str, object] = {
        "runId": "run",
        "decisionId": "decision",
        "observationSequence": 1,
        "idempotencyKey": "key",
        "setpointC": 24.0,
    }

    def action(**changes: object) -> ActionRequest:
        return ActionRequest.model_validate({**common, **changes})

    with pytest.raises(ValidationError, match="separate energy and comfort"):
        action(controlSource="advisory_proposal")
    with pytest.raises(ValidationError, match="cannot carry advisory"):
        action(
            controlSource="deterministic_fallback",
            energyEvidence="not allowed",
        )
    with pytest.raises(ValidationError, match="not a valid fallback"):
        action(
            controlSource="deterministic_fallback",
            fallbackTrigger=ValidationReasonCode.APPROVED,
        )
    with pytest.raises(ValidationError, match="at most 512"):
        action(
            controlSource="advisory_proposal",
            energyEvidence="x" * 513,
            comfortEvidence="bounded",
        )

    no_trigger = action(controlSource="deterministic_fallback")
    assert no_trigger.fallbackTrigger is None


def test_fastmcp_lists_typed_structured_tools() -> None:
    server = build_server(SessionRegistry(project_root()))

    async def list_typed_tools() -> list[ListedTool]:
        raw: object = await server.list_tools()
        assert isinstance(raw, list)
        return cast(list[ListedTool], raw)

    tools = asyncio.run(list_typed_tools())
    by_name = {tool.name: tool for tool in tools}

    assert set(by_name) == {
        "start_simulation",
        "await_observation",
        "latest_observation",
        "get_recent_trend",
        "get_control_constraints",
        "submit_action",
        "get_session_status",
        "inspect_simulation_errors",
        "stop_simulation",
        "reset_simulation",
        "get_run_summary",
    }
    action_schema = by_name["submit_action"].inputSchema
    input_definitions = cast(dict[str, object], action_schema["$defs"])
    request_schema = cast(dict[str, object], input_definitions["ActionRequest"])
    assert request_schema["additionalProperties"] is False
    required_fields = cast(list[str], request_schema["required"])
    assert set(required_fields) == {
        "runId",
        "decisionId",
        "observationSequence",
        "idempotencyKey",
        "setpointC",
        "controlSource",
    }
    properties = cast(dict[str, object], request_schema["properties"])
    assert {"energyEvidence", "comfortEvidence", "fallbackTrigger"} <= set(properties)
    output_schema = by_name["submit_action"].outputSchema
    assert output_schema is not None
    output_definitions = cast(dict[str, object], output_schema["$defs"])
    assert "ActionData" in output_definitions
    assert "Units" in output_definitions


def test_real_fastmcp_call_tool_reaches_one_live_actuator() -> None:
    async def exercise() -> None:
        run_id = f"pytest-mcp-live-{uuid.uuid4().hex[:10]}"
        registry = SessionRegistry(project_root())
        server = build_server(registry)

        missing = await _call(
            server,
            "get_session_status",
            RunRequest(runId=run_id),
            ToolResponse[StatusData],
        )
        assert missing.error is not None and missing.error.code == "NO_SESSION"

        started = await _call(
            server,
            "start_simulation",
            StartRequest(
                runId=run_id,
                maxWeatherTimesteps=96,
                actionWaitSeconds=0.25,
            ),
            ToolResponse[StartData],
        )
        assert started.ok is True
        assert started.data is not None and started.data.runId == run_id

        second_start = await _call(
            server,
            "start_simulation",
            StartRequest(runId=f"{run_id}-second", maxWeatherTimesteps=1),
            ToolResponse[StartData],
        )
        assert second_start.error is not None
        assert second_start.error.code == "ACTIVE_SESSION_EXISTS"

        no_latest = await _call(
            server,
            "latest_observation",
            RunRequest(runId=run_id),
            ToolResponse[ObservationData],
        )
        assert no_latest.error is not None
        assert no_latest.error.code == "NO_OBSERVATION"
        unavailable = await _call(
            server,
            "submit_action",
            ActionRequest(
                runId=run_id,
                decisionId="not-yet-available",
                observationSequence=1,
                idempotencyKey="no-safety-context",
                setpointC=24.0,
                controlSource="deterministic_fallback",
            ),
            ToolResponse[ActionData],
        )
        assert unavailable.error is not None
        assert unavailable.error.code == "SAFETY_CONTEXT_UNAVAILABLE"

        constraints = await _call(
            server,
            "get_control_constraints",
            RunRequest(runId=run_id),
            ToolResponse[ConstraintsData],
        )
        assert constraints.data is not None
        assert constraints.data.coolingSetpointMinC == 22.0
        assert constraints.data.coolingSetpointMaxC == 28.0
        assert constraints.data.units.temperature == "degC"

        observation: ObservationData | None = None
        for _ in range(30):
            awaited = await _call(
                server,
                "await_observation",
                AwaitObservationRequest(runId=run_id, timeoutSeconds=2),
                ToolResponse[ObservationData],
            )
            if awaited.data is not None and any(
                zone.occupancyPeople > 0 for zone in awaited.data.zones
            ):
                observation = awaited.data
                break
        assert observation is not None

        latest = await _call(
            server,
            "latest_observation",
            RunRequest(runId=run_id),
            ToolResponse[ObservationData],
        )
        assert latest.data is not None
        assert latest.data.sequence == observation.sequence

        trend = await _call(
            server,
            "get_recent_trend",
            TrendRequest(runId=run_id, sampleCount=3),
            ToolResponse[TrendData],
        )
        assert trend.data is not None
        assert len(trend.data.samples) == 3

        stale = await _call(
            server,
            "submit_action",
            ActionRequest(
                runId=run_id,
                decisionId=observation.decisionId,
                observationSequence=observation.sequence + 1,
                idempotencyKey="stale-action",
                setpointC=observation.coolingScheduleValueC,
                controlSource="advisory_proposal",
                energyEvidence="Stale read must not authorize energy control.",
                comfortEvidence="Stale read must not authorize comfort control.",
            ),
            ToolResponse[ActionData],
        )
        assert stale.error is not None and stale.error.code == "SAFETY_REJECTED"
        assert stale.error.details["reasonCode"] == "STALE_OBSERVATION"

        target_setpoint = observation.coolingScheduleValueC + 0.5
        substitution = await _call(
            server,
            "submit_action",
            ActionRequest(
                runId=run_id,
                decisionId=observation.decisionId,
                observationSequence=observation.sequence,
                idempotencyKey="substituted-action",
                setpointC=25.0,
                controlSource="advisory_proposal",
                energyEvidence="Caller claims an energy benefit.",
                comfortEvidence="Caller claims comfort remains safe.",
            ),
            ToolResponse[ActionData],
        )
        assert substitution.error is not None
        assert substitution.error.code == "SAFETY_REJECTED"
        assert substitution.error.details["reasonCode"] == "RATE_LIMIT_EXCEEDED"

        fallback_mismatch = await _call(
            server,
            "submit_action",
            ActionRequest(
                runId=run_id,
                decisionId=observation.decisionId,
                observationSequence=observation.sequence,
                idempotencyKey="fallback-mismatch",
                setpointC=target_setpoint + 0.1,
                controlSource="deterministic_fallback",
                fallbackTrigger=ValidationReasonCode.COLD_CORRECTION_REQUIRED,
            ),
            ToolResponse[ActionData],
        )
        assert fallback_mismatch.error is not None
        assert fallback_mismatch.error.code == "FALLBACK_MISMATCH"

        action = ActionRequest(
            runId=run_id,
            decisionId=observation.decisionId,
            observationSequence=observation.sequence,
            idempotencyKey="accepted-action",
            setpointC=target_setpoint,
            controlSource="advisory_proposal",
            energyEvidence="A bounded 0.5 C raise reduces cooling demand.",
            comfortEvidence="All occupied zones are cold; raise toward neutral.",
        )
        accepted = await _call(
            server, "submit_action", action, ToolResponse[ActionData]
        )
        replay = await _call(server, "submit_action", action, ToolResponse[ActionData])
        conflict = await _call(
            server,
            "submit_action",
            action.model_copy(update={"energyEvidence": "Changed evidence payload."}),
            ToolResponse[ActionData],
        )
        source_conflict = await _call(
            server,
            "submit_action",
            ActionRequest(
                runId=run_id,
                decisionId=observation.decisionId,
                observationSequence=observation.sequence,
                idempotencyKey="accepted-action",
                setpointC=target_setpoint,
                controlSource="deterministic_fallback",
            ),
            ToolResponse[ActionData],
        )
        duplicate = await _call(
            server,
            "submit_action",
            action.model_copy(update={"idempotencyKey": "duplicate-action"}),
            ToolResponse[ActionData],
        )

        assert accepted.data is not None and accepted.data.cached is False
        assert accepted.data.authorizedSetpointC == target_setpoint
        assert accepted.data.authorizationReasonCode == "APPROVED"
        assert replay.data is not None and replay.data.cached is True
        assert conflict.error is not None
        assert conflict.error.code == "IDEMPOTENCY_CONFLICT"
        assert source_conflict.error is not None
        assert source_conflict.error.code == "IDEMPOTENCY_CONFLICT"
        assert duplicate.error is not None
        assert duplicate.error.code == "DUPLICATE_ACTION"

        while True:
            status = await _call(
                server,
                "get_session_status",
                RunRequest(runId=run_id),
                ToolResponse[StatusData],
            )
            assert status.data is not None
            if status.data.status in {"completed", "cancelled", "failed"}:
                break
            await asyncio.sleep(0.05)

        inspected = await _call(
            server,
            "inspect_simulation_errors",
            RunRequest(runId=run_id),
            ToolResponse[ErrorInspectionData],
        )
        assert inspected.data is not None
        assert inspected.data.severeOrFatal == []

        summary = await _call(
            server,
            "get_run_summary",
            RunRequest(runId=run_id),
            ToolResponse[SummaryData],
        )
        assert summary.data is not None
        assert summary.data.status == "completed"
        assert summary.data.actionsApplied == 1
        assert summary.data.latestScheduleValueC == target_setpoint
        assert summary.data.latestFiveZoneSetpointsC == [target_setpoint] * 5

        audit_records = [
            ActionAuditRecord.model_validate_json(line)
            for line in (project_root() / "runs" / "mcp-tool-audit.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if f'"runId": "{run_id}"' in line and '"tool": "submit_action"' in line
        ]
        assert audit_records
        assert all(record.request.controlSource for record in audit_records)
        assert any(
            record.request.energyEvidence
            == "A bounded 0.5 C raise reduces cooling demand."
            for record in audit_records
        )
        assert any(
            record.request.fallbackTrigger == "COLD_CORRECTION_REQUIRED"
            for record in audit_records
        )

        stopped = await _call(
            server,
            "stop_simulation",
            StopRequest(runId=run_id, timeoutSeconds=1),
            ToolResponse[StopData],
        )
        assert stopped.data is not None and stopped.data.stopped is True

        reset = await _call(
            server,
            "reset_simulation",
            RunRequest(runId=run_id),
            ToolResponse[ResetData],
        )
        assert reset.data is not None and reset.data.reset is True

    asyncio.run(exercise())
