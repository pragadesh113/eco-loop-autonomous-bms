"""Strict persisted records and aggregate contracts for MET-001."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bms_agent.control import BoundedTimestamp, EventFieldName, SafeIdentity, UtcTimestamp

SCHEMA_VERSION = "1.0"
TARIFF_INR_PER_KWH = 8.0


class MetricsContract(BaseModel):
    """Immutable finite contract used by the metrics and event-store boundary."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class RunMode(StrEnum):
    BASELINE = "baseline"
    CONTROLLED = "controlled"


class DecisionPhase(StrEnum):
    PROPOSED = "proposed"
    APPLIED = "applied"
    REJECTED = "rejected"
    REVISION = "revision"
    FALLBACK = "fallback"


class RunEventType(StrEnum):
    ERROR = "error"
    RECOVERY = "recovery"


class RunMetadata(MetricsContract):
    """Run-scoped timing metadata shared by both evaluation modes."""

    record_type: Literal["run_metadata"] = "run_metadata"
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: SafeIdentity
    timestamp_utc: UtcTimestamp
    mode: RunMode
    timestep_minutes: float = Field(gt=0.0, le=60.0)


class MetricSample(MetricsContract):
    """One zone sample with repeated whole-building interval HVAC joules."""

    record_type: Literal["metric_sample"] = "metric_sample"
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: SafeIdentity
    timestamp_utc: UtcTimestamp
    simulation_timestamp: BoundedTimestamp
    sequence: int = Field(ge=1)
    zone_id: SafeIdentity
    hvac_electricity_j: float = Field(ge=0.0)
    hvac_energy_unit: Literal["J"]
    occupancy_people: float = Field(ge=0.0)
    occupancy_unit: Literal["people"]
    pmv: float
    pmv_unit: Literal["dimensionless"]
    ppd_percent: float | None = Field(default=None, ge=0.0, le=100.0)
    ppd_unit: Literal["percent"]


class DecisionAuditRecord(MetricsContract):
    """Allowlisted decision lifecycle record with no free-form model text."""

    record_type: Literal["decision"] = "decision"
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: SafeIdentity
    timestamp_utc: UtcTimestamp
    decision_id: SafeIdentity
    observation_sequence: int = Field(ge=1)
    phase: DecisionPhase
    revision_index: int = Field(default=0, ge=0, le=2)
    llm_latency_seconds: float | None = Field(default=None, ge=0.0)
    mcp_latency_seconds: float | None = Field(default=None, ge=0.0)


class GraphAuditRecord(MetricsContract):
    """Compact graph transition suitable for append-only persistence."""

    record_type: Literal["graph_event"] = "graph_event"
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: SafeIdentity
    timestamp_utc: UtcTimestamp
    decision_id: SafeIdentity | None
    node: EventFieldName
    phase: Literal["start", "finish", "update", "error"]
    error: bool


class RunAuditRecord(MetricsContract):
    """Normalized error or recovery without exception messages or payloads."""

    record_type: Literal["run_event"] = "run_event"
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: SafeIdentity
    timestamp_utc: UtcTimestamp
    decision_id: SafeIdentity | None = None
    event_type: RunEventType
    error_code: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Z][A-Z0-9_]{0,79}$",
    )

    @field_validator("error_code")
    @classmethod
    def reject_reserved_error_codes(cls, value: str) -> str:
        lowered = value.lower()
        reserved = ("password", "prompt", "raw_output", "secret", "token")
        if any(marker in lowered for marker in reserved):
            raise ValueError("error code contains a reserved redaction keyword")
        return value


class EnergyMetrics(MetricsContract):
    hvac_kwh: float = Field(ge=0.0)
    cost_inr: float = Field(ge=0.0)
    tariff_inr_per_kwh: float = Field(gt=0.0)


class ComfortMetrics(MetricsContract):
    occupied_samples: int = Field(ge=0)
    compliant_samples: int = Field(ge=0)
    occupied_pmv_compliance_percent: float = Field(ge=0.0, le=100.0)
    emergency_violations: int = Field(ge=0)
    comfort_violation_minutes: float = Field(ge=0.0)
    emergency_violation_minutes: float = Field(ge=0.0)
    mean_abs_pmv: float | None = Field(default=None, ge=0.0)
    max_abs_pmv: float | None = Field(default=None, ge=0.0)
    mean_ppd_percent: float | None = Field(default=None, ge=0.0, le=100.0)


class DecisionMetrics(MetricsContract):
    proposed: int = Field(ge=0)
    applied: int = Field(ge=0)
    rejected: int = Field(ge=0)
    revisions: int = Field(ge=0)
    fallbacks: int = Field(ge=0)
    mean_llm_latency_seconds: float | None = Field(default=None, ge=0.0)
    max_llm_latency_seconds: float | None = Field(default=None, ge=0.0)
    mean_mcp_latency_seconds: float | None = Field(default=None, ge=0.0)
    max_mcp_latency_seconds: float | None = Field(default=None, ge=0.0)


class ReliabilityMetrics(MetricsContract):
    errors: int = Field(ge=0)
    recoveries: int = Field(ge=0)
    autonomy_percent: float | None = Field(default=None, ge=0.0, le=100.0)
    reliability_percent: float = Field(ge=0.0, le=100.0)
    longest_without_approved_action_minutes: float | None = Field(default=None, ge=0.0)


class RunMetricsSummary(MetricsContract):
    """Final JSON-compatible summary calculated identically for both run modes."""

    record_type: Literal["run_summary"] = "run_summary"
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: SafeIdentity
    timestamp_utc: UtcTimestamp
    mode: RunMode
    energy: EnergyMetrics
    comfort: ComfortMetrics
    decisions: DecisionMetrics
    reliability: ReliabilityMetrics


class ComparisonSummary(MetricsContract):
    """Presentation-ready baseline-versus-controlled comparison."""

    record_type: Literal["comparison"] = "comparison"
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: SafeIdentity
    timestamp_utc: UtcTimestamp
    baseline_run_id: SafeIdentity
    baseline_hvac_kwh: float = Field(ge=0.0)
    controlled_hvac_kwh: float = Field(ge=0.0)
    savings_kwh: float
    savings_percent: float | None
    baseline_cost_inr: float = Field(ge=0.0)
    controlled_cost_inr: float = Field(ge=0.0)
    cost_savings_inr: float
    baseline_compliance_percent: float = Field(ge=0.0, le=100.0)
    controlled_compliance_percent: float = Field(ge=0.0, le=100.0)
    baseline_emergency_violations: int = Field(ge=0)
    controlled_emergency_violations: int = Field(ge=0)


__all__ = [
    "ComfortMetrics",
    "ComparisonSummary",
    "DecisionAuditRecord",
    "DecisionMetrics",
    "DecisionPhase",
    "EnergyMetrics",
    "GraphAuditRecord",
    "MetricSample",
    "MetricsContract",
    "ReliabilityMetrics",
    "RunAuditRecord",
    "RunEventType",
    "RunMetadata",
    "RunMetricsSummary",
    "RunMode",
    "SCHEMA_VERSION",
    "TARIFF_INR_PER_KWH",
]
