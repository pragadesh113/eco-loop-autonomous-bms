"""Typed audit storage and quantitative run evaluation."""

from bms_agent.metrics.contracts import (
    SCHEMA_VERSION,
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
from bms_agent.metrics.evaluation import (
    MetricsEvaluationError,
    calculate_run_summary,
    compare_run_summaries,
)
from bms_agent.metrics.store import (
    DecisionTraceRecord,
    EventStore,
    MetricsStoreError,
    ReadResult,
)

__all__ = [
    "ComfortMetrics",
    "ComparisonSummary",
    "DecisionAuditRecord",
    "DecisionMetrics",
    "DecisionPhase",
    "DecisionTraceRecord",
    "EnergyMetrics",
    "EventStore",
    "GraphAuditRecord",
    "MetricSample",
    "MetricsEvaluationError",
    "MetricsStoreError",
    "ReadResult",
    "ReliabilityMetrics",
    "RunAuditRecord",
    "RunEventType",
    "RunMetadata",
    "RunMetricsSummary",
    "RunMode",
    "SCHEMA_VERSION",
    "TARIFF_INR_PER_KWH",
    "calculate_run_summary",
    "compare_run_summaries",
]
