# Safety and Decision Log

Last updated: 2026-07-26 01:47 IST

This is the append-only project record for unsafe conditions, safety-relevant findings,
isolated feature pauses, approval needs, and mitigations. Deterministic safety rules in
`docs/techspec.md` always outrank model output and schedule pressure.

## Open items

No feature is currently paused.

## Resolved and mitigated items

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

## Required entry fields

Every new item records an ID, time, affected feature, evidence, risk, isolated pause
state, required approval if any, mitigation, verification, and resolution time. Unsafe
actions are never retried blindly.
