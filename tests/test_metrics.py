"""MET-001 calculation, audit, isolation, and persistence tests."""

from __future__ import annotations

import csv
import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from bms_agent.metrics import (
    ComparisonSummary,
    DecisionAuditRecord,
    DecisionPhase,
    EventStore,
    GraphAuditRecord,
    MetricSample,
    MetricsEvaluationError,
    MetricsStoreError,
    RunAuditRecord,
    RunEventType,
    RunMetadata,
    RunMetricsSummary,
    RunMode,
    calculate_run_summary,
    compare_run_summaries,
)

UTC_0 = "2026-07-26T00:00:00Z"
UTC_1 = "2026-07-26T00:00:01Z"
UTC_2 = "2026-07-26T00:00:02Z"


def metadata(run_id: str, mode: RunMode, *, timestep_minutes: float = 15.0) -> RunMetadata:
    return RunMetadata(
        run_id=run_id,
        timestamp_utc=UTC_0,
        mode=mode,
        timestep_minutes=timestep_minutes,
    )


def sample(
    run_id: str,
    *,
    sequence: int,
    zone_id: str,
    hvac_j: float,
    occupancy: float = 1.0,
    pmv: float = 0.0,
    ppd: float | None = 5.0,
) -> MetricSample:
    elapsed_minutes = sequence * 15
    hour, minute = divmod(elapsed_minutes, 60)
    return MetricSample(
        run_id=run_id,
        timestamp_utc=UTC_1,
        simulation_timestamp=f"2011-05-23T{hour:02d}:{minute:02d}:00+05:30",
        sequence=sequence,
        zone_id=zone_id,
        hvac_electricity_j=hvac_j,
        hvac_energy_unit="J",
        occupancy_people=occupancy,
        occupancy_unit="people",
        pmv=pmv,
        pmv_unit="dimensionless",
        ppd_percent=ppd,
        ppd_unit="percent",
    )


def decision(
    run_id: str,
    decision_id: str,
    phase: DecisionPhase,
    *,
    sequence: int,
    timestamp: str = UTC_1,
    revision_index: int = 0,
    llm_latency: float | None = None,
    mcp_latency: float | None = None,
) -> DecisionAuditRecord:
    return DecisionAuditRecord(
        run_id=run_id,
        timestamp_utc=timestamp,
        decision_id=decision_id,
        observation_sequence=sequence,
        phase=phase,
        revision_index=revision_index,
        llm_latency_seconds=llm_latency,
        mcp_latency_seconds=mcp_latency,
    )


def summary_fixture(run_id: str, mode: RunMode, *, hvac_j: float) -> RunMetricsSummary:
    return calculate_run_summary(
        metadata=metadata(run_id, mode),
        timestamp_utc=UTC_2,
        samples=(sample(run_id, sequence=1, zone_id="ZONE-A", hvac_j=hvac_j),),
    )


def test_shared_evaluator_deduplicates_building_energy_by_sequence_and_calculates_metrics() -> (
    None
):
    run_id = "controlled-math"
    samples = (
        sample(
            run_id,
            sequence=1,
            zone_id="ZONE-A",
            hvac_j=3_600_000.0,
            pmv=0.2,
            ppd=10.0,
        ),
        sample(
            run_id,
            sequence=1,
            zone_id="ZONE-B",
            hvac_j=3_600_000.0,
            pmv=0.6,
            ppd=None,
        ),
        sample(
            run_id,
            sequence=2,
            zone_id="ZONE-A",
            hvac_j=1_800_000.0,
            pmv=1.2,
            ppd=30.0,
        ),
        sample(
            run_id,
            sequence=2,
            zone_id="ZONE-B",
            hvac_j=1_800_000.0,
            occupancy=0.0,
            pmv=4.0,
            ppd=100.0,
        ),
    )
    decisions = (
        decision(
            run_id,
            "decision-1",
            DecisionPhase.PROPOSED,
            sequence=1,
            llm_latency=1.0,
        ),
        decision(
            run_id,
            "decision-1",
            DecisionPhase.APPLIED,
            sequence=1,
            mcp_latency=0.2,
        ),
        decision(
            run_id,
            "decision-2",
            DecisionPhase.PROPOSED,
            sequence=2,
            timestamp=UTC_2,
            llm_latency=3.0,
        ),
        decision(
            run_id,
            "decision-2",
            DecisionPhase.REJECTED,
            sequence=2,
            timestamp=UTC_2,
        ),
        decision(
            run_id,
            "decision-2",
            DecisionPhase.REVISION,
            sequence=2,
            timestamp=UTC_2,
            revision_index=1,
        ),
        decision(
            run_id,
            "decision-2",
            DecisionPhase.FALLBACK,
            sequence=2,
            timestamp=UTC_2,
        ),
        decision(
            run_id,
            "decision-2",
            DecisionPhase.APPLIED,
            sequence=2,
            timestamp=UTC_2,
            mcp_latency=0.4,
        ),
    )
    events = (
        RunAuditRecord(
            run_id=run_id,
            timestamp_utc=UTC_1,
            decision_id="decision-2",
            event_type=RunEventType.ERROR,
            error_code="PROVIDER_UNAVAILABLE",
        ),
        RunAuditRecord(
            run_id=run_id,
            timestamp_utc=UTC_2,
            decision_id="decision-2",
            event_type=RunEventType.RECOVERY,
            error_code="PROVIDER_UNAVAILABLE",
        ),
    )

    result = calculate_run_summary(
        metadata=metadata(run_id, RunMode.CONTROLLED),
        timestamp_utc=UTC_2,
        samples=samples,
        decisions=decisions,
        run_events=events,
    )

    assert math.isclose(result.energy.hvac_kwh, 1.5)
    assert math.isclose(result.energy.cost_inr, 12.0)
    assert result.comfort.occupied_samples == 3
    assert result.comfort.compliant_samples == 1
    assert math.isclose(result.comfort.occupied_pmv_compliance_percent, 100 / 3)
    assert result.comfort.emergency_violations == 1
    assert result.comfort.comfort_violation_minutes == 30.0
    assert result.comfort.emergency_violation_minutes == 15.0
    assert result.comfort.mean_abs_pmv is not None
    assert math.isclose(result.comfort.mean_abs_pmv, 2.0 / 3.0)
    assert result.comfort.max_abs_pmv == 1.2
    assert result.comfort.mean_ppd_percent == 20.0
    assert result.decisions.proposed == result.decisions.applied == 2
    assert result.decisions.rejected == 1
    assert result.decisions.revisions == 1
    assert result.decisions.fallbacks == 1
    assert result.decisions.mean_llm_latency_seconds == 2.0
    assert result.decisions.mean_mcp_latency_seconds is not None
    assert math.isclose(result.decisions.mean_mcp_latency_seconds, 0.3)
    assert result.reliability.errors == result.reliability.recoveries == 1
    assert result.reliability.autonomy_percent == 100.0
    assert result.reliability.reliability_percent == 100.0
    assert result.reliability.longest_without_approved_action_minutes == 0.0


def test_zero_energy_zero_occupancy_missing_ppd_and_baseline_has_no_fake_decisions() -> None:
    run_id = "baseline-zero"
    result = calculate_run_summary(
        metadata=metadata(run_id, RunMode.BASELINE),
        timestamp_utc=UTC_2,
        samples=(
            sample(
                run_id,
                sequence=1,
                zone_id="ZONE-A",
                hvac_j=0.0,
                occupancy=0.0,
                pmv=9.0,
                ppd=None,
            ),
        ),
    )

    assert result.energy.hvac_kwh == result.energy.cost_inr == 0.0
    assert result.comfort.occupied_samples == 0
    assert result.comfort.occupied_pmv_compliance_percent == 0.0
    assert result.comfort.mean_abs_pmv is None
    assert result.comfort.max_abs_pmv is None
    assert result.comfort.mean_ppd_percent is None
    assert result.decisions.proposed == 0
    assert result.reliability.autonomy_percent is None
    assert result.reliability.longest_without_approved_action_minutes is None


def test_action_gap_uses_hourly_decision_domain_not_metric_timesteps() -> None:
    run_id = "decision-domain"
    decisions = tuple(
        record
        for sequence in range(1, 4)
        for record in (
            decision(
                run_id,
                f"decision-{sequence}",
                DecisionPhase.PROPOSED,
                sequence=sequence,
            ),
            *(
                (
                    decision(
                        run_id,
                        f"decision-{sequence}",
                        DecisionPhase.APPLIED,
                        sequence=sequence,
                    ),
                )
                if sequence != 2
                else ()
            ),
        )
    )
    result = calculate_run_summary(
        metadata=RunMetadata(
            run_id=run_id,
            timestamp_utc=UTC_0,
            mode=RunMode.CONTROLLED,
            timestep_minutes=15.0,
            decision_interval_minutes=60.0,
            control_observation_count=3,
        ),
        timestamp_utc=UTC_2,
        samples=tuple(
            sample(run_id, sequence=sequence, zone_id="ZONE-A", hvac_j=0.0)
            for sequence in range(1, 13)
        ),
        decisions=decisions,
    )
    assert result.reliability.longest_without_approved_action_minutes == 60.0


def test_comfort_boundaries_are_inclusive_and_emergency_is_strictly_outside() -> None:
    run_id = "comfort-boundaries"
    values = (-0.5, 0.5, 0.5001, -1.0, -1.0001)
    result = calculate_run_summary(
        metadata=metadata(run_id, RunMode.BASELINE),
        timestamp_utc=UTC_2,
        samples=tuple(
            sample(
                run_id,
                sequence=index,
                zone_id="ZONE-A",
                hvac_j=0.0,
                pmv=pmv,
            )
            for index, pmv in enumerate(values, start=1)
        ),
    )

    assert result.comfort.compliant_samples == 2
    assert result.comfort.occupied_pmv_compliance_percent == 40.0
    assert result.comfort.emergency_violations == 1
    assert result.comfort.comfort_violation_minutes == 45.0
    assert result.comfort.emergency_violation_minutes == 15.0
    assert result.comfort.mean_abs_pmv is not None
    assert math.isclose(result.comfort.mean_abs_pmv, 0.70004)
    assert result.comfort.max_abs_pmv == 1.0001


def test_comparison_savings_cost_and_zero_baseline_guard() -> None:
    baseline = summary_fixture("baseline-a", RunMode.BASELINE, hvac_j=7_200_000.0)
    controlled = summary_fixture("controlled-a", RunMode.CONTROLLED, hvac_j=5_400_000.0)
    comparison = compare_run_summaries(
        baseline=baseline,
        controlled=controlled,
        timestamp_utc=UTC_2,
    )
    assert math.isclose(comparison.savings_kwh, 0.5)
    assert comparison.savings_percent is not None
    assert math.isclose(comparison.savings_percent, 25.0)
    assert math.isclose(comparison.cost_savings_inr, 4.0)

    zero = summary_fixture("baseline-zero-a", RunMode.BASELINE, hvac_j=0.0)
    zero_comparison = compare_run_summaries(
        baseline=zero,
        controlled=controlled.model_copy(
            update={
                "energy": controlled.energy.model_copy(
                    update={"hvac_kwh": 0.0, "cost_inr": 0.0}
                )
            }
        ),
        timestamp_utc=UTC_2,
    )
    assert zero_comparison.savings_kwh == 0.0
    assert zero_comparison.savings_percent is None


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("hvac_electricity_j", float("nan")),
        ("hvac_electricity_j", float("inf")),
        ("occupancy_people", float("nan")),
        ("pmv", float("nan")),
        ("ppd_percent", float("nan")),
    ],
)
def test_metric_sample_rejects_every_nonfinite_numeric(field_name: str, value: float) -> None:
    values = sample(
        "finite-run",
        sequence=1,
        zone_id="ZONE-A",
        hvac_j=0.0,
    ).model_dump()
    values[field_name] = value
    with pytest.raises(ValidationError):
        MetricSample.model_validate(values)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("hvac_energy_unit", "kWh"),
        ("occupancy_unit", "persons"),
        ("pmv_unit", "pmv"),
        ("ppd_unit", "%"),
    ],
)
def test_metric_sample_rejects_wrong_or_missing_units(field_name: str, value: str) -> None:
    values = sample(
        "unit-run",
        sequence=1,
        zone_id="ZONE-A",
        hvac_j=0.0,
    ).model_dump()
    values[field_name] = value
    with pytest.raises(ValidationError):
        MetricSample.model_validate(values)
    del values[field_name]
    with pytest.raises(ValidationError):
        MetricSample.model_validate(values)


def test_evaluator_rejects_inconsistent_energy_duplicates_identity_and_correlation() -> None:
    run_id = "invalid-evaluation"
    first = sample(run_id, sequence=1, zone_id="ZONE-A", hvac_j=1.0)
    mismatch = sample(run_id, sequence=1, zone_id="ZONE-B", hvac_j=2.0)
    with pytest.raises(MetricsEvaluationError, match="disagree"):
        calculate_run_summary(
            metadata=metadata(run_id, RunMode.BASELINE),
            timestamp_utc=UTC_2,
            samples=(first, mismatch),
        )
    with pytest.raises(MetricsEvaluationError, match="duplicate"):
        calculate_run_summary(
            metadata=metadata(run_id, RunMode.BASELINE),
            timestamp_utc=UTC_2,
            samples=(first, first),
        )
    with pytest.raises(MetricsEvaluationError, match="identity"):
        calculate_run_summary(
            metadata=metadata(run_id, RunMode.BASELINE),
            timestamp_utc=UTC_2,
            samples=(first.model_copy(update={"run_id": "another-run"}),),
        )
    with pytest.raises(MetricsEvaluationError, match="no proposal"):
        calculate_run_summary(
            metadata=metadata(run_id, RunMode.CONTROLLED),
            timestamp_utc=UTC_2,
            samples=(first,),
            decisions=(
                decision(
                    run_id,
                    "decision-1",
                    DecisionPhase.APPLIED,
                    sequence=1,
                ),
            ),
        )

    with pytest.raises(MetricsEvaluationError, match="changes its observation sequence"):
        calculate_run_summary(
            metadata=metadata(run_id, RunMode.CONTROLLED),
            timestamp_utc=UTC_2,
            samples=(first, sample(run_id, sequence=2, zone_id="ZONE-A", hvac_j=1.0)),
            decisions=(
                decision(
                    run_id,
                    "decision-sequence-mismatch",
                    DecisionPhase.PROPOSED,
                    sequence=1,
                ),
                decision(
                    run_id,
                    "decision-sequence-mismatch",
                    DecisionPhase.APPLIED,
                    sequence=2,
                ),
            ),
        )

    with pytest.raises(MetricsEvaluationError, match="absent from metric samples"):
        calculate_run_summary(
            metadata=metadata(run_id, RunMode.CONTROLLED),
            timestamp_utc=UTC_2,
            samples=(first,),
            decisions=(
                decision(
                    run_id,
                    "decision-missing-sequence",
                    DecisionPhase.PROPOSED,
                    sequence=99,
                ),
                decision(
                    run_id,
                    "decision-missing-sequence",
                    DecisionPhase.APPLIED,
                    sequence=99,
                ),
            ),
        )

    second_decision_same_sequence = (
        decision(
            run_id,
            "decision-a",
            DecisionPhase.PROPOSED,
            sequence=1,
        ),
        decision(
            run_id,
            "decision-a",
            DecisionPhase.APPLIED,
            sequence=1,
        ),
        decision(
            run_id,
            "decision-b",
            DecisionPhase.PROPOSED,
            sequence=1,
            timestamp=UTC_2,
        ),
    )
    with pytest.raises(MetricsEvaluationError, match="more than one proposed decision"):
        calculate_run_summary(
            metadata=metadata(run_id, RunMode.CONTROLLED),
            timestamp_utc=UTC_2,
            samples=(first,),
            decisions=second_decision_same_sequence,
        )


def test_event_store_appends_distinct_normalized_streams_and_correlates_decisions(
    tmp_path: Path,
) -> None:
    run_id = "store-run"
    store = EventStore(tmp_path, run_id)
    records = (
        metadata(run_id, RunMode.CONTROLLED),
        sample(run_id, sequence=1, zone_id="ZONE-A", hvac_j=1.0),
        decision(run_id, "decision-1", DecisionPhase.PROPOSED, sequence=1),
        decision(run_id, "decision-1", DecisionPhase.APPLIED, sequence=1),
        GraphAuditRecord(
            run_id=run_id,
            timestamp_utc=UTC_1,
            decision_id="decision-1",
            node="apply_action",
            phase="finish",
            error=False,
        ),
        RunAuditRecord(
            run_id=run_id,
            timestamp_utc=UTC_2,
            decision_id="decision-1",
            event_type=RunEventType.ERROR,
            error_code="NORMALIZED_FAILURE",
        ),
    )
    paths = [store.append(record) for record in records]

    assert {path.name for path in paths} == {
        "normalized-run-metadata.jsonl",
        "normalized-metrics.jsonl",
        "normalized-decisions.jsonl",
        "normalized-graph-events.jsonl",
        "normalized-run-events.jsonl",
    }
    trace = store.decision_trace("decision-1")
    assert len(trace) == 4
    assert [
        record.phase
        for record in trace
        if isinstance(record, DecisionAuditRecord)
    ] == [DecisionPhase.PROPOSED, DecisionPhase.APPLIED]
    for path in set(paths):
        for line in path.read_text(encoding="utf-8").splitlines():
            payload = json.loads(line)
            assert payload["schema_version"] == "1.0"
            assert payload["run_id"] == run_id
            assert payload["timestamp_utc"].endswith("Z")


def test_store_fails_closed_on_malformed_or_cross_run_records(tmp_path: Path) -> None:
    store = EventStore(tmp_path, "malformed-run")
    store.append(
        decision(
            "malformed-run",
            "decision-1",
            DecisionPhase.PROPOSED,
            sequence=1,
        )
    )
    path = store.run_dir / "normalized-decisions.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{malformed}\n")
        handle.write('{"record_type":"decision","run_id":"other-run"}\n')

    with pytest.raises(
        MetricsStoreError,
        match=r"malformed normalized-decisions\.jsonl at line 2",
    ):
        store.read("normalized-decisions.jsonl")
    valid = decision(
        "malformed-run",
        "decision-1",
        DecisionPhase.PROPOSED,
        sequence=1,
    )
    foreign = decision(
        "other-run",
        "decision-2",
        DecisionPhase.PROPOSED,
        sequence=2,
    )
    path.write_text(
        valid.model_dump_json() + "\n" + foreign.model_dump_json() + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        MetricsStoreError,
        match=r"cross-run record in normalized-decisions\.jsonl at line 2",
    ):
        store.read("normalized-decisions.jsonl")


def test_contracts_forbid_free_form_payloads_and_reserved_identifiers() -> None:
    values = decision(
        "redacted-run",
        "decision-1",
        DecisionPhase.PROPOSED,
        sequence=1,
    ).model_dump()
    values["raw_prompt"] = "not persisted"
    with pytest.raises(ValidationError):
        DecisionAuditRecord.model_validate(values)
    values.pop("raw_prompt")
    values["decision_id"] = "reserved-" + "sec" + "ret"
    with pytest.raises(ValidationError):
        DecisionAuditRecord.model_validate(values)
    with pytest.raises(ValidationError):
        RunAuditRecord(
            run_id="redacted-run",
            timestamp_utc=UTC_1,
            event_type=RunEventType.ERROR,
            error_code="MODEL_" + "SEC" + "RET",
        )


def test_summary_and_store_reject_events_for_ghost_decisions(tmp_path: Path) -> None:
    run_id = "ghost-correlation"
    real_decision = decision(
        run_id,
        "real-decision",
        DecisionPhase.PROPOSED,
        sequence=1,
    )
    ghost_event = RunAuditRecord(
        run_id=run_id,
        timestamp_utc=UTC_2,
        decision_id="ghost-decision",
        event_type=RunEventType.ERROR,
        error_code="NORMALIZED_FAILURE",
    )
    with pytest.raises(MetricsEvaluationError, match="without a proposed lifecycle"):
        calculate_run_summary(
            metadata=metadata(run_id, RunMode.CONTROLLED),
            timestamp_utc=UTC_2,
            samples=(sample(run_id, sequence=1, zone_id="ZONE-A", hvac_j=0.0),),
            decisions=(real_decision,),
            run_events=(ghost_event,),
        )

    store = EventStore(tmp_path, run_id)
    store.append(
        GraphAuditRecord(
            run_id=run_id,
            timestamp_utc=UTC_1,
            decision_id="ghost-decision",
            node="apply_action",
            phase="error",
            error=True,
        )
    )
    with pytest.raises(MetricsStoreError, match="no proposed lifecycle"):
        store.decision_trace("ghost-decision")


def test_store_path_isolation_and_cross_run_refusal(tmp_path: Path) -> None:
    with pytest.raises(MetricsStoreError):
        EventStore(tmp_path, "../escape")
    store = EventStore(tmp_path, "isolated-run")
    with pytest.raises(MetricsStoreError, match="differs"):
        store.append(
            sample(
                "another-run",
                sequence=1,
                zone_id="ZONE-A",
                hvac_j=0.0,
            )
        )
    assert store.run_dir.parent == tmp_path.resolve()


def test_concurrent_append_produces_complete_parseable_jsonl(tmp_path: Path) -> None:
    store = EventStore(tmp_path, "concurrent-run")
    records = tuple(
        decision(
            "concurrent-run",
            f"decision-{index}",
            DecisionPhase.PROPOSED,
            sequence=index + 1,
        )
        for index in range(64)
    )
    with ThreadPoolExecutor(max_workers=8) as executor:
        paths = tuple(executor.map(store.append, records))

    assert len(set(paths)) == 1
    result = store.read("normalized-decisions.jsonl")
    assert len(result.records) == 64
    assert result.malformed_count == 0
    assert {record.decision_id for record in result.records if isinstance(
        record, DecisionAuditRecord
    )} == {f"decision-{index}" for index in range(64)}


def test_batch_append_is_bounded_and_preserves_all_records(tmp_path: Path) -> None:
    store = EventStore(tmp_path, "batch-run")
    assert store.append_many(()) == ()
    records = tuple(
        decision(
            "batch-run",
            f"decision-{index}",
            DecisionPhase.PROPOSED,
            sequence=index + 1,
        )
        for index in range(3)
    )
    paths = store.append_many(records)
    assert len(paths) == 3
    assert len(set(paths)) == 1
    assert store.read("normalized-decisions.jsonl").records == records


def test_exports_are_stable_atomic_run_scoped_and_no_overwrite(tmp_path: Path) -> None:
    run_id = "export-run"
    store = EventStore(tmp_path, run_id)
    samples = (
        sample(run_id, sequence=2, zone_id="ZONE-B", hvac_j=1.0),
        sample(run_id, sequence=1, zone_id="ZONE-A", hvac_j=2.0),
    )
    summary = calculate_run_summary(
        metadata=metadata(run_id, RunMode.CONTROLLED),
        timestamp_utc=UTC_2,
        samples=samples,
    )
    baseline = summary_fixture("baseline-export", RunMode.BASELINE, hvac_j=4.0)
    comparison = compare_run_summaries(
        baseline=baseline,
        controlled=summary,
        timestamp_utc=UTC_2,
    )

    observations_path = store.write_observations_csv(samples)
    summary_path = store.write_summary(summary)
    comparison_path = store.write_comparison(comparison)

    with observations_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["sequence"] for row in rows] == ["1", "2"]
    assert json.loads(summary_path.read_text(encoding="utf-8"))["run_id"] == run_id
    with comparison_path.open(encoding="utf-8", newline="") as handle:
        comparison_rows = list(csv.DictReader(handle))
    assert len(comparison_rows) == 1
    assert comparison_rows[0]["baseline_run_id"] == "baseline-export"
    assert not tuple(store.run_dir.glob("*.tmp"))
    with pytest.raises(MetricsStoreError, match="already exists"):
        store.write_observations_csv(samples)
    with pytest.raises(MetricsStoreError, match="already exists"):
        store.write_summary(summary)
    with pytest.raises(MetricsStoreError, match="already exists"):
        store.write_comparison(comparison)


def test_atomic_summary_concurrency_allows_exactly_one_creator(tmp_path: Path) -> None:
    store = EventStore(tmp_path, "atomic-run")
    summary = summary_fixture("atomic-run", RunMode.CONTROLLED, hvac_j=1.0)

    def write(_: int) -> str:
        try:
            store.write_summary(summary)
        except MetricsStoreError:
            return "refused"
        return "created"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(write, range(2)))
    assert sorted(outcomes) == ["created", "refused"]
    assert json.loads((store.run_dir / "summary.json").read_text(encoding="utf-8"))[
        "run_id"
    ] == "atomic-run"


def test_comparison_rejects_wrong_modes_and_cross_run_exports(tmp_path: Path) -> None:
    baseline = summary_fixture("baseline-mode", RunMode.BASELINE, hvac_j=1.0)
    with pytest.raises(MetricsEvaluationError, match="requires baseline"):
        compare_run_summaries(
            baseline=baseline.model_copy(update={"mode": RunMode.CONTROLLED}),
            controlled=baseline.model_copy(
                update={"run_id": "controlled-mode", "mode": RunMode.CONTROLLED}
            ),
            timestamp_utc=UTC_2,
        )
    comparison = ComparisonSummary(
        run_id="another-run",
        timestamp_utc=UTC_2,
        baseline_run_id="baseline-mode",
        baseline_hvac_kwh=0.0,
        controlled_hvac_kwh=0.0,
        savings_kwh=0.0,
        savings_percent=None,
        baseline_cost_inr=0.0,
        controlled_cost_inr=0.0,
        cost_savings_inr=0.0,
        baseline_compliance_percent=0.0,
        controlled_compliance_percent=0.0,
        baseline_emergency_violations=0,
        controlled_emergency_violations=0,
    )
    with pytest.raises(MetricsStoreError, match="another controlled run"):
        EventStore(tmp_path, "export-owner").write_comparison(comparison)
