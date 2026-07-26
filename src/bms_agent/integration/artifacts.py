"""Normalize a completed controlled graph run into MET-001 artifacts."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bms_agent.graph import GatewayActionRequest
from bms_agent.integration.runner import ControlledGraphResult
from bms_agent.metrics import (
    ComparisonSummary,
    DecisionAuditRecord,
    DecisionPhase,
    EventStore,
    GraphAuditRecord,
    MetricSample,
    RunMetadata,
    RunMetricsSummary,
    RunMode,
    calculate_run_summary,
    compare_run_summaries,
)
from bms_agent.simulation.baseline import NormalizedBaseline, normalize_energyplus_csv


@dataclass(frozen=True, slots=True)
class ExperimentArtifacts:
    controlled_summary: RunMetricsSummary
    baseline_summary: RunMetricsSummary
    comparison: ComparisonSummary
    run_dir: Path


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _metric_samples(
    normalized: NormalizedBaseline,
    *,
    run_id: str,
    timestamp_utc: str,
) -> tuple[MetricSample, ...]:
    return tuple(
        MetricSample(
            run_id=run_id,
            timestamp_utc=timestamp_utc,
            simulation_timestamp=row.timestamp,
            sequence=row.sequence,
            zone_id=row.zone,
            hvac_electricity_j=row.hvac_electricity_j,
            hvac_energy_unit="J",
            occupancy_people=row.occupancy_people,
            occupancy_unit="people",
            pmv=row.pmv,
            pmv_unit="dimensionless",
            ppd_percent=row.ppd_percent,
            ppd_unit="percent",
        )
        for row in normalized.observations
    )


def _decision_records(
    result: ControlledGraphResult,
    *,
    timestamp_utc: str,
) -> tuple[DecisionAuditRecord, ...]:
    records: list[DecisionAuditRecord] = []
    for request in result.action_requests:
        records.append(
            _decision_record(request, timestamp_utc, DecisionPhase.PROPOSED)
        )
        if request.control_source == "deterministic_fallback":
            records.extend(
                (
                    _decision_record(
                        request,
                        timestamp_utc,
                        DecisionPhase.REVISION,
                        revision_index=1,
                    ),
                    _decision_record(
                        request,
                        timestamp_utc,
                        DecisionPhase.REVISION,
                        revision_index=2,
                    ),
                    _decision_record(
                        request,
                        timestamp_utc,
                        DecisionPhase.FALLBACK,
                    ),
                )
            )
        records.append(
            _decision_record(request, timestamp_utc, DecisionPhase.APPLIED)
        )
    return tuple(records)


def _decision_record(
    request: GatewayActionRequest,
    timestamp_utc: str,
    phase: DecisionPhase,
    *,
    revision_index: int = 0,
) -> DecisionAuditRecord:
    return DecisionAuditRecord(
        run_id=request.run_id,
        timestamp_utc=timestamp_utc,
        decision_id=request.decision_id,
        observation_sequence=request.observation_sequence,
        phase=phase,
        revision_index=revision_index,
    )


def _graph_records(
    result: ControlledGraphResult,
) -> tuple[GraphAuditRecord, ...]:
    return tuple(
        GraphAuditRecord(
            run_id=event.run_id,
            timestamp_utc=event.timestamp_utc,
            decision_id=event.decision_id,
            node=event.node,
            phase=event.phase,
            error=event.error,
        )
        for event in result.events
    )


def persist_experiment_artifacts(
    *,
    project_root: Path,
    result: ControlledGraphResult,
    baseline_run_id: str,
) -> ExperimentArtifacts:
    """Persist one completed seven-day result and deterministic comparison."""

    run_id = result.state.get("run_id")
    summary = result.state.get("summary")
    if not isinstance(run_id, str) or summary is None:
        raise ValueError("controlled graph result is not complete")
    run_dir = project_root / "runs" / run_id
    baseline_dir = project_root / "runs" / baseline_run_id
    generated_names = (
        "normalized-run-metadata.jsonl",
        "normalized-metrics.jsonl",
        "normalized-decisions.jsonl",
        "normalized-graph-events.jsonl",
        "normalized-run-events.jsonl",
        "observations.csv",
        "summary.json",
        "comparison.csv",
    )
    if any((run_dir / name).exists() for name in generated_names):
        raise ValueError("normalized experiment artifacts already exist")
    _ensure_readvars_csv(project_root, run_dir)
    controlled_normalized = normalize_energyplus_csv(
        run_dir / "eplusout.csv",
        run_id,
    )
    baseline_normalized = normalize_energyplus_csv(
        baseline_dir / "eplusout.csv",
        baseline_run_id,
    )
    timestamp_utc = _utc_now()
    controlled_samples = _metric_samples(
        controlled_normalized,
        run_id=run_id,
        timestamp_utc=timestamp_utc,
    )
    baseline_samples = _metric_samples(
        baseline_normalized,
        run_id=baseline_run_id,
        timestamp_utc=timestamp_utc,
    )
    decisions = _decision_records(result, timestamp_utc=timestamp_utc)
    graph_events = _graph_records(result)
    controlled_summary = calculate_run_summary(
        metadata=RunMetadata(
            run_id=run_id,
            timestamp_utc=timestamp_utc,
            mode=RunMode.CONTROLLED,
            timestep_minutes=15.0,
            decision_interval_minutes=60.0,
            control_observation_count=len(result.observations),
        ),
        timestamp_utc=timestamp_utc,
        samples=controlled_samples,
        decisions=decisions,
        graph_events=graph_events,
    )
    baseline_summary = calculate_run_summary(
        metadata=RunMetadata(
            run_id=baseline_run_id,
            timestamp_utc=timestamp_utc,
            mode=RunMode.BASELINE,
            timestep_minutes=15.0,
        ),
        timestamp_utc=timestamp_utc,
        samples=baseline_samples,
    )
    comparison = compare_run_summaries(
        baseline=baseline_summary,
        controlled=controlled_summary,
        timestamp_utc=timestamp_utc,
    )
    store = EventStore(project_root / "runs", run_id)
    metadata = RunMetadata(
        run_id=run_id,
        timestamp_utc=timestamp_utc,
        mode=RunMode.CONTROLLED,
        timestep_minutes=15.0,
        decision_interval_minutes=60.0,
        control_observation_count=len(result.observations),
    )
    store.append_many((metadata, *controlled_samples, *decisions, *graph_events))
    store.write_observations_csv(controlled_samples)
    store.write_summary(controlled_summary)
    store.write_comparison(comparison)
    return ExperimentArtifacts(
        controlled_summary=controlled_summary,
        baseline_summary=baseline_summary,
        comparison=comparison,
        run_dir=run_dir,
    )


def _ensure_readvars_csv(project_root: Path, run_dir: Path) -> Path:
    output_path = run_dir / "eplusout.csv"
    if output_path.is_file():
        return output_path
    eso_path = run_dir / "eplusout.eso"
    if not eso_path.is_file():
        raise ValueError("controlled EnergyPlus ESO output is missing")
    matches = tuple(
        (project_root / ".tools" / "energyplus").glob(
            "*/EnergyPlus-*/PostProcess/ReadVarsESO.exe"
        )
    )
    if len(matches) != 1:
        raise ValueError("exactly one project-local ReadVarsESO executable is required")
    request_path = run_dir / "normalized-output.rvi"
    if request_path.exists():
        raise ValueError("ReadVarsESO request already exists without accepted CSV")
    request_path.write_text(
        f"{eso_path.resolve()}\n{output_path.resolve()}\n",
        encoding="utf-8",
        newline="\n",
    )
    completed = subprocess.run(
        [str(matches[0]), str(request_path), "unlimited"],
        cwd=run_dir,
        capture_output=True,
        text=True,
        timeout=30.0,
        check=False,
    )
    if completed.returncode != 0 or not output_path.is_file():
        raise ValueError("ReadVarsESO failed to create normalized output")
    return output_path


__all__ = ["ExperimentArtifacts", "persist_experiment_artifacts"]
