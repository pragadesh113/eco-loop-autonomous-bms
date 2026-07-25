# Project Progress

Last synchronized: 2026-07-26 03:19 IST

`docs/featurelist.json` is the canonical feature registry. This file is its human-readable execution checklist. A checkbox is marked only after the corresponding acceptance criteria pass.

## Overall status

- Completed: 10 of 17 features
- In progress or rework: 1
- Blocked: 0
- Remaining todo: 6
- Current stage: `LLM-001` is approved; `AGT-001` typed LangGraph state machine is in
  progress.

## Feature checklist

### Documentation

- [x] `DOC-001` Engineering documentation baseline — **done**
- [x] `OPS-001` Autonomous feature-delivery orchestration — **done**
  - Evidence: `docs/automation.md` defines role ownership, state transitions, retries, approval gates, and automatic document synchronization.
  - Evidence: active heartbeat `eco-loop-autonomous-delivery-loop` resumes this task hourly.
  - Evidence: schedule is bounded through July 26, 2026 at 11:59 PM IST.

### Foundation and environment

- [x] `FND-001` Python repository scaffold — **done**
  - Evidence: Git repository initialized on `main`.
  - Evidence: verified Gate 3 checkpoint committed locally as `1ea849b` on
    `codex/hackathon-delivery`; no remote push performed.
  - Evidence: editable package installed in `.venv` with Python 3.12.1.
  - Evidence: Ruff passed, Pyright reported 0 errors, and Pytest passed 6 tests at 91.67% coverage.
  - Evidence: `.gitignore` excludes secrets, local model weights, EnergyPlus output, weather downloads, and generated runs.
- [x] `ENV-001` EnergyPlus and Ollama feasibility — **done**
  - Evidence: project-local EnergyPlus 26.1.0 completed official
    `5ZoneAirCooled.idf` using the New Delhi EPW in 4.46 seconds with zero severe errors.
  - Evidence: project-local Ollama 0.32.4 serves `qwen3:4b-instruct` (4.0B Q4_K_M);
    bounded structured output completed in 4.34 seconds.
  - Evidence: `bms-agent doctor --json` reports exact runtime, API, resource, weather,
    model, and storage paths.
  - Evidence: Ruff passed, strict Pyright reported 0 errors, and Pytest passed 13 tests
    at 92.47% coverage; `uv.lock` resolves 92 packages.
  - Safety: LLM PMV semantics and latency findings are mitigated in
    `docs/safety-log.md`; the model remains advisory.
  - Independent test: fresh EnergyPlus run exited 0 with zero severe errors; fresh
    Qwen schema parse completed in 4.246 seconds; all acceptance criteria passed.

### Simulation

- [x] `SIM-001` Model and weather preparation — **done**
  - Evidence: official source IDF is preserved byte-identically; baseline and controlled
    v1 IDFs differ only in their mode comment.
  - Evidence: both prepared New Delhi May 23–29 models exited 0 with zero severe errors
    and report every required timestep observation plus `Electricity:HVAC`.
  - Evidence: EDD contains exactly
    `CLG-SETP-SCH,Schedule:Compact,Schedule Value`.
  - Evidence: Ruff passed, strict Pyright reported 0 errors, and Pytest passed 21 tests
    at 93.20% coverage.
  - Independent test: both fresh runs emitted 672 records per required series; HVAC
    energy had 280 nonzero records; the Python API resolved the actuator to handle `9`.
- [x] `SIM-002` Baseline simulation pipeline — **done**
  - Evidence: two concurrent isolated runs each produced 672 timesteps, 3,360 zone
    observations, 40.330583833437416 HVAC kWh, and 76.0% occupied PMV compliance.
  - Evidence: energy and comfort repeatability differences were exactly zero under
    `1e-9` absolute tolerances.
  - Evidence: Ruff passed, strict Pyright reported 0 errors, and Pytest passed 36 tests
    at 93.13% coverage.
  - Safety: per-run staged IDFs and working directories mitigate `SAFE-003`; independent
    verification passed with two fresh overlapping CLI runs.
  - Independent test: all normalized values matched raw outputs, repeatability was exact,
    no-overwrite/failure behavior passed, and the original collision evidence remained.
- [x] `SIM-003` Active control session — **done**
  - Evidence: real one-day run completed 96 timesteps with 24 hourly observations,
    exit code 0, and zero severe errors.
  - Evidence: a fresh occupied-hours 25°C action changed the shared schedule and all
    five thermostat setpoint readings from 23.9°C to exactly 25°C.
  - Evidence: Ruff passed, strict Pyright reported 0 errors, and Pytest passed 42 tests
    at 93.01% coverage.
  - Independent test: invalid/stale/duplicate actions caused zero writes; one fresh
    action changed the schedule and all five thermostats; state creation/deletion was 1/1.

### Protocol and model

- [x] `MCP-001` FastMCP EnergyPlus server — **done**
  - Evidence: 11 Pydantic structured-output tools expose the active session with
    explicit units, normalized errors, localhost/stdio defaults, and append-only audit.
  - Evidence: real `FastMCP.call_tool` action changed the live schedule and all five
    thermostats to 25°C; exact replay was cached and conflicting replay rejected.
  - Evidence: Ruff passed, strict Pyright reported 0 errors, and Pytest passed 45 tests
    at 92.83% coverage.
  - Independent test: 97 `FastMCP.call_tool` requests produced 97 audit records;
    exactly one physical action was applied under replay/conflict/stale pressure.
- [x] `LLM-001` Local structured LLM provider — **done**
  - Developer evidence: strict role-paired Energy, Comfort, and Supervisor schemas;
    injectable provider boundary; loopback-only Ollama adapter; 8-second production
    timeout; 64-token cap; one global correction; three-attempt absolute bound; compact
    redacted JSONL timing audit; no actuation imports.
  - Live smoke: the existing Qwen3 4B model returned all three schemas on the first call
    in 5.54, 3.65, and 5.03 seconds. No model was pulled or modified.
  - Developer gate: Ruff passed, strict Pyright reported 0 errors, and Pytest passed
    165 tests at 93.86% coverage.
  - Independent failure: Energy and Comfort parsed, but Supervisor remained malformed
    after the single correction under the 64-token cap. `SAFE-006` requires a more
    compact Supervisor wire schema and a fresh three-role smoke; all failures remained
    controlled and no actuation occurred.
  - Rework evidence: the Supervisor wire schema now uses five concise aliases and
    28-character evidence limits while preserving descriptive internal fields. A fresh
    three-role smoke passed 3/3 first-attempt calls, followed by 5/5 first-attempt
    Supervisor parses with no correction or fallback.
  - Rework gate: Ruff passed, strict Pyright reported 0 errors, and Pytest passed
    172 tests at 93.87% coverage. `SAFE-006` awaits independent closure.
  - Independent rework test: fresh Energy/Comfort/Supervisor calls and five additional
    Supervisor calls all parsed on the first attempt; failure matrices and the full gate
    passed. Semantic weaknesses remained contained as advisory output. `SAFE-006` is
    resolved.

### Control and agents

- [x] `CTL-001` Safety validator and fallback policy — **done**
  - Developer evidence: 18 validation and 7 fallback reason codes enforce identity,
    freshness, complete finite zone data, separate evidence, hard bounds, rate limits,
    PMV-safe direction, emergency correction, and shared-setpoint conflict hold.
  - Live integration: a stale proposal authorized zero actions, a substituted `29°C`
    action was independently rejected, and one validated `24.4°C` cold correction
    reached the schedule and all five zones.
  - Developer gate: Ruff passed, strict Pyright reported 0 errors, and Pytest passed
    116 tests at 93.55% coverage.
  - Independent failure: an in-bounds post-validation substitution from the guard-approved
    `24.4°C` to `25.0°C` bypassed semantic/rate authorization and physically wrote once.
    `SAFE-004` pauses dependent actuation until MCP independently re-authorizes the exact
    proposal or recomputed fallback and a fresh real-session retest passes.
  - Independent schema failure: omitted observation unit fields silently received
    defaults. `SAFE-005` requires explicit `degC`, `dimensionless`, and `people` literals
    with fail-closed omission tests.
  - Rework evidence: MCP now reconstructs its own explicit-unit observation and
    independently validates advisory requests or recomputes fallback. Fresh real runs
    rejected the `25.0°C` advisory substitution and `24.5°C` fallback mismatch with zero
    writes; exact server-authorized `24.4°C` advisory/fallback actions wrote once.
  - Rework gate: Ruff passed, strict Pyright reported 0 errors, and Pytest passed
    165 tests at 93.86% coverage. `SAFE-004`/`SAFE-005` await independent closure.
  - Independent rework test: both substitutions caused zero writes, exact server
    fallback wrote once, replay/idempotency remained bounded, every missing/wrong unit
    failed closed, and the full gate passed. `SAFE-004` and `SAFE-005` are resolved.
- [ ] `AGT-001` LangGraph typed state machine — **in_progress**
- [ ] `AGT-002` Energy, Comfort, and Supervisor agents — **todo**

### Metrics and integration

- [ ] `MET-001` Audit log and quantitative evaluation — **todo**
- [ ] `RUN-001` Closed-loop controlled experiment — **todo**

### Interface, quality, and delivery

- [ ] `UI-001` Streamlit results dashboard — **todo**
- [ ] `TST-001` Automated and manual verification — **todo**
- [ ] `DEL-001` Hackathon submission package — **todo**

## Milestone gates

- [x] Gate 1: EnergyPlus example and Qwen structured output both run locally.
- [x] Gate 2: Baseline produces reproducible HVAC kWh and PMV metrics.
- [x] Gate 3: MCP action changes a cooling setpoint inside an active EnergyPlus run.
- [ ] Gate 4: LangGraph completes approved, retry, fallback, and finish routes.
- [ ] Gate 5: Controlled run saves energy with at least 90% occupied PMV compliance.
- [ ] Gate 6: Dashboard, tests, video, presentation, and public repository are complete.

## Progress update procedure

1. Change the feature's `status` in `featurelist.json` using a state allowed by `docs/automation.md` and the `statusValues` contract.
2. Update its checkbox and the totals above.
3. Record evidence such as a test, run ID, or output path beneath the feature if useful.
4. Replace the snapshot in `current-status.md` with the latest stage, last action, next action, and blockers.
5. Update `plan.md` when completion changes the active stage, gate status, ordering, estimate, fallback, or technical assumption.
6. Perform this synchronization automatically before handing work off; do not wait for the user to request it.
7. Never mark a feature done based only on code presence; its acceptance criteria must pass.
