# Current Status

Last updated: 2026-07-26 03:19 IST

## Snapshot

- **Project:** Eco-Loop Building Agents
- **Phase:** LangGraph multi-agent control
- **Active feature:** `AGT-001` LangGraph typed state machine
- **Feature state:** `in_progress`
- **Last completed feature:** `LLM-001` Local structured LLM provider
- **Deadline:** July 26, 2026, 11:59 PM IST
- **Workspace:** `V:\BMS_simulation`

## Latest work

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

## Safety state

`SAFE-004`, `SAFE-005`, and `SAFE-006` are resolved after fresh independent testing.
No feature is currently paused.
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

Developer implements `AGT-001` as an explicit typed LangGraph process:

1. Build named nodes and conditional approved, retry, fallback, finish, and fatal routes
   over immutable typed records.
2. Keep semantic retries in graph state with exactly two revisions; use exception retry
   only for narrow idempotent transient reads, never actuator writes.
3. Add in-process checkpointing with unique run thread IDs, a derived recursion limit,
   v2 update/task event normalization, and safe abort cleanup. Do not claim restart
   durability from the in-memory saver.
4. Test every route with fakes before connecting live role prompts or EnergyPlus.

## Blockers and approvals

- No local implementation blocker.
- Publishing, public deployment, repository push, video upload, or submission upload
  remains approval-gated. Those items do not block current safe features.

## New-agent startup checklist

1. Read `AGENTS.md` and this file.
2. Read `docs/safety-log.md`.
3. Read `docs/progress.md` and locate dependency-ready or isolated paused features.
4. Confirm acceptance criteria in `docs/featurelist.json`.
5. Follow `docs/automation.md` and the relevant `docs/techspec.md` sections.
6. Run proportional verification and preserve evidence.
7. Automatically synchronize all affected control documents before handoff.
