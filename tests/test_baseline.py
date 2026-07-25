from __future__ import annotations

import csv
import json
import subprocess
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from bms_agent import cli
from bms_agent.cli import project_root
from bms_agent.simulation import baseline
from bms_agent.simulation.baseline import (
    EXPECTED_TIMESTEPS,
    ZONES,
    BaselineConfig,
    BaselineConfigurationError,
    BaselineExecutionError,
    BaselineOutputError,
    BaselineRunner,
    NormalizedBaseline,
    Observation,
    calculate_summary,
    compare_summaries,
    discover_default_config,
    make_run_id,
    normalize_energyplus_csv,
    validate_run_id,
)


def _config(runs_dir: Path) -> BaselineConfig:
    default = discover_default_config(project_root())
    return BaselineConfig(
        project_root=default.project_root,
        energyplus_executable=default.energyplus_executable,
        model_path=default.model_path,
        weather_path=default.weather_path,
        runs_dir=runs_dir,
    )


def test_real_baseline_runner_creates_normalized_artifacts(tmp_path: Path) -> None:
    result = BaselineRunner(_config(tmp_path)).run(run_id="pytest-real-baseline")

    assert result.run_id == "pytest-real-baseline"
    assert result.warning_count == 2
    assert result.hvac_kwh > 0
    assert 0 <= result.occupied_pmv_compliance_percent <= 100
    assert result.observations_path.is_file()
    assert result.summary_path.is_file()
    assert result.metadata_path.is_file()
    assert (result.run_dir / "eplusout.eso").is_file()
    assert (result.run_dir / "eplusout.mtr").is_file()
    assert (result.run_dir / "eplusout.csv").is_file()
    assert (result.run_dir / "input.idf").is_file()
    assert (result.run_dir / "eplusout.rvaudit").is_file()

    with result.observations_path.open(encoding="utf-8", newline="") as file_handle:
        rows = list(csv.DictReader(file_handle))
    assert len(rows) == EXPECTED_TIMESTEPS * len(ZONES)
    assert set(rows[0]) == set(Observation.__dataclass_fields__)
    assert rows[0]["timestamp"] == "2011-05-23T00:15:00+05:30"
    assert rows[-1]["timestamp"] == "2011-05-30T00:00:00+05:30"

    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert summary["energy"]["timestepCount"] == EXPECTED_TIMESTEPS
    assert summary["comfort"]["totalZoneSamples"] == EXPECTED_TIMESTEPS * len(ZONES)
    assert summary["comfort"]["occupiedSamples"] > 0
    assert summary["reliability"]["energyPlusSevereErrorCount"] == 0
    assert metadata["runPeriod"]["begin_month"] == 5
    assert metadata["runPeriod"]["end_day"] == 29
    assert metadata["timestepsPerHour"] == 4
    assert metadata["energyPlus"] == {"build": "6f2e40d102", "version": "26.1.0"}
    assert len(metadata["model"]["sha256"]) == 64
    assert len(metadata["weather"]["sha256"]) == 64


def test_concurrent_baselines_isolate_readvars_sidecars(tmp_path: Path) -> None:
    runner = BaselineRunner(_config(tmp_path))
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(runner.run, run_id=f"pytest-concurrent-{index}")
            for index in (1, 2)
        ]
        results = [future.result(timeout=30) for future in futures]

    assert len({result.hvac_kwh for result in results}) == 1
    assert len({result.occupied_pmv_compliance_percent for result in results}) == 1
    for result in results:
        assert (result.run_dir / "input.idf").is_file()
        assert (result.run_dir / "eplusout.rvaudit").is_file()
        assert (result.run_dir / "eplusout.csv").is_file()


def test_cli_run_baseline_json(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)

    def return_config(_root: Path) -> BaselineConfig:
        return config

    monkeypatch.setattr(cli, "discover_default_config", return_config)

    exit_code = cli.main(["run-baseline", "--run-id", "pytest-cli", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert payload["runId"] == "pytest-cli"
    assert payload["hvacKwh"] > 0


def test_run_id_is_stable_and_rejects_unsafe_values() -> None:
    moment = datetime(2026, 7, 26, 1, 2, 3, 456789, tzinfo=UTC)
    assert make_run_id(moment, "abcdef123456") == "baseline-20260726T010203456789Z-abcdef12"
    validate_run_id("baseline-safe_1.0")

    for unsafe in ("", "../escape", "has spaces", "-leading", "x" * 129):
        with pytest.raises(BaselineConfigurationError, match="Run ID"):
            validate_run_id(unsafe)


def test_missing_inputs_and_existing_run_are_rejected(tmp_path: Path) -> None:
    config = _config(tmp_path / "runs")
    missing = BaselineConfig(
        project_root=config.project_root,
        energyplus_executable=tmp_path / "missing.exe",
        model_path=config.model_path,
        weather_path=config.weather_path,
        runs_dir=config.runs_dir,
    )
    with pytest.raises(BaselineConfigurationError, match="EnergyPlus executable"):
        BaselineRunner(missing).run(run_id="missing-executable")

    config.runs_dir.mkdir()
    (config.runs_dir / "already-there").mkdir()
    with pytest.raises(BaselineConfigurationError, match="will not be overwritten"):
        BaselineRunner(config).run(run_id="already-there")


def _fake_config(tmp_path: Path) -> BaselineConfig:
    executable = tmp_path / "energyplus.exe"
    model = tmp_path / "baseline.idf"
    weather = tmp_path / "weather.epw"
    executable.write_text("fake", encoding="utf-8")
    model.write_text("Version,26.1;", encoding="utf-8")
    weather.write_text("LOCATION,fake", encoding="utf-8")
    return BaselineConfig(tmp_path, executable, model, weather, tmp_path / "runs")


def test_nonzero_energyplus_run_is_preserved(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    config = _fake_config(tmp_path)

    def fake_run(
        arguments: Sequence[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if "--version" in arguments:
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout="EnergyPlus, Version 26.1.0-6f2e40d102",
                stderr="",
            )
        return subprocess.CompletedProcess(arguments, 7, stdout="failed", stderr="reason")

    monkeypatch.setattr(baseline.subprocess, "run", fake_run)
    with pytest.raises(BaselineExecutionError, match="exited 7"):
        BaselineRunner(config).run(run_id="failed-run")
    assert (config.runs_dir / "failed-run" / "runner.stdout.log").read_text(
        encoding="utf-8"
    ) == "failed"


@pytest.mark.parametrize("condition", ["missing-output", "severe", "no-success"])
def test_energyplus_output_failures_are_safe(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    condition: str,
) -> None:
    config = _fake_config(tmp_path)

    def fake_run(
        arguments: Sequence[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if "--version" in arguments:
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout="EnergyPlus, Version 26.1.0-6f2e40d102",
                stderr="",
            )
        output_dir = Path(arguments[arguments.index("-d") + 1])
        if condition != "missing-output":
            (output_dir / "eplusout.end").write_text(
                "not complete" if condition == "no-success" else "Completed Successfully",
                encoding="utf-8",
            )
            error = "** Severe ** test failure" if condition == "severe" else "no errors"
            (output_dir / "eplusout.err").write_text(error, encoding="utf-8")
            (output_dir / "eplusout.csv").write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

    monkeypatch.setattr(baseline.subprocess, "run", fake_run)
    expected_error = (
        BaselineExecutionError if condition != "missing-output" else BaselineOutputError
    )
    with pytest.raises(expected_error):
        BaselineRunner(config).run(run_id=f"failure-{condition}")


def test_version_mismatch_is_rejected(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    config = _fake_config(tmp_path)

    def wrong_version(
        arguments: Sequence[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            arguments,
            0,
            stdout="EnergyPlus, Version 25.2.0-other",
            stderr="",
        )

    monkeypatch.setattr(baseline.subprocess, "run", wrong_version)
    with pytest.raises(BaselineConfigurationError, match="Expected EnergyPlus"):
        BaselineRunner(config).run(run_id="wrong-version")


def test_normalization_rejects_missing_and_malformed_output(tmp_path: Path) -> None:
    with pytest.raises(BaselineOutputError, match="missing"):
        normalize_energyplus_csv(tmp_path / "missing.csv", "run")

    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(BaselineOutputError, match="no header"):
        normalize_energyplus_csv(empty, "run")

    incomplete = tmp_path / "incomplete.csv"
    incomplete.write_text("Date/Time\n 05/23  00:15:00\n", encoding="utf-8")
    with pytest.raises(BaselineOutputError, match="exactly one"):
        normalize_energyplus_csv(incomplete, "run")


def test_timestamp_and_float_parsers_reject_bad_values() -> None:
    assert (
        baseline._parse_energyplus_timestamp(  # pyright: ignore[reportPrivateUsage]
            " 05/23  24:00:00"
        )
        == "2011-05-24T00:00:00+05:30"
    )
    for invalid in ("bad", "05/23  25:00:00", "05/23  24:15:00"):
        with pytest.raises(BaselineOutputError):
            baseline._parse_energyplus_timestamp(invalid)  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(BaselineOutputError, match="Missing numeric"):
        baseline._required_float({}, "value", 2)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(BaselineOutputError, match="Invalid numeric"):
        baseline._required_float(  # pyright: ignore[reportPrivateUsage]
            {"value": "invalid"}, "value", 2
        )
    with pytest.raises(BaselineOutputError, match="Non-finite"):
        baseline._required_float(  # pyright: ignore[reportPrivateUsage]
            {"value": "nan"}, "value", 2
        )


def test_summary_handles_no_occupied_samples() -> None:
    observation = Observation(
        schema_version="1.0",
        run_id="empty-comfort",
        sequence=1,
        timestamp="2011-05-23T00:15:00+05:30",
        zone="SPACE1-1",
        temperature_c=25.0,
        pmv=0.0,
        ppd_percent=5.0,
        occupancy_people=0.0,
        outdoor_dry_bulb_c=35.0,
        cooling_setpoint_c=29.4,
        cooling_schedule_value_c=29.4,
        hvac_electricity_j=0.0,
    )
    summary = calculate_summary(
        NormalizedBaseline((observation,), (0.0,), 1),
        run_id="empty-comfort",
        metadata={},
        warnings=[],
    )
    comfort = summary["comfort"]
    assert isinstance(comfort, dict)
    assert comfort["occupiedSamples"] == 0
    assert comfort["compliancePercent"] == 0.0
    assert comfort["meanPmv"] is None


def test_summary_repeatability_comparison(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    payload = {
        "energy": {"hvacElectricityKwh": 12.5},
        "comfort": {"compliancePercent": 91.0},
    }
    first.write_text(json.dumps(payload), encoding="utf-8")
    second.write_text(json.dumps(payload), encoding="utf-8")

    comparison = compare_summaries(first, second)
    assert comparison.passed is True
    assert comparison.energy_difference_kwh == 0.0
    assert comparison.to_dict()["energy_tolerance_kwh"] == 1e-9

    payload["energy"]["hvacElectricityKwh"] = 12.6
    second.write_text(json.dumps(payload), encoding="utf-8")
    assert compare_summaries(first, second).passed is False

    with pytest.raises(ValueError, match="non-negative"):
        compare_summaries(first, second, energy_tolerance_kwh=-1)

    second.write_text("{}", encoding="utf-8")
    with pytest.raises(BaselineOutputError, match="Cannot compare"):
        compare_summaries(first, second)


def test_cli_baseline_failure_returns_two(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _fake_config(tmp_path)
    config.energyplus_executable.unlink()

    def return_config(_root: Path) -> BaselineConfig:
        return config

    monkeypatch.setattr(cli, "discover_default_config", return_config)

    assert cli.main(["run-baseline"]) == 2
    assert "failed safely" in capsys.readouterr().err
