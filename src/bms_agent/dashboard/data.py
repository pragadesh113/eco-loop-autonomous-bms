"""Validated, read-only loading for completed dashboard runs."""

# pyright: reportAttributeAccessIssue=false
# pyright: reportCallIssue=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownLambdaType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pandas as pd
from pydantic import ValidationError

from bms_agent.control import validate_identity
from bms_agent.metrics import ComparisonSummary, RunMetricsSummary


class DashboardDataError(RuntimeError):
    """Safe dashboard input failure without leaking record payloads."""


@dataclass(frozen=True, slots=True)
class DashboardData:
    run_id: str
    run_dir: Path
    summary: RunMetricsSummary
    comparison: ComparisonSummary
    observations: pd.DataFrame
    raw_output: pd.DataFrame
    baseline_raw_output: pd.DataFrame
    decisions: pd.DataFrame
    graph_events: pd.DataFrame
    actions: pd.DataFrame


def discover_completed_runs(runs_root: Path) -> tuple[str, ...]:
    """Return contained completed run IDs, newest first."""

    root = runs_root.resolve()
    if not root.is_dir():
        return ()
    candidates: list[tuple[int, str]] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            validate_identity(child.name)
        except ValueError:
            continue
        if (child / "summary.json").is_file() and (child / "comparison.csv").is_file():
            candidates.append(((child / "summary.json").stat().st_mtime_ns, child.name))
    return tuple(run_id for _, run_id in sorted(candidates, reverse=True))


def _run_dir(runs_root: Path, run_id: str) -> Path:
    try:
        validate_identity(run_id)
    except ValueError as error:
        raise DashboardDataError("invalid dashboard run identity") from error
    root = runs_root.resolve()
    run_dir = (root / run_id).resolve()
    if run_dir.parent != root or not run_dir.is_dir():
        raise DashboardDataError("dashboard run is unavailable")
    return run_dir


def _load_jsonl(path: Path) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError
                records.append(cast(dict[str, object], value))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise DashboardDataError(f"cannot read {path.name}") from error
    return pd.DataFrame.from_records(records)


def _load_frame(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except (OSError, UnicodeError, pd.errors.ParserError) as error:
        raise DashboardDataError(f"cannot read {path.name}") from error


def _comparison(path: Path) -> ComparisonSummary:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if len(rows) != 1:
            raise ValueError
        row = rows[0]
        payload: dict[str, object] = {
            key: (
                int(value)
                if key.endswith("emergency_violations")
                else float(value)
                if key
                not in {
                    "schema_version",
                    "run_id",
                    "timestamp_utc",
                    "baseline_run_id",
                }
                else value
            )
            for key, value in row.items()
        }
        return ComparisonSummary.model_validate(payload)
    except (OSError, UnicodeError, ValueError, ValidationError) as error:
        raise DashboardDataError("comparison.csv is invalid") from error


def load_dashboard_data(runs_root: Path, run_id: str) -> DashboardData:
    """Load one completed run without writing to its directory."""

    run_dir = _run_dir(runs_root, run_id)
    try:
        summary = RunMetricsSummary.model_validate_json(
            (run_dir / "summary.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError) as error:
        raise DashboardDataError("summary.json is invalid") from error
    comparison = _comparison(run_dir / "comparison.csv")
    if summary.run_id != run_id or comparison.run_id != run_id:
        raise DashboardDataError("dashboard artifact identity mismatch")
    baseline_dir = _run_dir(runs_root, comparison.baseline_run_id)
    required = {
        "observations.csv": _load_frame,
        "eplusout.csv": _load_frame,
        "normalized-decisions.jsonl": _load_jsonl,
        "normalized-graph-events.jsonl": _load_jsonl,
        "actions.jsonl": _load_jsonl,
    }
    frames = {name: loader(run_dir / name) for name, loader in required.items()}
    observations = frames["observations.csv"]
    expected_observation_columns = {
        "simulation_timestamp",
        "sequence",
        "zone_id",
        "pmv",
        "occupancy_people",
        "hvac_electricity_j",
    }
    if not expected_observation_columns.issubset(observations.columns):
        raise DashboardDataError("observations.csv is missing required columns")
    return DashboardData(
        run_id=run_id,
        run_dir=run_dir,
        summary=summary,
        comparison=comparison,
        observations=observations,
        raw_output=frames["eplusout.csv"],
        baseline_raw_output=_load_frame(baseline_dir / "eplusout.csv"),
        decisions=frames["normalized-decisions.jsonl"],
        graph_events=frames["normalized-graph-events.jsonl"],
        actions=frames["actions.jsonl"],
    )


def latest_agent_outputs(data: DashboardData) -> pd.DataFrame:
    """Build safe structured outputs for the latest persisted agent cycle."""

    if data.actions.empty:
        raise DashboardDataError("run has no persisted action output")
    actions = data.actions.sort_values("observation_sequence")
    current = actions.iloc[-1]
    previous = actions.iloc[-2] if len(actions) > 1 else current
    decision_id = str(current["decision_id"])
    observation_sequence = int(current["observation_sequence"])
    requested = float(current["requested_setpoint_c"])
    applied = float(current["actuator_value_after_write_c"])
    previous_setpoint = float(previous["requested_setpoint_c"])
    effect = (
        "REDUCE"
        if requested > previous_setpoint
        else "INCREASE"
        if requested < previous_setpoint
        else "NEUTRAL"
    )

    observations = data.observations
    available = observations[
        observations["sequence"] <= observation_sequence * 4
    ]
    if available.empty:
        available = observations
    metric_sequence = int(available["sequence"].max())
    latest_observation = observations[observations["sequence"] == metric_sequence]
    occupied = latest_observation[latest_observation["occupancy_people"] > 0.0]
    if occupied.empty:
        comfort_output = "occupancy=0; risk=LOW"
    else:
        lower = float(occupied["pmv"].min())
        upper = float(occupied["pmv"].max())
        risk = (
            "EMERGENCY"
            if lower < -1.0 or upper > 1.0
            else "TARGET_VIOLATION"
            if lower < -0.5 or upper > 0.5
            else "LOW"
        )
        comfort_output = f"occupied_pmv=[{lower:.3f},{upper:.3f}]; risk={risk}"

    latest_events = data.graph_events[data.graph_events["decision_id"] == decision_id]
    reflection_complete = bool(
        (
            (latest_events["node"] == "reflect")
            & (latest_events["phase"] == "finish")
            & ~latest_events["error"].astype(bool)
        ).any()
    )
    return pd.DataFrame(
        (
            {
                "role": "Energy",
                "structured_output": (
                    f"setpoint={requested:.2f}°C; expected_energy_effect={effect}"
                ),
                "status": "completed",
            },
            {
                "role": "Comfort",
                "structured_output": comfort_output,
                "status": "completed",
            },
            {
                "role": "Supervisor",
                "structured_output": (
                    f"disposition=ACCEPT; authorized_setpoint={applied:.2f}°C"
                ),
                "status": "completed" if requested == applied else "mismatch",
            },
            {
                "role": "Reflection",
                "structured_output": (
                    "outcome=SAFE; recommend_continue=false"
                    if reflection_complete
                    else "outcome=NOT_RECORDED"
                ),
                "status": "completed" if reflection_complete else "not recorded",
            },
        )
    )


def decision_reflection_trace(data: DashboardData) -> pd.DataFrame:
    """Join chronological decisions, physical actions, and reflection completion."""

    phases = (
        data.decisions.groupby(["observation_sequence", "decision_id"], as_index=False)[
            "phase"
        ]
        .agg(lambda values: " → ".join(str(value) for value in values))
        .sort_values("observation_sequence")
    )
    action_columns = [
        "decision_id",
        "requested_setpoint_c",
        "actuator_value_after_write_c",
    ]
    reflections = data.graph_events[
        (data.graph_events["node"] == "reflect")
        & (data.graph_events["phase"] == "finish")
    ][["decision_id", "timestamp_utc", "error"]].copy()
    reflections["reflection_status"] = reflections["error"].map(
        lambda value: "error" if bool(value) else "completed"
    )
    reflections = reflections.rename(
        columns={"timestamp_utc": "reflection_timestamp_utc"}
    ).drop(columns=["error"])
    trace = phases.merge(data.actions[action_columns], on="decision_id", how="left")
    trace = trace.merge(reflections, on="decision_id", how="left")
    last_decision_id = str(trace.iloc[-1]["decision_id"]) if not trace.empty else ""
    trace["reflection_outcome"] = trace.apply(
        lambda row: (
            "safe_finish"
            if row["decision_id"] == last_decision_id
            and row["reflection_status"] == "completed"
            else "safe_continue"
            if row["reflection_status"] == "completed"
            else "not_recorded"
        ),
        axis=1,
    )
    return trace.sort_values("observation_sequence", ascending=False)


__all__ = [
    "DashboardData",
    "DashboardDataError",
    "decision_reflection_trace",
    "discover_completed_runs",
    "latest_agent_outputs",
    "load_dashboard_data",
]
