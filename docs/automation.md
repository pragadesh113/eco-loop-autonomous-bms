# Autonomous Feature Delivery Workflow

## Purpose

This workflow lets the project advance feature-by-feature without requiring the user to repeatedly instruct the team. It is a controlled engineering loop, not unrestricted autonomy. Clear, reversible, in-scope work proceeds automatically; ambiguous or risky decisions pause and request user approval.

The canonical feature queue is `docs/featurelist.json`. The canonical live handoff is
`docs/current-status.md`. Safety findings and isolated pauses are recorded in
`docs/safety-log.md`.

## Roles

### Senior Technical Lead / Orchestrator

- Reads project state and selects the first dependency-ready feature.
- Confirms the feature's scope, acceptance criteria, dependencies, and risk class.
- Assigns implementation to the Developer and verification to an independent Tester.
- Triages failures, chooses the safest in-scope correction, and sends rework to the Developer.
- Protects architecture, deadline, reproducibility, comfort safety, and deliverable completeness.
- Is the only role allowed to declare a feature `done`.

### Developer

- Implements only the selected feature and required dependencies.
- Preserves unrelated work and follows `techspec.md`.
- Runs fast local checks while developing.
- Records implementation evidence and known limitations.
- Moves work to `dev_complete`; the Developer cannot self-approve it.

### Tester

- Reads acceptance criteria independently.
- Reviews the implementation and runs the required unit, integration, contract, simulation, or UI checks.
- Tests failure paths and guardrails, not only the happy path.
- Returns `test_passed` with evidence or `test_failed` with reproducible findings.
- Does not weaken tests or acceptance criteria to obtain a pass.

## Feature state machine

```text
todo
  ↓ dependencies satisfied
ready
  ↓ Senior Lead assigns
in_progress
  ↓ Developer finishes and runs local checks
dev_complete
  ↓ independent verification
testing
  ├─ test_failed → lead_triage → rework → in_progress
  ├─ ambiguous/risky → waiting_approval
  └─ test_passed → lead_review
                         ├─ rejected → rework → in_progress
                         └─ approved → done → select_next_feature
```

`blocked` is used only when progress requires unavailable software/data or a user/external decision. `waiting_approval` is used when the exact user decision is known and has been requested.

## Autonomous loop

1. Read `AGENTS.md`, `docs/current-status.md`, `docs/progress.md`,
   `docs/featurelist.json`, `docs/safety-log.md`, this file, and the relevant
   specification/plan sections.
2. If a prior approval request is unanswered, do not bypass it or start conflicting work.
3. Select the first `todo` feature whose dependencies are `done`.
4. Change it to `in_progress` and synchronize the progress/current-status documents.
5. Senior Lead defines the bounded work unit and risk classification.
6. Developer implements it and supplies check results.
7. Tester independently verifies every acceptance criterion.
8. If testing fails, Senior Lead identifies the safest correction and returns a specific rework task.
9. Repeat development and testing until acceptance passes, the retry budget is exhausted, or approval is needed.
10. Senior Lead reviews evidence and marks the feature `done`.
11. Synchronize all control documents automatically.
12. Continue with the next dependency-ready feature while time and safety allow.
13. If one feature is paused for safety or approval, record it in
    `docs/safety-log.md` and continue any non-conflicting dependency-ready feature. A
    local pause must not stop the whole delivery loop.

## Risk and approval policy

### Proceed autonomously

- Read-only investigation and diagnostics.
- Edits inside `V:\BMS_simulation` that implement an approved feature.
- Creation of tests, documentation, local configuration templates, and generated run artifacts.
- Commands already documented in the repository.
- Installing Python dependencies into the project `.venv`.
- Reversible refactors that do not change public behavior or scope.
- Safe rework required to pass existing acceptance criteria.

### Ask for user approval

- A requirement admits multiple materially different product or architecture choices.
- The proposed change alters locked architecture, scope, comfort limits, success criteria, deadline allocation, or public interfaces.
- Deleting or overwriting material data, models, results, documentation, or user-authored changes.
- System-wide installers, administrative changes, paid services, credentials, tokens, or account connections.
- Publishing a repository, pushing to a remote, creating a public deployment, sending messages, or uploading the submission.
- Replacing the selected EnergyPlus model or primary LLM after the documented fallback has also failed.
- Continuing when available evidence cannot distinguish a safe choice.

The approval request must state the decision, evidence, recommended choice, alternatives, and impact of waiting. Work that does not conflict with the pending decision may continue.

## Safety rules

- Deterministic safety code always outranks an LLM recommendation.
- Never weaken PMV, actuator, stale-data, retry, or fallback guards to make a test pass.
- Never mark a feature done because files merely exist.
- Never hide a failure; preserve diagnostics and failed run artifacts.
- Retry a development/test cycle at most three times for the same root cause before escalating.
- Avoid destructive Git/filesystem operations. Preserve baseline models and previous experiment runs.
- External actions remain manual approval gates even when `DEL-001` is active.

## Automatic document synchronization

At every meaningful state transition, the working agent updates:

All paths below are inside `docs/`. `safety-log.md` is additionally updated for every
material safety finding, isolated pause, mitigation, approval, and resolution.

1. `featurelist.json` — canonical feature status and acceptance criteria.
2. `progress.md` — checkbox, totals, milestone status, and verification evidence.
3. `current-status.md` — latest action, active feature/state, blockers, last successful command, and exact next action.
4. `plan.md` — execution ledger whenever stage/gate changes; implementation sequence only when evidence changes the plan.
5. `automation.md` — only when workflow, risk policy, role ownership, or approval rules change.

These updates happen in the same work unit and do not require a user reminder.

## Completion and pause conditions

Pause only the affected feature when:

- Status is `waiting_approval`.
- A blocking dependency is unavailable.
- The same root cause fails three development/test cycles.
- A destructive, external, paid, credentialed, or public action is next.

After isolating the feature, select another safe dependency-ready feature. Pause the
whole loop only when every incomplete feature is dependency-blocked or waiting for
approval, or when the submission deadline has passed.

Stop the loop when `DEL-001` is `done`. Report the final evidence and leave the automation with no further implementation work.

## Context rollover

When the active Codex task approaches its context limit, the Senior Lead creates a new
task in the same `BMS_simulation` project. Before handoff it synchronizes all control
documents, then sends the new task the workspace path, active and paused features,
latest verification evidence, exact next command, and a requirement to resume this
loop. The new task reads `AGENTS.md` and `docs/current-status.md` before making changes.

## Automation schedule

The active Codex heartbeat is `eco-loop-autonomous-delivery-loop`. It wakes this task hourly through the submission deadline. Each wake resumes from `current-status.md`, performs the safest useful work, synchronizes documentation, and reports only new progress, a concrete blocker, or an approval request.
