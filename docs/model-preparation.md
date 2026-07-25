# SIM-001 Model and Weather Preparation

## Reproducible model set

The experiment uses EnergyPlus 26.1.0 `5ZoneAirCooled.idf`. The model generator rejects
any source whose byte count or SHA-256 differs from the pinned official example.

| Artifact | Purpose | SHA-256 |
|---|---|---|
| `models/5ZoneAirCooled.official.idf` | Untouched official source copy | `0187CF7F...0C716CD` |
| `models/5ZoneAirCooled.baseline.v1.idf` | Fixed-schedule baseline | `88CD53D1...664A34F1` |
| `models/5ZoneAirCooled.controlled.v1.idf` | Runtime-actuated experiment | `E0C69260...14F3A13B` |
| `models/model-manifest.v1.json` | Machine-readable provenance and assumptions | Generated |

Baseline and controlled IDFs contain identical EnergyPlus objects. Only their leading
mode comment differs. This allows the runtime controller to be the sole experimental
difference.

Regenerate them from the installed example:

```powershell
.\.venv\Scripts\python.exe -m bms_agent.simulation.model_prep
```

## Experiment configuration

- Location: New Delhi Safdarjung Airport (`28.58860 N`, `77.22220 E`, UTC+5:30,
  elevation 214.9 m).
- Run period: May 23 through May 29, modeled as Monday through Sunday.
- Timestep: four per hour (15 minutes).
- Sizing: matching New Delhi TMYx DDY heating 99% and cooling 1% design conditions.
- Five occupied zones retain the official occupancy and activity schedules.
- Every `People` object uses Fanger with explicit schedules:
  work efficiency `0.0`, summer clothing `0.50 clo`, and air velocity `0.15 m/s`.
- The baseline retains the official `Clg-SetP-Sch` values. The controlled run later
  overrides that same shared schedule at runtime.

Both models request timestep values for zone mean air temperature, Fanger PMV/PPD,
people count, outdoor dry-bulb, zone cooling thermostat setpoint, `Clg-SetP-Sch`
schedule value, and `Electricity:HVAC`.

## Weather acquisition

Weather data comes from the OneBuilding New Delhi Safdarjung TMYx 2011-2025 package:

`https://climate.onebuilding.org/WMO_Region_2_Asia/IND_India/DL_Delhi/IND_DL_New.Delhi-Safdarjung.AP.421820_TMYx.2011-2025.zip`

Download and extract it project-locally. Copy the `.epw`, `.ddy`, and `.stat` files into
`weather/`; these large acquired inputs are intentionally ignored by Git. The required
EPW SHA-256 is
`8201E41AA7517016558C369053A06B000ED038647DCDD0681512C3775DDE486B`.

## Validation and actuator proof

Run the complete SIM-001 check:

```powershell
.\scripts\validate_sim001.ps1
```

The baseline and controlled validation runs both exited `0` with zero severe errors.
EnergyPlus generated this exact actuator dictionary record:

```text
EnergyManagementSystem:Actuator Available,CLG-SETP-SCH,Schedule:Compact,Schedule Value,[ ]
```

This proves that the selected shared cooling schedule is available through the
EnergyPlus `Schedule Value` actuator. Curated evidence is stored in
`evidence/sim001/actuator-proof.csv` and `evidence/sim001/validation-report.v1.json`;
full generated dictionaries remain under ignored `.cache/validation/sim001/`.

Both runs report two preserved non-severe warnings: the warm-climate heating sizing case
autosizes the outdoor-air preheat coil flow and its controller flow to zero. The warnings
are confined to the unused summer-experiment heating path; they do not affect the
cooling-period simulation, output dictionaries, or cooling-schedule actuator.
