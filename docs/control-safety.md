# CTL-001 Deterministic Safety Policy

## Authority boundary

`bms_agent.control.safety` defines the authoritative semantic policy. It does not
predict PMV and never treats LLM claims as hard evidence. A successful validation
authorizes only the exact run, decision, observation sequence, evidence, and setpoint
that were checked. The MCP registry reconstructs the observation from server-owned
state and reruns that policy or recomputes fallback before `SimulationSession`
independently rechecks hard bounds and one-action lifecycle.

The accepted baseline is predominantly cold: it has 259 occupied samples between
PMV `-1.0` and `-0.5` plus five below `-1.0`. Therefore, raising the cooling
setpoint can save energy and improve comfort only while every occupied zone remains
on the cold side of neutral. This measured tendency does not weaken per-observation
checks.

## Contracts

All Pydantic contracts are frozen and reject extra fields:

- `ZoneSnapshot` contains zone ID, temperature, Fanger PMV, and occupancy.
- `ObservationSnapshot` contains the current shared setpoint, every expected zone,
  and required explicit `degC`, dimensionless PMV, and people units. No unit has a
  default; omission or a wrong literal fails contract parsing.
- `ObservationEnvelope` adds run, decision, sequence, and observation-time
  identity.
- `ControlProposal` repeats exact identity and carries separate energy and comfort
  evidence.
- `ValidationResult` contains approval, one stable machine reason, the exact
  authorized setpoint or `null`, emergency state, and deterministic evidence.
- `FallbackDecision` contains a bounded setpoint, fallback reason, emergency
  state, reference-source flag, and evidence.

`SafetyPolicy` is an immutable dataclass. Invalid bounds, PMV ordering, rates,
default setpoint, non-finite configuration, or zone lists fail at construction.

At the MCP boundary, advisory evidence fields are individually limited to 512
characters. Action idempotency binds the control source, identity, value, both
evidence fields, and fallback trigger. An exact replay is cache-only; changing any
bound field under the same key is an idempotency conflict.

## Validation precedence

The first failed rule becomes the stable machine reason:

1. Exact run and decision identity.
2. Matching sequence, positive sequence, and freshness versus the last accepted
   sequence.
3. Complete, unique expected zones and complete sensor values.
4. Finite observation and proposal values, non-negative occupancy, and a valid
   observed setpoint.
5. Non-empty energy evidence and comfort evidence.
6. Proposed setpoint inside `22..28 degC`.
7. No simultaneous occupied hot (`PMV > +0.5`) and cold (`PMV < -0.5`) violation
   under the one shared setpoint.
8. Emergency PMV outside `[-1.0, +1.0]` routes to deterministic fallback; it never
   overrides identity, finite-value, or bound checks.
9. Normal change no greater than `1 degC`.
10. A hold during a hot/cold violation is rejected as
    `HOT_CORRECTION_REQUIRED` or `COLD_CORRECTION_REQUIRED`.
11. Normal optimization may move only toward thermal neutrality. Raising is
    allowed only when every occupied PMV is negative; lowering is allowed only
    when every occupied PMV is positive. At exactly neutral, only hold is allowed.
    An unoccupied proposal may hold or move upward, never increase cooling.

Exact `PMV = +/-0.5`, `PMV = +/-1.0`, `22/28 degC`, and a `1 degC` step are
inclusive. Emergency is strictly outside `[-1.0, +1.0]`.

## Fallback table

| State | Deterministic action |
|---|---|
| Invalid, stale, missing, or unsafe proposal | Hold last safe setpoint |
| No valid last-safe value | Use bounded `24 degC` default |
| Simultaneous occupied hot and cold violation | Hold; shared-zone conflict |
| Occupied hot only | Lower last-safe setpoint by `0.5 degC` |
| Occupied cold only | Raise last-safe setpoint by `0.5 degC` |
| Occupied and within `[-0.5, +0.5]` | Hold |
| Unoccupied | Move toward `28 degC` by at most `1 degC` |

Every fallback clamps to `22..28 degC`. Emergency hot/cold uses the same bounded
`0.5 degC` correction; there is no unbounded override. Conflict never guesses
which zone to favor.

## Verification

`tests/test_control_safety.py` table-tests every validation and fallback reason,
both setpoint/rate directions, exact PMV boundaries, NaN/infinity, missing fields
and zones, stale/identity mismatches, missing evidence, direction, shared-zone
conflict, emergency routing, clamping, invalid last-safe/default behavior, and
fail-fast policy configuration.

The integration test starts a real EnergyPlus session. A stale proposal produces
no authorized setpoint and action count remains zero. A post-validation in-bounds
substitution from authorized `24.4 degC` to `25.0 degC` is rejected server-side as
`SAFETY_REJECTED:RATE_LIMIT_EXCEEDED`; a mismatched `24.5 degC` fallback is rejected
as `FALLBACK_MISMATCH`. Both remain at zero writes. The exact server-recomputed
`24.4 degC` fallback then reaches the shared schedule and all five thermostat
observations once. Compact evidence is in
`evidence/ctl001/safety-policy.v1.json`.
