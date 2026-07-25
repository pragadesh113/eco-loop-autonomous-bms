from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from bms_agent.cli import project_root
from bms_agent.simulation.session import (
    ActionRejected,
    EnergyPlusApiProtocol,
    SessionConfig,
    SessionConfigurationError,
    SessionError,
    SessionStatus,
    SimulationSession,
    default_session_config,
)


class FakeStateManager:
    def __init__(self) -> None:
        self.deleted = False

    def new_state(self) -> object:
        return object()

    def delete_state(self, _state: object) -> None:
        self.deleted = True


class FakeExchange:
    def __init__(self, *, missing: bool = False) -> None:
        self.missing = missing
        self.requested: list[tuple[str, str]] = []
        self.actuator_value = 29.4
        self.next_handle = 1

    def request_variable(self, _state: object, name: str, key: str) -> None:
        self.requested.append((name, key))

    def api_data_fully_ready(self, _state: object) -> bool:
        return True

    def list_available_api_data_csv(self, _state: object) -> bytes:
        return b"what,name,key\nActuator,Schedule Value,CLG-SETP-SCH\n"

    def _handle(self) -> int:
        if self.missing:
            self.missing = False
            return -1
        self.next_handle += 1
        return self.next_handle

    def get_variable_handle(self, _state: object, _name: str, _key: str) -> int:
        return self._handle()

    def get_variable_value(self, _state: object, _handle: int) -> float:
        return self.actuator_value

    def get_meter_handle(self, _state: object, _name: str) -> int:
        return self._handle()

    def get_meter_value(self, _state: object, _handle: int) -> float:
        return 100.0

    def get_actuator_handle(
        self,
        _state: object,
        _component_type: str,
        _control_type: str,
        _key: str,
    ) -> int:
        return self._handle()

    def set_actuator_value(self, _state: object, _handle: int, value: float) -> None:
        self.actuator_value = value

    def get_actuator_value(self, _state: object, _handle: int) -> float:
        return self.actuator_value

    def kind_of_sim(self, _state: object) -> int:
        return 3

    def warmup_flag(self, _state: object) -> bool:
        return False

    def zone_time_step_number(self, _state: object) -> int:
        return 1

    def month(self, _state: object) -> int:
        return 5

    def day_of_month(self, _state: object) -> int:
        return 23

    def hour(self, _state: object) -> int:
        return 9

    def minutes(self, _state: object) -> int:
        return 15


class FakeRuntime:
    def __init__(self, *, exit_code: int = 0) -> None:
        self.begin: Callable[[object], None] | None = None
        self.end: Callable[[object], None] | None = None
        self.exit_code = exit_code
        self.stopped = False
        self.cleared = False

    def callback_begin_zone_timestep_before_init_heat_balance(
        self,
        _state: object,
        callback: Callable[[object], None],
    ) -> None:
        self.begin = callback

    def callback_end_zone_timestep_after_zone_reporting(
        self,
        _state: object,
        callback: Callable[[object], None],
    ) -> None:
        self.end = callback

    def run_energyplus(self, state: object, _arguments: list[str]) -> int:
        if self.exit_code != 0:
            return self.exit_code
        assert callable(self.begin)
        assert callable(self.end)
        self.begin(state)
        if not self.stopped:
            self.end(state)
        return 0

    def stop_simulation(self, _state: object) -> None:
        self.stopped = True

    def clear_callbacks(self) -> None:
        self.cleared = True


class FakeApi:
    def __init__(self, *, missing: bool = False, exit_code: int = 0) -> None:
        self.state_manager = FakeStateManager()
        self.exchange = FakeExchange(missing=missing)
        self.runtime = FakeRuntime(exit_code=exit_code)


def _as_api(api: FakeApi) -> EnergyPlusApiProtocol:
    return cast(EnergyPlusApiProtocol, api)


def _fake_config(tmp_path: Path, *, wait: float = 0.2) -> SessionConfig:
    tmp_path.mkdir(parents=True, exist_ok=True)
    model = tmp_path / "controlled.idf"
    weather = tmp_path / "weather.epw"
    model.write_text("Version,26.1;", encoding="utf-8")
    weather.write_text("LOCATION,fake", encoding="utf-8")
    return replace(
        default_session_config(project_root()),
        model_path=model,
        weather_path=weather,
        runs_dir=tmp_path / "runs",
        max_weather_timesteps=1,
        action_wait_seconds=wait,
    )


def test_real_one_day_session_changes_all_setpoints(tmp_path: Path) -> None:
    config = replace(
        default_session_config(project_root()),
        runs_dir=tmp_path,
        max_weather_timesteps=96,
        action_wait_seconds=0.02,
    )
    session = SimulationSession(config, "pytest-real-session")
    session.start()
    submitted = False
    while not session.join(0):
        observation = session.await_observation(2)
        if observation is None:
            continue
        if not submitted and any(zone.occupancy_people > 0 for zone in observation.zones):
            session.submit_action(
                decision_id=observation.decision_id,
                observation_sequence=observation.sequence,
                setpoint_c=25.0,
            )
            submitted = True
    result = session.result()
    audit = session.action_audits[0]

    assert result.status is SessionStatus.COMPLETED
    assert result.exit_code == 0
    assert result.weather_timesteps == 96
    assert result.observation_count == 24
    assert submitted is True
    assert audit.requested_setpoint_c == 25.0
    assert audit.actuator_value_after_write_c == 25.0
    assert audit.observed_schedule_value_c == 25.0
    assert audit.observed_zone_setpoints_c == (25.0,) * 5
    assert (result.run_dir / "api-data.csv").is_file()
    assert (result.run_dir / "actions.jsonl").is_file()


def test_action_validation_stale_duplicate_and_bounds(tmp_path: Path) -> None:
    api = FakeApi()
    config = _fake_config(tmp_path, wait=1.0)
    session = SimulationSession(config, "fake-action", api=_as_api(api))
    session.start()
    observation = session.await_observation(1)
    assert observation is not None

    for value in (math.nan, 21.9, 28.1):
        with pytest.raises(ActionRejected):
            session.submit_action(
                decision_id=observation.decision_id,
                observation_sequence=observation.sequence,
                setpoint_c=value,
            )
    with pytest.raises(ActionRejected, match="Decision ID"):
        session.submit_action(
            decision_id="stale",
            observation_sequence=observation.sequence,
            setpoint_c=25,
        )
    with pytest.raises(ActionRejected, match="sequence"):
        session.submit_action(
            decision_id=observation.decision_id,
            observation_sequence=999,
            setpoint_c=25,
        )
    session.submit_action(
        decision_id=observation.decision_id,
        observation_sequence=observation.sequence,
        setpoint_c=25,
    )
    with pytest.raises(ActionRejected):
        session.submit_action(
            decision_id=observation.decision_id,
            observation_sequence=observation.sequence,
            setpoint_c=25,
        )
    assert session.join(2)
    assert session.action_audits[0].observed_zone_setpoints_c == (25.0,) * 5


def test_missing_handle_fails_with_dictionary_and_unblocks(tmp_path: Path) -> None:
    api = FakeApi(missing=True)
    config = _fake_config(tmp_path, wait=0)
    session = SimulationSession(config, "fake-missing", api=_as_api(api))
    session.start()

    assert session.join(2)
    result = session.result()
    assert result.status is SessionStatus.FAILED
    assert result.failure is not None and "Missing EnergyPlus handles" in result.failure
    assert session.await_observation(0) is None
    assert (result.run_dir / "api-data.csv").is_file()
    assert (result.run_dir / "handle-diagnostics.json").is_file()
    assert api.runtime.stopped is True
    assert api.state_manager.deleted is True


def test_nonzero_exit_and_no_action_timeout_are_bounded(tmp_path: Path) -> None:
    fatal_api = FakeApi(exit_code=9)
    fatal = SimulationSession(
        _fake_config(tmp_path / "fatal"), "fake-fatal", api=_as_api(fatal_api)
    )
    fatal.start()
    assert fatal.join(2)
    assert fatal.result().status is SessionStatus.FAILED
    assert fatal.result().failure == "EnergyPlus exited with code 9."

    timeout_api = FakeApi()
    timeout = SimulationSession(
        _fake_config(tmp_path / "timeout", wait=0),
        "fake-timeout",
        api=_as_api(timeout_api),
    )
    timeout.start()
    assert timeout.join(2)
    assert timeout.result().status is SessionStatus.COMPLETED
    assert timeout.result().action_count == 0


def test_cancel_unblocks_waiter_and_lifecycle_guards(tmp_path: Path) -> None:
    api = FakeApi()
    session = SimulationSession(
        _fake_config(tmp_path, wait=10), "fake-cancel", api=_as_api(api)
    )
    assert session.join(0)
    with pytest.raises(ValueError):
        session.join(-1)
    with pytest.raises(ValueError):
        session.await_observation(-1)
    session.start()
    assert session.await_observation(1) is not None
    session.cancel()
    assert session.join(2)
    assert session.result().status is SessionStatus.CANCELLED
    assert session.shutdown(0)
    with pytest.raises(SessionError, match="started once"):
        session.start()


def test_session_configuration_guards(tmp_path: Path) -> None:
    config = default_session_config(project_root())
    with pytest.raises(SessionConfigurationError, match="positive"):
        SimulationSession(
            replace(config, max_weather_timesteps=0),
            "bad-timesteps",
        )
    with pytest.raises(SessionConfigurationError, match="finite"):
        SimulationSession(
            replace(config, action_wait_seconds=math.inf),
            "bad-wait",
        )

    missing = replace(
        config,
        model_path=tmp_path / "missing.idf",
        runs_dir=tmp_path / "runs",
    )
    with pytest.raises(SessionConfigurationError, match="Controlled IDF"):
        SimulationSession(missing, "missing-model").start()
