# Eco-Loop Building Agents

Eco-Loop is a hackathon proof of concept for autonomous, PMV-aware building control.
EnergyPlus provides the physics-based building environment; a Python LangGraph workflow
coordinates Energy, Comfort, and Supervisor/Reflection roles; FastMCP exposes sensing and
control tools; and Streamlit presents the measured baseline-versus-agent results.

## Current stage

The repository foundation and environment feasibility are complete. Read
[`docs/current-status.md`](docs/current-status.md) before working, then follow the active
feature and acceptance criteria in [`docs/featurelist.json`](docs/featurelist.json).

## Foundation setup

PowerShell:

```powershell
uv venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -e ".[dev]"
.venv\Scripts\python.exe -m bms_agent.cli doctor
.venv\Scripts\ruff.exe check .
.venv\Scripts\pyright.exe
.venv\Scripts\pytest.exe
```

The EnergyPlus, agent, MCP, and dashboard dependencies will be installed and locked after
the environment feasibility feature confirms compatible versions.

## EnergyPlus model preparation

Prepare and validate the pinned New Delhi baseline/controlled models:

```powershell
.\.venv\Scripts\python.exe -m bms_agent.simulation.model_prep
.\scripts\validate_sim001.ps1
```

See [`docs/model-preparation.md`](docs/model-preparation.md) for model assumptions,
weather acquisition, hashes, output requests, and actuator-dictionary evidence.

Run the reproducible fixed-schedule baseline:

```powershell
.\.venv\Scripts\python.exe -m bms_agent.cli run-baseline --json
```

See [`docs/baseline-pipeline.md`](docs/baseline-pipeline.md) for the normalized observation
schema, summary metrics, failure behavior, and repeatability evidence.

Run the real one-day active-session actuator smoke:

```powershell
.\.venv\Scripts\python.exe scripts\run_session_smoke.py
```

See [`docs/active-simulation-session.md`](docs/active-simulation-session.md) for callback
timing, typed observation/action contracts, failure behavior, and actuator proof.

## Documentation

- [`docs/techspec.md`](docs/techspec.md): requirements, architecture, interfaces, and tests.
- [`docs/plan.md`](docs/plan.md): risk-first 20-hour implementation plan.
- [`docs/progress.md`](docs/progress.md): full feature checklist.
- [`agent.md`](agent.md): role definitions and handoff protocol.
