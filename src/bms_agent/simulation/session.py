"""Thread-safe EnergyPlus Python API session for active schedule control."""

from __future__ import annotations

import importlib
import json
import math
import shutil
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast

from bms_agent.simulation.baseline import (
    ZONES,
    sha256_file,
    validate_run_id,
)
from bms_agent.simulation.model_prep import (
    CONTROLLED_MODEL_NAME,
    ENERGYPLUS_VERSION,
    WEATHER_STEM,
)

SETPOINT_MIN_C = 22.0
SETPOINT_MAX_C = 28.0
WEATHER_RUN_KIND = 3
ACTUATOR_COMPONENT_TYPE = "Schedule:Compact"
ACTUATOR_CONTROL_TYPE = "Schedule Value"
ACTUATOR_KEY = "CLG-SETP-SCH"
SCHEMA_VERSION = "1.0"


class SessionError(RuntimeError):
    """Base error for active simulation lifecycle and control failures."""


class SessionConfigurationError(SessionError):
    """Raised when local API inputs are missing or inconsistent."""


class ActionRejected(SessionError):
    """Raised when an external action fails deterministic session validation."""


class SessionStatus(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    WAITING = "waiting"
    STOPPING = "stopping"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class SessionConfig:
    project_root: Path
    energyplus_home: Path
    model_path: Path
    weather_path: Path
    runs_dir: Path
    max_weather_timesteps: int = 96
    action_wait_seconds: float = 0.1


@dataclass(frozen=True)
class ZoneReading:
    zone: str
    temperature_c: float
    pmv: float
    ppd_percent: float
    occupancy_people: float
    cooling_setpoint_c: float


@dataclass(frozen=True)
class ControlObservation:
    schema_version: str
    run_id: str
    decision_id: str
    sequence: int
    month: int
    day: int
    hour: int
    minute: int
    outdoor_dry_bulb_c: float
    cooling_schedule_value_c: float
    hvac_electricity_j: float
    zones: tuple[ZoneReading, ...]


@dataclass(frozen=True)
class ControlAction:
    decision_id: str
    observation_sequence: int
    setpoint_c: float
    submitted_at_utc: str


@dataclass(frozen=True)
class ActionAudit:
    decision_id: str
    observation_sequence: int
    requested_setpoint_c: float
    actuator_value_after_write_c: float
    observed_schedule_value_c: float | None = None
    observed_zone_setpoints_c: tuple[float, ...] = ()


@dataclass(frozen=True)
class SessionResult:
    run_id: str
    status: SessionStatus
    exit_code: int | None
    run_dir: Path
    weather_timesteps: int
    observation_count: int
    action_count: int
    failure: str | None

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["run_dir"] = str(self.run_dir)
        return payload


@dataclass(frozen=True)
class HandleSet:
    zone_temperature: tuple[int, ...]
    zone_pmv: tuple[int, ...]
    zone_ppd: tuple[int, ...]
    zone_occupancy: tuple[int, ...]
    zone_cooling_setpoint: tuple[int, ...]
    outdoor_dry_bulb: int
    cooling_schedule: int
    hvac_meter: int
    cooling_actuator: int


class StateManagerProtocol(Protocol):
    def new_state(self) -> object: ...
    def delete_state(self, state: object) -> None: ...


class ExchangeProtocol(Protocol):
    def request_variable(self, state: object, name: str, key: str) -> None: ...
    def api_data_fully_ready(self, state: object) -> bool: ...
    def list_available_api_data_csv(self, state: object) -> bytes: ...
    def get_variable_handle(self, state: object, name: str, key: str) -> int: ...
    def get_variable_value(self, state: object, handle: int) -> float: ...
    def get_meter_handle(self, state: object, name: str) -> int: ...
    def get_meter_value(self, state: object, handle: int) -> float: ...
    def get_actuator_handle(
        self, state: object, component_type: str, control_type: str, key: str
    ) -> int: ...
    def set_actuator_value(self, state: object, handle: int, value: float) -> None: ...
    def get_actuator_value(self, state: object, handle: int) -> float: ...
    def kind_of_sim(self, state: object) -> int: ...
    def warmup_flag(self, state: object) -> bool: ...
    def zone_time_step_number(self, state: object) -> int: ...
    def month(self, state: object) -> int: ...
    def day_of_month(self, state: object) -> int: ...
    def hour(self, state: object) -> int: ...
    def minutes(self, state: object) -> int: ...


class RuntimeProtocol(Protocol):
    def callback_begin_zone_timestep_before_init_heat_balance(
        self, state: object, callback: Callable[[object], None]
    ) -> None: ...
    def callback_end_zone_timestep_after_zone_reporting(
        self, state: object, callback: Callable[[object], None]
    ) -> None: ...
    def run_energyplus(self, state: object, arguments: list[str]) -> int: ...
    def stop_simulation(self, state: object) -> None: ...
    def clear_callbacks(self) -> None: ...


class EnergyPlusApiProtocol(Protocol):
    state_manager: StateManagerProtocol
    exchange: ExchangeProtocol
    runtime: RuntimeProtocol


def default_session_config(project_root: Path) -> SessionConfig:
    matches = sorted(
        (project_root / ".tools" / "energyplus" / ENERGYPLUS_VERSION).glob(
            "EnergyPlus-*/EnergyPlusAPI.dll"
        )
    )
    if len(matches) != 1:
        raise SessionConfigurationError(
            f"Expected one project-local EnergyPlus API library; found {len(matches)}."
        )
    home = matches[0].parent
    return SessionConfig(
        project_root=project_root,
        energyplus_home=home,
        model_path=project_root / "models" / CONTROLLED_MODEL_NAME,
        weather_path=project_root / "weather" / f"{WEATHER_STEM}.epw",
        runs_dir=project_root / "runs",
    )


def load_energyplus_api(energyplus_home: Path) -> EnergyPlusApiProtocol:
    """Dynamically load only the pinned project-local pyenergyplus package."""

    api_module = energyplus_home / "pyenergyplus" / "api.py"
    dll = energyplus_home / "EnergyPlusAPI.dll"
    if not api_module.is_file() or not dll.is_file():
        raise SessionConfigurationError(f"Incomplete EnergyPlus Python API at {energyplus_home}.")
    home_text = str(energyplus_home.resolve())
    if home_text not in sys.path:
        sys.path.insert(0, home_text)
    module = importlib.import_module("pyenergyplus.api")
    resolved_module = Path(module.__file__ or "").resolve()
    if energyplus_home.resolve() not in resolved_module.parents:
        raise SessionConfigurationError(
            f"Loaded pyenergyplus from unexpected location: {resolved_module}"
        )
    api_class = getattr(module, "EnergyPlusAPI", None)
    if api_class is None:
        raise SessionConfigurationError("EnergyPlusAPI class is unavailable.")
    return cast(EnergyPlusApiProtocol, api_class())


class SimulationSession:
    """Own one EnergyPlus state, callback thread, observation queue, and action bridge."""

    def __init__(
        self,
        config: SessionConfig,
        run_id: str,
        *,
        api: EnergyPlusApiProtocol | None = None,
    ) -> None:
        validate_run_id(run_id)
        if config.max_weather_timesteps < 1:
            raise SessionConfigurationError("max_weather_timesteps must be positive.")
        if config.action_wait_seconds < 0 or not math.isfinite(config.action_wait_seconds):
            raise SessionConfigurationError("action_wait_seconds must be finite and non-negative.")
        self.config = config
        self.run_id = run_id
        self._api = api
        self._state: object | None = None
        self._thread: threading.Thread | None = None
        self._condition = threading.Condition()
        self._observations: deque[ControlObservation] = deque()
        self._pending_observation: ControlObservation | None = None
        self._pending_action: ControlAction | None = None
        self._used_decisions: set[str] = set()
        self._status = SessionStatus.CREATED
        self._handles: HandleSet | None = None
        self._failure: str | None = None
        self._exit_code: int | None = None
        self._cancel_requested = False
        self._weather_timesteps = 0
        self._sequence = 0
        self._last_setpoint_c: float | None = None
        self._action_audits: list[ActionAudit] = []
        self._run_dir = config.runs_dir / run_id

    @property
    def status(self) -> SessionStatus:
        with self._condition:
            return self._status

    @property
    def run_dir(self) -> Path:
        return self._run_dir

    @property
    def action_audits(self) -> tuple[ActionAudit, ...]:
        with self._condition:
            return tuple(self._action_audits)

    def start(self) -> None:
        """Create isolated artifacts/state, register callbacks, and start the worker."""

        for path, label in (
            (self.config.model_path, "Controlled IDF"),
            (self.config.weather_path, "New Delhi EPW"),
        ):
            if not path.is_file():
                raise SessionConfigurationError(f"{label} is missing: {path}")
        with self._condition:
            if self._status is not SessionStatus.CREATED:
                raise SessionError("Session can only be started once.")
            self.config.runs_dir.mkdir(parents=True, exist_ok=True)
            try:
                self._run_dir.mkdir(exist_ok=False)
            except FileExistsError as error:
                raise SessionConfigurationError(
                    f"Run directory exists and will not be overwritten: {self._run_dir}"
                ) from error
            staged = self._run_dir / "input.idf"
            shutil.copy2(self.config.model_path, staged)
            if sha256_file(staged) != sha256_file(self.config.model_path):
                raise SessionConfigurationError("Staged controlled IDF hash mismatch.")
            self._api = self._api or load_energyplus_api(self.config.energyplus_home)
            self._state = self._api.state_manager.new_state()
            self._request_variables(self._state)
            self._api.runtime.callback_begin_zone_timestep_before_init_heat_balance(
                self._state, self._on_begin_zone_timestep
            )
            self._api.runtime.callback_end_zone_timestep_after_zone_reporting(
                self._state, self._on_end_zone_timestep
            )
            self._status = SessionStatus.STARTING
            self._thread = threading.Thread(
                target=self._run_worker,
                name=f"EnergyPlus-{self.run_id}",
                daemon=True,
            )
            self._thread.start()

    def _request_variables(self, state: object) -> None:
        assert self._api is not None
        exchange = self._api.exchange
        for zone in ZONES:
            people = f"{zone} PEOPLE 1"
            exchange.request_variable(state, "Zone Mean Air Temperature", zone)
            exchange.request_variable(state, "Zone Thermal Comfort Fanger Model PMV", people)
            exchange.request_variable(state, "Zone Thermal Comfort Fanger Model PPD", people)
            exchange.request_variable(state, "People Occupant Count", people)
            exchange.request_variable(
                state, "Zone Thermostat Cooling Setpoint Temperature", zone
            )
        exchange.request_variable(
            state, "Site Outdoor Air Drybulb Temperature", "Environment"
        )
        exchange.request_variable(state, "Schedule Value", ACTUATOR_KEY)

    def _resolve_handles(self, state: object) -> bool:
        assert self._api is not None
        if self._handles is not None:
            return True
        exchange = self._api.exchange
        if not exchange.api_data_fully_ready(state):
            return False
        temperature: list[int] = []
        pmv: list[int] = []
        ppd: list[int] = []
        occupancy: list[int] = []
        setpoint: list[int] = []
        missing: list[str] = []

        def variable(name: str, key: str) -> int:
            handle = exchange.get_variable_handle(state, name, key)
            if handle == -1:
                missing.append(f"variable|{name}|{key}")
            return handle

        for zone in ZONES:
            people = f"{zone} PEOPLE 1"
            temperature.append(variable("Zone Mean Air Temperature", zone))
            pmv.append(variable("Zone Thermal Comfort Fanger Model PMV", people))
            ppd.append(variable("Zone Thermal Comfort Fanger Model PPD", people))
            occupancy.append(variable("People Occupant Count", people))
            setpoint.append(
                variable("Zone Thermostat Cooling Setpoint Temperature", zone)
            )
        outdoor = variable("Site Outdoor Air Drybulb Temperature", "Environment")
        schedule = variable("Schedule Value", ACTUATOR_KEY)
        meter = exchange.get_meter_handle(state, "Electricity:HVAC")
        if meter == -1:
            missing.append("meter|Electricity:HVAC")
        actuator = exchange.get_actuator_handle(
            state, ACTUATOR_COMPONENT_TYPE, ACTUATOR_CONTROL_TYPE, ACTUATOR_KEY
        )
        if actuator == -1:
            missing.append(
                f"actuator|{ACTUATOR_COMPONENT_TYPE}|{ACTUATOR_CONTROL_TYPE}|{ACTUATOR_KEY}"
            )
        if missing:
            self._write_api_diagnostics(state, missing)
            self._fail("Missing EnergyPlus handles: " + "; ".join(missing), state)
            return False
        self._handles = HandleSet(
            tuple(temperature),
            tuple(pmv),
            tuple(ppd),
            tuple(occupancy),
            tuple(setpoint),
            outdoor,
            schedule,
            meter,
            actuator,
        )
        self._write_api_diagnostics(state, [])
        return True

    def _write_api_diagnostics(self, state: object, missing: Sequence[str]) -> None:
        assert self._api is not None
        raw = self._api.exchange.list_available_api_data_csv(state)
        (self._run_dir / "api-data.csv").write_bytes(raw)
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "runId": self.run_id,
            "missing": list(missing),
            "actuator": {
                "componentType": ACTUATOR_COMPONENT_TYPE,
                "controlType": ACTUATOR_CONTROL_TYPE,
                "key": ACTUATOR_KEY,
            },
        }
        (self._run_dir / "handle-diagnostics.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _weather_ready(self, state: object) -> bool:
        assert self._api is not None
        return (
            self._api.exchange.kind_of_sim(state) == WEATHER_RUN_KIND
            and not self._api.exchange.warmup_flag(state)
        )

    def _on_begin_zone_timestep(self, state: object) -> None:
        try:
            if not self._resolve_handles(state) or not self._weather_ready(state):
                return
            assert self._api is not None and self._handles is not None
            exchange = self._api.exchange
            if self._last_setpoint_c is not None:
                exchange.set_actuator_value(
                    state, self._handles.cooling_actuator, self._last_setpoint_c
                )
            if exchange.zone_time_step_number(state) != 1:
                return
            self._sequence += 1
            zones = tuple(
                ZoneReading(
                    zone=zone,
                    temperature_c=exchange.get_variable_value(
                        state, self._handles.zone_temperature[index]
                    ),
                    pmv=exchange.get_variable_value(state, self._handles.zone_pmv[index]),
                    ppd_percent=exchange.get_variable_value(
                        state, self._handles.zone_ppd[index]
                    ),
                    occupancy_people=exchange.get_variable_value(
                        state, self._handles.zone_occupancy[index]
                    ),
                    cooling_setpoint_c=exchange.get_variable_value(
                        state, self._handles.zone_cooling_setpoint[index]
                    ),
                )
                for index, zone in enumerate(ZONES)
            )
            observation = ControlObservation(
                schema_version=SCHEMA_VERSION,
                run_id=self.run_id,
                decision_id=f"{self.run_id}-decision-{self._sequence:04d}",
                sequence=self._sequence,
                month=exchange.month(state),
                day=exchange.day_of_month(state),
                hour=exchange.hour(state),
                minute=exchange.minutes(state),
                outdoor_dry_bulb_c=exchange.get_variable_value(
                    state, self._handles.outdoor_dry_bulb
                ),
                cooling_schedule_value_c=exchange.get_variable_value(
                    state, self._handles.cooling_schedule
                ),
                hvac_electricity_j=exchange.get_meter_value(
                    state, self._handles.hvac_meter
                ),
                zones=zones,
            )
            action = self._publish_and_wait(observation)
            if action is not None:
                exchange.set_actuator_value(
                    state, self._handles.cooling_actuator, action.setpoint_c
                )
                self._last_setpoint_c = action.setpoint_c
                audit = ActionAudit(
                    decision_id=action.decision_id,
                    observation_sequence=action.observation_sequence,
                    requested_setpoint_c=action.setpoint_c,
                    actuator_value_after_write_c=exchange.get_actuator_value(
                        state, self._handles.cooling_actuator
                    ),
                )
                with self._condition:
                    self._action_audits.append(audit)
        except Exception as error:
            self._fail(f"Callback failure: {type(error).__name__}: {error}", state)

    def _publish_and_wait(self, observation: ControlObservation) -> ControlAction | None:
        deadline = time.monotonic() + self.config.action_wait_seconds
        with self._condition:
            if self._cancel_requested or self._status is SessionStatus.FAILED:
                return None
            self._pending_observation = observation
            self._observations.append(observation)
            self._status = SessionStatus.WAITING
            self._condition.notify_all()
            while self._pending_action is None and not self._cancel_requested:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            action = self._pending_action
            self._pending_action = None
            self._pending_observation = None
            if not self._cancel_requested:
                self._status = SessionStatus.RUNNING
            return action

    def _on_end_zone_timestep(self, state: object) -> None:
        try:
            if not self._weather_ready(state) or self._handles is None:
                return
            assert self._api is not None
            self._weather_timesteps += 1
            if self._action_audits and not self._action_audits[-1].observed_zone_setpoints_c:
                exchange = self._api.exchange
                observed = tuple(
                    exchange.get_variable_value(state, handle)
                    for handle in self._handles.zone_cooling_setpoint
                )
                self._action_audits[-1] = replace(
                    self._action_audits[-1],
                    observed_schedule_value_c=exchange.get_variable_value(
                        state, self._handles.cooling_schedule
                    ),
                    observed_zone_setpoints_c=observed,
                )
            if self._weather_timesteps >= self.config.max_weather_timesteps:
                with self._condition:
                    self._status = SessionStatus.STOPPING
                    self._condition.notify_all()
                self._api.runtime.stop_simulation(state)
        except Exception as error:
            self._fail(f"End callback failure: {type(error).__name__}: {error}", state)

    def await_observation(self, timeout: float) -> ControlObservation | None:
        """Return the next hourly observation, or ``None`` after a bounded wait."""

        if timeout < 0 or not math.isfinite(timeout):
            raise ValueError("timeout must be finite and non-negative.")
        deadline = time.monotonic() + timeout
        with self._condition:
            while not self._observations:
                if self._status in {
                    SessionStatus.COMPLETED,
                    SessionStatus.CANCELLED,
                    SessionStatus.FAILED,
                }:
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            return self._observations.popleft()

    def submit_action(
        self,
        *,
        decision_id: str,
        observation_sequence: int,
        setpoint_c: float,
    ) -> ControlAction:
        """Validate and submit one fresh action for the currently pending decision."""

        if not math.isfinite(setpoint_c):
            raise ActionRejected("Setpoint must be finite.")
        if not SETPOINT_MIN_C <= setpoint_c <= SETPOINT_MAX_C:
            raise ActionRejected(
                f"Setpoint must be within {SETPOINT_MIN_C:.0f}..{SETPOINT_MAX_C:.0f} C."
            )
        with self._condition:
            pending = self._pending_observation
            if decision_id in self._used_decisions:
                raise ActionRejected("Decision already received an action.")
            if pending is None:
                raise ActionRejected("No decision is currently waiting for an action.")
            if decision_id != pending.decision_id:
                raise ActionRejected("Decision ID is stale or does not match.")
            if observation_sequence != pending.sequence:
                raise ActionRejected("Observation sequence is stale or does not match.")
            if self._pending_action is not None:
                raise ActionRejected("Decision already has a pending action.")
            action = ControlAction(
                decision_id=decision_id,
                observation_sequence=observation_sequence,
                setpoint_c=setpoint_c,
                submitted_at_utc=datetime.now(UTC).isoformat(),
            )
            self._pending_action = action
            self._used_decisions.add(decision_id)
            self._condition.notify_all()
            return action

    def _fail(self, message: str, state: object | None = None) -> None:
        with self._condition:
            if self._failure is None:
                self._failure = message
            self._status = SessionStatus.FAILED
            self._cancel_requested = True
            self._pending_observation = None
            self._pending_action = None
            self._condition.notify_all()
        if state is not None and self._api is not None:
            self._api.runtime.stop_simulation(state)

    def _run_worker(self) -> None:
        assert self._api is not None and self._state is not None
        staged = self._run_dir / "input.idf"
        arguments = [
            "-w",
            str(self.config.weather_path),
            "-d",
            str(self._run_dir),
            str(staged),
        ]
        try:
            exit_code = self._api.runtime.run_energyplus(self._state, arguments)
            self._exit_code = exit_code
            severe_details = self._energyplus_error_details()
            if (exit_code != 0 or severe_details) and self._failure is None:
                detail = f" Diagnostics: {' | '.join(severe_details)}" if severe_details else ""
                self._fail(f"EnergyPlus exited with code {exit_code}.{detail}")
            with self._condition:
                if self._status is not SessionStatus.FAILED:
                    self._status = (
                        SessionStatus.CANCELLED
                        if self._cancel_requested
                        else SessionStatus.COMPLETED
                    )
                self._condition.notify_all()
        except Exception as error:
            self._fail(f"EnergyPlus worker failure: {type(error).__name__}: {error}")
        finally:
            try:
                self._persist_audits()
                (self._run_dir / "session-result.json").write_text(
                    json.dumps(self.result().to_dict(), indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            finally:
                try:
                    self._api.runtime.clear_callbacks()
                finally:
                    self._api.state_manager.delete_state(self._state)
                    self._state = None

    def _energyplus_error_details(self) -> list[str]:
        error_path = self._run_dir / "eplusout.err"
        if not error_path.is_file():
            return []
        details: list[str] = []
        for line in error_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "** Severe" in line or "** Fatal" in line:
                details.append(line.strip())
        return details[:10]

    def _persist_audits(self) -> None:
        path = self._run_dir / "actions.jsonl"
        with path.open("w", encoding="utf-8", newline="\n") as file_handle:
            for audit in self.action_audits:
                file_handle.write(json.dumps(asdict(audit), sort_keys=True) + "\n")

    def cancel(self) -> None:
        """Request bounded simulation termination and unblock callback waiters."""

        state: object | None
        with self._condition:
            if self._status in {
                SessionStatus.COMPLETED,
                SessionStatus.CANCELLED,
                SessionStatus.FAILED,
            }:
                return
            self._cancel_requested = True
            self._status = SessionStatus.STOPPING
            state = self._state
            self._condition.notify_all()
        if state is not None and self._api is not None:
            self._api.runtime.stop_simulation(state)

    def join(self, timeout: float) -> bool:
        """Wait at most ``timeout`` seconds and report whether the worker stopped."""

        if timeout < 0 or not math.isfinite(timeout):
            raise ValueError("timeout must be finite and non-negative.")
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def shutdown(self, timeout: float = 5.0) -> bool:
        """Cancel if needed and join without an unbounded wait."""

        self.cancel()
        return self.join(timeout)

    def result(self) -> SessionResult:
        """Return the latest immutable lifecycle result."""

        with self._condition:
            return SessionResult(
                run_id=self.run_id,
                status=self._status,
                exit_code=self._exit_code,
                run_dir=self._run_dir,
                weather_timesteps=self._weather_timesteps,
                observation_count=self._sequence,
                action_count=len(self._action_audits),
                failure=self._failure,
            )
