# LLM-001 Structured Advisory Provider

## Authority boundary

`bms_agent.llm` produces typed advice only. It imports no control, MCP, simulation,
session, or LangGraph module and has no action or actuator operation. A successful
result means only that local inference returned JSON matching the requested role
schema. Deterministic validation remains authoritative, including when model wording
or PMV semantics are wrong.

`SAFE-004` therefore does not block this package: no provider result is an
authorization, fallback action, or simulation write.

## Contracts

All public Pydantic contracts are frozen, strict, and reject extra fields:

- `EnergyProposal`: bounded candidate setpoint, expected energy effect, confidence,
  and a reason of at most 48 characters.
- `ComfortAssessment`: comfort state, setpoint direction, PMV risk, and a reason of
  at most 48 characters.
- `SupervisorDecision`: accept/revise/abstain disposition, optional bounded
  setpoint, conflict flag, and separate energy/comfort evidence. An abstention must
  use a null setpoint; accept/revise must include one. Each evidence value is limited
  to 28 characters on both the wire and internal contract.
- `AdvisoryResult[T]`: stable status, role/schema/model identity, typed output or
  controlled failure, attempt/correction/fallback metadata, and total wall time.

The provider enforces exact role/schema pairing before inference:

| Role | Required schema |
|---|---|
| `energy` | `EnergyProposal` |
| `comfort` | `ComfortAssessment` |
| `supervisor` | `SupervisorDecision` |

Stable provider statuses are `success`, `timeout`, `unavailable`, `model_missing`,
and `malformed`. Failed results never carry advisory output.

### Supervisor wire contract

The Supervisor keeps descriptive internal Python field names while using a compact
JSON wire representation:

| Internal field | JSON wire key |
|---|---|
| `disposition` | `decision` |
| `proposed_setpoint_c` | `setpoint_c` |
| `conflict` | `conflict` |
| `energy_evidence` | `energy` |
| `comfort_evidence` | `comfort` |

`model_json_schema(by_alias=True)` exposes exactly `decision`, `setpoint_c`,
`conflict`, `energy`, and `comfort` as required properties, with
`additionalProperties: false`. The provider validates response JSON with aliases
enabled and internal field names disabled, so long internal names are rejected on
the wire. Internal code may still construct the model using descriptive field names.
Default `model_dump()` retains those internal names, while
`model_dump(by_alias=True)` produces the compact wire keys.

## Ollama request

The current local adapter uses Ollama Python 0.6.2:

- loopback HTTP(S) host only; credentials and URL paths are rejected;
- `Client(host=..., timeout=...)`;
- `chat(..., stream=False, think=False)`;
- Pydantic `model_json_schema()` passed through `format`;
- `temperature=0`, `seed=0`, and `num_predict=64`;
- bounded `keep_alive=10m`;
- compact system/user messages with explicit positive/negative PMV semantics;
- a Supervisor instruction naming the five exact wire keys and requiring both
  evidence strings to be at most 28 characters.

Production timeout defaults to 8 seconds per client attempt and configuration is
rejected above 30 seconds. `AdvisoryResult.wall_duration_ms` includes model discovery,
time waiting for the shared sequential inference lock, all attempts, and audit writes.
The graph must use that total plus its remaining session deadline to decide whether to
call another advisory role.

Environment settings are:

```text
OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:4b-instruct
OLLAMA_FALLBACK_MODEL=qwen3:1.7b
BMS_LLM_TIMEOUT_SECONDS=8
BMS_LLM_KEEP_ALIVE=10m
BMS_LLM_AUDIT_PATH=./runs/llm-attempts.jsonl
```

Keep-alive accepts integer seconds or minutes from zero through 30 minutes.

## Model selection and bounded failure

The provider lists already-installed model tags; it has no pull/install API.

1. Use `qwen3:4b-instruct` when installed.
2. Otherwise use configured `qwen3:1.7b` only when that exact tag is already listed.
3. If neither is listed, return `model_missing`.
4. If the selected primary disappears between discovery and chat, switch once to the
   already-listed fallback.
5. Timeout/unavailable signals are returned immediately; the same local endpoint is
   not blindly retried.

Malformed `message.content` is validated with `model_validate_json`. The provider may
make exactly one correction request and never includes the raw malformed output in
that request or audit. Correction state is global across a primary-to-fallback switch.
The absolute chat-attempt maximum is three: initial, one correction, and at most one
fallback transition. The edge case initial-malformed → correction-model-missing →
fallback-malformed terminates as `malformed` after three attempts.

## Sequential inference and audit

A process-wide lock serializes discovery, primary/fallback selection, chat, correction,
and append-only audit writes. Every discovery failure or chat attempt appends one
compact JSON object containing:

- UTC timestamp, role, schema, and exact model;
- wall and Ollama API durations;
- prompt and generated token counts;
- correction index and controlled outcome.

Prompts, malformed content, raw model output, and thinking are never written to this
metadata log. The generated log belongs under ignored `runs/` by default.

The provider depends on a small `ChatClient` protocol rather than Ollama response
classes. A future remote open-source client can implement `list_models()` and `chat()`
without changing graph role code or advisory result contracts.

## Verification

`tests/test_llm_provider.py` covers all three schemas, frozen/extra-forbidden behavior,
role/schema mismatches, exact Ollama request settings, environment/config bounds,
primary/fallback selection, timeout/unavailable/model-missing mapping, one correction,
the three-attempt cross-model edge, audit redaction/metadata, loopback enforcement,
dependency error normalization, and concurrent-call serialization. Supervisor
regressions additionally cover alias schema generation and round-trip mapping,
internal-name versus wire-name behavior, missing/extra keys, evidence length, recovery
from the previously observed truncated response, and fail-closed repeated malformed
responses.

The SAFE-006 rework smoke used the unchanged already-installed
`qwen3:4b-instruct` with no pull, `64` output tokens, and an explicit 30-second
smoke-only timeout. The fresh Energy/Comfort/Supervisor sequence succeeded on the
first attempt for every role. Five further consecutive Supervisor calls also
succeeded on their first attempt (5/5), with no correction or fallback. Some evidence
text was semantically thin or inconsistent despite schema validity; it remains
advisory and must be logged and checked by deterministic validation, never trusted as
control authority. Compact evidence is in
`evidence/llm001/structured-provider-smoke.v2.json`; redacted attempt metadata remains
in the ignored `.cache/validation/llm001` directory. The earlier v1 smoke is retained
for audit history.
