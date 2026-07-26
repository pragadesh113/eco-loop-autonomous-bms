"""Shared deterministic calculations for baseline and controlled run summaries."""

from __future__ import annotations

import math
from collections.abc import Sequence

from bms_agent.metrics.contracts import (
    TARIFF_INR_PER_KWH,
    ComfortMetrics,
    ComparisonSummary,
    DecisionAuditRecord,
    DecisionMetrics,
    DecisionPhase,
    EnergyMetrics,
    GraphAuditRecord,
    MetricSample,
    ReliabilityMetrics,
    RunAuditRecord,
    RunEventType,
    RunMetadata,
    RunMetricsSummary,
    RunMode,
)

TARGET_PMV_LOWER = -0.5
TARGET_PMV_UPPER = 0.5
EMERGENCY_PMV_LOWER = -1.0
EMERGENCY_PMV_UPPER = 1.0
ENERGY_MATCH_ABSOLUTE_TOLERANCE = 1e-12
JOULES_PER_KWH = 3_600_000.0


class MetricsEvaluationError(ValueError):
    """Raised when normalized inputs cannot produce an auditable summary."""


def _mean(values: Sequence[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


def _validate_run_identity(
    run_id: str,
    samples: Sequence[MetricSample],
    decisions: Sequence[DecisionAuditRecord],
    graph_events: Sequence[GraphAuditRecord],
    run_events: Sequence[RunAuditRecord],
) -> None:
    if any(
        record.run_id != run_id
        for record in (*samples, *decisions, *graph_events, *run_events)
    ):
        raise MetricsEvaluationError("record run identity differs from summary run")


def _validate_decision_references(
    decisions: Sequence[DecisionAuditRecord],
    graph_events: Sequence[GraphAuditRecord],
    run_events: Sequence[RunAuditRecord],
) -> None:
    proposed_ids = {
        record.decision_id
        for record in decisions
        if record.phase is DecisionPhase.PROPOSED
    }
    for record in (*graph_events, *run_events):
        if record.decision_id is not None and record.decision_id not in proposed_ids:
            raise MetricsEvaluationError(
                "audit event references a decision without a proposed lifecycle"
            )


def _energy_metrics(samples: Sequence[MetricSample]) -> EnergyMetrics:
    by_sequence: dict[int, tuple[float, str]] = {}
    seen_zone_samples: set[tuple[int, str]] = set()
    for sample in samples:
        sample_key = (sample.sequence, sample.zone_id)
        if sample_key in seen_zone_samples:
            raise MetricsEvaluationError("duplicate zone sample")
        seen_zone_samples.add(sample_key)
        existing = by_sequence.get(sample.sequence)
        current = (sample.hvac_electricity_j, sample.simulation_timestamp)
        if existing is not None and (
            not math.isclose(
                existing[0],
                current[0],
                rel_tol=0.0,
                abs_tol=ENERGY_MATCH_ABSOLUTE_TOLERANCE,
            )
            or existing[1] != sample.simulation_timestamp
        ):
            raise MetricsEvaluationError("zone samples disagree on interval energy or timestamp")
        by_sequence[sample.sequence] = current
    hvac_kwh = math.fsum(value[0] for value in by_sequence.values()) / JOULES_PER_KWH
    return EnergyMetrics(
        hvac_kwh=hvac_kwh,
        cost_inr=hvac_kwh * TARIFF_INR_PER_KWH,
        tariff_inr_per_kwh=TARIFF_INR_PER_KWH,
    )


def _comfort_metrics(
    samples: Sequence[MetricSample],
    timestep_minutes: float,
) -> ComfortMetrics:
    occupied = [sample for sample in samples if sample.occupancy_people > 0.0]
    compliant = [
        sample for sample in occupied if TARGET_PMV_LOWER <= sample.pmv <= TARGET_PMV_UPPER
    ]
    emergency = [
        sample
        for sample in occupied
        if sample.pmv < EMERGENCY_PMV_LOWER or sample.pmv > EMERGENCY_PMV_UPPER
    ]
    violations = [sample for sample in occupied if sample not in compliant]
    absolute_pmv = [abs(sample.pmv) for sample in occupied]
    available_ppd = [
        sample.ppd_percent for sample in occupied if sample.ppd_percent is not None
    ]
    compliance = 100.0 * len(compliant) / len(occupied) if occupied else 0.0
    return ComfortMetrics(
        occupied_samples=len(occupied),
        compliant_samples=len(compliant),
        occupied_pmv_compliance_percent=compliance,
        emergency_violations=len(emergency),
        comfort_violation_minutes=len(violations) * timestep_minutes,
        emergency_violation_minutes=len(emergency) * timestep_minutes,
        mean_abs_pmv=_mean(absolute_pmv),
        max_abs_pmv=max(absolute_pmv) if absolute_pmv else None,
        mean_ppd_percent=_mean(available_ppd),
    )


def _decision_metrics(
    decisions: Sequence[DecisionAuditRecord],
    observed_sequences: set[int],
) -> DecisionMetrics:
    counts = {phase: 0 for phase in DecisionPhase}
    llm_latency: list[float] = []
    mcp_latency: list[float] = []
    proposed_ids: set[str] = set()
    proposed_sequences: set[int] = set()
    applied_ids: set[str] = set()
    applied_sequences: set[int] = set()
    decision_sequences: dict[str, int] = {}
    for record in sorted(decisions, key=lambda item: item.timestamp_utc):
        if record.observation_sequence not in observed_sequences:
            raise MetricsEvaluationError(
                "decision references an observation sequence absent from metric samples"
            )
        counts[record.phase] += 1
        if record.llm_latency_seconds is not None:
            llm_latency.append(record.llm_latency_seconds)
        if record.mcp_latency_seconds is not None:
            mcp_latency.append(record.mcp_latency_seconds)
        if record.phase is DecisionPhase.PROPOSED:
            if record.decision_id in proposed_ids:
                raise MetricsEvaluationError("decision has more than one proposed record")
            if record.observation_sequence in proposed_sequences:
                raise MetricsEvaluationError(
                    "observation sequence has more than one proposed decision"
                )
            proposed_ids.add(record.decision_id)
            proposed_sequences.add(record.observation_sequence)
            decision_sequences[record.decision_id] = record.observation_sequence
        elif record.decision_id not in proposed_ids:
            raise MetricsEvaluationError("decision lifecycle record has no proposal correlation")
        elif decision_sequences[record.decision_id] != record.observation_sequence:
            raise MetricsEvaluationError(
                "decision lifecycle changes its observation sequence"
            )
        if record.phase is DecisionPhase.APPLIED:
            if record.decision_id in applied_ids:
                raise MetricsEvaluationError("decision has more than one applied record")
            if record.observation_sequence in applied_sequences:
                raise MetricsEvaluationError(
                    "observation sequence has more than one applied decision"
                )
            applied_ids.add(record.decision_id)
            applied_sequences.add(record.observation_sequence)
    return DecisionMetrics(
        proposed=counts[DecisionPhase.PROPOSED],
        applied=counts[DecisionPhase.APPLIED],
        rejected=counts[DecisionPhase.REJECTED],
        revisions=counts[DecisionPhase.REVISION],
        fallbacks=counts[DecisionPhase.FALLBACK],
        mean_llm_latency_seconds=_mean(llm_latency),
        max_llm_latency_seconds=max(llm_latency) if llm_latency else None,
        mean_mcp_latency_seconds=_mean(mcp_latency),
        max_mcp_latency_seconds=max(mcp_latency) if mcp_latency else None,
    )


def _longest_without_action(
    samples: Sequence[MetricSample],
    decisions: Sequence[DecisionAuditRecord],
    proposed_count: int,
    timestep_minutes: float,
) -> float | None:
    if not samples or proposed_count == 0:
        return None
    sequences = sorted({sample.sequence for sample in samples})
    applied_sequences = {
        record.observation_sequence
        for record in decisions
        if record.phase is DecisionPhase.APPLIED
    }
    longest = 0
    current = 0
    for sequence in sequences:
        if sequence in applied_sequences:
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return longest * timestep_minutes


def _reliability_metrics(
    samples: Sequence[MetricSample],
    decisions: Sequence[DecisionAuditRecord],
    run_events: Sequence[RunAuditRecord],
    decision_metrics: DecisionMetrics,
    timestep_minutes: float,
) -> ReliabilityMetrics:
    errors = sum(record.event_type is RunEventType.ERROR for record in run_events)
    recoveries = sum(record.event_type is RunEventType.RECOVERY for record in run_events)
    if recoveries > errors:
        raise MetricsEvaluationError("recoveries cannot exceed recorded errors")
    autonomy = (
        min(100.0, 100.0 * decision_metrics.applied / decision_metrics.proposed)
        if decision_metrics.proposed
        else None
    )
    reliability = 100.0 if errors == 0 else 100.0 * recoveries / errors
    return ReliabilityMetrics(
        errors=errors,
        recoveries=recoveries,
        autonomy_percent=autonomy,
        reliability_percent=reliability,
        longest_without_approved_action_minutes=_longest_without_action(
            samples,
            decisions,
            decision_metrics.proposed,
            timestep_minutes,
        ),
    )


def calculate_run_summary(
    *,
    metadata: RunMetadata,
    timestamp_utc: str,
    samples: Sequence[MetricSample],
    decisions: Sequence[DecisionAuditRecord] = (),
    graph_events: Sequence[GraphAuditRecord] = (),
    run_events: Sequence[RunAuditRecord] = (),
) -> RunMetricsSummary:
    """Calculate one run summary using the same path for both run modes."""

    _validate_run_identity(
        metadata.run_id,
        samples,
        decisions,
        graph_events,
        run_events,
    )
    _validate_decision_references(decisions, graph_events, run_events)
    energy = _energy_metrics(samples)
    comfort = _comfort_metrics(samples, metadata.timestep_minutes)
    decision_metrics = _decision_metrics(
        decisions,
        {sample.sequence for sample in samples},
    )
    reliability = _reliability_metrics(
        samples,
        decisions,
        run_events,
        decision_metrics,
        metadata.timestep_minutes,
    )
    return RunMetricsSummary(
        run_id=metadata.run_id,
        timestamp_utc=timestamp_utc,
        mode=metadata.mode,
        energy=energy,
        comfort=comfort,
        decisions=decision_metrics,
        reliability=reliability,
    )


def compare_run_summaries(
    *,
    baseline: RunMetricsSummary,
    controlled: RunMetricsSummary,
    timestamp_utc: str,
) -> ComparisonSummary:
    """Create a finite comparison, guarding the zero-baseline denominator."""

    if baseline.mode is not RunMode.BASELINE or controlled.mode is not RunMode.CONTROLLED:
        raise MetricsEvaluationError("comparison requires baseline then controlled summaries")
    savings_kwh = baseline.energy.hvac_kwh - controlled.energy.hvac_kwh
    savings_percent = (
        savings_kwh / baseline.energy.hvac_kwh * 100.0
        if baseline.energy.hvac_kwh > 0.0
        else None
    )
    return ComparisonSummary(
        run_id=controlled.run_id,
        timestamp_utc=timestamp_utc,
        baseline_run_id=baseline.run_id,
        baseline_hvac_kwh=baseline.energy.hvac_kwh,
        controlled_hvac_kwh=controlled.energy.hvac_kwh,
        savings_kwh=savings_kwh,
        savings_percent=savings_percent,
        baseline_cost_inr=baseline.energy.cost_inr,
        controlled_cost_inr=controlled.energy.cost_inr,
        cost_savings_inr=baseline.energy.cost_inr - controlled.energy.cost_inr,
        baseline_compliance_percent=baseline.comfort.occupied_pmv_compliance_percent,
        controlled_compliance_percent=controlled.comfort.occupied_pmv_compliance_percent,
        baseline_emergency_violations=baseline.comfort.emergency_violations,
        controlled_emergency_violations=controlled.comfort.emergency_violations,
    )


__all__ = [
    "EMERGENCY_PMV_LOWER",
    "EMERGENCY_PMV_UPPER",
    "MetricsEvaluationError",
    "TARGET_PMV_LOWER",
    "TARGET_PMV_UPPER",
    "calculate_run_summary",
    "compare_run_summaries",
]
