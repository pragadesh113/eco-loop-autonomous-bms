# Current Status

Last updated: 2026-07-26 02:23 IST

## Snapshot

- **Project:** Eco-Loop Building Agents
- **Phase:** Deterministic control safety
- **Active feature:** `CTL-001` Safety validator and fallback policy
- **Feature state:** `in_progress`
- **Last completed feature:** `MCP-001` FastMCP EnergyPlus server
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

## Safety state

No feature is currently paused. `SAFE-003` passed independent verification and is
resolved. `docs/safety-log.md` also contains two mitigated LLM findings:

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

Developer implements `CTL-001` before LLM integration:

1. Define typed observations, proposals, validation decisions, fallback decisions, and
   machine-readable rejection reason codes.
2. Enforce finite setpoints, `22..28°C`, maximum normal 1°C step, observation freshness,
   exact run/decision/sequence identity, occupied PMV target/emergency semantics, and
   comfort-safe direction of change.
3. Make deterministic policy authoritative over all LLM/MCP proposals.
4. Implement occupied hot/cold correction, comfortable hold, unoccupied setback, and
   invalid-data last-safe fallback without weakening bounds.
5. Add exhaustive boundary/property-style tables for unsafe, stale, retry, and fallback
   paths, then independently test before any LLM control is allowed.

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
