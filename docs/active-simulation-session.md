# SIM-003 Active Simulation Session

## Runtime boundary

`SimulationSession` owns exactly one EnergyPlus Python API state and worker thread. It
dynamically imports `pyenergyplus` from the pinned project-local EnergyPlus 26.1.0
directory, stages the controlled IDF in a unique no-overwrite run directory, requests
all sensor variables before execution, and deletes the state after every completed,
cancelled, or failed worker path.

Handles are resolved only after `api_data_fully_ready`. The session requires all five
zone temperature, Fanger PMV/PPD, occupancy, and cooling thermostat setpoint handles,
plus outdoor dry-bulb, `Clg-SetP-Sch` schedule value, `Electricity:HVAC`, and the exact
`Schedule:Compact / Schedule Value / CLG-SETP-SCH` actuator. A `-1` handle is fatal:
the API dictionary and structured missing-handle diagnostics are written, EnergyPlus is
stopped, condition waiters are released, and the state is deleted.

## Observation and action bridge

Only weather-run callbacks (`kind_of_sim == 3`) after warmup participate in control.
At the first 15-minute zone timestep of each simulated hour, the begin-zone-timestep
callback publishes an immutable observation through a condition-protected queue and
waits for at most the configured action timeout.

An action is accepted only when:

- a decision is currently pending;
- decision ID and observation sequence exactly match;
- the decision has not already been used;
- the setpoint is finite and inside `22..28 C`.

The callback writes an accepted action through the schedule actuator before heat-balance
initialization. The end-zone-timestep callback then records the requested value, direct
actuator value, observed shared schedule value, and all five observed thermostat
setpoints. No LLM participates in this session-level validation or smoke action.

`cancel`, `join`, and `shutdown` are bounded and unblock pending callback waits. A
nonzero EnergyPlus result, severe/fatal `.err` record, callback exception, missing output,
or missing handle becomes a typed failed result while preserving the unique run
directory.

## One-day real smoke

Run:

```powershell
.\.venv\Scripts\python.exe scripts\run_session_smoke.py
```

The accepted smoke run `sim003-smoke-20260726-015626`:

- completed 96 weather timesteps and published 24 hourly observations;
- resolved every required sensor, meter, schedule, and actuator handle;
- observed all five zones occupied at May 23 09:15;
- observed the original shared schedule and five thermostat setpoints at `23.9 C`;
- accepted the fresh deterministic request for `25.0 C`;
- read back the actuator, shared schedule, and all five thermostat setpoints at
  exactly `25.0 C`;
- stopped after one simulated day with EnergyPlus exit code `0`.

The full raw run remains under ignored `runs/sim003-smoke-20260726-015626/`.
Compact evidence is in `evidence/sim003/active-session-smoke.v1.json`.

## Verification coverage

Fake-API tests cover invalid, non-finite, out-of-bounds, stale, duplicate, no-pending,
timeout, cancellation, lifecycle, missing-handle, and nonzero/fatal paths. A real
integration test repeats the one-day occupied actuator proof using the pinned local API.

