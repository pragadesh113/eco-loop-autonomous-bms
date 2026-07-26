# Current Status

Last updated: 2026-07-26 18:29 IST

## Snapshot

- **Project:** Eco-Loop Building Agents
- **Phase:** Delivery approval gate
- **Active feature:** `DEL-001` Hackathon submission package
- **Feature state:** `waiting_approval`
- **Last completed feature:** `LAB-001` Interactive live scenario lab
- **Deadline:** July 26, 2026, 11:59 PM IST
- **Workspace:** `V:\BMS_simulation`

## Latest work

The user-reported Live Scenario Lab display defects are resolved. Scenario changes now
load the matching five inputs and reset prior scenario history/temperature state;
simulated provider failure renders valid structured status objects rather than JSON
parse errors; the provider KPI shows a compact `Fallback` state; and the setpoint/HVAC
legend no longer overlaps the x-axis. A new AppTest covers preset reset and all three
fallback role cards. Eight focused dashboard tests and the full 377-test suite passed
at 90.47% coverage, with Ruff and strict Pyright clean. Dark-theme browser verification
found zero Streamlit exceptions and zero JSON parse errors.

The original problem statement was re-read from all seven images in
`V:\BMS_automation\questions`. The verified implementation satisfies the core
EnergyPlus feedback, LangGraph/MCP control, automatic forward injection, quantitative
energy reduction, and occupied PMV comfort requirements. The accepted run proves
16.09135% HVAC savings with 90.63636% occupied comfort compliance. The principal
remaining judging gap is presentation of the OSS-LLM path: the accepted reproducible
run used the typed deterministic optimizer, while local Qwen remains implemented,
verified, advisory, and safely fallible.

The user approved `LAB-001` as a separate simulation-only Live Scenario Lab with
adjustable dynamic conditions, a visible agent-to-safety process, and an optional
local-Qwen mode. It cannot write EnergyPlus sessions or accepted-run artifacts and
explicitly distinguishes reduced-order demonstration estimates from the accepted
EnergyPlus evidence.

`LAB-001` passed its Developer gate. The dashboard now has separate Results and Live
Scenario Lab views. The lab runs eight explicit LangGraph stages, offers deterministic,
local-Qwen, and simulated-failure provider modes, preserves state across steps, charts
PMV/setpoint/illustrative HVAC response, and exposes structured role outputs plus
reflection. Local Qwen failure was reproduced safely and caused a bounded deterministic
action. All 376 tests passed at 90.47% coverage; Ruff and strict Pyright are clean.
Playwright completed a live interaction with zero Streamlit exceptions and saved
`artifacts/demo/06-live-scenario-lab.png`. Accepted-run aggregate hashes are unchanged.

Independent Tester review approved `LAB-001`: 7/7 focused tests, hot/cold emergency,
boundary, invalid-input, provider-failure, unsafe-advisory, two-cycle session/reset,
filesystem-isolation, and accepted-artifact probes passed. The live screenshot is
`artifacts/demo/06-live-scenario-lab.png`. `LAB-001` is Lead-approved and done.
`DEL-001` returns to the approval gate for public repository, presentation, video, and
submission upload actions.

`RUN-001` completed its Developer gate. The concrete in-process FastMCP gateway now
dispatches the locked start/constraints/observation/trend/action/status/stop/summary/reset
lifecycle, rejects changed constraints, maps unknown server error codes to a bounded
generic code, and never gives agents direct session access. Pre-control EnergyPlus
schedule values outside `22..28°C` are treated as invalid observations until the first
safe actuation; the final completed-session observation timeout is classified as a
terminal boundary rather than a failed decision.

The fresh immutable candidate `controlled-run001-optimized-v3` completed 672/672
timesteps with 168/168 exact server-authorized actions, zero severe errors,
33.84084809588941 kWh versus the 40.330583833437416 kWh baseline
(16.091350832782815% savings), and 90.63636363636364% occupied PMV compliance. Its
reliability summary reports 100% autonomy and zero minutes without an approved action.
The MCP audit contains one constraints and one reset dispatch. Independent verification
closed every prior finding, confirmed all 168 actions were bounded and exact, and passed
36 focused plus all 369 tests at 90.16% coverage with Ruff and strict Pyright clean.
`RUN-001` and Gate 5 are approved.

`UI-001` Developer work is complete. The Streamlit dashboard is strictly read-only and
loads only validated, contained completed-run artifacts. It shows the exact accepted
KPIs, run/node status, five-zone PMV with the target band, temperatures and applied
setpoint, cumulative baseline/control energy, latest agent-cycle completion, and
decision/reflection chronology. Three dashboard tests and all 372 tests passed at
90.07% coverage; Ruff and Pyright passed. The real loopback health endpoint returned
HTTP 200 before the bounded test server was stopped.

Independent UI testing initially found that role cards showed only completion state and
the decision table omitted reflection timestamps/outcomes. Rework now reconstructs
allowlisted structured Energy, Comfort, Supervisor, and Reflection output from persisted
evidence and joins all 168 decisions to their timestamped reflection results. Independent
retesting passed the accepted v3 AppTest with zero exceptions and approved `UI-001`.

`TST-001` is independently approved. A locked all-extras sync resolved 92 packages;
doctor confirmed every local prerequisite. All 372 tests passed at 90.04% coverage with
Ruff, Pyright, diff, and lock checks clean. The unattended full rehearsal
`tst001-rehearsal-v1` exactly reproduced the accepted 16.09135% savings and 90.63636%
comfort result with zero severe errors. A duplicate invocation failed safely without
changing accepted run artifacts, and the dashboard loaded the rehearsal with zero
exceptions.

The local `DEL-001` package is ready and independently reviewed: final README, MIT
license, architecture/novelty report, three-minute demo guide, exact compact accepted
artifacts, rendered dashboard screenshot, submission manifest, and verification records.
No remote is configured, nothing is published/deployed/uploaded, and no presentation
file was touched. The remaining acceptance criteria require explicit external/PPT/video
authorization.

`ENV-001` passed independent testing and Senior Lead review:

- Provisioned project-local EnergyPlus 26.1.0 and Ollama 0.32.4; no system-wide
  installer or administrator change was used.
- Added New Delhi Safdarjung TMYx 2011–2025 EPW/DDY/STAT weather files.
- Ran the official EnergyPlus `5ZoneAirCooled.idf` with the New Delhi EPW: completed
  successfully in 4.46 seconds with one location warning and zero severe errors.
- Pulled `qwen3:4b-instruct` (4.0B, Q4_K_M, digest prefix `0edcdef34593`) into
  project-local storage.
- Verified schema-constrained output. The safe compact trial completed in 4.34 seconds
  and returned a 25.0°C proposal for PMV +0.7.
- Expanded `bms-agent doctor` to report exact EnergyPlus resources/weather plus Ollama
  API/model state, and normalized scheme-less `OLLAMA_HOST`.
- Created `uv.lock` with 92 resolved packages.
- Verification passed: Ruff, strict Pyright with 0 errors, and 13 Pytest tests at
  92.47% coverage.
- Independent evidence: a fresh EnergyPlus run exited 0 with zero severe errors, a
  fresh schema-constrained Qwen response parsed in 4.246 seconds, and diagnostics,
  hashes, lockfile, ignore rules, and failure behavior were consistent.

`SIM-001` development is complete. Read-only weather analysis selected May 23–29 as the hottest
seven-day window in the New Delhi EPW (35.97°C mean dry-bulb, 45.6°C peak). The official
example has been transformed reproducibly into separate baseline and controlled models.
All five occupied People objects now request Fanger calculations; required outputs and
the HVAC meter are emitted at timestep resolution. Fresh EnergyPlus validation passed
for both models, and the EDD proves the shared `Clg-SetP-Sch` schedule actuator.

Independent testing approved `SIM-001`: both fresh models produced 672 records per
required series, HVAC electricity contained 280 nonzero timestep records, and the
EnergyPlus Python API found the exact shared schedule actuator with valid handle `9`.

Detailed feasibility evidence is in `docs/environment-report.md`.

`SIM-002` has a working normalization path, but a concurrent development stress test
revealed that EnergyPlus `-r` writes a model-adjacent `.rvi` sidecar. Two overlapping
runs using the canonical IDF caused one safe file-in-use failure. The failed artifacts
are preserved; no accepted model or run was corrupted. Rework now isolates a verified
IDF copy inside each unique run directory.

The mitigation is implemented: each run stages a hash-verified `input.idf`, uses its
own working directory, and refuses overwrite. Two simultaneous accepted baselines
completed with identical outputs: 40.330583833437416 kWh, 76.0% occupied PMV compliance,
672 timesteps, and 3,360 normalized rows. Independent verification is next.

Independent testing approved `SIM-002`: fresh overlapping CLI runs remained isolated,
every normalized value matched raw EnergyPlus data, exact repeatability passed, and
failure/no-overwrite behavior preserved diagnostics. Gate 2 is complete.

`SIM-003` development completed a real one-day API smoke: 96 weather timesteps, 24
hourly observations, all required handles resolved, and a fresh occupied-hours 25°C
action changed `Clg-SetP-Sch` plus all five thermostat readings from 23.9°C to 25°C.
EnergyPlus exited 0 with zero severe errors. Full quality checks passed at 93.01%
coverage; independent verification is next.

Independent testing approved `SIM-003`: invalid, non-finite, out-of-range, stale,
duplicate, and no-pending actions never reached the actuator; one fresh 25°C action
changed the live schedule and all five thermostat readings. State creation/deletion was
exactly 1/1 and all bounded failure/cancel paths unblocked safely.

`MCP-001` development added 11 typed structured-output FastMCP tools with explicit
units, normalized errors, one-session ownership, idempotent action writes, stdio default,
and loopback-only HTTP. A real `FastMCP.call_tool` request changed EnergyPlus to 25°C;
an exact replay was cached without a second action and a conflicting replay was rejected.
The full gate passed 45 tests at 92.83% coverage.

Independent testing approved `MCP-001`: all tool schemas, 97 real protocol calls, exact
idempotency, conflict/stale/duplicate failures, one-active-session lifecycle, append-only
audit, stdio default, and loopback-only HTTP were verified. Only one physical write
occurred. Gate 3 is complete.

`CTL-001` development is complete and has moved to independent testing. The deterministic
guard now exposes strict typed contracts, 18 validation reasons, seven fallback reasons,
exact identity/freshness checks, complete finite five-zone observations, evidence checks,
`22..28°C` bounds, a `1°C` rate limit, conservative PMV direction, explicit corrective
routing, emergency fallback, and shared hot/cold conflict hold. A real EnergyPlus
integration kept a stale proposal at zero writes, rejected a substituted `29°C` action,
and applied one validated `24.4°C` correction to the shared schedule and all five zones.
The Developer gate passed Ruff, strict Pyright, and 116 tests at 93.55% coverage.

Independent testing did not approve `CTL-001`. It found `SAFE-004`: after the guard
authorized exactly `24.4°C`, a substituted in-bounds `25.0°C` request was correctly
classified `RATE_LIMIT_EXCEEDED` by the guard but was still accepted by FastMCP and
physically written once. Stale and `29°C` requests remained blocked, so the isolated gap
is the missing semantic binding between guard authorization and MCP actuation. Dependent
LangGraph actuation is paused. Mandatory server-side re-authorization is in rework, while
the non-actuating `LLM-001` provider proceeds independently.

The same independent matrix found `SAFE-005`: `ObservationSnapshot` silently supplies
default unit literals when all unit fields are omitted. Rework must make temperature,
PMV, and occupancy units explicitly required and prove missing/wrong units fail closed.

The Developer completed rework for both findings. FastMCP now reconstructs the current
explicit-unit observation from its own registry and independently validates an advisory
request or recomputes deterministic fallback; only that server-authorized value reaches
the session. Fresh fallback and advisory runs rejected the prior `25.0°C` substitution
and a mismatched `24.5°C` fallback with zero writes, then wrote exact authorized
`24.4°C` actions once. Idempotency now binds source, identity, value, evidence, and
trigger. All unit keys are required. The rework gate passed Ruff, strict Pyright, and
165 tests at 93.86% coverage. Independent retesting is active before either safety item
can close.

`LLM-001` development is complete and independently testable. The package has strict
role-paired Energy, Comfort, and Supervisor schemas, a model-independent provider
boundary, loopback-only Ollama adapter, primary/already-installed-fallback selection,
an 8-second production timeout, 64-token output cap, one global schema correction,
three-chat absolute limit, sequential locking, and redacted timing/token audit. It has
no control, MCP, session, simulation, LangGraph, or actuator imports. A fresh existing-
model smoke parsed all three schemas on their first request in 5.54, 3.65, and 5.03
seconds without pulling or modifying a model. Schema success remains advisory only.

Independent retesting approved `CTL-001` and resolved `SAFE-004`/`SAFE-005`. In a fresh
run, the `25.0°C` advisory substitution and `24.5°C` fallback mismatch caused zero
writes; only the exact server-computed `24.4°C` fallback wrote once to all five zones.
Replay was cache-only, changed source/evidence/trigger payloads were rejected, all
missing/wrong unit fields failed, EnergyPlus exited 0 with zero severe errors, and the
full 165-test gate passed at 93.86% coverage.

Independent `LLM-001` testing found `SAFE-006`. Energy and Comfort parsed in 5.72 and
4.37 seconds, but Supervisor was malformed on both its initial and single correction
attempt, then returned a controlled failure after 11.71 seconds. No action or model
change occurred. The feature is in bounded rework to compact the Supervisor wire schema
while preserving separate evidence, the 64-token cap, one correction, and deterministic
authority.

`SAFE-006` rework is complete. Supervisor keeps descriptive internal fields while its
wire schema is exactly `decision,setpoint_c,conflict,energy,comfort`, with each evidence
string capped at 28 characters and alias-only provider parsing. A fresh three-role smoke
passed all roles on their first call, then five additional Supervisor calls passed 5/5
without correction or fallback. Semantically weak evidence remained possible and is
explicitly untrusted. Ruff, strict Pyright, and 172 tests at 93.87% coverage passed;
independent retesting is active.

Independent retesting approved `LLM-001` and resolved `SAFE-006`. Energy, Comfort, and
Supervisor all parsed on their first fresh requests, then five additional Supervisor
requests passed 5/5 without correction or fallback. Alias contract, missing/extra
rejection, timeouts, model selection, retry limits, serialization, redacted audit,
import isolation, Ruff, strict Pyright, and 172 tests at 93.87% coverage passed.
Semantic errors were independently reproduced and remain untrusted advisory evidence.

A recoverability checkpoint was created locally on branch
`codex/hackathon-delivery`: root commit `1ea849b` contains all verified source,
documentation, model artifacts, compact evidence, tests, and `uv.lock`. Ignored
runtimes, model weights, weather, caches, and generated runs were not committed. Nothing
was pushed or published.

The latest local checkpoint is commit `4864414`, containing independently approved
`CTL-001`/`LLM-001` source, safety rework, tests, documentation, and compact evidence.
Nothing was pushed or published.

`AGT-001` development is complete. The typed LangGraph workflow has the specified
14 named nodes and 21 edges, two state-bounded semantic revisions, deterministic
fallback, fail-closed fatal abort, continuation, and finish routes. Injected fakes cover
the route matrix without EnergyPlus, Qwen, network, or actuator access. Actuation has no
exception retry policy. Unique run/thread identity, explicitly allowlisted same-process
checkpoints, a derived recursion limit, bounded state history, and redacted dashboard
events are implemented. The Developer gate passed Ruff, strict Pyright, and all 194
tests at 92.50% total coverage. Independent testing is now active.

Independent testing found `SAFE-007`. A final-summary identity mismatch, an expected
finalization failure, and an unexpected finalization exception each produced failed
graph state but followed `finalize_run -> END` with `cleanup_completed=false` and zero
safe-cleanup calls. `AGT-001` and its dependents are paused while the Tester completes
the finding set and the Developer adds exactly-once abort cleanup for every fatal
finalization outcome. No automatic finalization or actuation retry is permitted.

The same audit found `SAFE-008`: a contradictory validator record with
`approved=true` and reason `RATE_LIMIT_EXCEEDED` caused one fake apply at `25.5°C`.
No real EnergyPlus write occurred, and the verified MCP boundary would independently
reject that request later, but the graph must provide its own defense in depth. Rework
must enforce a self-consistent `APPROVED`, non-emergency, finite, exact, bounded
authorization with nonempty evidence; every contradiction must apply zero actions and
clean up exactly once.

The extended matrix found `SAFE-009`: malformed approved proposals with non-finite or
out-of-bound setpoints and empty evidence can raise an uncaught contract exception
during `GraphAction` construction. Rework must keep construction inside a protected,
redacted fail-closed boundary so every malformed case returns controlled failed state,
applies zero actions, and invokes cleanup exactly once.

Checkpoint identity testing found `SAFE-010`: validation can construct an action from
a checkpointed proposal whose run, decision, and sequence do not match the current
observation. Normal supervisor flow derives matching identity, but restored/corrupted
state must also fail closed. Rework must bind every proposal identity field exactly to
the current observation and graph run before authorization.

Event testing found `SAFE-011`: a 10,018-character decision ID containing a secret
marker was accepted from a fake observation and copied into every dashboard event,
creating 10,450-character event records. Event payloads otherwise remained compact and
redacted. Direct schema probes also accepted whitespace-only run IDs and unbounded
event timestamp/run/decision/node/changed-field strings. Rework must allowlist identity
and bound every event string, rejecting hostile input without copying it into errors or
events.

Read-only AGT-002 planning found preventive timing risk `SAFE-012`: three sequential
role calls, each capable of a bounded correction attempt, can outlive EnergyPlus's
30-second maximum action window. AGT-002 must use one monotonic decision deadline,
reserve worst-case provider and transport margin before every call, and skip remaining
roles into timely deterministic fallback rather than submit a stale action.

The Tester gate is formally `TEST_FAILED`. Standard graph routes, no-retry policy,
recursion cleanup, checkpoint isolation/resume/history, serializer allowlisting,
normal-event redaction, `langgraph==1.2.9` pin/lock, Ruff, strict Pyright, and all 194
existing tests at 92.50% coverage passed. Those checks do not override the adversarial
failures. One bounded Developer rework is active; no live service is involved.

The read-only AGT-002 implementation plan is decision-complete but remains gated. It
uses distinct role contracts and compact prompts, a fake-first MCP gateway, canonical
Energy/Comfort evidence binding, deterministic predicted-versus-measured reflection,
and one monotonic 30-second decision budget with reserved provider/transport margin.
No AGT-002 file or live service has been changed.

The `SAFE-007` through `SAFE-011` Developer rework is complete. Fatal finalization now
uses a conditional route to the existing abort node, making the graph 14 nodes/22 edges
while retaining zero retry policies. Deterministic authorization records are
self-consistent and independently checked; observation/proposal identity is rebound;
internal action construction is protected; and run/decision/event strings use bounded
allowlisted contracts. The gate passed 71 focused graph tests, 150 control/graph tests,
and all 243 tests at 91.95% coverage, plus Ruff, strict Pyright, lock, evidence, and diff
checks. No live service was used. All five items remain open pending independent retest.

The independent retest found residual `SAFE-011`: direct `GraphEvent` validation still
accepted secret/prompt/raw-output markers in `node` and `changed_fields`. Runtime
normalization filtered those names, but persisted/dashboard event contracts must be
independently safe. AGT-001 returned to rework while the Tester completes the rest of
the adversarial matrix.

The retest also found residual `SAFE-009`: a structurally incomplete Supervisor record
raised raw `AttributeError` because the node accessed it before normalization. Rework
must validate each runtime-returned Energy, Comfort, and Supervisor record before
checkpointing or attribute use. Empty unchecked Energy/Comfort records otherwise reach
completion, while foreign objects can fail serialization without cleanup. Other
runtime-returned contract types already fail closed. Internal construction must also
normalize any exception class, not only `ValueError`/`TypeError`.

Coordinated checkpoint testing found residual `SAFE-010`: changing all mutable current
observation/proposal/action identity fields together still resumed and applied once.
Single-field mismatches fail closed. Rework must compare graph run ID with LangGraph's
configured thread ID and compare current decision/sequence with the retained accepted
observation history, not only with other mutable current fields.

The second Tester report is `TEST_FAILED` with three residual roots only.
`SAFE-007` finalization cleanup and `SAFE-008` validation consistency are independently
resolved. Standard routes, checkpoints, recursion, serializer allowlisting, normalized
event redaction, LangGraph pin/lock, Ruff, strict Pyright, 71 graph tests, 150
control/graph tests, and all 243 tests at 91.95% coverage passed. These results do not
override the remaining structural, checkpoint-binding, and direct-event-contract gaps.

The second Developer rework is complete. Energy, Comfort, and Supervisor returns are
normalized before use/checkpointing; all internal construction exceptions are
generically contained; authorization is anchored to configured LangGraph thread ID and
the last retained accepted observation; and direct event fields share the redaction
keyword validator. The gate passed 94 graph tests, 173 control/graph tests, and all 266
tests at 91.96% coverage, plus Ruff, strict Pyright, lock, JSON, hashes, and diff checks.
No live service was used. The final independent cycle is active.

The final independent cycle resolved `SAFE-009`, `SAFE-010`, and `SAFE-011`: fresh
role/constructor checks passed 19/19, identity-anchor checks 24/24, direct event/stream
checks 36/36, and prior invariants 27/27. It isolated new `SAFE-013`: 40 individually
valid changed-field names are not sliced before the 32-item event contract, so event
normalization raises. The fix is a minimal deterministic sorted-first-32 bound; no
authorization, graph, retry, or contract-limit change is allowed.

The isolated `SAFE-013` patch now sorts filtered changed-field names and slices them to
32 before event construction. Its gate passed 100 graph tests, 179 control/graph tests,
and all 272 tests at 91.96% coverage, plus Ruff, strict Pyright, lock, JSON, hashes, and
diff checks. Independent 0/1/32/33/40-field verification is active.

Independent `SAFE-013` testing passed: the fresh cardinality matrix was 5/5, mixed
filtering and direct 32-item contract passed, normal/error streams stayed bounded, and
the production patch was exactly one deterministic slice. Graph tests passed 100/100;
the full suite passed 272/272 at 91.96% coverage with Ruff, Pyright, lock, JSON, hashes,
and diff checks clean. `AGT-001` and Gate 4 are approved. `SAFE-007` through
`SAFE-011` and `SAFE-013` are resolved.

A recoverability checkpoint was created locally as commit `cdba391` on
`codex/hackathon-delivery`, containing the independently approved AGT-001 source,
contracts, tests, architecture document, evidence, safety record, and control ledgers.
Nothing was pushed or published.

`AGT-002` fake-first development is complete. Distinct Energy, Comfort, Supervisor,
and deterministic Reflection contracts/prompts feed an injected `McpGateway` through
`AgentGraphRuntime`; there are no direct session, EnergyPlus, or actuator imports.
Provider failures latch typed fallback state; Supervisor evidence is rebound to
canonical separate Energy/Comfort outputs; gateway results are identity/value checked;
submit has no retry; and the next observation is consumed once and cached.

`SAFE-012` is implemented with one 30-second monotonic deadline, 21 seconds required
before every role, and a 3-second submit margin. Fake-clock matrices cover every role,
revision, boundary, and timely fallback. The Developer gate passed 38 focused and all
310 tests at 91.48% coverage, plus Ruff, strict Pyright, lock, JSON, hashes, and diff
checks. Concrete FastMCP transport instantiation is deferred to `RUN-001` so the
verified server boundary is not refactored.

Independent testing did not approve `AGT-002`. Provider controlled-failure containment
passed 12/12, reversed-PMV advice was contained 2/2, revision boundaries passed, and a
gateway exception was invoked exactly once with safe cleanup. Four gaps remain:

- `SAFE-012`: fallback can submit below the 3-second reserve, and a fixed 21-second
  reserve skips Supervisor at previously observed Energy+Comfort timings.
- `SAFE-014`: contradictory authorization reason and cached response metadata are not
  rejected.
- `SAFE-015`: bounded free-form role rationale is copied into the Supervisor prompt.

Rework keeps the 30-second action window but uses a deadline-bounded provider with
role-specific reserves (29/20/11 seconds) and a hard 3-second submit check. Model
rationale is removed from inter-role prompts/evidence, and gateway reason/cache metadata
becomes part of exact authorization binding.

The rework passed its Developer gate. Deadline-bound provider mode caches one selected
model and allows one chat maximum while regular LLM-001 behavior remains unchanged.
Role reserves are 29/20/11 seconds with a hard 3-second submit gate. Both measured
timing profiles reach all three roles; exact 3.000 submits and 2.999 makes zero gateway
calls. Advisory/fallback authorization reason and `cached=false` are exact-bound, and
canonical E/C evidence uses only enums/numerics. The gate passed 105 focused and all
330 tests at 91.56% coverage, plus Ruff, strict Pyright, lock, JSON, 16 hashes, and diff
checks. Independent retesting is active.

Independent testing approved `AGT-002`. Both measured timing profiles reached Energy,
Comfort, Supervisor, and one advisory submission. Exact 3.000-second submission was
allowed; 2.999 seconds and every late role path made zero gateway calls. Provider
failures passed 12/12, reversed PMV passed 2/2, and all 20 advisory/fallback gateway
response mutations failed closed after one counted attempt. Injected role rationale was
absent from Supervisor prompts, gateway evidence, and graph events. Deadline-bound
one-chat behavior, ordinary provider compatibility, deterministic reflection, cached
observation consumption, and import isolation passed. The final gate passed 105 focused
tests and all 330 tests at 91.56% coverage, plus Ruff, strict Pyright, lock, JSON,
16 hashes, and diff checks. `SAFE-012`, `SAFE-014`, and `SAFE-015` are resolved.
No live service, network, EnergyPlus actuator, or MCP server was used. The concrete
FastMCP transport remains explicitly deferred to `RUN-001`.

A recoverability checkpoint was created locally as commit `aebaffd` on
`codex/hackathon-delivery`, containing the approved AGT-002 contracts, runtime, prompts,
provider integration, tests, evidence, safety closures, and synchronized ledgers.
Nothing was pushed or published.

`MET-001` development produced strict finite metric contracts, one shared evaluator for
baseline and controlled modes, sequence-deduplicated whole-building HVAC energy,
occupied PMV/PPD and decision/reliability metrics, append-only normalized JSONL, and
atomic no-overwrite exports. The Developer/Lead gate passed 21 focused tests, Ruff,
strict Pyright, and all 351 tests at 91.81% coverage, plus lock, JSON, and diff checks.

Independent testing isolated `SAFE-016`: decision lifecycle records can change their
observation sequence between proposal and application, or reference a sequence absent
from the run's metric samples. Metrics approval is paused for a narrow exact-correlation
fix. No actuation, simulation, graph, MCP, or earlier feature is affected.

The bounded correlation rework now binds every lifecycle record to one observed
sequence, permits only one proposed/applied decision per sequence, validates non-null
graph/run-event decision references, and returns a unified correlated trace. Independent
testing passed all exact `SAFE-016` probes, 23 focused tests, and all 353 tests at
91.89% coverage with Ruff, strict Pyright, lock, JSON, and diff checks clean.
`MET-001` is approved and `SAFE-016` is resolved.

## Safety state

No AGT-001 or AGT-002 safety item remains open. The next active feature, `MET-001`,
has no actuator authority and must preserve append-only, run-scoped evidence.
`SAFE-004`, `SAFE-005`, and `SAFE-006` are resolved after fresh independent testing.
`SAFE-003` passed independent verification and is resolved. `docs/safety-log.md` also
contains two mitigated LLM findings:

- Qwen initially reversed positive-PMV semantics and proposed a comfort-worsening action.
- An unconstrained response took 25.81 seconds and used CPU with zero VRAM.
- Concurrent ReadVarsESO sidecars can collide unless each run receives an isolated IDF
  copy; the Developer is implementing and regression-testing that mitigation.

Consequently, the LLM is advisory only. Deterministic control owns PMV semantics,
optimization constraints, validation, fallback, and actuator authority. Inference is
compact, sequential, bounded, and optional.

## Locked decisions

- Python 3.12, EnergyPlus 26.1, FastMCP, LangGraph, Ollama/Qwen3 4B, Pydantic, and
  Streamlit.
- Three explicit logical roles: Energy, Comfort, and Supervisor/Reflection.
- New Delhi seven-day summer experiment, initially based on `5ZoneAirCooled.idf`.
- One shared writable cooling setpoint.
- Occupied PMV target `[-0.5, +0.5]`; hard and semantic safety is deterministic.
- Native Windows/project-local runtimes; Docker remains optional.
- Public GitHub delivery remains an approval-gated external action.

## Exact next action

WAITING_APPROVAL: user must authorize and identify the public GitHub destination; decide
whether the earlier “do not touch PPT” restriction is now lifted; and choose whether to
proceed with video recording/upload, optional public dashboard deployment, and portal
submission. Until then, preserve the verified local package unchanged.

## Blockers and approvals

- No local implementation blocker.
- Exact approval needed: permission plus destination for public repository creation/push;
  permission and template location for presentation work; permission/target for video,
  optional dashboard deployment, and portal upload.

## New-agent startup checklist

1. Read `AGENTS.md` and this file.
2. Read `docs/safety-log.md`.
3. Read `docs/progress.md` and locate dependency-ready or isolated paused features.
4. Confirm acceptance criteria in `docs/featurelist.json`.
5. Follow `docs/automation.md` and the relevant `docs/techspec.md` sections.
6. Run proportional verification and preserve evidence.
7. Automatically synchronize all affected control documents before handoff.
