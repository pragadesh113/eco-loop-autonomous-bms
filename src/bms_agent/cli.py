"""Command-line entry points for Eco-Loop development and operations."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TypedDict, cast
from urllib.error import URLError
from urllib.request import Request, urlopen

from bms_agent.simulation.baseline import (
    BaselineConfig,
    BaselineError,
    BaselineRunner,
    discover_default_config,
)


class ToolStatus(TypedDict):
    """Availability details for one external executable."""

    available: bool
    path: str | None
    version: str | None
    source: str | None


class EnergyPlusStatus(TypedDict):
    """EnergyPlus executable and bundled resource diagnostics."""

    available: bool
    executable: str | None
    version: str | None
    source: str | None
    home: str | None
    python_api_path: str | None
    examples_path: str | None
    weather_data_path: str | None
    new_delhi_epw_path: str | None
    new_delhi_ddy_path: str | None


class OllamaStatus(TypedDict):
    """Ollama executable, API, and configured-model diagnostics."""

    available: bool
    executable: str | None
    cli_version: str | None
    source: str | None
    host: str
    api_reachable: bool
    server_version: str | None
    configured_model: str
    configured_model_available: bool
    fallback_model: str
    fallback_model_available: bool
    installed_models: list[str]
    model_storage: str | None


class DoctorReport(TypedDict):
    """Stable, JSON-serializable environment diagnostic result."""

    project_root: str
    python_version: str
    platform: str
    tools: dict[str, ToolStatus]
    energyplus: EnergyPlusStatus
    ollama: OllamaStatus


REQUIRED_EXTERNAL_TOOLS = ("git", "docker", "energyplus", "ollama")
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3:4b-instruct"
DEFAULT_OLLAMA_FALLBACK_MODEL = "qwen3:1.7b"
NEW_DELHI_WEATHER_STEM = "IND_DL_New.Delhi-Safdarjung.AP.421820_TMYx.2011-2025"


def project_root() -> Path:
    """Return the repository root from the installed source layout."""

    return Path(__file__).resolve().parents[2]


def _command_version(executable: Path | str, *arguments: str) -> str | None:
    """Return the first non-empty version line from a bounded subprocess."""

    try:
        completed = subprocess.run(
            [str(executable), *arguments],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    output = "\n".join((completed.stdout, completed.stderr))
    return next((line.strip() for line in output.splitlines() if line.strip()), None)


def _project_tool_candidates(tool: str) -> list[Path]:
    """Return newest-first project-local executable candidates."""

    executable = f"{tool}.exe" if os.name == "nt" else tool
    tool_root = project_root() / ".tools" / tool
    candidates = [
        *tool_root.glob(f"*/{executable}"),
        *tool_root.glob(f"*/*/{executable}"),
    ]
    return sorted({path.resolve() for path in candidates}, reverse=True)


def _discover_executable(
    tool: str,
    *,
    home_environment_variable: str | None = None,
) -> tuple[Path | None, str | None]:
    """Discover a tool without changing PATH, preferring explicit configuration."""

    executable_name = f"{tool}.exe" if os.name == "nt" else tool
    if home_environment_variable:
        configured_home = os.environ.get(home_environment_variable)
        if configured_home:
            candidate = Path(configured_home).expanduser() / executable_name
            if candidate.is_file():
                return candidate.resolve(), "environment"

    project_candidates = _project_tool_candidates(tool)
    if project_candidates:
        return project_candidates[0].resolve(), "project-local"

    executable = shutil.which(tool)
    if executable:
        return Path(executable).resolve(), "PATH"
    return None, None


def _tool_status(name: str) -> ToolStatus:
    """Collect generic tool status used for Git and Docker."""

    executable = shutil.which(name)
    version_arguments = ("--version",)
    version = _command_version(executable, *version_arguments) if executable else None
    return {
        "available": executable is not None,
        "path": executable,
        "version": version,
        "source": "PATH" if executable else None,
    }


def _first_existing_directory(*candidates: Path) -> Path | None:
    """Return the first candidate directory that exists."""

    return next((candidate.resolve() for candidate in candidates if candidate.is_dir()), None)


def _energyplus_status() -> EnergyPlusStatus:
    """Discover EnergyPlus and its bundled resources."""

    executable, source = _discover_executable(
        "energyplus",
        home_environment_variable="ENERGYPLUS_HOME",
    )
    if executable is None:
        return {
            "available": False,
            "executable": None,
            "version": None,
            "source": None,
            "home": None,
            "python_api_path": None,
            "examples_path": None,
            "weather_data_path": None,
            "new_delhi_epw_path": None,
            "new_delhi_ddy_path": None,
        }

    home = executable.parent
    python_api_path = _first_existing_directory(
        home / "pyenergyplus",
        home / "Python",
        home / "python_lib",
    )
    examples_path = _first_existing_directory(home / "ExampleFiles", home / "examples")
    weather_path = _first_existing_directory(home / "WeatherData", home / "weather")
    new_delhi_epw = project_root() / "weather" / f"{NEW_DELHI_WEATHER_STEM}.epw"
    new_delhi_ddy = project_root() / "weather" / f"{NEW_DELHI_WEATHER_STEM}.ddy"
    return {
        "available": True,
        "executable": str(executable),
        "version": _command_version(executable, "--version"),
        "source": source,
        "home": str(home),
        "python_api_path": str(python_api_path) if python_api_path else None,
        "examples_path": str(examples_path) if examples_path else None,
        "weather_data_path": str(weather_path) if weather_path else None,
        "new_delhi_epw_path": str(new_delhi_epw.resolve()) if new_delhi_epw.is_file() else None,
        "new_delhi_ddy_path": str(new_delhi_ddy.resolve()) if new_delhi_ddy.is_file() else None,
    }


def _read_json_api(url: str) -> dict[str, object] | None:
    """Read one local JSON endpoint with a short timeout."""

    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=2) as response:  # noqa: S310 - localhost is configured
            payload = cast(object, json.load(response))
    except (OSError, URLError, TimeoutError, json.JSONDecodeError):
        return None
    return cast(dict[str, object], payload) if isinstance(payload, dict) else None


def _normalize_http_host(host: str) -> str:
    """Return an HTTP base URL from Ollama CLI-style host configuration."""

    normalized = host.rstrip("/")
    return normalized if "://" in normalized else f"http://{normalized}"


def _ollama_status() -> OllamaStatus:
    """Discover Ollama, query its local API, and list installed models."""

    executable, source = _discover_executable(
        "ollama",
        home_environment_variable="OLLAMA_HOME",
    )
    host = _normalize_http_host(os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST))
    configured_model = os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
    fallback_model = os.environ.get(
        "OLLAMA_FALLBACK_MODEL",
        DEFAULT_OLLAMA_FALLBACK_MODEL,
    )

    version_payload = _read_json_api(f"{host}/api/version")
    tags_payload = _read_json_api(f"{host}/api/tags") if version_payload else None
    raw_model_records = tags_payload.get("models") if tags_payload else None
    model_records = (
        cast(list[object], raw_model_records) if isinstance(raw_model_records, list) else []
    )
    installed_models = sorted(
        str(cast(dict[str, object], record)["name"])
        for record in model_records
        if isinstance(record, dict)
        and isinstance(cast(dict[str, object], record).get("name"), str)
    )

    model_storage = os.environ.get("OLLAMA_MODELS")
    default_project_storage = project_root() / ".cache" / "ollama-models"
    if model_storage is None and default_project_storage.is_dir():
        model_storage = str(default_project_storage.resolve())

    server_version = version_payload.get("version") if version_payload else None
    return {
        "available": executable is not None,
        "executable": str(executable) if executable else None,
        "cli_version": _command_version(executable, "--version") if executable else None,
        "source": source,
        "host": host,
        "api_reachable": version_payload is not None,
        "server_version": str(server_version) if server_version is not None else None,
        "configured_model": configured_model,
        "configured_model_available": configured_model in installed_models,
        "fallback_model": fallback_model,
        "fallback_model_available": fallback_model in installed_models,
        "installed_models": installed_models,
        "model_storage": model_storage,
    }


def collect_doctor_report() -> DoctorReport:
    """Collect environment facts without modifying the machine."""

    energyplus = _energyplus_status()
    ollama = _ollama_status()
    tools: dict[str, ToolStatus] = {
        "git": _tool_status("git"),
        "docker": _tool_status("docker"),
        "energyplus": {
            "available": energyplus["available"],
            "path": energyplus["executable"],
            "version": energyplus["version"],
            "source": energyplus["source"],
        },
        "ollama": {
            "available": ollama["available"],
            "path": ollama["executable"],
            "version": ollama["cli_version"],
            "source": ollama["source"],
        },
    }
    return {
        "project_root": str(project_root()),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "tools": tools,
        "energyplus": energyplus,
        "ollama": ollama,
    }


def format_doctor_report(report: DoctorReport) -> str:
    """Render a concise human-readable diagnostic report."""

    lines = [
        "Eco-Loop environment diagnostics",
        f"Project root: {report['project_root']}",
        f"Python: {report['python_version']}",
        f"Platform: {report['platform']}",
        "External tools:",
    ]
    for name, status in report["tools"].items():
        value = status["path"] if status["available"] else "NOT FOUND"
        version = f" ({status['version']})" if status["version"] else ""
        source = f" [{status['source']}]" if status["source"] else ""
        lines.append(f"  - {name}: {value}{version}{source}")

    energyplus = report["energyplus"]
    lines.extend(
        [
            "EnergyPlus resources:",
            f"  - home: {energyplus['home'] or 'NOT FOUND'}",
            f"  - Python API: {energyplus['python_api_path'] or 'NOT FOUND'}",
            f"  - examples: {energyplus['examples_path'] or 'NOT FOUND'}",
            f"  - bundled weather: {energyplus['weather_data_path'] or 'NOT FOUND'}",
            f"  - New Delhi EPW: {energyplus['new_delhi_epw_path'] or 'NOT FOUND'}",
            f"  - New Delhi DDY: {energyplus['new_delhi_ddy_path'] or 'NOT FOUND'}",
        ]
    )
    ollama = report["ollama"]
    lines.extend(
        [
            "Ollama runtime:",
            f"  - host: {ollama['host']}",
            f"  - API: {'reachable' if ollama['api_reachable'] else 'NOT REACHABLE'}",
            f"  - server version: {ollama['server_version'] or 'NOT FOUND'}",
            f"  - model storage: {ollama['model_storage'] or 'DEFAULT USER LOCATION'}",
            (
                f"  - configured model: {ollama['configured_model']} "
                f"({'available' if ollama['configured_model_available'] else 'NOT FOUND'})"
            ),
            (
                f"  - fallback model: {ollama['fallback_model']} "
                f"({'available' if ollama['fallback_model_available'] else 'NOT FOUND'})"
            ),
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the command parser."""

    parser = argparse.ArgumentParser(
        prog="bms-agent",
        description="Eco-Loop Building Agents development and operations CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser(
        "doctor",
        help="Report local prerequisites without changing the environment.",
    )
    doctor.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit a machine-readable JSON report.",
    )
    baseline = subparsers.add_parser(
        "run-baseline",
        help="Run and normalize the fixed May 23-29 New Delhi baseline.",
    )
    baseline.add_argument(
        "--run-id",
        help="Optional safe unique run ID; generated automatically when omitted.",
    )
    baseline.add_argument(
        "--runs-dir",
        type=Path,
        help="Override the artifact root (defaults to project-local runs/).",
    )
    baseline.add_argument(
        "--energyplus",
        type=Path,
        help="Override the EnergyPlus executable.",
    )
    baseline.add_argument(
        "--model",
        type=Path,
        help="Override the baseline IDF.",
    )
    baseline.add_argument(
        "--weather",
        type=Path,
        help="Override the New Delhi EPW.",
    )
    baseline.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the normalized run result as JSON.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Execute the requested CLI command and return a process exit code."""

    arguments = build_parser().parse_args(argv)

    if arguments.command == "doctor":
        report = collect_doctor_report()
        if arguments.as_json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(format_doctor_report(report))
        return 0

    if arguments.command == "run-baseline":
        try:
            default = discover_default_config(project_root())
            config = BaselineConfig(
                project_root=default.project_root,
                energyplus_executable=arguments.energyplus or default.energyplus_executable,
                model_path=arguments.model or default.model_path,
                weather_path=arguments.weather or default.weather_path,
                runs_dir=arguments.runs_dir or default.runs_dir,
            )
            result = BaselineRunner(config).run(run_id=arguments.run_id)
        except BaselineError as error:
            print(f"Baseline failed safely: {error}", file=sys.stderr)
            return 2
        if arguments.as_json:
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        else:
            print(f"Baseline completed: {result.run_id}")
            print(f"Artifacts: {result.run_dir}")
            print(f"HVAC electricity: {result.hvac_kwh:.6f} kWh")
            print(
                "Occupied PMV compliance: "
                f"{result.occupied_pmv_compliance_percent:.2f}%"
            )
        return 0

    raise AssertionError(f"Unhandled command: {arguments.command}")


def entrypoint() -> None:
    """Console-script entry point."""

    sys.exit(main())


if __name__ == "__main__":
    entrypoint()
