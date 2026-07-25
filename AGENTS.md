# Repository Guidelines

## Project Context

This repository implements Eco-Loop Building Agents, a hackathon proof of concept that uses EnergyPlus as a physics-based building environment. A Python LangGraph workflow coordinates Energy, Comfort, and Supervisor/Reflection roles through FastMCP. The system must reduce HVAC electricity while keeping occupied Fanger PMV within documented comfort limits. The delivery deadline is July 26, 2026 at 11:59 PM IST, so reliability and measurable results take precedence over optional scope.

## Required Reading and Sources of Truth

Read `docs/current-status.md` before starting. It identifies the active stage, last completed work, next action, and blockers. Then use:

- `docs/techspec.md` for requirements, interfaces, architecture, constraints, and acceptance tests.
- `docs/featurelist.json` for canonical feature IDs, dependencies, status, and completion criteria.
- `docs/plan.md` for implementation order and time gates.
- `docs/progress.md` for the project-wide checklist.
- `docs/automation.md` for Developer → Tester → Senior Lead orchestration, risk classes, and approval gates.
- `agent.md` for role selection and handoff expectations.
- `docs/safety-log.md` for unsafe conditions, isolated feature pauses, mitigations, and approvals.

If documents disagree, preserve safety constraints from `techspec.md`, then reconcile status to `docs/featurelist.json`.

## Working Rules

Implement only dependency-ready features. Do not begin Carbon, Fault Detection, Next.js, cloud deployment, database, or Docker stretch work before `RUN-001` passes. LLM output never bypasses deterministic validation. Preserve baseline inputs and previous run artifacts; every controlled result must remain reproducible and auditable.

The verified foundation commands are documented in `README.md`. Before ending any work unit, automatically synchronize `docs/featurelist.json`, `docs/progress.md`, and `docs/current-status.md`, citing tests, run IDs, or output evidence. Update `docs/safety-log.md` for every material safety finding or isolated pause. Update the execution status in `docs/plan.md` when the active stage or gate changes, and revise its sequence only when evidence changes dependencies, timing, fallbacks, architecture, or scope. This synchronization is part of the work and must never wait for a user reminder. Never mark work done merely because files exist.

Follow `docs/automation.md` for autonomous delivery. Clear, reversible, in-scope changes may proceed without interruption. Pause only the affected feature for material ambiguity, destructive actions, system-wide changes, credentials, paid services, remote publishing, deployment, or submission uploads; log the condition and continue safe non-conflicting features.
