from __future__ import annotations

import math
import time
import uuid
from collections.abc import Callable

import pytest
from pydantic import ValidationError

from bms_agent.cli import project_root
from bms_agent.control.safety import (
    ControlProposal,
    FallbackReasonCode,
    ObservationEnvelope,
    ObservationSnapshot,
    SafetyPolicy,
    ValidationReasonCode,
    ZoneSnapshot,
    choose_fallback,
    validate_proposal,
)
from bms_agent.mcp_server.server import (
    ActionRequest,
    AwaitObservationRequest,
    RunRequest,
    SessionRegistry,
    StartRequest,
)

POLICY = SafetyPolicy(expected_zone_ids=("z1", "z2"))


def _observation(
    pmvs: tuple[float | None, float | None] = (-0.4, -0.2),
    *,
    occupancies: tuple[float | None, float | None] = (1.0, 1.0),
    temperatures: tuple[float | None, float | None] = (24.0, 24.5),
    current_setpoint_c: float = 24.0,
    run_id: str = "run-1",
    decision_id: str = "decision-2",
    sequence: int = 2,
    zone_ids: tuple[str, ...] = ("z1", "z2"),
) -> ObservationEnvelope:
    return ObservationEnvelope(
        run_id=run_id,
        decision_id=decision_id,
        sequence=sequence,
        observed_at_utc="2026-07-25T21:00:00+00:00",
        snapshot=ObservationSnapshot(
            current_setpoint_c=current_setpoint_c,
            zones=tuple(
                ZoneSnapshot(
                    zone_id=zone_id,
                    temperature_c=temperatures[index],
                    pmv=pmvs[index],
                    occupancy_people=occupancies[index],
                )
                for index, zone_id in enumerate(zone_ids)
            ),
            temperature_unit="degC",
            pmv_unit="dimensionless",
            occupancy_unit="people",
        ),
    )


def _proposal(
    setpoint_c: float = 25.0,
    *,
    run_id: str = "run-1",
    decision_id: str = "decision-2",
    sequence: int = 2,
    energy_evidence: str = "Raising the cooling setpoint reduces cooling demand.",
    comfort_evidence: str = "Both occupied zones are below neutral PMV.",
) -> ControlProposal:
    return ControlProposal(
        run_id=run_id,
        decision_id=decision_id,
        observation_sequence=sequence,
        proposed_setpoint_c=setpoint_c,
        energy_evidence=energy_evidence,
        comfort_evidence=comfort_evidence,
    )


def test_contracts_forbid_extra_fields_and_are_frozen() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        ControlProposal.model_validate(
            {
                **_proposal().model_dump(),
                "untrusted_override": True,
            }
        )

    proposal = _proposal()
    with pytest.raises(ValidationError, match="frozen"):
        proposal.proposed_setpoint_c = 28.0


@pytest.mark.parametrize(
    "omitted",
    [
        {"temperature_unit"},
        {"pmv_unit"},
        {"occupancy_unit"},
        {"temperature_unit", "pmv_unit", "occupancy_unit"},
    ],
)
def test_observation_units_are_individually_required(omitted: set[str]) -> None:
    payload = _observation().snapshot.model_dump()
    for field in omitted:
        del payload[field]

    with pytest.raises(ValidationError, match="Field required"):
        ObservationSnapshot.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "wrong"),
    [
        ("temperature_unit", "degF"),
        ("pmv_unit", "score"),
        ("occupancy_unit", "fraction"),
    ],
)
def test_observation_units_reject_wrong_literals(field: str, wrong: str) -> None:
    payload = _observation().snapshot.model_dump()
    payload[field] = wrong

    with pytest.raises(ValidationError, match="Input should be"):
        ObservationSnapshot.model_validate(payload)


def test_observation_units_accept_only_explicit_expected_literals() -> None:
    snapshot = _observation().snapshot

    assert snapshot.temperature_unit == "degC"
    assert snapshot.pmv_unit == "dimensionless"
    assert snapshot.occupancy_unit == "people"


@pytest.mark.parametrize(
    ("observation", "proposal", "last_sequence"),
    [
        (
            _observation(pmvs=(0.4, 0.2), current_setpoint_c=23.0),
            _proposal(22.0),
            None,
        ),
        (_observation(current_setpoint_c=27.0), _proposal(28.0), None),
        (_observation(pmvs=(-0.5, -0.1)), _proposal(25.0), None),
        (_observation(pmvs=(0.5, -0.5)), _proposal(24.0), None),
        (_observation(sequence=3), _proposal(sequence=3), 2),
        (
            _observation(occupancies=(0.0, 0.0)),
            _proposal(25.0),
            None,
        ),
    ],
)
def test_exact_safe_boundaries_are_approved(
    observation: ObservationEnvelope,
    proposal: ControlProposal,
    last_sequence: int | None,
) -> None:
    result = validate_proposal(
        observation,
        proposal,
        last_accepted_sequence=last_sequence,
        policy=POLICY,
    )

    assert result.approved is True
    assert result.reason_code is ValidationReasonCode.APPROVED
    assert result.validated_setpoint_c == proposal.proposed_setpoint_c
    assert result.emergency_observed is False


CaseTransform = Callable[
    [ObservationEnvelope, ControlProposal],
    tuple[ObservationEnvelope, ControlProposal, int | None],
]


def _identity_case(
    observation: ObservationEnvelope, proposal: ControlProposal
) -> tuple[ObservationEnvelope, ControlProposal, int | None]:
    return observation, proposal.model_copy(update={"run_id": "other"}), None


def _stale_case(
    observation: ObservationEnvelope, proposal: ControlProposal
) -> tuple[ObservationEnvelope, ControlProposal, int | None]:
    return observation, proposal, observation.sequence


def _missing_zone_case(
    observation: ObservationEnvelope, proposal: ControlProposal
) -> tuple[ObservationEnvelope, ControlProposal, int | None]:
    snapshot = observation.snapshot.model_copy(update={"zones": observation.snapshot.zones[:1]})
    return observation.model_copy(update={"snapshot": snapshot}), proposal, None


def _non_finite_case(
    observation: ObservationEnvelope, proposal: ControlProposal
) -> tuple[ObservationEnvelope, ControlProposal, int | None]:
    return observation, proposal.model_copy(update={"proposed_setpoint_c": math.nan}), None


def _invalid_observation_case(
    observation: ObservationEnvelope, proposal: ControlProposal
) -> tuple[ObservationEnvelope, ControlProposal, int | None]:
    zones = list(observation.snapshot.zones)
    zones[0] = zones[0].model_copy(update={"occupancy_people": -1.0})
    snapshot = observation.snapshot.model_copy(update={"zones": tuple(zones)})
    return observation.model_copy(update={"snapshot": snapshot}), proposal, None


def _missing_energy_case(
    observation: ObservationEnvelope, proposal: ControlProposal
) -> tuple[ObservationEnvelope, ControlProposal, int | None]:
    return observation, proposal.model_copy(update={"energy_evidence": "  "}), None


def _missing_comfort_case(
    observation: ObservationEnvelope, proposal: ControlProposal
) -> tuple[ObservationEnvelope, ControlProposal, int | None]:
    return observation, proposal.model_copy(update={"comfort_evidence": ""}), None


def _bounds_case(
    observation: ObservationEnvelope, proposal: ControlProposal
) -> tuple[ObservationEnvelope, ControlProposal, int | None]:
    return observation, proposal.model_copy(update={"proposed_setpoint_c": 28.0001}), None


def _rate_case(
    observation: ObservationEnvelope, proposal: ControlProposal
) -> tuple[ObservationEnvelope, ControlProposal, int | None]:
    return observation, proposal.model_copy(update={"proposed_setpoint_c": 25.0001}), None


def _conflict_case(
    observation: ObservationEnvelope, proposal: ControlProposal
) -> tuple[ObservationEnvelope, ControlProposal, int | None]:
    return _observation(pmvs=(0.6, -0.6)), proposal.model_copy(
        update={"proposed_setpoint_c": 24.0}
    ), None


def _emergency_case(
    observation: ObservationEnvelope, proposal: ControlProposal
) -> tuple[ObservationEnvelope, ControlProposal, int | None]:
    return _observation(pmvs=(1.0001, 0.2)), proposal.model_copy(
        update={"proposed_setpoint_c": 24.0}
    ), None


def _hot_direction_case(
    observation: ObservationEnvelope, proposal: ControlProposal
) -> tuple[ObservationEnvelope, ControlProposal, int | None]:
    return _observation(pmvs=(0.1, 0.2)), proposal, None


def _hot_correction_case(
    observation: ObservationEnvelope, proposal: ControlProposal
) -> tuple[ObservationEnvelope, ControlProposal, int | None]:
    return _observation(pmvs=(0.5001, 0.2)), proposal.model_copy(
        update={"proposed_setpoint_c": 24.0}
    ), None


def _cold_correction_case(
    observation: ObservationEnvelope, proposal: ControlProposal
) -> tuple[ObservationEnvelope, ControlProposal, int | None]:
    return _observation(pmvs=(-0.5001, -0.2)), proposal.model_copy(
        update={"proposed_setpoint_c": 24.0}
    ), None


def _cold_direction_case(
    observation: ObservationEnvelope, proposal: ControlProposal
) -> tuple[ObservationEnvelope, ControlProposal, int | None]:
    return _observation(pmvs=(-0.1, -0.2)), proposal.model_copy(
        update={"proposed_setpoint_c": 23.0}
    ), None


def _neutral_direction_case(
    observation: ObservationEnvelope, proposal: ControlProposal
) -> tuple[ObservationEnvelope, ControlProposal, int | None]:
    return _observation(pmvs=(0.0, 0.0)), proposal, None


def _unoccupied_direction_case(
    observation: ObservationEnvelope, proposal: ControlProposal
) -> tuple[ObservationEnvelope, ControlProposal, int | None]:
    return _observation(occupancies=(0.0, 0.0)), proposal.model_copy(
        update={"proposed_setpoint_c": 23.0}
    ), None


@pytest.mark.parametrize(
    ("transform", "expected"),
    [
        (_identity_case, ValidationReasonCode.IDENTITY_MISMATCH),
        (_stale_case, ValidationReasonCode.STALE_OBSERVATION),
        (_missing_zone_case, ValidationReasonCode.MISSING_ZONE_DATA),
        (_non_finite_case, ValidationReasonCode.NON_FINITE_VALUE),
        (_invalid_observation_case, ValidationReasonCode.INVALID_OBSERVATION),
        (_missing_energy_case, ValidationReasonCode.MISSING_ENERGY_EVIDENCE),
        (_missing_comfort_case, ValidationReasonCode.MISSING_COMFORT_EVIDENCE),
        (_bounds_case, ValidationReasonCode.SETPOINT_OUT_OF_BOUNDS),
        (_rate_case, ValidationReasonCode.RATE_LIMIT_EXCEEDED),
        (_conflict_case, ValidationReasonCode.SHARED_ZONE_CONFLICT),
        (_emergency_case, ValidationReasonCode.EMERGENCY_FALLBACK_REQUIRED),
        (_hot_correction_case, ValidationReasonCode.HOT_CORRECTION_REQUIRED),
        (_cold_correction_case, ValidationReasonCode.COLD_CORRECTION_REQUIRED),
        (_hot_direction_case, ValidationReasonCode.HOT_DIRECTION_WORSENING),
        (_cold_direction_case, ValidationReasonCode.COLD_DIRECTION_WORSENING),
        (_neutral_direction_case, ValidationReasonCode.NEUTRAL_DIRECTION_UNSAFE),
        (_unoccupied_direction_case, ValidationReasonCode.UNOCCUPIED_ENERGY_DIRECTION),
    ],
)
def test_every_rejection_has_a_stable_machine_reason(
    transform: CaseTransform,
    expected: ValidationReasonCode,
) -> None:
    observation, proposal, last_sequence = transform(_observation(), _proposal())

    result = validate_proposal(
        observation,
        proposal,
        last_accepted_sequence=last_sequence,
        policy=POLICY,
    )

    assert result.approved is False
    assert result.reason_code is expected
    assert result.validated_setpoint_c is None
    assert result.evidence


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_zone_and_setpoint_values_never_pass(value: float) -> None:
    invalid_zone = _observation(pmvs=(value, -0.2))
    invalid_setpoint = _proposal(value)

    zone_result = validate_proposal(invalid_zone, _proposal(), policy=POLICY)
    setpoint_result = validate_proposal(_observation(), invalid_setpoint, policy=POLICY)

    assert zone_result.reason_code is ValidationReasonCode.NON_FINITE_VALUE
    assert setpoint_result.reason_code is ValidationReasonCode.NON_FINITE_VALUE


@pytest.mark.parametrize("field", ["temperature_c", "pmv", "occupancy_people"])
def test_missing_zone_fields_are_rejected(field: str) -> None:
    observation = _observation()
    zones = list(observation.snapshot.zones)
    zones[0] = zones[0].model_copy(update={field: None})
    snapshot = observation.snapshot.model_copy(update={"zones": tuple(zones)})

    result = validate_proposal(
        observation.model_copy(update={"snapshot": snapshot}),
        _proposal(),
        policy=POLICY,
    )

    assert result.reason_code is ValidationReasonCode.MISSING_ZONE_DATA


@pytest.mark.parametrize(
    "proposal",
    [
        _proposal(decision_id="other-decision"),
        _proposal(sequence=1),
        _proposal(sequence=3),
    ],
)
def test_decision_and_sequence_identity_must_match(proposal: ControlProposal) -> None:
    result = validate_proposal(_observation(), proposal, policy=POLICY)

    expected = (
        ValidationReasonCode.IDENTITY_MISMATCH
        if proposal.decision_id != "decision-2"
        else ValidationReasonCode.STALE_OBSERVATION
    )
    assert result.reason_code is expected


@pytest.mark.parametrize("setpoint", [21.9999, 28.0001])
def test_both_hard_setpoint_bounds_reject_outside_values(setpoint: float) -> None:
    result = validate_proposal(
        _observation(),
        _proposal(setpoint),
        policy=POLICY,
    )

    assert result.reason_code is ValidationReasonCode.SETPOINT_OUT_OF_BOUNDS


@pytest.mark.parametrize(
    ("pmvs", "setpoint"),
    [
        ((-0.4, -0.2), 25.0001),
        ((0.4, 0.2), 22.9999),
    ],
)
def test_rate_limit_rejects_both_directions(
    pmvs: tuple[float | None, float | None],
    setpoint: float,
) -> None:
    result = validate_proposal(
        _observation(pmvs=pmvs),
        _proposal(setpoint),
        policy=POLICY,
    )

    assert result.reason_code is ValidationReasonCode.RATE_LIMIT_EXCEEDED


@pytest.mark.parametrize(
    ("pmv", "reason", "emergency"),
    [
        (0.5, FallbackReasonCode.HOLD_OCCUPIED_COMFORTABLE, False),
        (-0.5, FallbackReasonCode.HOLD_OCCUPIED_COMFORTABLE, False),
        (1.0, FallbackReasonCode.CORRECT_OCCUPIED_HOT, False),
        (-1.0, FallbackReasonCode.CORRECT_OCCUPIED_COLD, False),
        (1.0001, FallbackReasonCode.CORRECT_OCCUPIED_HOT, True),
        (-1.0001, FallbackReasonCode.CORRECT_OCCUPIED_COLD, True),
    ],
)
def test_pmv_boundaries_are_exact(
    pmv: float,
    reason: FallbackReasonCode,
    emergency: bool,
) -> None:
    decision = choose_fallback(
        _observation(pmvs=(pmv, pmv)),
        last_safe_setpoint_c=24.0,
        policy=POLICY,
    )

    assert decision.reason_code is reason
    assert decision.emergency_observed is emergency


@pytest.mark.parametrize(
    ("pmvs", "occupancies", "last_safe", "trigger", "expected_setpoint", "expected_reason"),
    [
        (
            (0.6, 0.2),
            (1.0, 1.0),
            24.0,
            None,
            23.5,
            FallbackReasonCode.CORRECT_OCCUPIED_HOT,
        ),
        (
            (-0.6, -0.2),
            (1.0, 1.0),
            24.0,
            None,
            24.5,
            FallbackReasonCode.CORRECT_OCCUPIED_COLD,
        ),
        (
            (-0.5, 0.5),
            (1.0, 1.0),
            24.0,
            None,
            24.0,
            FallbackReasonCode.HOLD_OCCUPIED_COMFORTABLE,
        ),
        (
            (0.0, 0.0),
            (0.0, 0.0),
            27.5,
            None,
            28.0,
            FallbackReasonCode.SETBACK_UNOCCUPIED,
        ),
        (
            (0.0, 0.0),
            (0.0, 0.0),
            24.0,
            None,
            25.0,
            FallbackReasonCode.SETBACK_UNOCCUPIED,
        ),
        (
            (0.7, -0.7),
            (1.0, 1.0),
            24.0,
            ValidationReasonCode.SHARED_ZONE_CONFLICT,
            24.0,
            FallbackReasonCode.LAST_SAFE_SHARED_CONFLICT,
        ),
        (
            (1.2, 0.2),
            (1.0, 1.0),
            24.0,
            ValidationReasonCode.EMERGENCY_FALLBACK_REQUIRED,
            23.5,
            FallbackReasonCode.CORRECT_OCCUPIED_HOT,
        ),
        (
            (-1.2, -0.2),
            (1.0, 1.0),
            24.0,
            ValidationReasonCode.EMERGENCY_FALLBACK_REQUIRED,
            24.5,
            FallbackReasonCode.CORRECT_OCCUPIED_COLD,
        ),
        (
            (-0.2, -0.1),
            (1.0, 1.0),
            25.0,
            ValidationReasonCode.STALE_OBSERVATION,
            25.0,
            FallbackReasonCode.LAST_SAFE_INVALID_INPUT,
        ),
        (
            (-0.7, -0.2),
            (1.0, 1.0),
            24.0,
            ValidationReasonCode.ADVISORY_UNAVAILABLE,
            24.5,
            FallbackReasonCode.CORRECT_OCCUPIED_COLD,
        ),
        (
            (0.0, 0.0),
            (0.0, 0.0),
            24.0,
            ValidationReasonCode.ADVISORY_DEADLINE_EXHAUSTED,
            25.0,
            FallbackReasonCode.SETBACK_UNOCCUPIED,
        ),
    ],
)
def test_fallback_policy_table(
    pmvs: tuple[float | None, float | None],
    occupancies: tuple[float | None, float | None],
    last_safe: float | None,
    trigger: ValidationReasonCode | None,
    expected_setpoint: float,
    expected_reason: FallbackReasonCode,
) -> None:
    decision = choose_fallback(
        _observation(pmvs=pmvs, occupancies=occupancies),
        last_safe_setpoint_c=last_safe,
        trigger=trigger,
        policy=POLICY,
    )

    assert decision.setpoint_c == expected_setpoint
    assert decision.reason_code is expected_reason
    assert 22.0 <= decision.setpoint_c <= 28.0
    assert decision.evidence


@pytest.mark.parametrize(
    ("pmv", "trigger", "expected_setpoint", "expected_reason"),
    [
        (
            0.5001,
            ValidationReasonCode.HOT_CORRECTION_REQUIRED,
            23.5,
            FallbackReasonCode.CORRECT_OCCUPIED_HOT,
        ),
        (
            -0.5001,
            ValidationReasonCode.COLD_CORRECTION_REQUIRED,
            24.5,
            FallbackReasonCode.CORRECT_OCCUPIED_COLD,
        ),
    ],
)
def test_correction_required_routes_to_bounded_fallback(
    pmv: float,
    trigger: ValidationReasonCode,
    expected_setpoint: float,
    expected_reason: FallbackReasonCode,
) -> None:
    decision = choose_fallback(
        _observation(pmvs=(pmv, pmv)),
        last_safe_setpoint_c=24.0,
        trigger=trigger,
        policy=POLICY,
    )

    assert decision.setpoint_c == expected_setpoint
    assert decision.reason_code is expected_reason


def test_fallback_clamps_boundaries_and_uses_safe_default() -> None:
    hot_at_minimum = choose_fallback(
        _observation(pmvs=(0.6, 0.7)),
        last_safe_setpoint_c=22.0,
        policy=POLICY,
    )
    cold_at_maximum = choose_fallback(
        _observation(pmvs=(-0.6, -0.7)),
        last_safe_setpoint_c=28.0,
        policy=POLICY,
    )
    missing = choose_fallback(
        None,
        last_safe_setpoint_c=math.nan,
        trigger=ValidationReasonCode.MISSING_ZONE_DATA,
        policy=POLICY,
    )

    assert hot_at_minimum.setpoint_c == 22.0
    assert cold_at_maximum.setpoint_c == 28.0
    assert missing.setpoint_c == 24.0
    assert missing.reason_code is FallbackReasonCode.DEFAULT_SAFE_INVALID_INPUT
    assert missing.used_default_reference is True


@pytest.mark.parametrize(
    "last_safe",
    [None, math.nan, math.inf, -math.inf, 21.999, 28.001],
)
def test_invalid_last_safe_values_use_bounded_default(last_safe: float | None) -> None:
    decision = choose_fallback(
        None,
        last_safe_setpoint_c=last_safe,
        trigger=ValidationReasonCode.STALE_OBSERVATION,
        policy=POLICY,
    )

    assert decision.setpoint_c == 24.0
    assert decision.reason_code is FallbackReasonCode.DEFAULT_SAFE_INVALID_INPUT
    assert decision.used_default_reference is True


def test_machine_reason_code_catalog_is_stable_and_complete() -> None:
    assert {reason.value for reason in ValidationReasonCode} == {
        "APPROVED",
        "ADVISORY_UNAVAILABLE",
        "ADVISORY_DEADLINE_EXHAUSTED",
        "ADVISORY_ABSTAINED",
        "IDENTITY_MISMATCH",
        "STALE_OBSERVATION",
        "MISSING_ZONE_DATA",
        "NON_FINITE_VALUE",
        "INVALID_OBSERVATION",
        "MISSING_ENERGY_EVIDENCE",
        "MISSING_COMFORT_EVIDENCE",
        "SETPOINT_OUT_OF_BOUNDS",
        "RATE_LIMIT_EXCEEDED",
        "SHARED_ZONE_CONFLICT",
        "EMERGENCY_FALLBACK_REQUIRED",
        "HOT_CORRECTION_REQUIRED",
        "COLD_CORRECTION_REQUIRED",
        "HOT_DIRECTION_WORSENING",
        "COLD_DIRECTION_WORSENING",
        "NEUTRAL_DIRECTION_UNSAFE",
        "UNOCCUPIED_ENERGY_DIRECTION",
    }
    assert {reason.value for reason in FallbackReasonCode} == {
        "LAST_SAFE_INVALID_INPUT",
        "DEFAULT_SAFE_INVALID_INPUT",
        "LAST_SAFE_SHARED_CONFLICT",
        "CORRECT_OCCUPIED_HOT",
        "CORRECT_OCCUPIED_COLD",
        "HOLD_OCCUPIED_COMFORTABLE",
        "SETBACK_UNOCCUPIED",
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"setpoint_min_c": math.nan},
        {"setpoint_min_c": 28.0, "setpoint_max_c": 22.0},
        {"normal_rate_limit_c": 0.0},
        {"fallback_step_c": -0.5},
        {"comfortable_pmv_lower": -1.5},
        {"default_safe_setpoint_c": 29.0},
        {"expected_zone_ids": ()},
        {"expected_zone_ids": ("z1", "z1")},
    ],
)
def test_policy_configuration_is_fail_fast(changes: dict[str, object]) -> None:
    values: dict[str, object] = {
        "setpoint_min_c": 22.0,
        "setpoint_max_c": 28.0,
        "normal_rate_limit_c": 1.0,
        "fallback_step_c": 0.5,
        "comfortable_pmv_lower": -0.5,
        "comfortable_pmv_upper": 0.5,
        "emergency_pmv_lower": -1.0,
        "emergency_pmv_upper": 1.0,
        "default_safe_setpoint_c": 24.0,
        "expected_zone_ids": ("z1", "z2"),
    }
    values.update(changes)

    with pytest.raises(ValueError):
        SafetyPolicy(**values)  # type: ignore[arg-type]


def test_validated_proposal_does_not_bypass_mcp_session_guard() -> None:
    run_id = f"pytest-ctl-mcp-{uuid.uuid4().hex[:10]}"
    registry = SessionRegistry(project_root())
    started = registry.start(
        StartRequest(
            runId=run_id,
            maxWeatherTimesteps=96,
            actionWaitSeconds=0.25,
        )
    )
    assert started.ok is True

    live = None
    for _ in range(30):
        response = registry.await_observation(
            AwaitObservationRequest(runId=run_id, timeoutSeconds=2)
        )
        if response.data is not None and any(
            zone.occupancyPeople > 0 for zone in response.data.zones
        ):
            live = response.data
            break
    assert live is not None

    envelope = ObservationEnvelope(
        run_id=live.runId,
        decision_id=live.decisionId,
        sequence=live.sequence,
        observed_at_utc="2026-07-25T21:00:00+00:00",
        snapshot=ObservationSnapshot(
            current_setpoint_c=live.coolingScheduleValueC,
            zones=tuple(
                ZoneSnapshot(
                    zone_id=zone.zone,
                    temperature_c=zone.temperatureC,
                    pmv=zone.pmv,
                    occupancy_people=zone.occupancyPeople,
                )
                for zone in live.zones
            ),
            temperature_unit="degC",
            pmv_unit="dimensionless",
            occupancy_unit="people",
        ),
    )
    live_policy = SafetyPolicy(expected_zone_ids=tuple(zone.zone for zone in live.zones))
    stale_proposal = ControlProposal(
        run_id=live.runId,
        decision_id=live.decisionId,
        observation_sequence=live.sequence - 1,
        proposed_setpoint_c=live.coolingScheduleValueC,
        energy_evidence="No new action is justified.",
        comfort_evidence="This proposal deliberately carries a stale sequence.",
    )
    stale_result = validate_proposal(envelope, stale_proposal, policy=live_policy)
    assert stale_result.reason_code is ValidationReasonCode.STALE_OBSERVATION
    assert stale_result.validated_setpoint_c is None
    before_submit = registry.status(RunRequest(runId=run_id))
    assert before_submit.data is not None
    assert before_submit.data.actionCount == 0

    correction = choose_fallback(
        envelope,
        last_safe_setpoint_c=live.coolingScheduleValueC,
        trigger=ValidationReasonCode.COLD_CORRECTION_REQUIRED,
        policy=live_policy,
    )
    safe_proposal = ControlProposal(
        run_id=live.runId,
        decision_id=live.decisionId,
        observation_sequence=live.sequence,
        proposed_setpoint_c=correction.setpoint_c,
        energy_evidence="Use the smallest deterministic correction step.",
        comfort_evidence="Raise the setpoint by 0.5 C to correct measured cold PMV.",
    )
    validated = validate_proposal(envelope, safe_proposal, policy=live_policy)
    assert validated.approved is True
    after_rejected_route = registry.status(RunRequest(runId=run_id))
    assert after_rejected_route.data is not None
    assert after_rejected_route.data.actionCount == 0

    malicious_substitution = registry.submit_action(
        ActionRequest(
            runId=live.runId,
            decisionId=live.decisionId,
            observationSequence=live.sequence,
            idempotencyKey="ctl-malicious-substitution",
            setpointC=25.0,
            controlSource="advisory_proposal",
            energyEvidence=safe_proposal.energy_evidence,
            comfortEvidence=safe_proposal.comfort_evidence,
        )
    )
    assert malicious_substitution.error is not None
    assert malicious_substitution.error.code == "SAFETY_REJECTED"
    assert malicious_substitution.error.details["reasonCode"] == "RATE_LIMIT_EXCEEDED"
    after_substitution = registry.status(RunRequest(runId=run_id))
    assert after_substitution.data is not None
    assert after_substitution.data.actionCount == 0

    mismatched_fallback = registry.submit_action(
        ActionRequest(
            runId=live.runId,
            decisionId=live.decisionId,
            observationSequence=live.sequence,
            idempotencyKey="ctl-mismatched-fallback",
            setpointC=correction.setpoint_c + 0.1,
            controlSource="deterministic_fallback",
            fallbackTrigger=ValidationReasonCode.COLD_CORRECTION_REQUIRED,
        )
    )
    assert mismatched_fallback.error is not None
    assert mismatched_fallback.error.code == "FALLBACK_MISMATCH"
    after_fallback_mismatch = registry.status(RunRequest(runId=run_id))
    assert after_fallback_mismatch.data is not None
    assert after_fallback_mismatch.data.actionCount == 0

    assert validated.validated_setpoint_c is not None
    exact_fallback_request = ActionRequest(
        runId=live.runId,
        decisionId=live.decisionId,
        observationSequence=live.sequence,
        idempotencyKey="ctl-validated-action",
        setpointC=validated.validated_setpoint_c,
        controlSource="deterministic_fallback",
    )
    accepted = registry.submit_action(exact_fallback_request)
    assert accepted.ok is True
    assert accepted.data is not None
    assert accepted.data.authorizedSetpointC == correction.setpoint_c
    assert accepted.data.authorizationReasonCode == "CORRECT_OCCUPIED_COLD"
    replay = registry.submit_action(exact_fallback_request)
    assert replay.data is not None and replay.data.cached is True
    trigger_conflict = registry.submit_action(
        exact_fallback_request.model_copy(
            update={"fallbackTrigger": ValidationReasonCode.STALE_OBSERVATION}
        )
    )
    assert trigger_conflict.error is not None
    assert trigger_conflict.error.code == "IDEMPOTENCY_CONFLICT"

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        status = registry.status(RunRequest(runId=run_id))
        assert status.data is not None
        if status.data.status in {"completed", "cancelled", "failed"}:
            break
        time.sleep(0.05)
    summary = registry.summary(RunRequest(runId=run_id))
    assert summary.data is not None
    assert summary.data.status == "completed"
    assert summary.data.actionsApplied == 1
