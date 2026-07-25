# SIM-002 Baseline Pipeline

## Run the baseline

The baseline CLI uses EnergyPlus 26.1.0 with ReadVarsESO (`-r`), the pinned fixed-schedule
IDF, and the New Delhi TMYx EPW:

```powershell
.\.venv\Scripts\python.exe -m bms_agent.cli run-baseline --json
```

An optional safe `--run-id` may be supplied. Every run receives a new directory under
ignored `runs/`; an existing directory is never overwritten. Custom executable, model,
weather, and artifact-root paths are available through `--energyplus`, `--model`,
`--weather`, and `--runs-dir`.

Before execution the runner validates the executable, model, weather, EnergyPlus version,
and run ID. It records canonical model/weather hashes in `run-request.json`, stages a
hash-verified `input.idf` inside the unique run directory, and executes with that directory
as the process working directory. This isolates ReadVarsESO audit/sidecar files and permits
safe concurrent independent runs.

## Preserved artifacts

Each successful run retains the raw EnergyPlus files, staged input, runner logs, and:

- `observations.csv`: 3,360 normalized rows (672 timesteps x five occupied zones).
- `metadata.json`: model/weather hashes, May 23-29 dates, simulation year, timestep,
  EnergyPlus version/build, zones, location, and comfort/energy assumptions.
- `summary.json`: HVAC joules/kWh, occupied PMV compliance and distribution, PPD,
  sample counts, and EnergyPlus warning/severe counts.

Each observation contains schema/run IDs, sequence, local interval-ending timestamp,
zone, temperature, PMV, PPD, occupancy, outdoor dry-bulb, zone cooling setpoint, shared
cooling schedule value, and aligned HVAC electricity. HVAC joules are repeated on the five
zone rows for convenient joins but are summed exactly once per sequence.

Missing inputs, invalid IDs, an unverifiable version, nonzero exit, severe error, missing
completion marker, missing output, malformed values, non-finite values, and an unexpected
timestep count fail explicitly. The unique run directory and all diagnostics remain
available after failure.

## Accepted baseline

Two simultaneous acceptance runs completed independently:

| Metric | Run A | Run B | Difference |
|---|---:|---:|---:|
| Timesteps | 672 | 672 | 0 |
| Zone observations | 3,360 | 3,360 | 0 |
| HVAC electricity | 40.330583833437416 kWh | 40.330583833437416 kWh | 0 kWh |
| Occupied PMV compliance | 76.0% | 76.0% | 0 percentage points |
| Occupied samples | 1,100 | 1,100 | 0 |
| Mean occupied PPD | 7.986975781373332% | 7.986975781373332% | 0 |

Repeatability uses absolute tolerances of `1e-9 kWh` and `1e-9` percentage points. Both
differences are zero. Compact machine-readable evidence is in
`evidence/sim002/repeatability.v1.json`; full raw runs remain under
`runs/baseline-sim002-isolated-a/` and `runs/baseline-sim002-isolated-b/`.

The fixed baseline reaches only 76.0% occupied PMV compliance. It has 259 occupied cold
samples between `-1.0` and `-0.5`, plus five below `-1.0`; this is measured baseline
behavior, not an accepted controlled-run result. The later deterministic guard must
correct or fall back when a controlled occupied sample crosses the documented bands.

Both successful runs preserve the two known non-severe New Delhi heating-sizing warnings.
They are confined to the unused summer-experiment outdoor-air preheat path.

## Concurrency regression

The first attempted parallel pair exposed a ReadVarsESO collision: EnergyPlus generated
shared sidecar/audit files outside the output directories. Failed artifacts are preserved
in `runs/baseline-sim002-acceptance-a/` and `runs/baseline-sim002-acceptance-b/`.

The runner now combines a per-run staged IDF with a per-run process working directory.
`test_concurrent_baselines_isolate_readvars_sidecars` runs two real EnergyPlus processes
simultaneously and verifies both complete with identical metrics and isolated artifacts.

