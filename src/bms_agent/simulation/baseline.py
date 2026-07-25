"""Reproducible fixed-schedule EnergyPlus baseline pipeline."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import cast

from bms_agent.simulation.model_prep import (
    BASELINE_MODEL_NAME,
    ENERGYPLUS_VERSION,
    RUN_PERIOD,
    WEATHER_STEM,
)

SCHEMA_VERSION = "1.0"
SIMULATION_YEAR = 2011
TIMESTEPS_PER_HOUR = 4
EXPECTED_TIMESTEPS = 7 * 24 * TIMESTEPS_PER_HOUR
NEW_DELHI_TIMEZONE = timezone(timedelta(hours=5, minutes=30))
ZONES = ("SPACE1-1", "SPACE2-1", "SPACE3-1", "SPACE4-1", "SPACE5-1")
COMFORT_LOWER = -0.5
COMFORT_UPPER = 0.5
EMERGENCY_LOWER = -1.0
EMERGENCY_UPPER = 1.0
JOULES_PER_KWH = 3_600_000.0
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ENERGYPLUS_VERSION_PATTERN = re.compile(
    r"EnergyPlus,\s+Version\s+([0-9.]+)-([A-Za-z0-9]+)",
    re.IGNORECASE,
)
ENERGYPLUS_TIMESTAMP_PATTERN = re.compile(
    r"^(?P<month>\d{2})/(?P<day>\d{2})\s+"
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})$"
)


class BaselineError(RuntimeError):
    """Base class for safe baseline-pipeline failures."""


class BaselineConfigurationError(BaselineError):
    """Raised before execution when a required input is unavailable."""


class BaselineExecutionError(BaselineError):
    """Raised when EnergyPlus exits unsuccessfully or reports a severe error."""


class BaselineOutputError(BaselineError):
    """Raised when required EnergyPlus results are absent or malformed."""


@dataclass(frozen=True)
class BaselineConfig:
    """Resolved inputs for one fixed-schedule baseline run."""

    project_root: Path
    energyplus_executable: Path
    model_path: Path
    weather_path: Path
    runs_dir: Path


@dataclass(frozen=True)
class Observation:
    """One normalized zone observation at an EnergyPlus timestep boundary."""

    schema_version: str
    run_id: str
    sequence: int
    timestamp: str
    zone: str
    temperature_c: float
    pmv: float
    ppd_percent: float
    occupancy_people: float
    outdoor_dry_bulb_c: float
    cooling_setpoint_c: float
    cooling_schedule_value_c: float
    hvac_electricity_j: float


@dataclass(frozen=True)
class NormalizedBaseline:
    """Normalized observations and aggregates calculated from the same data."""

    observations: tuple[Observation, ...]
    timestep_hvac_j: tuple[float, ...]
    timestep_count: int


@dataclass(frozen=True)
class BaselineRunResult:
    """Stable result returned after a successful baseline run."""

    run_id: str
    run_dir: Path
    observations_path: Path
    summary_path: Path
    metadata_path: Path
    hvac_kwh: float
    occupied_pmv_compliance_percent: float
    warning_count: int

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible result for CLI output."""

        return {
            "runId": self.run_id,
            "runDirectory": str(self.run_dir),
            "observations": str(self.observations_path),
            "summary": str(self.summary_path),
            "metadata": str(self.metadata_path),
            "hvacKwh": self.hvac_kwh,
            "occupiedPmvCompliancePercent": self.occupied_pmv_compliance_percent,
            "warningCount": self.warning_count,
        }


@dataclass(frozen=True)
class RepeatabilityResult:
    """Comparison of two summaries produced by the baseline calculation path."""

    passed: bool
    energy_difference_kwh: float
    comfort_difference_percentage_points: float
    energy_tolerance_kwh: float
    comfort_tolerance_percentage_points: float

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible repeatability result."""

        return asdict(self)


def sha256_file(path: Path) -> str:
    """Stream a file into a lowercase SHA-256 digest."""

    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_run_id(started_at: datetime, model_sha256: str) -> str:
    """Create a deterministic-format unique run ID from time and model identity."""

    utc_time = started_at.astimezone(UTC)
    stamp = utc_time.strftime("%Y%m%dT%H%M%S%fZ")
    return f"baseline-{stamp}-{model_sha256[:8]}"


def validate_run_id(run_id: str) -> None:
    """Reject unsafe or path-like user-provided run IDs."""

    if RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise BaselineConfigurationError(
            "Run ID must start with an alphanumeric character, contain only "
            "letters, numbers, '.', '_' or '-', and be at most 128 characters."
        )


def discover_default_config(project_root: Path) -> BaselineConfig:
    """Resolve the pinned baseline inputs from the project-local environment."""

    energyplus_matches = sorted(
        (project_root / ".tools" / "energyplus" / ENERGYPLUS_VERSION).glob("*/energyplus.exe")
    )
    if len(energyplus_matches) != 1:
        raise BaselineConfigurationError(
            f"Expected one project-local EnergyPlus executable; found {len(energyplus_matches)}."
        )
    return BaselineConfig(
        project_root=project_root,
        energyplus_executable=energyplus_matches[0],
        model_path=project_root / "models" / BASELINE_MODEL_NAME,
        weather_path=project_root / "weather" / f"{WEATHER_STEM}.epw",
        runs_dir=project_root / "runs",
    )


def _require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise BaselineConfigurationError(f"{label} is missing: {path}")


def _relative_or_name(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.name


def _energyplus_version(executable: Path) -> tuple[str, str]:
    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BaselineConfigurationError(f"Cannot execute EnergyPlus: {error}") from error
    output = "\n".join((completed.stdout, completed.stderr))
    match = ENERGYPLUS_VERSION_PATTERN.search(output)
    if completed.returncode != 0 or match is None:
        raise BaselineConfigurationError(
            "EnergyPlus version could not be verified before simulation."
        )
    return match.group(1), match.group(2)


def _parse_energyplus_timestamp(value: str) -> str:
    match = ENERGYPLUS_TIMESTAMP_PATTERN.fullmatch(value.strip())
    if match is None:
        raise BaselineOutputError(f"Invalid EnergyPlus Date/Time value: {value!r}")
    parts = {name: int(raw) for name, raw in match.groupdict().items()}
    hour = parts["hour"]
    if hour > 24 or (hour == 24 and (parts["minute"] != 0 or parts["second"] != 0)):
        raise BaselineOutputError(f"Invalid EnergyPlus interval-ending time: {value!r}")
    base = datetime(
        SIMULATION_YEAR,
        parts["month"],
        parts["day"],
        0 if hour == 24 else hour,
        parts["minute"],
        parts["second"],
        tzinfo=NEW_DELHI_TIMEZONE,
    )
    timestamp = base + timedelta(days=1) if hour == 24 else base
    return timestamp.isoformat()


def _required_column(fieldnames: Sequence[str], expected: str) -> str:
    matching = [field for field in fieldnames if field.strip().casefold() == expected.casefold()]
    if len(matching) != 1:
        raise BaselineOutputError(
            f"Expected exactly one EnergyPlus CSV column {expected!r}; found {len(matching)}."
        )
    return matching[0]


def _required_float(row: Mapping[str, str | None], column: str, row_number: int) -> float:
    raw_value = row.get(column)
    if raw_value is None or not raw_value.strip():
        raise BaselineOutputError(
            f"Missing numeric value for {column!r} at EnergyPlus row {row_number}."
        )
    try:
        value = float(raw_value)
    except ValueError as error:
        raise BaselineOutputError(
            f"Invalid numeric value for {column!r} at EnergyPlus row {row_number}."
        ) from error
    if not math.isfinite(value):
        raise BaselineOutputError(
            f"Non-finite numeric value for {column!r} at EnergyPlus row {row_number}."
        )
    return value


def normalize_energyplus_csv(csv_path: Path, run_id: str) -> NormalizedBaseline:
    """Normalize ReadVarsESO output into one typed record per zone and timestep."""

    if not csv_path.is_file():
        raise BaselineOutputError(f"Required ReadVarsESO output is missing: {csv_path}")
    with csv_path.open("r", encoding="utf-8-sig", newline="") as file_handle:
        reader = csv.DictReader(file_handle)
        if reader.fieldnames is None:
            raise BaselineOutputError("EnergyPlus CSV has no header.")
        fieldnames = reader.fieldnames
        date_column = _required_column(fieldnames, "Date/Time")
        outdoor_column = _required_column(
            fieldnames,
            "Environment:Site Outdoor Air Drybulb Temperature [C](TimeStep)",
        )
        schedule_column = _required_column(
            fieldnames,
            "CLG-SETP-SCH:Schedule Value [](TimeStep)",
        )
        hvac_column = _required_column(fieldnames, "Electricity:HVAC [J](TimeStep)")
        zone_columns: dict[str, dict[str, str]] = {}
        for zone in ZONES:
            people_key = f"{zone} PEOPLE 1"
            zone_columns[zone] = {
                "temperature": _required_column(
                    fieldnames,
                    f"{zone}:Zone Mean Air Temperature [C](TimeStep)",
                ),
                "pmv": _required_column(
                    fieldnames,
                    f"{people_key}:Zone Thermal Comfort Fanger Model PMV [](TimeStep)",
                ),
                "ppd": _required_column(
                    fieldnames,
                    f"{people_key}:Zone Thermal Comfort Fanger Model PPD [%](TimeStep)",
                ),
                "occupancy": _required_column(
                    fieldnames,
                    f"{people_key}:People Occupant Count [](TimeStep)",
                ),
                "setpoint": _required_column(
                    fieldnames,
                    f"{zone}:Zone Thermostat Cooling Setpoint Temperature [C](TimeStep)",
                ),
            }

        observations: list[Observation] = []
        timestep_hvac_j: list[float] = []
        for sequence, row in enumerate(reader, start=1):
            row_number = sequence + 1
            raw_timestamp = row.get(date_column)
            if raw_timestamp is None:
                raise BaselineOutputError(f"Missing Date/Time at EnergyPlus row {row_number}.")
            timestamp = _parse_energyplus_timestamp(raw_timestamp)
            outdoor_c = _required_float(row, outdoor_column, row_number)
            schedule_c = _required_float(row, schedule_column, row_number)
            hvac_j = _required_float(row, hvac_column, row_number)
            if hvac_j < 0:
                raise BaselineOutputError(
                    f"Negative HVAC electricity at EnergyPlus row {row_number}."
                )
            timestep_hvac_j.append(hvac_j)
            for zone in ZONES:
                columns = zone_columns[zone]
                observations.append(
                    Observation(
                        schema_version=SCHEMA_VERSION,
                        run_id=run_id,
                        sequence=sequence,
                        timestamp=timestamp,
                        zone=zone,
                        temperature_c=_required_float(row, columns["temperature"], row_number),
                        pmv=_required_float(row, columns["pmv"], row_number),
                        ppd_percent=_required_float(row, columns["ppd"], row_number),
                        occupancy_people=_required_float(row, columns["occupancy"], row_number),
                        outdoor_dry_bulb_c=outdoor_c,
                        cooling_setpoint_c=_required_float(
                            row, columns["setpoint"], row_number
                        ),
                        cooling_schedule_value_c=schedule_c,
                        hvac_electricity_j=hvac_j,
                    )
                )

    timestep_count = len(timestep_hvac_j)
    if timestep_count != EXPECTED_TIMESTEPS:
        raise BaselineOutputError(
            f"Expected {EXPECTED_TIMESTEPS} timestep rows; found {timestep_count}."
        )
    if len(observations) != EXPECTED_TIMESTEPS * len(ZONES):
        raise BaselineOutputError("Normalized observation count is inconsistent.")
    return NormalizedBaseline(tuple(observations), tuple(timestep_hvac_j), timestep_count)


def _mean(values: Sequence[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


def calculate_summary(
    normalized: NormalizedBaseline,
    *,
    run_id: str,
    metadata: Mapping[str, object],
    warnings: Sequence[str],
) -> dict[str, object]:
    """Calculate energy and occupied-comfort metrics from normalized records."""

    occupied = [item for item in normalized.observations if item.occupancy_people > 0]
    occupied_pmv = [item.pmv for item in occupied]
    occupied_ppd = [item.ppd_percent for item in occupied]
    comfortable = [pmv for pmv in occupied_pmv if COMFORT_LOWER <= pmv <= COMFORT_UPPER]
    emergency = [
        pmv for pmv in occupied_pmv if pmv < EMERGENCY_LOWER or pmv > EMERGENCY_UPPER
    ]
    compliance = 100.0 * len(comfortable) / len(occupied_pmv) if occupied_pmv else 0.0
    distribution = {
        "coldBelowMinusOne": sum(pmv < EMERGENCY_LOWER for pmv in occupied_pmv),
        "coldMinusOneToMinusPointFive": sum(
            EMERGENCY_LOWER <= pmv < COMFORT_LOWER for pmv in occupied_pmv
        ),
        "comfortableMinusPointFiveToPointFive": len(comfortable),
        "warmPointFiveToOne": sum(
            COMFORT_UPPER < pmv <= EMERGENCY_UPPER for pmv in occupied_pmv
        ),
        "hotAboveOne": sum(pmv > EMERGENCY_UPPER for pmv in occupied_pmv),
    }
    hvac_j = math.fsum(normalized.timestep_hvac_j)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "runId": run_id,
        "mode": "baseline",
        "status": "completed",
        "metadata": dict(metadata),
        "energy": {
            "hvacElectricityJ": hvac_j,
            "hvacElectricityKwh": hvac_j / JOULES_PER_KWH,
            "timestepCount": normalized.timestep_count,
        },
        "comfort": {
            "targetBand": {"lower": COMFORT_LOWER, "upper": COMFORT_UPPER},
            "emergencyBand": {"lower": EMERGENCY_LOWER, "upper": EMERGENCY_UPPER},
            "totalZoneSamples": len(normalized.observations),
            "occupiedSamples": len(occupied_pmv),
            "unoccupiedSamples": len(normalized.observations) - len(occupied_pmv),
            "compliantOccupiedSamples": len(comfortable),
            "compliancePercent": compliance,
            "emergencyViolationSamples": len(emergency),
            "meanPmv": _mean(occupied_pmv),
            "meanAbsolutePmv": _mean([abs(value) for value in occupied_pmv]),
            "minimumPmv": min(occupied_pmv) if occupied_pmv else None,
            "maximumPmv": max(occupied_pmv) if occupied_pmv else None,
            "maximumAbsolutePmv": max(map(abs, occupied_pmv)) if occupied_pmv else None,
            "meanPpdPercent": _mean(occupied_ppd),
            "distribution": distribution,
        },
        "reliability": {
            "energyPlusWarningCount": len(warnings),
            "energyPlusWarnings": list(warnings),
            "energyPlusSevereErrorCount": 0,
        },
    }


def _warning_messages(error_text: str) -> list[str]:
    warnings: list[str] = []
    marker = "** Warning **"
    for line in error_text.splitlines():
        if marker in line:
            warnings.append(line.split(marker, 1)[1].strip())
    return warnings


def _severe_messages(error_text: str) -> list[str]:
    severe: list[str] = []
    marker = "** Severe  **"
    alternate_marker = "** Severe **"
    for line in error_text.splitlines():
        selected = marker if marker in line else alternate_marker
        if selected in line:
            severe.append(line.split(selected, 1)[1].strip())
    return severe


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_observations(path: Path, observations: Sequence[Observation]) -> None:
    fieldnames = list(Observation.__dataclass_fields__)
    with path.open("w", encoding="utf-8", newline="") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(asdict(observation) for observation in observations)


def _float_value(record: Mapping[str, object], key: str) -> float:
    value = record[key]
    if not isinstance(value, (int, float, str)):
        raise TypeError(f"{key} is not numeric.")
    return float(value)


class BaselineRunner:
    """Execute, preserve, normalize, and summarize a baseline simulation."""

    def __init__(self, config: BaselineConfig) -> None:
        self.config = config

    def run(
        self,
        *,
        run_id: str | None = None,
        started_at: datetime | None = None,
    ) -> BaselineRunResult:
        """Execute one unique baseline run without overwriting prior artifacts."""

        _require_file(self.config.energyplus_executable, "EnergyPlus executable")
        _require_file(self.config.model_path, "Baseline IDF")
        _require_file(self.config.weather_path, "New Delhi EPW")
        version, build = _energyplus_version(self.config.energyplus_executable)
        if version != ENERGYPLUS_VERSION:
            raise BaselineConfigurationError(
                f"Expected EnergyPlus {ENERGYPLUS_VERSION}; found {version}."
            )

        model_hash = sha256_file(self.config.model_path)
        weather_hash = sha256_file(self.config.weather_path)
        effective_started_at = started_at or datetime.now(UTC)
        effective_run_id = run_id or make_run_id(effective_started_at, model_hash)
        validate_run_id(effective_run_id)

        self.config.runs_dir.mkdir(parents=True, exist_ok=True)
        run_dir = self.config.runs_dir / effective_run_id
        try:
            run_dir.mkdir(exist_ok=False)
        except FileExistsError as error:
            raise BaselineConfigurationError(
                f"Run directory already exists and will not be overwritten: {run_dir}"
            ) from error

        metadata: dict[str, object] = {
            "schemaVersion": SCHEMA_VERSION,
            "runId": effective_run_id,
            "mode": "baseline",
            "createdAtUtc": effective_started_at.astimezone(UTC).isoformat(),
            "model": {
                "path": _relative_or_name(self.config.model_path, self.config.project_root),
                "sha256": model_hash,
            },
            "weather": {
                "path": _relative_or_name(self.config.weather_path, self.config.project_root),
                "sha256": weather_hash,
                "location": "New Delhi Safdarjung Airport, India",
            },
            "runPeriod": {
                **RUN_PERIOD,
                "simulationYear": SIMULATION_YEAR,
                "timezone": "Asia/Kolkata (+05:30)",
            },
            "timestepsPerHour": TIMESTEPS_PER_HOUR,
            "expectedTimesteps": EXPECTED_TIMESTEPS,
            "zones": list(ZONES),
            "energyPlus": {"version": version, "build": build},
            "assumptions": {
                "coolingSchedule": "Clg-SetP-Sch",
                "comfortModel": "Fanger",
                "occupiedComfortBand": [COMFORT_LOWER, COMFORT_UPPER],
                "summerClothingClo": 0.5,
                "airVelocityMPerS": 0.15,
                "workEfficiency": 0.0,
                "hvacEnergyAlignment": (
                    "The same whole-building timestep HVAC joules value is repeated on each "
                    "zone row and summed only once per sequence."
                ),
            },
        }
        _write_json(run_dir / "run-request.json", metadata)

        staged_model = run_dir / "input.idf"
        shutil.copy2(self.config.model_path, staged_model)
        if sha256_file(staged_model) != model_hash:
            raise BaselineOutputError(
                "Staged model hash differs from the canonical model; "
                f"artifacts remain in {run_dir}."
            )

        command = [
            str(self.config.energyplus_executable),
            "-r",
            "-w",
            str(self.config.weather_path),
            "-d",
            str(run_dir),
            str(staged_model),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                check=False,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                cwd=run_dir,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise BaselineExecutionError(
                f"EnergyPlus could not complete; artifacts remain in {run_dir}: {error}"
            ) from error
        (run_dir / "runner.stdout.log").write_text(
            completed.stdout, encoding="utf-8", newline="\n"
        )
        (run_dir / "runner.stderr.log").write_text(
            completed.stderr, encoding="utf-8", newline="\n"
        )
        if completed.returncode != 0:
            raise BaselineExecutionError(
                f"EnergyPlus exited {completed.returncode}; artifacts remain in {run_dir}."
            )

        end_path = run_dir / "eplusout.end"
        error_path = run_dir / "eplusout.err"
        csv_path = run_dir / "eplusout.csv"
        for path, label in (
            (end_path, "completion record"),
            (error_path, "error record"),
            (csv_path, "ReadVarsESO CSV"),
        ):
            if not path.is_file():
                raise BaselineOutputError(
                    f"EnergyPlus {label} is missing; artifacts remain in {run_dir}."
                )

        end_text = end_path.read_text(encoding="utf-8", errors="replace")
        error_text = error_path.read_text(encoding="utf-8", errors="replace")
        severe = _severe_messages(error_text)
        if severe:
            raise BaselineExecutionError(
                f"EnergyPlus reported {len(severe)} severe errors; artifacts remain in {run_dir}."
            )
        if "Completed Successfully" not in end_text:
            raise BaselineExecutionError(
                f"EnergyPlus did not report successful completion; artifacts remain in {run_dir}."
            )

        normalized = normalize_energyplus_csv(csv_path, effective_run_id)
        warnings = _warning_messages(error_text)
        summary = calculate_summary(
            normalized,
            run_id=effective_run_id,
            metadata=metadata,
            warnings=warnings,
        )
        observations_path = run_dir / "observations.csv"
        summary_path = run_dir / "summary.json"
        metadata_path = run_dir / "metadata.json"
        _write_observations(observations_path, normalized.observations)
        _write_json(metadata_path, metadata)
        _write_json(summary_path, summary)

        energy = cast(dict[str, object], summary["energy"])
        comfort = cast(dict[str, object], summary["comfort"])
        return BaselineRunResult(
            run_id=effective_run_id,
            run_dir=run_dir,
            observations_path=observations_path,
            summary_path=summary_path,
            metadata_path=metadata_path,
            hvac_kwh=_float_value(energy, "hvacElectricityKwh"),
            occupied_pmv_compliance_percent=_float_value(comfort, "compliancePercent"),
            warning_count=len(warnings),
        )


def compare_summaries(
    first_summary: Path,
    second_summary: Path,
    *,
    energy_tolerance_kwh: float = 1e-9,
    comfort_tolerance_percentage_points: float = 1e-9,
) -> RepeatabilityResult:
    """Compare baseline metrics using explicit absolute tolerances."""

    if energy_tolerance_kwh < 0 or comfort_tolerance_percentage_points < 0:
        raise ValueError("Repeatability tolerances must be non-negative.")
    try:
        first = cast(dict[str, object], json.loads(first_summary.read_text(encoding="utf-8")))
        second = cast(dict[str, object], json.loads(second_summary.read_text(encoding="utf-8")))
        first_energy = cast(dict[str, object], first["energy"])
        second_energy = cast(dict[str, object], second["energy"])
        first_comfort = cast(dict[str, object], first["comfort"])
        second_comfort = cast(dict[str, object], second["comfort"])
        energy_difference = abs(
            _float_value(first_energy, "hvacElectricityKwh")
            - _float_value(second_energy, "hvacElectricityKwh")
        )
        comfort_difference = abs(
            _float_value(first_comfort, "compliancePercent")
            - _float_value(second_comfort, "compliancePercent")
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise BaselineOutputError(f"Cannot compare baseline summaries: {error}") from error
    return RepeatabilityResult(
        passed=(
            energy_difference <= energy_tolerance_kwh
            and comfort_difference <= comfort_tolerance_percentage_points
        ),
        energy_difference_kwh=energy_difference,
        comfort_difference_percentage_points=comfort_difference,
        energy_tolerance_kwh=energy_tolerance_kwh,
        comfort_tolerance_percentage_points=comfort_tolerance_percentage_points,
    )
