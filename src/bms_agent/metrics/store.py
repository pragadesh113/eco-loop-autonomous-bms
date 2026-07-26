"""Append-only JSONL storage and atomic run-scoped exports."""

from __future__ import annotations

import csv
import io
import json
import os
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, TypeAlias
from uuid import uuid4

from pydantic import Field, TypeAdapter, ValidationError

from bms_agent.control import validate_identity
from bms_agent.metrics.contracts import (
    ComparisonSummary,
    DecisionAuditRecord,
    GraphAuditRecord,
    MetricSample,
    RunAuditRecord,
    RunMetadata,
    RunMetricsSummary,
)

PersistedRecord: TypeAlias = Annotated[
    RunMetadata | MetricSample | DecisionAuditRecord | GraphAuditRecord | RunAuditRecord,
    Field(discriminator="record_type"),
]
DecisionTraceRecord: TypeAlias = DecisionAuditRecord | GraphAuditRecord | RunAuditRecord
_RECORD_ADAPTER: TypeAdapter[PersistedRecord] = TypeAdapter(PersistedRecord)
_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[Path, threading.Lock] = {}

OBSERVATION_FIELDS = (
    "schema_version",
    "run_id",
    "timestamp_utc",
    "simulation_timestamp",
    "sequence",
    "zone_id",
    "hvac_electricity_j",
    "hvac_energy_unit",
    "occupancy_people",
    "occupancy_unit",
    "pmv",
    "pmv_unit",
    "ppd_percent",
    "ppd_unit",
)
COMPARISON_FIELDS = (
    "schema_version",
    "run_id",
    "timestamp_utc",
    "baseline_run_id",
    "baseline_hvac_kwh",
    "controlled_hvac_kwh",
    "savings_kwh",
    "savings_percent",
    "baseline_cost_inr",
    "controlled_cost_inr",
    "cost_savings_inr",
    "baseline_compliance_percent",
    "controlled_compliance_percent",
    "baseline_emergency_violations",
    "controlled_emergency_violations",
)


class MetricsStoreError(RuntimeError):
    """Safe storage error without record payload content."""


@dataclass(frozen=True, slots=True)
class ReadResult:
    records: tuple[PersistedRecord, ...]
    malformed_count: int


def _path_lock(path: Path) -> threading.Lock:
    resolved = path.resolve()
    with _LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(resolved, threading.Lock())


def _atomic_create(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    except FileExistsError as error:
        raise MetricsStoreError(f"output already exists: {path.name}") from error
    except OSError as error:
        raise MetricsStoreError(f"cannot create output: {path.name}") from error
    finally:
        temporary.unlink(missing_ok=True)


class EventStore:
    """Run-isolated JSONL store with in-process concurrent append serialization."""

    def __init__(self, root: Path, run_id: str) -> None:
        try:
            validate_identity(run_id)
        except ValueError as error:
            raise MetricsStoreError("invalid metrics run identity") from error
        self.root = root.resolve()
        self.run_id = run_id
        self.run_dir = (self.root / run_id).resolve()
        if self.run_dir.parent != self.root:
            raise MetricsStoreError("metrics run path escapes storage root")
        self.run_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _stream_name(record: PersistedRecord) -> str:
        if isinstance(record, RunMetadata):
            return "normalized-run-metadata.jsonl"
        if isinstance(record, MetricSample):
            return "normalized-metrics.jsonl"
        if isinstance(record, DecisionAuditRecord):
            return "normalized-decisions.jsonl"
        if isinstance(record, GraphAuditRecord):
            return "normalized-graph-events.jsonl"
        return "normalized-run-events.jsonl"

    def append(self, record: PersistedRecord) -> Path:
        if record.run_id != self.run_id:
            raise MetricsStoreError("record run identity differs from event store")
        path = self.run_dir / self._stream_name(record)
        serialized = record.model_dump_json()
        with _path_lock(path):
            try:
                with path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(serialized)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as error:
                raise MetricsStoreError(f"cannot append {path.name}") from error
        return path

    def read(self, stream_name: str) -> ReadResult:
        allowed = {
            "normalized-run-metadata.jsonl",
            "normalized-metrics.jsonl",
            "normalized-decisions.jsonl",
            "normalized-graph-events.jsonl",
            "normalized-run-events.jsonl",
        }
        if stream_name not in allowed:
            raise MetricsStoreError("unknown metrics stream")
        path = self.run_dir / stream_name
        if not path.exists():
            return ReadResult(records=(), malformed_count=0)
        records: list[PersistedRecord] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    try:
                        record = _RECORD_ADAPTER.validate_json(line)
                    except ValidationError as error:
                        raise MetricsStoreError(
                            f"malformed {path.name} at line {line_number}"
                        ) from error
                    if record.run_id != self.run_id:
                        raise MetricsStoreError(
                            f"cross-run record in {path.name} at line {line_number}"
                        )
                    records.append(record)
        except MetricsStoreError:
            raise
        except (OSError, UnicodeError) as error:
            raise MetricsStoreError(f"cannot read {path.name}") from error
        return ReadResult(records=tuple(records), malformed_count=0)

    def decision_trace(self, decision_id: str) -> tuple[DecisionTraceRecord, ...]:
        try:
            validate_identity(decision_id)
        except ValueError as error:
            raise MetricsStoreError("invalid decision identity") from error
        decision_records = self.read("normalized-decisions.jsonl").records
        graph_records = self.read("normalized-graph-events.jsonl").records
        run_records = self.read("normalized-run-events.jsonl").records
        trace = tuple(
            record
            for record in (*decision_records, *graph_records, *run_records)
            if isinstance(record, (DecisionAuditRecord, GraphAuditRecord, RunAuditRecord))
            and record.decision_id == decision_id
        )
        has_proposal = any(
            isinstance(record, DecisionAuditRecord)
            and record.phase.value == "proposed"
            for record in trace
        )
        if trace and not has_proposal:
            raise MetricsStoreError("decision trace has no proposed lifecycle")
        return tuple(
            sorted(
                trace,
                key=lambda record: (
                    record.timestamp_utc,
                    record.record_type,
                ),
            )
        )

    def write_observations_csv(self, samples: Sequence[MetricSample]) -> Path:
        if any(sample.run_id != self.run_id for sample in samples):
            raise MetricsStoreError("observation export contains another run")
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=OBSERVATION_FIELDS, lineterminator="\n")
        writer.writeheader()
        for sample in sorted(
            samples,
            key=lambda item: (item.timestamp_utc, item.sequence, item.zone_id),
        ):
            payload = sample.model_dump()
            writer.writerow({field: payload[field] for field in OBSERVATION_FIELDS})
        path = self.run_dir / "observations.csv"
        _atomic_create(path, output.getvalue())
        return path

    def write_summary(self, summary: RunMetricsSummary) -> Path:
        if summary.run_id != self.run_id:
            raise MetricsStoreError("summary belongs to another run")
        content = json.dumps(
            summary.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        path = self.run_dir / "summary.json"
        _atomic_create(path, content + "\n")
        return path

    def write_comparison(self, comparison: ComparisonSummary) -> Path:
        if comparison.run_id != self.run_id:
            raise MetricsStoreError("comparison belongs to another controlled run")
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=COMPARISON_FIELDS, lineterminator="\n")
        writer.writeheader()
        payload = comparison.model_dump(mode="json")
        writer.writerow({field: payload[field] for field in COMPARISON_FIELDS})
        path = self.run_dir / "comparison.csv"
        _atomic_create(path, output.getvalue())
        return path


__all__ = [
    "COMPARISON_FIELDS",
    "DecisionTraceRecord",
    "EventStore",
    "MetricsStoreError",
    "OBSERVATION_FIELDS",
    "PersistedRecord",
    "ReadResult",
]
