# Submission Manifest

Last updated: 2026-07-26 12:45 IST

## Ready locally

- Source code for EnergyPlus simulation, FastMCP tools, deterministic safety,
  LangGraph roles, local Qwen provider, metrics, and Streamlit dashboard.
- Pinned baseline and controlled IDF models under `models/`.
- Locked Python dependency graph in `uv.lock`.
- Reproducible setup, baseline, controlled-run, dashboard, and test commands in
  `README.md`.
- Architecture and novelty report in `docs/architecture.md`.
- Three-minute recording script in `docs/demo-guide.md`.
- Compact accepted result under `artifacts/accepted-run/`.
- Rendered dashboard evidence at `artifacts/dashboard-accepted-run.png`.
- Feature verification records under `evidence/`.
- Final local gate: 372 tests, 90.04% branch coverage, Ruff/Pyright/lock clean.

## Accepted metrics

- Baseline HVAC electricity: 40.330583833437416 kWh.
- Controlled HVAC electricity: 33.84084809588941 kWh.
- Savings: 6.489735737548003 kWh / 16.091350832782815%.
- Occupied PMV compliance: 76.0% baseline / 90.63636363636364% controlled.
- Emergency violations: 5 baseline / 5 controlled.
- Controlled run: 672 timesteps, 168/168 applied actions, zero severe errors.

## Approval-gated external deliverables

These are intentionally not claimed complete:

- Public GitHub repository URL: no remote is configured or pushed.
- Public dashboard deployment URL: not deployed.
- Demonstration video: guide ready; recording/upload not performed.
- Presentation: not modified, following the user's explicit instruction not to touch it.
- Portal PDF/ZIP upload: not performed.

Publishing, deployment, recording/upload, presentation work, and submission require an
explicit user decision and any necessary target/template/account access.
