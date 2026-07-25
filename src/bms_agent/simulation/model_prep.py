"""Deterministically prepare the EnergyPlus models used by Eco-Loop.

The transformation intentionally accepts only the pinned EnergyPlus 26.1.0
``5ZoneAirCooled.idf`` source. This prevents an unnoticed upstream model change from
altering the baseline/controlled experiment.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

SOURCE_SHA256 = "0187cf7f2ca9c27c43d435a68a8c66a557a43678846813a7e21463a0b0c716cd"
SOURCE_SIZE_BYTES = 169_736
ENERGYPLUS_VERSION = "26.1.0"
WEATHER_STEM = "IND_DL_New.Delhi-Safdarjung.AP.421820_TMYx.2011-2025"
RUN_PERIOD = {"begin_month": 5, "begin_day": 23, "end_month": 5, "end_day": 29}
PEOPLE_COUNT = 5

SOURCE_MODEL_NAME = "5ZoneAirCooled.official.idf"
BASELINE_MODEL_NAME = "5ZoneAirCooled.baseline.v1.idf"
CONTROLLED_MODEL_NAME = "5ZoneAirCooled.controlled.v1.idf"
MANIFEST_NAME = "model-manifest.v1.json"

_ORIGINAL_RUN_PERIOD = """  RunPeriod,
    Run Period 1,            !- Name
    1,                       !- Begin Month
    1,                       !- Begin Day of Month
    ,                        !- Begin Year
    12,                      !- End Month
    31,                      !- End Day of Month
    ,                        !- End Year
    Tuesday,                 !- Day of Week for Start Day
    Yes,                     !- Use Weather File Holidays and Special Days
    Yes,                     !- Use Weather File Daylight Saving Period
    No,                      !- Apply Weekend Holiday Rule
    Yes,                     !- Use Weather File Rain Indicators
    Yes;                     !- Use Weather File Snow Indicators"""

_EXPERIMENT_RUN_PERIOD = """  RunPeriod,
    EcoLoop New Delhi May 23-29,  !- Name
    5,                       !- Begin Month
    23,                      !- Begin Day of Month
    ,                        !- Begin Year
    5,                       !- End Month
    29,                      !- End Day of Month
    ,                        !- End Year
    Monday,                  !- Day of Week for Start Day
    Yes,                     !- Use Weather File Holidays and Special Days
    Yes,                     !- Use Weather File Daylight Saving Period
    No,                      !- Apply Weekend Holiday Rule
    Yes,                     !- Use Weather File Rain Indicators
    Yes;                     !- Use Weather File Snow Indicators"""

_ORIGINAL_SITE = """  Site:Location,
    CHICAGO_IL_USA TMY2-94846,  !- Name
    41.78,                   !- Latitude {deg}
    -87.75,                  !- Longitude {deg}
    -6.00,                   !- Time Zone {hr}
    190.00;                  !- Elevation {m}"""

_NEW_DELHI_SITE = """  Site:Location,
    New.Delhi-Safdarjung.AP IND TMYx.2011-2025,  !- Name
    28.58860,                !- Latitude {deg}
    77.22220,                !- Longitude {deg}
    5.5,                     !- Time Zone {hr}
    214.9;                   !- Elevation {m}"""

_ORIGINAL_DESIGN_DAY_START = "! CHICAGO_IL_USA Annual Heating 99% Design Conditions"
_ORIGINAL_DESIGN_DAY_END = "  Site:GroundTemperature:BuildingSurface"
_NEW_DELHI_DESIGN_DAYS = """! New Delhi design conditions selected from the matching TMYx DDY.

  SizingPeriod:DesignDay,
    New.Delhi-Safdarjung.AP Ann Htg 99% Condns DB,  !- Name
    1,                       !- Month
    21,                      !- Day of Month
    WinterDesignDay,         !- Day Type
    7.1,                     !- Maximum Dry-Bulb Temperature {C}
    0.0,                     !- Daily Dry-Bulb Temperature Range {deltaC}
    DefaultMultipliers,      !- Dry-Bulb Temperature Range Modifier Type
    ,                        !- Dry-Bulb Temperature Range Modifier Day Schedule Name
    Wetbulb,                 !- Humidity Condition Type
    7.1,                     !- Wetbulb at Maximum Dry-Bulb {C}
    ,                        !- Humidity Indicating Day Schedule Name
    ,                        !- Humidity Ratio at Maximum Dry-Bulb {kgWater/kgDryAir}
    ,                        !- Enthalpy at Maximum Dry-Bulb {J/kg}
    ,                        !- Daily Wet-Bulb Temperature Range {deltaC}
    98770.,                  !- Barometric Pressure {Pa}
    0.5,                     !- Wind Speed {m/s}
    270,                     !- Wind Direction {deg}
    No,                      !- Rain Indicator
    No,                      !- Snow Indicator
    No,                      !- Daylight Saving Time Indicator
    ASHRAEClearSky,          !- Solar Model Indicator
    ,                        !- Beam Solar Day Schedule Name
    ,                        !- Diffuse Solar Day Schedule Name
    ,                        !- ASHRAE Clear Sky Optical Depth for Beam Irradiance
    ,                        !- ASHRAE Clear Sky Optical Depth for Diffuse Irradiance
    0.0;                     !- Sky Clearness

  SizingPeriod:DesignDay,
    New.Delhi-Safdarjung.AP Ann Clg 1% Condns DB=>MWB,  !- Name
    6,                       !- Month
    21,                      !- Day of Month
    SummerDesignDay,         !- Day Type
    40.8,                    !- Maximum Dry-Bulb Temperature {C}
    11.5,                    !- Daily Dry-Bulb Temperature Range {deltaC}
    DefaultMultipliers,      !- Dry-Bulb Temperature Range Modifier Type
    ,                        !- Dry-Bulb Temperature Range Modifier Day Schedule Name
    Wetbulb,                 !- Humidity Condition Type
    24.0,                    !- Wetbulb at Maximum Dry-Bulb {C}
    ,                        !- Humidity Indicating Day Schedule Name
    ,                        !- Humidity Ratio at Maximum Dry-Bulb {kgWater/kgDryAir}
    ,                        !- Enthalpy at Maximum Dry-Bulb {J/kg}
    3.7,                     !- Daily Wet-Bulb Temperature Range {deltaC}
    98770.,                  !- Barometric Pressure {Pa}
    3.6,                     !- Wind Speed {m/s}
    320,                     !- Wind Direction {deg}
    No,                      !- Rain Indicator
    No,                      !- Snow Indicator
    No,                      !- Daylight Saving Time Indicator
    ASHRAETau2017,           !- Solar Model Indicator
    ,                        !- Beam Solar Day Schedule Name
    ,                        !- Diffuse Solar Day Schedule Name
    0.848,                   !- ASHRAE Clear Sky Optical Depth for Beam Irradiance
    1.403;                   !- ASHRAE Clear Sky Optical Depth for Diffuse Irradiance

"""

_ACTIVITY_SCHEDULE = """  Schedule:Compact,
    ActSchd,                 !- Name
    Any Number,              !- Schedule Type Limits Name
    Through: 12/31,          !- Field 1
    For: AllDays,            !- Field 2
    Until: 24:00,117.239997864; !- Field 3"""

_COMFORT_SCHEDULES = """

  Schedule:Constant,
    EcoLoop-Work-Efficiency, !- Name
    Fraction,                !- Schedule Type Limits Name
    0.0;                     !- Hourly Value

  Schedule:Constant,
    EcoLoop-Summer-Clothing, !- Name
    Any Number,              !- Schedule Type Limits Name
    0.50;                    !- Hourly Value {clo}

  Schedule:Constant,
    EcoLoop-Air-Velocity,    !- Name
    Any Number,              !- Schedule Type Limits Name
    0.15;                    !- Hourly Value {m/s}"""

_ORIGINAL_ACTIVITY_FIELD = "    ActSchd;                 !- Activity Level Schedule Name"
_COMFORT_PEOPLE_FIELDS = """    ActSchd,                 !- Activity Level Schedule Name
    3.82E-8,                 !- Carbon Dioxide Generation Rate {m3/s-W}
    No,                      !- Enable ASHRAE 55 Comfort Warnings
    EnclosureAveraged,       !- Mean Radiant Temperature Calculation Type
    ,                        !- Surface Name/Angle Factor List Name
    EcoLoop-Work-Efficiency, !- Work Efficiency Schedule Name
    ClothingInsulationSchedule, !- Clothing Insulation Calculation Method
    ,                        !- Clothing Insulation Calculation Method Schedule Name
    EcoLoop-Summer-Clothing, !- Clothing Insulation Schedule Name
    EcoLoop-Air-Velocity,    !- Air Velocity Schedule Name
    Fanger;                  !- Thermal Comfort Model 1 Type"""

_OUTPUT_REQUESTS = """

! Eco-Loop SIM-001 timestep observations and actuator-discovery evidence.
  Output:Variable,*,Zone Mean Air Temperature,Timestep;
  Output:Variable,*,Zone Thermal Comfort Fanger Model PMV,Timestep;
  Output:Variable,*,Zone Thermal Comfort Fanger Model PPD,Timestep;
  Output:Variable,*,People Occupant Count,Timestep;
  Output:Variable,*,Site Outdoor Air Drybulb Temperature,Timestep;
  Output:Variable,*,Zone Thermostat Cooling Setpoint Temperature,Timestep;
  Output:Variable,Clg-SetP-Sch,Schedule Value,Timestep;
  Output:Meter,Electricity:HVAC,Timestep;

  Output:EnergyManagementSystem,
    Verbose,                 !- Actuator Availability Dictionary Reporting
    None,                    !- Internal Variable Availability Dictionary Reporting
    ErrorsOnly;              !- EMS Runtime Language Debug Output Level
"""


class ModelPreparationError(RuntimeError):
    """Raised when the pinned source cannot be transformed safely."""


@dataclass(frozen=True)
class PreparedModels:
    """Paths and hashes of the prepared model set."""

    source: Path
    baseline: Path
    controlled: Path
    manifest: Path
    source_sha256: str
    baseline_sha256: str
    controlled_sha256: str


def sha256_bytes(content: bytes) -> str:
    """Return the lowercase SHA-256 digest of ``content``."""

    return hashlib.sha256(content).hexdigest()


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise ModelPreparationError(f"Expected exactly one {label}; found {count}.")
    return text.replace(old, new, 1)


def _replace_before_marker(
    text: str, start_marker: str, end_marker: str, replacement: str, label: str
) -> str:
    if text.count(start_marker) != 1 or text.count(end_marker) != 1:
        raise ModelPreparationError(f"Expected unique markers for {label}.")
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[:start] + replacement + text[end:]


def transform_model(source_text: str, mode: str) -> str:
    """Return a prepared baseline or controlled IDF from the pinned source text."""

    if mode not in {"baseline", "controlled"}:
        raise ValueError("mode must be 'baseline' or 'controlled'")

    transformed = _replace_once(
        source_text, _ORIGINAL_RUN_PERIOD, _EXPERIMENT_RUN_PERIOD, "RunPeriod"
    )
    transformed = _replace_once(transformed, _ORIGINAL_SITE, _NEW_DELHI_SITE, "Site:Location")
    transformed = _replace_before_marker(
        transformed,
        _ORIGINAL_DESIGN_DAY_START,
        _ORIGINAL_DESIGN_DAY_END,
        _NEW_DELHI_DESIGN_DAYS,
        "New Delhi sizing design days",
    )
    transformed = _replace_once(
        transformed,
        _ACTIVITY_SCHEDULE,
        _ACTIVITY_SCHEDULE + _COMFORT_SCHEDULES,
        "ActSchd schedule",
    )

    people_count = transformed.count(_ORIGINAL_ACTIVITY_FIELD)
    if people_count != PEOPLE_COUNT:
        raise ModelPreparationError(
            f"Expected {PEOPLE_COUNT} People activity fields; found {people_count}."
        )
    transformed = transformed.replace(_ORIGINAL_ACTIVITY_FIELD, _COMFORT_PEOPLE_FIELDS)

    if "Output:EnergyManagementSystem," in transformed:
        raise ModelPreparationError("Source unexpectedly already requests EMS dictionary output.")

    mode_header = (
        f"! Eco-Loop prepared model v1 | mode={mode} | "
        f"source_sha256={SOURCE_SHA256}\n"
    )
    return mode_header + transformed.rstrip() + "\n" + _OUTPUT_REQUESTS


def prepare_models(source_path: Path, models_dir: Path) -> PreparedModels:
    """Create the immutable source copy, prepared models, and provenance manifest."""

    source_bytes = source_path.read_bytes()
    digest = sha256_bytes(source_bytes)
    if digest != SOURCE_SHA256 or len(source_bytes) != SOURCE_SIZE_BYTES:
        raise ModelPreparationError(
            "Official model does not match the pinned EnergyPlus 26.1.0 source "
            f"(sha256={digest}, bytes={len(source_bytes)})."
        )

    models_dir.mkdir(parents=True, exist_ok=True)
    source_copy = models_dir / SOURCE_MODEL_NAME
    source_copy.write_bytes(source_bytes)

    source_text = source_bytes.decode("utf-8-sig").replace("\r\n", "\n")
    baseline = models_dir / BASELINE_MODEL_NAME
    controlled = models_dir / CONTROLLED_MODEL_NAME
    baseline.write_text(transform_model(source_text, "baseline"), encoding="utf-8", newline="\n")
    controlled.write_text(
        transform_model(source_text, "controlled"), encoding="utf-8", newline="\n"
    )

    baseline_digest = sha256_bytes(baseline.read_bytes())
    controlled_digest = sha256_bytes(controlled.read_bytes())
    manifest = models_dir / MANIFEST_NAME
    manifest_data: dict[str, object] = {
        "schemaVersion": "1.0",
        "feature": "SIM-001",
        "generator": "bms_agent.simulation.model_prep",
        "energyPlusVersion": ENERGYPLUS_VERSION,
        "source": {
            "repositoryPath": f"models/{SOURCE_MODEL_NAME}",
            "upstreamRelativePath": "ExampleFiles/5ZoneAirCooled.idf",
            "sha256": digest,
            "bytes": len(source_bytes),
            "byteIdenticalToUpstream": True,
        },
        "experiment": {
            "location": "New Delhi Safdarjung Airport, India",
            "weatherStem": WEATHER_STEM,
            "runPeriod": RUN_PERIOD,
            "startDay": "Monday",
            "timestepsPerHour": 4,
            "sizingDesignDays": [
                "New.Delhi-Safdarjung.AP Ann Htg 99% Condns DB",
                "New.Delhi-Safdarjung.AP Ann Clg 1% Condns DB=>MWB",
            ],
            "comfort": {
                "model": "Fanger",
                "workEfficiencySchedule": "EcoLoop-Work-Efficiency",
                "clothingSchedule": "EcoLoop-Summer-Clothing",
                "clothingClo": 0.5,
                "airVelocitySchedule": "EcoLoop-Air-Velocity",
                "airVelocityMPerS": 0.15,
            },
            "coolingScheduleActuator": {
                "componentType": "Schedule:Compact",
                "controlType": "Schedule Value",
                "key": "Clg-SetP-Sch",
            },
        },
        "models": {
            "baseline": {
                "repositoryPath": f"models/{BASELINE_MODEL_NAME}",
                "sha256": baseline_digest,
            },
            "controlled": {
                "repositoryPath": f"models/{CONTROLLED_MODEL_NAME}",
                "sha256": controlled_digest,
            },
            "physicsDifference": "None; only the leading mode comment differs.",
        },
    }
    manifest.write_text(
        json.dumps(manifest_data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return PreparedModels(
        source=source_copy,
        baseline=baseline,
        controlled=controlled,
        manifest=manifest,
        source_sha256=digest,
        baseline_sha256=baseline_digest,
        controlled_sha256=controlled_digest,
    )


def default_energyplus_source(project_root: Path) -> Path:
    """Return the expected project-local EnergyPlus 26.1.0 example path."""

    matches = sorted(
        (project_root / ".tools" / "energyplus" / ENERGYPLUS_VERSION).glob(
            "*/ExampleFiles/5ZoneAirCooled.idf"
        )
    )
    if len(matches) != 1:
        raise ModelPreparationError(
            f"Expected one project-local 5ZoneAirCooled.idf; found {len(matches)}."
        )
    return matches[0]


def main() -> int:
    """Prepare models in the repository's ``models`` directory."""

    project_root = Path(__file__).resolve().parents[3]
    prepared = prepare_models(default_energyplus_source(project_root), project_root / "models")
    print(
        json.dumps(
            {
                "source": str(prepared.source),
                "baseline": str(prepared.baseline),
                "controlled": str(prepared.controlled),
                "manifest": str(prepared.manifest),
                "sourceSha256": prepared.source_sha256,
                "baselineSha256": prepared.baseline_sha256,
                "controlledSha256": prepared.controlled_sha256,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
