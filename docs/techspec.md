# Eco-Loop Building Agents Technical Specification

## 1. Project summary

Eco-Loop Building Agents is a hackathon proof of concept for autonomous building control. EnergyPlus is the physics-based digital building, and LangGraph coordinates a closed perception-reasoning-action-feedback loop whose agent roles may use a local open-source LLM for bounded advice and explanations. Deterministic optimization and safety code remain authoritative. The system must reduce HVAC electricity consumption without sacrificing occupied-zone thermal comfort.

The implementation uses Python, LangGraph, FastMCP, EnergyPlus, Ollama/Qwen3, and Streamlit. Three logical agents share one local model:

- **Energy Agent:** recommends an energy-efficient HVAC setpoint.
- **Comfort Agent:** evaluates current and predicted comfort risk using Fanger PMV.
- **Supervisor/Reflection Agent:** reconciles recommendations, invokes MCP tools, evaluates outcomes, and revises rejected or harmful decisions.

The word "optimal" means a bounded supervisory decision that minimizes measured energy while satisfying explicit comfort and safety constraints. The project does not claim a mathematically global optimum.

## 2. Objective and success criteria

### Objective

Build a repeatable baseline-versus-agent experiment in which the agent:

1. Observes EnergyPlus sensor and meter values.
2. Reasons about energy and PMV objectives.
3. Calls MCP tools to propose and apply a control action.
4. Injects the approved setpoint into the active EnergyPlus run.
5. Measures the result and reflects before the next decision.
6. Completes an accelerated seven-day New Delhi summer simulation without manual control.

### Required success criteria

- Baseline and controlled runs use the same model, weather, run period, timestep, and occupancy assumptions.
- The controlled run completes without an unhandled exception.
- Every applied action passes deterministic validation.
- The action log proves observation, agent reasoning, MCP invocation, actuator write, and post-action evaluation.
- Controlled HVAC electricity is lower than the baseline.
- Occupied PMV is within `[-0.5, +0.5]` for at least 90% of occupied samples.
- No occupied PMV sample may remain outside `[-1.0, +1.0]` without triggering a corrective or fallback action.

### Target results

- At least 5% HVAC electricity reduction.
- At least 95% occupied-time PMV compliance in `[-0.5, +0.5]`.
- A maximum of two LLM correction attempts per decision.
- A maximum average agent decision latency of 10 seconds on the development machine.

## 3. Scope

### In scope

- One EnergyPlus small-office model, initially `5ZoneAirCooled.idf`.
- New Delhi EPW weather data and a representative seven-day cooling period.
- Zone temperature, Fanger PMV/PPD, occupancy, outdoor temperature, HVAC energy/power, and current setpoint observations.
- One shared cooling setpoint action for the initial multi-zone implementation.
- Fixed baseline schedule and AI-controlled schedule.
- Three LangGraph agent roles, deterministic safety guard, reflection, bounded retries, and fallback control.
- Local MCP server, local Ollama inference, structured event logging, Streamlit dashboard, tests, documentation, public GitHub repository, presentation, and a maximum three-minute demonstration video.

### Deferred unless the core system is stable early

- Independent setpoints for every zone.
- Fan-speed or ventilation control.
- Carbon-intensity, tariff-responsive, or forecast-based optimization.
- Fault detection, fault injection, reinforcement learning, model training, PostgreSQL, cloud deployment, and a Next.js frontend.
- Docker packaging is optional; native Windows execution is the primary development path.

## 4. Functional requirements

| ID | Requirement |
|---|---|
| FR-01 | Run an unchanged baseline EnergyPlus simulation and persist raw outputs plus normalized metrics. |
| FR-02 | Run an agent-controlled simulation with the same experimental inputs. |
| FR-03 | Read zone air temperature, PMV, PPD, occupancy, outdoor temperature, HVAC energy/power, and active setpoint from EnergyPlus. |
| FR-04 | Apply a validated cooling-setpoint override to the active simulation through an EnergyPlus actuator. |
| FR-05 | Execute Energy, Comfort, Supervisor, Validation, Actuation, Evaluation, and Reflection states through LangGraph. |
| FR-06 | Expose EnergyPlus observations and control operations as typed FastMCP tools. |
| FR-07 | Require Qwen3 responses to conform to Pydantic/JSON schemas. |
| FR-08 | Reject malformed, stale, out-of-range, or excessively changing setpoint proposals. |
| FR-09 | Retry rejected decisions at most twice and then apply a deterministic fallback action. |
| FR-10 | Persist every state transition, tool call, proposal, validation result, action, and outcome. |
| FR-11 | Calculate energy savings, cost savings, PMV compliance, comfort violations, decision latency, and fallback counts. |
| FR-12 | Display live/latest run state and baseline-versus-agent results in Streamlit. |
| FR-13 | Export presentation-ready CSV/JSON summaries and chart images. |
| FR-14 | Inspect EnergyPlus runtime errors and surface actionable failures through an MCP tool. |

## 5. Non-functional requirements

| Category | Requirement |
|---|---|
| Reliability | A seven-day accelerated simulation must complete without an unhandled exception. LLM or MCP failure must not terminate EnergyPlus unsafely. |
| Safety | Deterministic code, not the LLM, owns hard bounds, rate limits, retry limits, stale-data checks, and fallback behavior. |
| Reproducibility | Baseline and controlled configurations, seeds where supported, software versions, prompts, model tag, weather file, and run period must be recorded. |
| Auditability | Each decision receives a correlation ID linking observations, proposals, validation, MCP calls, actuator result, and outcome. |
| Performance | Sensor collection occurs each EnergyPlus timestep; LLM decisions occur every simulated 60 minutes or immediately after a safety violation. |
| Maintainability | EnergyPlus, LLM, MCP, graph, metrics, and dashboard logic must be separated behind typed interfaces. |
| Portability | Native Windows is required. Docker support must not become a prerequisite for the demonstration. |
| Security | Bind local services to `127.0.0.1`; never commit tokens, machine-specific secrets, or personal paths. |
| Explainability | Each applied action must include a concise reason referencing energy and comfort evidence. |

## 6. System architecture

```text
Streamlit dashboard  ←  JSONL/CSV event and metric stores
                              ↑
LangGraph orchestrator ── MCP client
   Energy Agent              │
   Comfort Agent             │
   Supervisor/Reflection     ▼
                       FastMCP server
                              │
                       SimulationSession
                      observations/actions
                              │
                 EnergyPlus Python Runtime API
                              │
                       active simulation
```

### Components

1. **SimulationSession**
   - Owns the EnergyPlus API state and worker thread.
   - Registers runtime callbacks.
   - Resolves variable, meter, and actuator handles only after API data is ready.
   - Publishes a control observation at each decision boundary.
   - Blocks that boundary until an approved or fallback action is available, then writes the actuator and resumes.

2. **FastMCP server**
   - Runs locally and owns or shares the `SimulationSession`.
   - Exposes typed observations, actions, status, summaries, and error inspection.
   - Returns structured errors rather than leaking raw exceptions.

3. **LangGraph orchestrator**
   - Uses a typed shared state.
   - Executes named nodes with conditional retry, fallback, continue, and finish edges.
   - Streams node/state events for dashboard visibility.

4. **LLM provider**
   - Primary: Ollama `qwen3:4b-instruct` in Q4 quantization.
   - Fallback: `qwen3:1.7b`.
   - Uses short prompts, zero/low temperature, at most 64 output tokens per role, and structured output.
   - Produces proposals only; it has no direct simulation or actuator authority.
   - The provider interface must permit a remotely hosted open-source model without changing graph logic.

5. **Metrics and event store**
   - JSONL is the authoritative append-only decision log.
   - CSV stores timestep metrics and final comparison data.
   - No database is required for the proof of concept.

6. **Dashboard**
   - Reads logs and outputs without controlling the simulation directly.
   - Shows current graph node, latest action, energy trajectory, PMV, comfort-band compliance, savings, and fallbacks.

## 7. LangGraph process

### Graph

```text
START → initialize_run → await_observation
      → energy_agent → comfort_agent → supervisor
      → validate_action
          approved → apply_action → advance_and_evaluate → reflect
          rejected → revise_decision → supervisor
          retries_exhausted → fallback_action → apply_action
      → continue_or_finish
          continue → await_observation
          complete → finalize_run → END
          fatal → abort_safely → END
```

Agent nodes execute sequentially because they share one local model. This avoids concurrent inference pressure while keeping responsibilities independent and observable. Each node has a deterministic implementation/fallback, so unavailable, slow, malformed, or incorrect LLM advice cannot block safe control.

### Shared state contract

`RunState` must contain:

- `run_id`, `decision_id`, `mode`, `simulation_status`, and current `graph_node`.
- Current `Observation` and a bounded recent trend window.
- `EnergyProposal`, `ComfortAssessment`, and `ControlAction`.
- Validation result, retry count, fallback reason, and error details.
- Previous applied action and its measured outcome.
- Accumulated metrics and final summary.

Agent outputs must be immutable typed records. Nodes return partial state updates rather than modifying global dictionaries.

## 8. MCP interface

The initial server exposes these tools:

| Tool | Input | Output/behavior |
|---|---|---|
| `start_simulation` | mode, IDF path, EPW path, run ID | Starts baseline or controlled run and returns status. |
| `get_observation` | run ID | Returns the current pending observation with timestamp and sequence number. |
| `get_recent_trend` | run ID, sample count | Returns bounded temperature, PMV, power, occupancy, and action history. |
| `get_control_constraints` | run ID | Returns comfort band, action bounds, rate limits, and decision interval. |
| `apply_control_action` | run ID, decision ID, setpoint | Accepts only a server-validated action and releases the simulation callback. |
| `get_run_status` | run ID | Returns initializing, waiting, running, completed, or failed. |
| `inspect_simulation_errors` | run ID | Returns normalized severe/fatal EnergyPlus errors and recent runtime exceptions. |
| `get_run_summary` | run ID | Returns final energy, comfort, latency, action, and failure metrics. |
| `reset_simulation` | run ID | Cleans a completed/failed session; must refuse to silently discard an active run. |

Tool models must include units. Observations include a monotonically increasing sequence number; actions for an old sequence are rejected as stale.

## 9. EnergyPlus integration

### Model and weather

- Start with the installed example `5ZoneAirCooled.idf`.
- Add or verify `People` objects configured for the Fanger comfort model.
- Add required `Output:Variable` and `Output:Meter` objects.
- Use an official New Delhi EPW file.
- Keep an untouched baseline IDF and a version-controlled controlled IDF.
- If the example cannot expose a reliable schedule actuator within the initial feasibility gate, switch to the smallest conditioned official example that supports People/PMV and a writable thermostat schedule.

### Sensors

- `Zone Mean Air Temperature`
- `Zone Thermal Comfort Fanger Model PMV`
- `Zone Thermal Comfort Fanger Model PPD`
- Occupant count or occupancy schedule value
- `Site Outdoor Air Drybulb Temperature`
- HVAC electricity meter or facility HVAC demand rate
- Active cooling schedule/setpoint value

### Actuation

Use a named cooling schedule with EnergyPlus's `Schedule Value` actuator. Exact component type, control type, and key must be discovered from the model's actuator dictionary and asserted during startup. Missing or `-1` handles are fatal configuration errors and must stop the run before control begins.

### Timing

- EnergyPlus timestep: 10 or 15 minutes, depending on the selected example.
- Observation capture: every timestep.
- Normal control interval: every simulated 60 minutes.
- Immediate control: next valid callback after occupied PMV crosses the emergency band.
- Action effect is evaluated over the following decision interval.

## 10. Control policy and guardrails

### Default constraints

- Comfort target while occupied: `-0.5 ≤ PMV ≤ +0.5`.
- Emergency occupied band: `-1.0 ≤ PMV ≤ +1.0`.
- Cooling setpoint: initially `22°C–28°C`.
- Maximum normal change: `1°C` per decision.
- Unoccupied strategy: allow the upper cooling limit unless pre-cooling is justified.
- Maximum LLM revisions: two.
- An LLM proposal is advisory until it passes deterministic semantic and safety validation.

### Deterministic validation

The guard rejects an action when:

- Required fields or units are missing.
- Observation sequence is stale.
- Setpoint is outside configured bounds.
- Change exceeds the rate limit without an emergency override.
- Action would worsen an existing hot/cold PMV violation.
- Decision reason does not reference both energy and comfort.

### Fallback controller

If the LLM, MCP client, or validation path fails:

- If occupied and PMV is above `+0.5`, lower the cooling setpoint by `0.5°C`, respecting the lower bound.
- If occupied and PMV is below `-0.5`, raise it by `0.5°C`, respecting the upper bound.
- If occupied and comfortable, keep the last safe setpoint.
- If unoccupied, use the configured setback setpoint.
- If observation data is invalid, retain the last known safe action and log the fault.

## 11. Metrics

### Energy

- Baseline HVAC electricity, kWh.
- Controlled HVAC electricity, kWh.
- `savings_percent = (baseline_kwh - controlled_kwh) / baseline_kwh * 100`.
- Illustrative cost savings at a documented flat tariff of `₹8/kWh`.

### Comfort

- Mean and maximum absolute occupied PMV.
- Occupied samples inside `[-0.5, +0.5]`.
- Occupied samples outside `[-1.0, +1.0]`.
- Comfort compliance percentage and violation duration.
- Mean PPD when available.

### Autonomy and reliability

- Decisions proposed/applied/rejected.
- Revision and fallback counts.
- LLM and MCP latency.
- Simulation errors and recovery events.
- Longest period without an approved action.

## 12. Data contracts and storage

All persisted records include `schema_version`, `run_id`, and UTC timestamp.

- `observations.csv`: one row per sampled timestep and zone.
- `actions.jsonl`: proposals, validation, MCP invocation, applied action, and explanation.
- `graph-events.jsonl`: LangGraph node transitions and errors.
- `summary.json`: final normalized metrics and experiment metadata.
- `comparison.csv`: baseline and controlled values used by dashboard charts.

Generated run artifacts belong under `runs/<run_id>/` and must not overwrite earlier runs. Large EnergyPlus-generated files should be excluded from Git, while compact summaries and a curated winning run may be committed.

## 13. Dashboard requirements

The local Streamlit dashboard must show:

- Run status and currently active LangGraph node.
- Latest Energy, Comfort, and Supervisor outputs.
- Zone PMV lines with the target band shaded.
- Zone temperatures and applied setpoint over simulated time.
- Baseline versus controlled cumulative HVAC energy.
- Savings percentage, cost savings, comfort compliance, rejected actions, and fallbacks.
- A chronological decision/reflection table suitable for the demonstration video.

The dashboard is read-only. Simulation control remains in the CLI/orchestrator so a UI failure cannot stop or corrupt a run.

## 14. Failure handling

- **EnergyPlus initialization failure:** normalize `.err` output, mark run failed, and do not start LangGraph decisions.
- **Missing handle:** fail fast with requested variable/actuator names and available API/EDD evidence.
- **Ollama unavailable:** try the configured smaller local model, then use deterministic fallback for the run.
- **Malformed model response:** record raw response safely, request one structured revision, then follow retry limits.
- **MCP timeout:** retry once for read-only tools; do not blindly repeat an action tool without checking decision ID/idempotency.
- **Stale observation/action:** reject and refresh observation.
- **Dashboard failure:** continue simulation and preserve logs.
- **Fatal EnergyPlus error:** release waiting threads, close the session, and preserve all diagnostic artifacts.

## 15. Planned code organization

```text
src/bms_agent/
  simulation/    EnergyPlus session, callbacks, handles, model preparation
  mcp_server/    FastMCP server, tool schemas, session registry
  graph/         LangGraph state, nodes, routing, prompts
  control/       constraints, validation, deterministic fallback
  llm/           Ollama provider and model-independent interface
  metrics/       event logging, aggregation, baseline comparison
  dashboard/     Streamlit application
  cli.py         setup checks, baseline run, agent run, comparison
tests/
models/
weather/
runs/
docs/
```

The implementation should use Python 3.11 or 3.12, `pyproject.toml`, Pydantic v2, Ruff, Pyright, and Pytest. Exact dependency versions must be locked after the EnergyPlus compatibility spike.

## 16. Verification and acceptance tests

1. Unit tests for schemas, units, constraints, stale actions, rate limits, retry routing, and fallback decisions.
2. LangGraph tests for approved, rejected/revised, retries-exhausted, completion, and fatal-error paths using fake agents.
3. MCP contract tests for structured responses, errors, and idempotent action IDs.
4. EnergyPlus one-day smoke test proving sensor handles and an observable setpoint actuator change.
5. Baseline repeatability test with equivalent energy totals across two runs within an agreed numerical tolerance.
6. Controlled seven-day integration test with no unhandled exception and no unsafe applied action.
7. Dashboard smoke test against a fixed sample run.
8. Final acceptance comparison against the required and target success criteria in Section 2.

## 17. Deliverables

- Public GitHub repository with source, tests, lockfile, setup instructions, and reproducible commands.
- Baseline and controlled `.idf` models plus weather acquisition instructions.
- Quantitative dashboard and exported comparison data.
- Architecture and technical approach documentation.
- Maximum three-minute demonstration video.
- Completed hackathon presentation based on the supplied template.
- PDF or ZIP submission package as required by the portal.

## 18. Fixed decisions and assumptions

- Deadline: July 26, 2026, 11:59 PM IST.
- One developer working with Codex.
- Local dashboard is sufficient.
- New Delhi is the weather location.
- Streamlit is the initial frontend.
- Qwen3 4B is the primary local model.
- LangGraph is required to make the process states explicit.
- Three logical agents share one model sequentially.
- PMV is the primary comfort measure.
- Native Windows execution is primary; Docker is optional.
- GitHub repository is public for submission.

## 19. Technical references

- EnergyPlus Python API: <https://energyplus.readthedocs.io/en/latest/api.html>
- EnergyPlus Fanger PMV output: <https://bigladdersoftware.com/epx/docs/25-1/input-output-reference/group-internal-gains-people-lights-other.html>
- LangGraph: <https://github.com/langchain-ai/langgraph>
- Model Context Protocol Python SDK: <https://github.com/modelcontextprotocol/python-sdk>
- Ollama tool calling and structured chat: <https://docs.ollama.com/api/chat>

## 20. Engineering automation

Feature delivery follows the controlled Developer → Tester → Senior Technical Lead state machine in `automation.md`. Work may proceed autonomously only when it is clear, reversible, and inside an already approved feature. Material ambiguity, destructive/system-wide/external actions, credentials, publication, deployment, and submission remain explicit user approval gates.
# Approved demonstration extension: LAB-001

`LAB-001` adds a separate interactive Live Scenario Lab to the local Streamlit
dashboard. It is a reduced-order, in-memory simulation for demonstrating how the
LangGraph Energy, Comfort, Supervisor, Safety, Action, and Reflection process responds
to adjustable weather, occupancy, PMV disturbance, and setpoint conditions.

The lab must:

- preserve the existing completed-run Results view as strictly read-only;
- never access an active EnergyPlus actuator, MCP session registry, or accepted-run
  artifact path;
- use the existing deterministic `22..28°C` setpoint bounds, maximum `1°C` adjacent
  change, occupied PMV target `[-0.5, +0.5]`, and emergency band `[-1, +1]`;
- support local Qwen as an advisory provider and deterministic fallback on unavailable,
  malformed, slow, or unsafe output;
- persist state only in the user's Streamlit session;
- label all sandbox energy and comfort projections as illustrative, not accepted
  EnergyPlus evidence.

This extension improves live demonstration quality but does not replace `RUN-001`
evidence or broaden the project to physical building control.
