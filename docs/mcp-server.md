# Local FastMCP EnergyPlus Boundary

## Purpose

`bms-mcp` is the only supported external control boundary for an active
`SimulationSession`. It exposes JSON-compatible Pydantic v2 contracts, keeps one
registered building session per process, and binds network transports to
`127.0.0.1`. The default transport is stdio.

The MCP layer does not decide whether a setpoint is safe. It validates message
identity and idempotency, then delegates physical bounds and pending-decision
validation to `SimulationSession`. The LangGraph supervisor remains responsible for
comfort policy, reflection, retries, and fallback selection.

## Start the server

```powershell
uv run bms-mcp
```

The command above uses stdio. For a local development client only:

```powershell
uv run bms-mcp --transport streamable-http
```

HTTP and SSE use the fixed loopback host `127.0.0.1`; the implementation does not
offer a command-line host override.

## Tool catalog

| Tool | Request model | Successful data model |
|---|---|---|
| `start_simulation` | `StartRequest` | `StartData` |
| `await_observation` | `AwaitObservationRequest` | `ObservationData` |
| `latest_observation` | `RunRequest` | `ObservationData` |
| `get_recent_trend` | `TrendRequest` | `TrendData` |
| `get_control_constraints` | `RunRequest` | `ConstraintsData` |
| `submit_action` | `ActionRequest` | `ActionData` |
| `get_session_status` | `RunRequest` | `StatusData` |
| `inspect_simulation_errors` | `RunRequest` | `ErrorInspectionData` |
| `stop_simulation` | `StopRequest` | `StopData` |
| `reset_simulation` | `ResetRequest` | `ResetData` |
| `get_run_summary` | `RunRequest` | `SummaryData` |

Every tool returns:

```json
{
  "ok": true,
  "data": {},
  "error": null
}
```

Failure responses set `ok` to `false`, set `data` to `null`, and return
`error.code`, `error.message`, `error.retryable`, and `error.details`. Raw
tracebacks are not returned.

Temperature, PMV, PPD, occupancy, and energy fields have an accompanying `units`
object. Temperature is `degC`, PMV is dimensionless, PPD is percent, occupancy is
people, and energy is joule.

## Safe action protocol

`submit_action` requires all of:

- `runId`: the active simulation correlation ID.
- `decisionId`: the pending decision from the observation.
- `observationSequence`: the matching monotonic observation sequence.
- `idempotencyKey`: a caller-generated key containing only letters, digits, `.`,
  `_`, or `-`.
- `setpointC`: the requested shared cooling setpoint.

The first accepted request writes the action once. Repeating the same key with the
exact same decision, sequence, and setpoint returns the cached acceptance with
`cached: true`; it never invokes the actuator again. Reusing the key with a changed
payload returns `IDEMPOTENCY_CONFLICT`. A different key cannot reuse an already
consumed decision. Stale sequences and decisions are rejected before actuation.
Clients must inspect the returned error and must not blindly retry actuator writes.

The deterministic session enforces a finite shared setpoint in `22..28 degC`. The
reported comfort target is occupied PMV `[-0.5, +0.5]`, with emergency bounds
`[-1.0, +1.0]`.

## Lifecycle and recovery

Only one non-terminal session may exist in a server process. `reset_simulation`
refuses an active session, so callers must stop it first. Starting a second session
while one is active returns `ACTIVE_SESSION_EXISTS`. A run-ID mismatch never exposes
or controls the registered session.

Expected normalized error codes include:

- `NO_SESSION`, `RUN_ID_MISMATCH`, and `ACTIVE_SESSION_EXISTS`
- `NO_OBSERVATION` and `OBSERVATION_TIMEOUT`
- `STALE_ACTION`, `DUPLICATE_ACTION`, `NO_PENDING_ACTION`, and `INVALID_ACTION`
- `IDEMPOTENCY_CONFLICT`
- `ACTIVE_RESET_REFUSED`
- `STOP_TIMEOUT`
- `START_FAILED`

Every completed tool call appends a compact record to
`runs/mcp-tool-audit.jsonl`. Physical actuator evidence remains in the run-specific
`actions.jsonl`; EnergyPlus diagnostics remain in `eplusout.err` and the other
preserved run artifacts.

## Verification

The contract tests list all FastMCP schemas, exercise normalized no-session
failures, and use the real `FastMCP.call_tool` method against a live one-day
EnergyPlus session. The live test proves one 25 degC action reached the schedule
actuator and all five zone thermostat observations, an exact replay was cached, a
conflicting replay was rejected, and only one action was applied.

Run:

```powershell
uv run ruff check .
uv run pyright
uv run pytest
```

Manual live evidence is preserved in
`evidence/mcp001/live-contract-smoke.v1.json`.
