# Eco-Loop Implementation Plan

## Execution status

Last synchronized: 2026-07-26 19:32 IST

- [x] `DOC-001` Documentation baseline
- [x] `FND-001` Python repository scaffold
- [x] `OPS-001` Autonomous feature-delivery orchestration
- [x] `ENV-001` EnergyPlus and Ollama feasibility
- [x] Gate 1: EnergyPlus example and Qwen structured output both run locally
- [x] `SIM-001` Model and weather preparation
- [x] `SIM-002` Baseline simulation pipeline
- [x] Gate 2: Baseline produces reproducible HVAC kWh and PMV metrics
- [x] `SIM-003` Active EnergyPlus control session
- [x] `MCP-001` FastMCP EnergyPlus server
- [x] Gate 3: MCP action changes the live cooling setpoint
- [x] `CTL-001` Deterministic safety validator and fallback
- [x] `LLM-001` Local structured LLM provider
- [x] `AGT-001` LangGraph typed state machine — approved; `SAFE-007` through
  `SAFE-011` and `SAFE-013` independently resolved
- [x] Gate 4: approved, retry, fallback, fatal, continuation, and finish routes verified
- [x] `AGT-002` Energy, Comfort, and Supervisor agents — independently approved;
  `SAFE-012`, `SAFE-014`, and `SAFE-015` resolved; concrete FastMCP transport
  instantiation remains `RUN-001`
- [x] `MET-001` Audit log and quantitative evaluation — independently approved;
  `SAFE-016` resolved
- [x] `RUN-001` Closed-loop controlled experiment — independently approved with
  `controlled-run001-optimized-v3`; Gate 5 passed; fresh real-EnergyPlus demonstration
  `energyplus-live-demo-20260726-1837` reproduced the accepted metrics
- [x] `UI-001` Streamlit results dashboard — independently approved
- [x] `TST-001` Automated and manual verification — independently approved after exact
  unattended seven-day rehearsal
- [x] `LAB-001` Interactive live scenario lab — independently approved with eight-stage
  LangGraph interaction, safe Qwen fallback, no accepted-run mutation or physical
  control; reported preset/JSON/legend UI regressions resolved and verified
- [ ] `DEL-001` Hackathon submission package — presentation, PDF, and 81.8-second
  dashboard demonstration video completed and verified locally; public repository,
  deployment/upload, and portal submission remain approval-gated

The implementation remains on the original risk-first sequence. Feasibility evidence
requires LLM output to be compact and advisory: Qwen3 4B stays as the primary model, but
deterministic control and validation own decisions and actuation. This safety
clarification does not change the selected stack or LangGraph multi-agent process.
Independent testing proved that the MCP boundary must recompute and bind the exact
semantic authorization, not merely trust a previously validated client value. This
mandatory rework preserves the architecture: deterministic policy remains authoritative,
and safe LLM-provider work continues without actuator access.

The user approved `LAB-001` as a post-Gate-5 demonstration extension. It is a
reduced-order, in-memory scenario sandbox inside the local Streamlit app. It reuses
LangGraph process stages, typed agent outputs, and the authoritative deterministic
safety policy, but cannot access EnergyPlus session actuation or accepted artifacts.
This separation preserves the reproducible experiment while making dynamic agent
behavior visually demonstrable. Local Qwen is optional and always falls back safely.

## Delivery strategy

Build the smallest reliable end-to-end vertical slice first: EnergyPlus baseline, one readable PMV sensor, one writable cooling setpoint, one LangGraph decision, one MCP action, and one measured outcome. Only expand to the full seven-day experiment and polished dashboard after that slice works.

The implementation order is governed by technical risk, not visual polish. EnergyPlus actuation is the first feasibility gate; local structured LLM output is the second. Streamlit, presentation work, optional Docker packaging, and stretch controls must not consume time before both gates pass.

## Stage 1 - Foundation and feasibility (hours 0-3)

Features: `FND-001`, `ENV-001`

1. Initialize Git and the Python project with `src/` packaging, Pytest, Ruff, and Pyright.
2. Install or locate EnergyPlus and record its version, executable, Python API path, example-file path, and weather directory.
3. Install Ollama and pull `qwen3:4b-instruct`; verify one structured response. If it materially affects the machine, test `qwen3:1.7b`.
4. Add a diagnostics command that reports required executable/model availability without modifying the environment.

Exit gate: EnergyPlus completes an official example and Qwen returns valid structured JSON.

## Stage 2 - Physics model and baseline (hours 3-6)

Features: `SIM-001`, `SIM-002`

1. Copy `5ZoneAirCooled.idf` into the project while preserving an untouched baseline.
2. Add/verify People/Fanger configuration, required output variables, HVAC electricity meter, and a named writable cooling schedule.
3. Acquire official New Delhi EPW data and select a seven-day summer run period.
4. Run the baseline twice, normalize output data, and verify energy/comfort repeatability.

Exit gate: baseline summary contains valid HVAC kWh and occupied PMV for every controlled zone.

Fallback: if the five-zone example cannot expose PMV and a stable schedule actuator within one hour, switch to the smallest official conditioned example meeting both requirements.

## Stage 3 - Active EnergyPlus control and MCP (hours 6-9)

Features: `SIM-003`, `MCP-001`, `CTL-001`

1. Create `SimulationSession` around the EnergyPlus Runtime and Data Transfer APIs.
2. Resolve and validate handles after API data is ready.
3. At each hourly control boundary, publish a typed observation and wait for an action.
4. Apply a schedule actuator override and confirm the resulting setpoint changes inside the active run.
5. Wrap lifecycle, observation, action, status, summary, and error operations in FastMCP.
6. Implement the deterministic safety validator and fallback policy before allowing LLM actions.

Exit gate: a one-day smoke run completes with at least one proven MCP-triggered actuator change and no unsafe action.

## Stage 4 - LangGraph multi-agent control (hours 9-13)

Features: `LLM-001`, `AGT-001`, `AGT-002`

1. Define Pydantic records and typed `RunState`.
2. Implement the LangGraph nodes and conditional routes specified in `techspec.md`.
3. Add Energy, Comfort, and Supervisor/Reflection prompts with compact evidence and strict schemas.
4. Connect graph nodes to MCP tools; do not let agents access the EnergyPlus session directly.
5. Give the three sequential roles one monotonic 30-second deadline. Use a
   deadline-bounded provider and role-specific reserves of 29 seconds before Energy,
   20 before Comfort, 11 before Supervisor, and 3 before any submit. Skip remaining
   inference into deterministic fallback if the relevant budget is unavailable.
6. Bind Supervisor evidence to separate canonical Energy and Comfort outputs; never
   forward model-invented evidence as trusted provenance.
7. Evaluate reflection deterministically by comparing predicted energy/comfort direction
   with measured post-action energy delta, PMV compliance, and emergency safety.
8. Stream only bounded redacted graph transitions to the event log.
9. Test approved, revised, retry-exhausted, provider/deadline fallback, completion, and
   fatal paths using a fake provider, MCP gateway, and monotonic clock before live use.

Exit gate: a simulated observation sequence passes through all agent states and safely applies or rejects actions.

## Stage 5 - Experiment, metrics, and tuning (hours 13-16)

Features: `MET-001`, `RUN-001`

1. Run the complete agent-controlled experiment.
2. Calculate energy, PMV, PPD, action, latency, error, rejection, and fallback metrics.
3. Tune only documented parameters: control interval, prompt wording, setpoint step, and unoccupied setback.
4. Preserve each run; never overwrite unfavorable results.
5. Select the best valid run by energy savings subject to at least 90% occupied PMV compliance.

Exit gate: controlled HVAC energy is below baseline, comfort acceptance passes, and the audit trail proves closed-loop autonomy.

## Stage 6 - Dashboard and quality (hours 16-18)

Features: `UI-001`, `TST-001`

1. Build the read-only Streamlit dashboard from persisted events and summaries.
2. Show the current graph state, agent reasoning, actuation, PMV band, and cumulative baseline/control energy.
3. Run lint, type checking, unit tests, MCP contract tests, the EnergyPlus smoke test, and dashboard smoke test.
4. Rehearse a complete local demonstration from clean startup.

Exit gate: all automated checks pass and the demo works without manual recovery.

## Stage 7 - Submission (hours 18-20)

Feature: `DEL-001`

1. Finalize README setup/run instructions and the short architecture report.
2. Commit the baseline and controlled IDFs, compact result artifacts, and screenshots.
3. Complete the supplied presentation template only after final metrics are known.
4. Record a maximum three-minute video showing graph states, live observation, MCP tool call, actuator update, and measured comparison.
5. Push the public GitHub repository and assemble the required PDF/ZIP upload.
6. Reserve the final 30 minutes for link, archive, and portal verification.

## Planned commands

These are interface targets to implement during `FND-001`; they do not exist yet:

```powershell
python -m bms_agent.cli doctor
python -m bms_agent.cli run-baseline
python -m bms_agent.cli run-agent
python -m bms_agent.cli compare --baseline <run-id> --controlled <run-id>
python -m streamlit run src/bms_agent/dashboard/app.py
pytest
ruff check .
pyright
```

## Change control

- A feature can move to `done` only when its acceptance criteria in `featurelist.json` pass.
- The working agent must update `featurelist.json`, `progress.md`, and `current-status.md` in the same change that completes, starts, or blocks a feature.
- The working agent must also update this execution-status section whenever a completed work unit changes the active stage or gate.
- Revise the implementation sequence only when evidence changes dependencies, estimates, fallbacks, architecture, or scope; preserve the original plan otherwise.
- Documentation synchronization is part of feature completion and happens automatically without a user reminder.
- Do not add stretch features before `RUN-001` passes.
- When a fallback model, EnergyPlus example, comfort band, or experiment period changes, record the reason and resulting compatibility impact in `current-status.md`.
