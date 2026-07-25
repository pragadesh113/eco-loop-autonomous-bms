# AGT-002 Bounded Advisory Agents

## Authority boundary

AGT-002 supplies `AgentGraphRuntime`, a fake-tested implementation of the AGT-001
`GraphRuntime` protocol. Energy, Comfort, and Supervisor are advisory roles only.
They cannot start or stop a simulation, read a `SimulationSession`, call EnergyPlus,
or submit an actuator value. The deterministic control validator remains
authoritative.

All simulation lifecycle, observation, action, status, and summary operations cross
the injected `McpGateway` protocol. The protocol is deliberately typed and narrow.
A concrete FastMCP transport binding is deferred to RUN-001, where the real server
process and client lifecycle can be exercised together. That binding must not expose
server internals to an advisory role or change the contracts below.

## Bounded role contracts

Each role receives a different frozen, strict Pydantic input:

- `EnergyAgentInput`: the current bounded observation and fixed energy unit.
- `ComfortAgentInput`: at most five occupied-zone PMV values, target and emergency
  PMV limits, and the fixed PMV unit.
- `SupervisorAgentInput`: current setpoint, the typed Energy and Comfort outputs,
  revision number, and at most one stable validation reason code.

`AgentObservation` contains the accepted `ObservationEnvelope`, current outdoor
temperature, HVAC electricity, and at most 12 compact trend samples. Setpoints are
bounded to `22..28 degC`; all prompt numbers use fixed precision and fixed units.
Prompt builders are pure and enforce a maximum of 1,200 characters.

Prompts never contain run or decision identifiers, timestamps, paths, exception text,
raw validation evidence, or arbitrary prior model text. Comfort prompts define the
sign convention explicitly: negative PMV is cold, positive PMV is hot, and zero is
neutral. The Supervisor prompt includes only allowlisted upstream enums and numeric
fields. It never includes `EnergyProposal.reason` or `ComfortAssessment.reason`.

The existing provider maps results only into `EnergyProposal`,
`ComfortAssessment`, and `SupervisorDecision`. The runtime round-trips those values
through their exact output schemas before use.

## Canonical supervisor evidence

The Supervisor model's evidence strings are not trusted or forwarded. On a successful
Supervisor response the runtime replaces them with two independently derived bounded
strings:

```text
E:<typed energy effect>:c<fixed-precision confidence>
C:<typed comfort risk>:<typed setpoint direction>
```

Validation therefore receives separate canonical Energy and Comfort evidence tied to
the typed upstream outputs without recursively forwarding model rationale. LLM output
cannot replace deterministic validation evidence or authorize a control action.

## Provider failure and revision behavior

`timeout`, `unavailable`, `model_missing`, and `malformed` are expected advisory
statuses. The first such status latches the decision into a deterministic safe path.
Later roles return typed placeholders and make no provider call. The graph still
performs its two bounded revision transitions; revisions reuse placeholders and do
not call the provider again. The final action is the deterministic fallback.

The stable validation reasons are:

- `ADVISORY_UNAVAILABLE` for any expected provider-status failure;
- `ADVISORY_DEADLINE_EXHAUSTED` when the remaining action window is insufficient;
- `ADVISORY_ABSTAINED` when no usable advisory proposal exists.

All three reasons are invalid-input fallback triggers and resolve to a bounded hold at
the current setpoint. No advisory failure can produce advisory actuation.

Every graph role requests the provider's optional deadline-bound mode. The first
deadline-bound call discovers and caches the exact installed primary or fallback model
under the provider's existing inference lock. Later calls reuse that selection without
another inventory request. A deadline-bound call makes at most one chat attempt: it
does not request schema correction and does not switch models after a chat failure.
Ordinary LLM-001 calls leave this flag false and retain the verified discovery,
one-correction, and bounded fallback behavior.

## SAFE-012 timing rule

The action window is exactly 30 seconds and begins when an observation is received.
An injected monotonic clock supplies the deadline:

```text
deadline = observation_received_monotonic + 30 seconds
```

Role-specific immutable reserves are checked before inference:

| Role | Required remaining time |
|---|---:|
| Energy | 29 seconds |
| Comfort | 20 seconds |
| Supervisor | 11 seconds |
| MCP submit | 3 seconds |

These thresholds budget first-call discovery plus one chat, later cached single chats,
graph transitions, and transport margin. If a role budget is unavailable, the runtime
latches
`ADVISORY_DEADLINE_EXHAUSTED`, avoids further inference, completes the bounded graph
transitions, and submits the fallback while at least three seconds remain. Apply checks
the margin independently: exactly 3.000 seconds is allowed, while 2.999 seconds is
rejected before any gateway invocation. The MCP submit timeout is exactly three
seconds.

Boundary tests use a fake monotonic clock. Exact and below-boundary cases are covered
for every role and submit. The measured fake profiles `5.72/4.37/5.03` seconds and
`5.54/3.65/5.03` seconds both reach all three roles and the advisory path. Elapsed
time, controlled provider failures, and revision stages are covered without sleeping
or live services.

## Action and evaluation contracts

An MCP action request includes typed source, validation evidence, stable trigger,
identity fields, and a canonical SHA-256-derived idempotency key. The runtime sends
exactly one request, never retries submission, and accepts success only when the
returned run, decision, observation sequence, key, source, requested setpoint,
authorized setpoint, and acceptance flag exactly match. A fresh graph submission also
requires `cached=false`. Advisory authorization reason must be exactly `APPROVED`.
A fallback `GraphAction` must carry its typed `FallbackDecision`, and the gateway
authorization reason must equal that decision's exact fallback reason code. Any
contradiction routes to fatal cleanup after the single gateway invocation.

The next observation is fetched once, cached, and becomes the next graph observation.
Evaluation is deterministic:

- measured energy delta is the next-minus-current HVAC electricity converted to kWh;
- comfort is the occupied-zone percentage within the declared PMV target;
- safety is recomputed from measured occupied PMV values.

Reflection records predicted and measured energy and comfort fields separately, plus
explicit match booleans. This is a sample-to-sample diagnostic, not the final
baseline-versus-controlled savings claim required by later measurement features.

## Fake-only verification

`tests/test_agent_prompts.py` and `tests/test_agent_runtime.py` use injected fakes only.
They do not start EnergyPlus or Ollama, open a network connection, or touch an
actuator. They cover prompt bounds and redaction, all provider-status failures at each
role, deadline-bound provider caching and single-chat behavior, measured timing
profiles, two revisions without additional inference, deterministic fallback, exact
MCP result and authorization-metadata verification, rationale containment, no submit
retry, observation caching, and predicted-versus-measured reflection.
