# Safety and Decision Log

Last updated: 2026-07-26 04:34 IST

This is the append-only project record for unsafe conditions, safety-relevant findings,
isolated feature pauses, approval needs, and mitigations. Deterministic safety rules in
`docs/techspec.md` always outrank model output and schedule pressure.

## Current and recently resolved items

Independent rework testing resolved `SAFE-007` through `SAFE-011`. One newly isolated
event-cardinality root, `SAFE-013`, also passed independent testing. `AGT-001` is
approved with no open safety item. `SAFE-012` is now the mandatory first control for
active `AGT-002`.

The second Developer gate passed 94 focused graph tests, 173 control/graph tests, and
266 full-suite tests at 91.96% coverage, with Ruff, strict Pyright, lock, evidence
hashes, JSON, and diff checks clean. No live service was used.

The isolated `SAFE-013` Developer gate passed 100 graph tests, 179 control/graph tests,
and 272 full-suite tests at 91.96% coverage, with Ruff, strict Pyright, lock, evidence,
and diff checks clean.

### SAFE-013 — Normalized changed-field cardinality can exceed event contract

- **Observed:** 2026-07-26 04:27 IST during the final independent `AGT-001` event
  boundary cycle.
- **Affected feature:** `AGT-001`; all dependent features remain paused.
- **Evidence:** Direct event validation, normal/error streams, and all prior redaction
  cases passed. A synthetic update containing 40 individually valid changed-field
  names survived filtering, then raised an uncaught validation failure because
  `GraphEvent.changed_fields` permits at most 32 items.
- **Risk:** A future graph node with a wide but otherwise valid update could terminate
  dashboard-event streaming instead of returning bounded telemetry.
- **Disposition:** Resolved at 2026-07-26 04:34 IST after independent cardinality
  testing.
- **Required approval:** None.
- **Controls:** Deterministically sort safe changed-field names and retain at most the
  first 32 before constructing `GraphEvent`; never relax the contract limit.
- **Verification required:** Independent tests for 0, 1, 32, 33, and 40 valid fields
  must return stable bounded tuples without exception, while unsafe names remain
  filtered and all graph/quality gates stay green.
- **Verification:** The fresh cardinality matrix passed 5/5; mixed filtering, direct
  32-item contract, normal/error stream bounds, production-patch isolation, 100 graph
  tests, and all 272 tests at 91.96% coverage passed independently.

### SAFE-012 — Sequential inference can outlive the simulation action window

- **Observed:** 2026-07-26 03:49 IST during read-only `AGT-002` integration planning.
- **Affected feature:** `AGT-002` must remain gated after `AGT-001` approval until the
  timing control is implemented and fake-tested.
- **Evidence:** The active simulation accepts an action-wait window no larger than
  30 seconds. Energy, Comfort, and Supervisor use one local model sequentially, and
  each provider call can include a bounded correction attempt; their unconstrained
  combined worst-case wall time exceeds the session window.
- **Risk:** A safe model or fallback action can arrive after EnergyPlus has already
  advanced, becoming stale and degrading the closed-loop run.
- **Disposition:** Open preventive design control; no AGT-002 code or live session was
  started.
- **Required approval:** None. Deadline ownership is mandatory in-scope safety work.
- **Controls:** Use one monotonic per-decision deadline derived from the MCP/session
  action-wait window. Before each role, reserve a conservative worst-case single-call
  budget plus communication margin; if insufficient time remains, skip all remaining
  model calls and submit deterministic fallback before expiry. Never extend or retry a
  stale action.
- **Verification required:** Fake-clock tests must cover normal three-role completion,
  timeout during each role, insufficient remaining budget, and MCP latency; every
  over-budget path must make no late advisory write and attempt one timely
  server-recomputed fallback.

### SAFE-011 — Unbounded decision identity leaks into dashboard events

- **Observed:** 2026-07-26 03:49 IST during independent `AGT-001` event testing.
- **Affected feature:** `AGT-001` and all dependents remain paused.
- **Evidence:** A fake observation used a 10,018-character decision ID containing a
  raw-output secret marker. The graph applied once and copied that identifier into
  every subsequent `GraphEvent`; maximum serialized event size grew from 441 to 10,450
  characters and the marker was present.
- **Retest evidence:** Normalized runtime event names are filtered, but direct
  `GraphEvent` validation still accepted and serialized marker-bearing values including
  `secret_node`, `raw_output_node`, `prompt_node`, `secret_field`,
  `raw_output_payload`, and `prompt_value`. The shared `EventFieldName` enforces only
  length/regex, not the marker policy.
- **Risk:** Event values exclude prompts/evidence/setpoints, but an untrusted runtime
  identifier can become an unbounded data and secret-exfiltration channel to the
  dashboard.
- **Disposition:** Resolved at 2026-07-26 04:27 IST. Direct event contracts rejected
  all reserved keyword, whitespace, control, and oversized cases; actual streams
  remained compact and redacted.
- **Required approval:** None.
- **Controls:** Constrain and validate decision identifiers at the observation/runtime
  boundary and independently constrain all run, decision, node, timestamp, and
  changed-field strings in graph input/action/event contracts. Require nonblank,
  short allowlisted run/decision identity. Never truncate and forward hostile identity
  text; reject it, record a generic error, and route to safe cleanup.
- **Verification required:** Oversized, control-character, whitespace, and marker-bearing
  decision IDs must cause zero apply calls, one cleanup call, bounded events, and no
  hostile marker in any event or error.

### SAFE-010 — Checkpointed proposal identity is not rebound to observation

- **Observed:** 2026-07-26 03:49 IST during independent `AGT-001` identity testing.
- **Affected feature:** `AGT-001` and all dependents remain paused.
- **Evidence:** Direct validation state paired observation
  `correct-run/decision-1/sequence-1` with a checkpointed proposal using
  `wrong-run/wrong-decision/sequence-99`. A fake approval produced an authorized
  `GraphAction` with the wrong proposal identity instead of a contract failure.
- **Retest evidence:** Independent single-field mutations now fail closed, but
  coordinated post-validation mutation of all mutable current identity fields still
  resumed to completion and applied once: observation/proposal/action decision ID,
  observation/proposal/action sequence, or graph/observation/proposal/action run ID.
  Cleanup was not invoked because the mutually consistent corrupted fields were treated
  as authoritative.
- **Risk:** Normal supervisor flow derives proposal identity correctly, but corrupted,
  stale, or externally restored checkpoint state is not rebound to the current
  observation before authorization.
- **Disposition:** Resolved at 2026-07-26 04:27 IST. All independent and coordinated
  current-field mutations failed closed against configured thread and retained accepted
  observation anchors; normal resume passed.
- **Required approval:** None.
- **Controls:** Bind run identity to LangGraph's configured checkpoint thread ID, not
  only mutable state. Bind current decision ID and sequence to the last retained
  accepted observation as well as proposal/action fields. A mismatch must route to
  redacted fatal cleanup.
- **Verification required:** Fresh normal and resumed-state tests must mutate each
  identity field independently; every mismatch must apply zero actions, checkpoint a
  controlled contract error, and clean up exactly once.

### SAFE-009 — Internal action-contract failure escapes controlled abort

- **Observed:** 2026-07-26 03:47 IST during the independent `AGT-001` authorization
  matrix.
- **Affected feature:** `AGT-001` remains paused; no dependent work may start.
- **Evidence:** Fake approved proposals containing `inf`, `21°C`, `29°C`, or empty
  evidence caused an uncaught Pydantic `ValidationError` while constructing the
  internal `GraphAction`. `GraphRunner.invoke` returned no controlled failed state and
  made zero safe-cleanup calls.
- **Retest evidence:** A protocol-shaped fake returned
  `SupervisorDecision.model_construct()` without required fields. The supervisor node
  accessed `proposed_setpoint_c` before revalidation, causing raw `AttributeError`, no
  failed checkpoint, zero cleanup calls, and zero apply calls.
- **Extended retest:** Unchecked empty Energy and Comfort model records were stored and
  reached completion with one fake actuation. Plain nonserializable Energy/Comfort
  objects escaped as serializer errors with zero cleanup; plain/empty Supervisor
  objects escaped as raw attribute errors. Malformed observation, fallback, applied,
  evaluation, reflection, and summary returns already fail closed with one cleanup
  call (after the one authorized apply where the failure is downstream).
- **Construction retest:** Expected `ValueError`/`TypeError` construction failures are
  controlled, but a different constructor exception class still escaped with zero
  cleanup. The entire internal construction boundary must normalize any exception
  without exposing its detail.
- **Risk:** Contract construction occurs outside the node's protected runtime boundary,
  so defective validator/proposal data can terminate orchestration without checkpointed
  failure or session cleanup.
- **Disposition:** Resolved at 2026-07-26 04:27 IST. Nineteen fresh role-normalization
  and construction-failure cases passed with controlled redacted cleanup.
- **Required approval:** None.
- **Controls:** Validate and normalize every Energy, Comfort, and Supervisor runtime
  return before checkpoint serialization or attribute access, plus every other
  external/internal contract boundary;
  catch contract-construction failures inside the node, emit only a redacted graph
  contract error, route to `abort_safely`, and never expose raw Pydantic input/errors.
  Strip and require nonblank separate evidence before action construction.
- **Verification required:** Each malformed approved value/evidence case returns a
  controlled failed checkpoint, calls cleanup exactly once, leaks no raw values or
  validation internals through dashboard events, and calls apply zero times.

### SAFE-008 — Contradictory validation record can authorize an action

- **Observed:** 2026-07-26 03:44 IST during independent `AGT-001` testing.
- **Affected feature:** `AGT-001` remains paused in rework with `SAFE-007`; dependent
  agent/runtime features remain paused.
- **Evidence:** A fresh fake returned `approved=true`,
  `reason_code=RATE_LIMIT_EXCEEDED`, and exact validated setpoint `25.5°C` for a
  `24.0°C` observation. The graph constructed and applied that action once, completed,
  and made zero abort calls.
- **Risk:** The graph trusts one Boolean field from a contradictory deterministic
  authorization record. A defective or substituted runtime validator result could
  therefore create an action despite its own rejection reason.
- **Disposition:** Resolved at 2026-07-26 04:13 IST after independent adversarial
  rework testing; no unsafe real actuation occurred.
- **Required approval:** None. This is mandatory safety-contract rework.
- **Controls:** Make `ValidationResult` self-consistent and require, before constructing
  an advisory `GraphAction`, `approved=true`, `reason_code=APPROVED`, no emergency,
  a finite exact bounded setpoint, and nonempty separate evidence. Contradictory
  validation records must enter fatal cleanup and cause zero apply calls.
- **Verification required:** Independently test contradictory reason/Boolean,
  emergency, missing/non-finite/mismatched setpoint, and empty-evidence combinations;
  every invalid combination must apply zero actions and clean up exactly once.
- **Verification:** Every contradictory validation combination failed closed with zero
  fake actuation, while one valid approval still succeeded.

### SAFE-007 — Finalization failure can bypass safe cleanup

- **Observed:** 2026-07-26 03:41 IST during independent `AGT-001` testing.
- **Affected feature:** `AGT-001` is paused in rework; dependent `AGT-002` and later
  closed-loop features must not start. Independent testing continues to collect the
  complete finding set.
- **Evidence:** Fresh fake runs reproduced three finalization failures: final-summary
  identity mismatch, expected runtime failure, and unexpected exception. Each returned
  `simulation_status=failed` with `cleanup_completed=false` and zero
  `abort_safely` calls; the recent path ended
  `continue_or_finish -> finalize_run -> END`.
- **Risk:** A runtime or contract failure while finalizing can leave the active
  simulation/session uncleaned even though graph state reports failure.
- **Disposition:** Resolved at 2026-07-26 04:13 IST after independent adversarial
  rework testing.
- **Required approval:** None.
- **Controls:** Preserve explicit graph routing and guarantee that every fatal
  finalization outcome reaches or invokes `abort_safely` exactly once, checkpointing
  the cleanup result. Do not add automatic retries to finalization or actuation.
- **Verification required:** Independent tests must cover summary mismatch, expected
  failure, and unexpected exception; each must record failed state and one cleanup
  call. The successful finish path must still finalize once with no abort call.
- **Verification:** Successful finalization ran once with no abort. Summary mismatch,
  expected failure, and unexpected exception each routed through one cleanup call.
  The graph has 14 nodes/22 edges and no retry policies.

## Resolved and mitigated items

### SAFE-006 — Supervisor schema unreliable within the 64-token cap

- **Observed:** 2026-07-26 03:03 IST during independent `LLM-001` testing.
- **Affected feature:** `LLM-001`; dependent `AGT-001` integration is paused while
  unrelated safe work may continue.
- **Evidence:** A fresh existing-model smoke returned schema-valid Energy and Comfort
  records, but the Supervisor response was malformed initially and remained malformed
  after the one allowed correction. The provider stopped safely after two attempts in
  11.71 seconds. Evidence is preserved in
  `.cache/tester/llm001/independent-20260725T213322112457Z/attempts.jsonl` with SHA-256
  `3FA5CDD7609750B2E46AD3BB0F7EE04168CE3DC4E6ADF8894EA1F852839BFE49`.
- **Risk:** The bounded provider fails closed, but unreliable Supervisor parsing would
  overuse deterministic fallback and prevent the intended observable multi-agent path.
  Schema-valid Energy/Comfort outputs also repeated semantic mistakes, confirming that
  model output must remain advisory.
- **Disposition:** Resolved at 2026-07-26 03:19 IST; no feature pause remains.
- **Required approval:** None. Compacting the same typed schema without weakening the
  64-token cap or deterministic authority is mandatory in-scope rework.
- **Controls:** Short JSON aliases and 28-character evidence limits/value
  guidance for the Supervisor wire schema while retaining separate typed energy and
  comfort evidence internally. Keep one correction, three attempts, and all timeout,
  redaction, and advisory-only controls unchanged.
- **Verification:** Independent testing parsed Energy, Comfort, and Supervisor on their
  first calls, then parsed five additional Supervisor requests 5/5 without correction
  or fallback. The fake failure matrix, redacted audit, Ruff, strict Pyright, and all
  172 tests passed. Semantic weaknesses were reproduced and remain advisory-only.

### SAFE-005 — Missing observation units silently defaulted

- **Observed:** 2026-07-26 02:45 IST during independent `CTL-001` contract testing.
- **Affected feature:** `CTL-001`; it is already paused from approval with `SAFE-004`.
- **Evidence:** `ObservationSnapshot.model_validate` accepts an otherwise valid payload
  that omits `temperature_unit`, `pmv_unit`, and `occupancy_unit`; Pydantic silently
  injects `degC`, `dimensionless`, and `people`. Wrong unit values are rejected, but
  missing required unit keys are not.
- **Risk:** A producer can omit measurement units and still receive semantic control
  authorization, contradicting the fail-closed unit requirement in `techspec.md`.
- **Disposition:** Resolved at 2026-07-26 03:06 IST; no feature pause remains.
- **Required approval:** None. Making required units explicit is mandatory in-scope
  rework.
- **Controls:** All three unit literals are required without defaults, and MCP
  reconstruction explicitly populates them.
- **Verification:** Independent tests rejected each omitted unit, all omitted units, and
  all wrong literals; explicit `degC`, `dimensionless`, and `people` passed and the JSON
  schema lists all three as required.

### SAFE-004 — Unbound semantic authorization at MCP actuation

- **Observed:** 2026-07-26 02:40 IST during independent `CTL-001` testing.
- **Affected feature:** `CTL-001`; dependent LangGraph actuation is paused. Independent
  `LLM-001` work may continue because it has no actuator authority.
- **Evidence:** At occupied observation sequence 10 and current setpoint `23.9°C`, the
  guard approved exactly `24.4°C`. A post-validation substitution to `25.0°C` was
  independently classified `RATE_LIMIT_EXCEEDED` because its change was `1.1°C`, but
  FastMCP accepted it and EnergyPlus wrote `25.0°C` once to the actuator, schedule, and
  all five thermostat readings. Evidence is preserved under
  `.cache/tester/ctl001/independent-20260726-024036-457/runs/tester-ctl-substitution`.
- **Risk:** The MCP/session boundary rechecks hard bounds and stale identity but is not
  cryptographically or semantically bound to the exact deterministic authorization.
  An in-bounds value substituted after validation can therefore bypass rate and PMV
  policy.
- **Disposition:** Resolved at 2026-07-26 03:06 IST; no feature pause remains.
- **Required approval:** None. Closing the guard-to-MCP authorization gap is mandatory
  rework within the locked architecture and acceptance criteria.
- **Controls:** The MCP server independently reconstructs the current
  observation and re-authorizes the exact requested setpoint. Advisory proposals must
  pass `validate_proposal`; deterministic fallback requests must exactly match a
  server-recomputed `choose_fallback` result. A mismatch must return a stable safety
  rejection and cause zero session writes.
- **Verification:** A fresh independent EnergyPlus/FastMCP run rejected the `25.0°C`
  advisory substitution and `24.5°C` fallback mismatch with zero writes. The exact
  server-computed `24.4°C` fallback wrote once to the actuator, schedule, and five zones;
  replay was cache-only and all changed payloads were rejected. EnergyPlus exited 0
  with zero severe errors.

### SAFE-003 — Concurrent ReadVarsESO sidecar collision

- **Observed:** 2026-07-26 during `SIM-002`.
- **Evidence:** Two simultaneous baseline development runs used the same canonical IDF.
  EnergyPlus `-r` attempted to create/remove the same model-adjacent `.rvi` sidecar; one
  run terminated with one severe file-in-use error before normalization.
- **Risk:** Concurrent or accidentally overlapping runs were not isolated despite unique
  output directories.
- **Disposition:** Resolved at 2026-07-26 01:47 IST; no feature pause remains.
- **Controls:** The runner stages a hash-verified `input.idf` inside every no-overwrite
  run directory and uses that directory as the EnergyPlus process working directory.
- **Verification:** Two fresh overlapping CLI runs independently produced identical
  672-timestep/3,360-row summaries with no canonical-model sidecars. No-overwrite and
  failure-path tests passed. Original collision diagnostics remain preserved.

### SAFE-001 — Qwen PMV semantic error

- **Observed:** 2026-07-26 during `ENV-001`.
- **Evidence:** With PMV `+0.7` and a 26°C cooling setpoint, the first Qwen3 4B response
  incorrectly described occupants as cold and recommended raising the setpoint to 27°C.
- **Risk:** Direct use of an LLM recommendation could worsen an occupied hot-zone comfort
  violation.
- **Disposition:** Mitigated; no feature pause remains.
- **Controls:** LLM outputs are advisory only. Deterministic code owns PMV semantics,
  setpoint bounds, rate limits, stale-data checks, rejection, and fallback. Agent prompts
  explicitly define the sign of PMV, and every proposal is validated before MCP actuation.
- **Verification:** A bounded follow-up returned schema-valid `25.0°C` with
  `COOL_WARM_ZONE`; this confirms structured advisory feasibility but does not grant the
  model actuator authority.

### SAFE-002 — Local Qwen latency and resource isolation

- **Observed:** 2026-07-26 during `ENV-001`.
- **Evidence:** An unconstrained 256-token response took 25.81 seconds on CPU with zero
  VRAM use. A warm, 35-token schema-constrained response took 4.34 seconds.
- **Risk:** Verbose or repeated inference could stall control-boundary processing.
- **Disposition:** Mitigated; no feature pause remains.
- **Controls:** Sequential inference only, compact schemas, at most 64 output tokens,
  bounded timeout/retries, latency logging, and immediate deterministic fallback.
- **Additional verification:** During `LLM-001`, a 96-character rationale allowance
  produced one bounded malformed Energy result after its single correction; no output
  or action was authorized. Rationale/evidence fields were compacted to 48 characters.
  A fresh three-role smoke then parsed all schemas on their first request in 5.54,
  3.65, and 5.03 seconds. Production timeout is 8 seconds per attempt, correction is
  globally limited to one, and total chat attempts are capped at three.

## Required entry fields

Every new item records an ID, time, affected feature, evidence, risk, isolated pause
state, required approval if any, mitigation, verification, and resolution time. Unsafe
actions are never retried blindly.
