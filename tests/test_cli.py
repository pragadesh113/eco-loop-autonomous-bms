"""Tests for the foundation command-line interface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from bms_agent import __version__, cli
from bms_agent.cli import (
    REQUIRED_EXTERNAL_TOOLS,
    collect_doctor_report,
    format_doctor_report,
    main,
    project_root,
)


def test_package_version_is_defined() -> None:
    assert __version__ == "0.1.0"


def test_project_root_contains_project_configuration() -> None:
    assert (project_root() / "pyproject.toml").is_file()


def test_collect_doctor_report_has_stable_shape() -> None:
    report = collect_doctor_report()

    assert Path(report["project_root"]).resolve() == project_root().resolve()
    assert report["python_version"]
    assert report["platform"]
    assert tuple(report["tools"]) == REQUIRED_EXTERNAL_TOOLS
    for tool in report["tools"].values():
        assert isinstance(tool["available"], bool)
        assert tool["path"] is None or isinstance(tool["path"], str)


def test_format_doctor_report_lists_every_tool() -> None:
    rendered = format_doctor_report(collect_doctor_report())

    assert "Eco-Loop environment diagnostics" in rendered
    for tool_name in REQUIRED_EXTERNAL_TOOLS:
        assert f"- {tool_name}:" in rendered


def test_doctor_json_output_is_valid(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["doctor", "--json"])

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 0
    assert payload["project_root"] == str(project_root())


def test_doctor_text_output_is_readable(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["doctor"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "External tools:" in output


def test_project_tool_discovery_supports_nested_energyplus_layout(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    executable = (
        tmp_path
        / ".tools"
        / "energyplus"
        / "26.1.0"
        / "EnergyPlus-26.1.0-Windows-x86_64"
        / "energyplus.exe"
    )
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(cli, "project_root", lambda: tmp_path)

    candidates = cli._project_tool_candidates("energyplus")  # pyright: ignore[reportPrivateUsage]

    assert candidates == [executable.resolve()]


def test_ollama_host_without_scheme_is_normalized() -> None:
    normalized = cli._normalize_http_host(  # pyright: ignore[reportPrivateUsage]
        "127.0.0.1:11434/",
    )

    assert normalized == "http://127.0.0.1:11434"


def test_invalid_local_json_endpoint_returns_none() -> None:
    payload = cli._read_json_api(  # pyright: ignore[reportPrivateUsage]
        "http://127.0.0.1:1/not-running",
    )

    assert payload is None


def test_missing_command_version_returns_none(tmp_path: Path) -> None:
    version = cli._command_version(  # pyright: ignore[reportPrivateUsage]
        tmp_path / "not-an-executable.exe",
        "--version",
    )

    assert version is None


def test_missing_energyplus_has_explicit_empty_resources(
    monkeypatch: MonkeyPatch,
) -> None:
    def missing_discovery(
        _tool: str,
        *,
        home_environment_variable: str | None = None,
    ) -> tuple[None, None]:
        _ = home_environment_variable
        return None, None

    monkeypatch.setattr(
        cli,
        "_discover_executable",
        missing_discovery,
    )

    status = cli._energyplus_status()  # pyright: ignore[reportPrivateUsage]

    assert status["available"] is False
    assert status["executable"] is None
    assert status["python_api_path"] is None
    assert status["new_delhi_epw_path"] is None


def test_explicit_tool_home_has_discovery_priority(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    executable = tmp_path / "energyplus.exe"
    executable.touch()
    monkeypatch.setenv("TEST_ENERGYPLUS_HOME", str(tmp_path))

    discovered, source = cli._discover_executable(  # pyright: ignore[reportPrivateUsage]
        "energyplus",
        home_environment_variable="TEST_ENERGYPLUS_HOME",
    )

    assert discovered == executable.resolve()
    assert source == "environment"


def test_entrypoint_exits_with_main_result(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "main", lambda: 7)

    with pytest.raises(SystemExit, match="7"):
        cli.entrypoint()
