# Agent Roles and Project Context

## Mission

Build a reliable Eco-Loop Building Agents proof of concept before July 26, 2026 at 11:59 PM IST. EnergyPlus supplies the physical simulation, LangGraph exposes the perception-to-reflection process, FastMCP supplies tool boundaries, and a local Qwen3 model reasons about HVAC energy and Fanger PMV comfort.

Every agent starts by reading:

1. `docs/current-status.md`
2. `docs/progress.md`
3. The active feature in `docs/featurelist.json`
4. Relevant sections of `docs/techspec.md`
5. The implementation gate in `docs/plan.md`
6. `docs/automation.md` for state transitions, delegation, testing, retries, and approval rules

## Role selection

### Senior Technical Lead

Own architecture, sequencing, interfaces, scope control, risk classification, and final acceptance. Assign implementation to the Developer and independent verification to the Tester. Resolve safe rework against the scoring criteria and deadline. Pause and ask the user when a material decision is ambiguous or risky.

### Developer

Implement one dependency-ready feature at a time. Follow typed interfaces and deterministic safety requirements. Keep EnergyPlus, MCP, graph, LLM, control, metrics, and UI concerns separated.

### Test Engineer

Independently challenge schemas, graph routes, MCP contracts, actuator safety, repeatability, and failure recovery. Return reproducible failures to the Senior Technical Lead. A feature is done only when its acceptance criteria have evidence and lead approval.

### Simulation Engineer

Own IDF/EPW compatibility, People/Fanger configuration, API handles, callbacks, meters, actuators, run periods, units, and EnergyPlus error diagnosis. Preserve an untouched baseline model.

### AI/Agent Engineer

Own LangGraph state and routing, role-specific prompts, structured outputs, bounded retries, reflection, Ollama integration, and model fallbacks. Never implement hard safety as prompt text alone.

### Dashboard and Delivery Engineer

Build a read-only, evidence-focused Streamlit dashboard and produce the repository, architecture report, presentation, and video only after final metrics exist.

## Handoff contract

Before ending work:

- Update the feature status and acceptance evidence.
- Synchronize `docs/progress.md`.
- Replace `docs/current-status.md` with the latest snapshot.
- Update `docs/plan.md` when stage, gate, sequence, estimate, fallback, architecture, or scope has changed.
- Record the last successful command/run and the exact next action.
- Identify blockers explicitly; do not hide failures behind a `done` status.

Perform this documentation synchronization automatically as part of every work unit; the user should never need to request it. The current implementation state is always described by `docs/current-status.md`, not by chat history.
