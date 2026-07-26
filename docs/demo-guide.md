# Three-Minute Demo Guide

## Before recording

```powershell
uv sync --locked --all-extras
.\.venv\Scripts\python.exe -m bms_agent.cli doctor
.\.venv\Scripts\python.exe -m streamlit run src\bms_agent\dashboard\app.py
```

Use the accepted `controlled-run001-optimized-v3` run. Keep
[`docs/architecture.md`](architecture.md) open for the sequence diagram. The dashboard
is read-only, so navigating it cannot affect simulation evidence.

## Recording sequence

### 0:00–0:25 — Problem and result

Say: “Eco-Loop uses EnergyPlus as a physics-based building digital twin. It optimizes
HVAC electricity while enforcing occupied PMV comfort. Over seven New Delhi summer days
it saved 16.09% and achieved 90.64% comfort compliance.”

Show the top KPI row: 33.84 kWh controlled, 6.49 kWh and ₹51.92 saved, 90.64%
compliance, zero rejected actions, and 30 bounded fallbacks.

### 0:25–0:55 — Architecture

Show the closed-loop diagram. Point out:

1. EnergyPlus observations enter only through typed FastMCP tools.
2. LangGraph explicitly runs Energy, Comfort, Supervisor, and Reflection roles.
3. Deterministic validation and server-side reauthorization own actuator safety.
4. Qwen is optional/advisory; timeout or malformed output falls back safely.

### 0:55–1:25 — Agent process

Show “Latest agent cycle” and “Decision and reflection chronology.” Explain that every
decision is bound to one observation, exact setpoint, idempotency key, reflection
timestamp, and terminal outcome. Highlight one decision and the exact authorized/applied
setpoint.

### 1:25–2:05 — Comfort and physical control

Show the five PMV traces and shaded `[-0.5, +0.5]` target band. Then show zone
temperatures with the thick applied-setpoint line. Explain the hard `22–28°C` range,
maximum `1°C` adjacent change, and emergency PMV guard.

### 2:05–2:35 — Quantitative comparison

Show cumulative baseline versus controlled HVAC electricity. State the exact values:
40.3306 kWh baseline, 33.8408 kWh controlled, 16.0914% saved. Comfort improved from
76.00% to 90.64%, with no increase in emergency violations.

### 2:35–2:55 — Reproducibility and safety

Briefly show the commands in `README.md` and the verification files under `evidence/`.
State: “The final gate has 372 tests at 90.04% branch coverage, Ruff and Pyright clean,
zero severe EnergyPlus errors, and an independent unattended rehearsal.”

### 2:55–3:00 — Close

Say: “Eco-Loop demonstrates explainable multi-agent building autonomy where comfort and
physical safety are constraints, not afterthoughts.”

## Optional live closed-loop replay

Only use if recording time allows:

```powershell
.\.venv\Scripts\python.exe scripts\run_controlled_experiment.py `
  --run-id demo-unique-id `
  --mode deterministic-optimizer
```

Never reuse a run ID; the system refuses overwrite by design.
