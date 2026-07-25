# Safety and Decision Log

Last updated: 2026-07-26 03:19 IST

This is the append-only project record for unsafe conditions, safety-relevant findings,
isolated feature pauses, approval needs, and mitigations. Deterministic safety rules in
`docs/techspec.md` always outrank model output and schedule pressure.

## Open items

No feature is currently paused.

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
