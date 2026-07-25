"""Authoritative deterministic validation and fallback for shared HVAC control."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from bms_agent.simulation.baseline import ZONES


class Contract(BaseModel):
    """Immutable, extra-forbidden control contract."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ZoneSnapshot(Contract):
    zone_id: str
    temperature_c: float | None
    pmv: float | None
    occupancy_people: float | None


class ObservationSnapshot(Contract):
    current_setpoint_c: float
    zones: tuple[ZoneSnapshot, ...]
    temperature_unit: Literal["degC"]
    pmv_unit: Literal["dimensionless"]
    occupancy_unit: Literal["people"]


class ObservationEnvelope(Contract):
    run_id: str
    decision_id: str
    sequence: int
    observed_at_utc: str
    snapshot: ObservationSnapshot


class ControlProposal(Contract):
    run_id: str
    decision_id: str
    observation_sequence: int
    proposed_setpoint_c: float
    energy_evidence: str
    comfort_evidence: str


class ValidationReasonCode(StrEnum):
    APPROVED = "APPROVED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    STALE_OBSERVATION = "STALE_OBSERVATION"
    MISSING_ZONE_DATA = "MISSING_ZONE_DATA"
    NON_FINITE_VALUE = "NON_FINITE_VALUE"
    INVALID_OBSERVATION = "INVALID_OBSERVATION"
    MISSING_ENERGY_EVIDENCE = "MISSING_ENERGY_EVIDENCE"
    MISSING_COMFORT_EVIDENCE = "MISSING_COMFORT_EVIDENCE"
    SETPOINT_OUT_OF_BOUNDS = "SETPOINT_OUT_OF_BOUNDS"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    SHARED_ZONE_CONFLICT = "SHARED_ZONE_CONFLICT"
    EMERGENCY_FALLBACK_REQUIRED = "EMERGENCY_FALLBACK_REQUIRED"
    HOT_CORRECTION_REQUIRED = "HOT_CORRECTION_REQUIRED"
    COLD_CORRECTION_REQUIRED = "COLD_CORRECTION_REQUIRED"
    HOT_DIRECTION_WORSENING = "HOT_DIRECTION_WORSENING"
    COLD_DIRECTION_WORSENING = "COLD_DIRECTION_WORSENING"
    NEUTRAL_DIRECTION_UNSAFE = "NEUTRAL_DIRECTION_UNSAFE"
    UNOCCUPIED_ENERGY_DIRECTION = "UNOCCUPIED_ENERGY_DIRECTION"


class ValidationResult(Contract):
    approved: bool
    reason_code: ValidationReasonCode
    validated_setpoint_c: float | None
    emergency_observed: bool
    evidence: tuple[str, ...]


class FallbackReasonCode(StrEnum):
    LAST_SAFE_INVALID_INPUT = "LAST_SAFE_INVALID_INPUT"
    DEFAULT_SAFE_INVALID_INPUT = "DEFAULT_SAFE_INVALID_INPUT"
    LAST_SAFE_SHARED_CONFLICT = "LAST_SAFE_SHARED_CONFLICT"
    CORRECT_OCCUPIED_HOT = "CORRECT_OCCUPIED_HOT"
    CORRECT_OCCUPIED_COLD = "CORRECT_OCCUPIED_COLD"
    HOLD_OCCUPIED_COMFORTABLE = "HOLD_OCCUPIED_COMFORTABLE"
    SETBACK_UNOCCUPIED = "SETBACK_UNOCCUPIED"


class FallbackDecision(Contract):
    setpoint_c: float
    reason_code: FallbackReasonCode
    emergency_observed: bool
    used_default_reference: bool
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SafetyPolicy:
    setpoint_min_c: float = 22.0
    setpoint_max_c: float = 28.0
    normal_rate_limit_c: float = 1.0
    fallback_step_c: float = 0.5
    comfortable_pmv_lower: float = -0.5
    comfortable_pmv_upper: float = 0.5
    emergency_pmv_lower: float = -1.0
    emergency_pmv_upper: float = 1.0
    default_safe_setpoint_c: float = 24.0
    expected_zone_ids: tuple[str, ...] = ZONES

    def __post_init__(self) -> None:
        numeric = (
            self.setpoint_min_c,
            self.setpoint_max_c,
            self.normal_rate_limit_c,
            self.fallback_step_c,
            self.comfortable_pmv_lower,
            self.comfortable_pmv_upper,
            self.emergency_pmv_lower,
            self.emergency_pmv_upper,
            self.default_safe_setpoint_c,
        )
        if not all(math.isfinite(value) for value in numeric):
            raise ValueError("Safety policy values must be finite.")
        if not self.setpoint_min_c < self.setpoint_max_c:
            raise ValueError("Setpoint minimum must be below maximum.")
        if self.normal_rate_limit_c <= 0 or self.fallback_step_c <= 0:
            raise ValueError("Rate limit and fallback step must be positive.")
        if not (
            self.emergency_pmv_lower
            < self.comfortable_pmv_lower
            < self.comfortable_pmv_upper
            < self.emergency_pmv_upper
        ):
            raise ValueError("PMV bands must be strictly ordered.")
        if not self.setpoint_min_c <= self.default_safe_setpoint_c <= self.setpoint_max_c:
            raise ValueError("Default safe setpoint must be within bounds.")
        if not self.expected_zone_ids or len(set(self.expected_zone_ids)) != len(
            self.expected_zone_ids
        ):
            raise ValueError("Expected zone IDs must be non-empty and unique.")


DEFAULT_POLICY = SafetyPolicy()

_INVALID_FALLBACK_TRIGGERS = frozenset(
    {
        ValidationReasonCode.IDENTITY_MISMATCH,
        ValidationReasonCode.STALE_OBSERVATION,
        ValidationReasonCode.MISSING_ZONE_DATA,
        ValidationReasonCode.NON_FINITE_VALUE,
        ValidationReasonCode.INVALID_OBSERVATION,
        ValidationReasonCode.MISSING_ENERGY_EVIDENCE,
        ValidationReasonCode.MISSING_COMFORT_EVIDENCE,
        ValidationReasonCode.SETPOINT_OUT_OF_BOUNDS,
        ValidationReasonCode.RATE_LIMIT_EXCEEDED,
        ValidationReasonCode.HOT_DIRECTION_WORSENING,
        ValidationReasonCode.COLD_DIRECTION_WORSENING,
        ValidationReasonCode.NEUTRAL_DIRECTION_UNSAFE,
        ValidationReasonCode.UNOCCUPIED_ENERGY_DIRECTION,
    }
)


def _result(
    reason: ValidationReasonCode,
    *evidence: str,
    setpoint_c: float | None = None,
    emergency: bool = False,
) -> ValidationResult:
    return ValidationResult(
        approved=reason is ValidationReasonCode.APPROVED,
        reason_code=reason,
        validated_setpoint_c=setpoint_c,
        emergency_observed=emergency,
        evidence=evidence,
    )


def _zone_data_problem(
    observation: ObservationEnvelope, policy: SafetyPolicy
) -> ValidationReasonCode | None:
    zones = observation.snapshot.zones
    identifiers = tuple(zone.zone_id for zone in zones)
    if (
        len(identifiers) != len(policy.expected_zone_ids)
        or set(identifiers) != set(policy.expected_zone_ids)
        or len(set(identifiers)) != len(identifiers)
        or any(
            zone.temperature_c is None
            or zone.pmv is None
            or zone.occupancy_people is None
            for zone in zones
        )
    ):
        return ValidationReasonCode.MISSING_ZONE_DATA
    values = [observation.snapshot.current_setpoint_c]
    for zone in zones:
        assert zone.temperature_c is not None
        assert zone.pmv is not None
        assert zone.occupancy_people is not None
        values.extend((zone.temperature_c, zone.pmv, zone.occupancy_people))
    if not all(math.isfinite(value) for value in values):
        return ValidationReasonCode.NON_FINITE_VALUE
    if any(
        zone.occupancy_people is not None and zone.occupancy_people < 0 for zone in zones
    ):
        return ValidationReasonCode.INVALID_OBSERVATION
    if not (
        policy.setpoint_min_c
        <= observation.snapshot.current_setpoint_c
        <= policy.setpoint_max_c
    ):
        return ValidationReasonCode.INVALID_OBSERVATION
    return None


def _occupied_pmvs(observation: ObservationEnvelope) -> tuple[float, ...]:
    return tuple(
        zone.pmv
        for zone in observation.snapshot.zones
        if zone.pmv is not None
        and zone.occupancy_people is not None
        and zone.occupancy_people > 0
    )


def _pmv_state(
    occupied_pmvs: tuple[float, ...], policy: SafetyPolicy
) -> tuple[bool, bool, bool]:
    hot = any(pmv > policy.comfortable_pmv_upper for pmv in occupied_pmvs)
    cold = any(pmv < policy.comfortable_pmv_lower for pmv in occupied_pmvs)
    emergency = any(
        pmv < policy.emergency_pmv_lower or pmv > policy.emergency_pmv_upper
        for pmv in occupied_pmvs
    )
    return hot, cold, emergency


def validate_proposal(
    observation: ObservationEnvelope,
    proposal: ControlProposal,
    *,
    last_accepted_sequence: int | None = None,
    policy: SafetyPolicy = DEFAULT_POLICY,
) -> ValidationResult:
    """Validate one advisory proposal without trusting model predictions."""

    if (
        proposal.run_id != observation.run_id
        or proposal.decision_id != observation.decision_id
    ):
        return _result(
            ValidationReasonCode.IDENTITY_MISMATCH,
            "proposal run/decision identity differs from observation",
        )
    if (
        proposal.observation_sequence != observation.sequence
        or observation.sequence < 1
        or (
            last_accepted_sequence is not None
            and observation.sequence <= last_accepted_sequence
        )
    ):
        return _result(
            ValidationReasonCode.STALE_OBSERVATION,
            "proposal sequence is not the next fresh observation",
        )

    observation_problem = _zone_data_problem(observation, policy)
    if observation_problem is not None:
        return _result(observation_problem, "observation data failed deterministic checks")
    if not math.isfinite(proposal.proposed_setpoint_c):
        return _result(
            ValidationReasonCode.NON_FINITE_VALUE,
            "proposed setpoint is non-finite",
        )
    if not proposal.energy_evidence.strip():
        return _result(
            ValidationReasonCode.MISSING_ENERGY_EVIDENCE,
            "energy evidence is empty",
        )
    if not proposal.comfort_evidence.strip():
        return _result(
            ValidationReasonCode.MISSING_COMFORT_EVIDENCE,
            "comfort evidence is empty",
        )
    if not (
        policy.setpoint_min_c
        <= proposal.proposed_setpoint_c
        <= policy.setpoint_max_c
    ):
        return _result(
            ValidationReasonCode.SETPOINT_OUT_OF_BOUNDS,
            "proposed setpoint is outside hard bounds",
        )

    occupied_pmvs = _occupied_pmvs(observation)
    hot, cold, emergency = _pmv_state(occupied_pmvs, policy)
    if hot and cold:
        return _result(
            ValidationReasonCode.SHARED_ZONE_CONFLICT,
            "shared setpoint cannot correct simultaneous hot and cold violations",
            emergency=emergency,
        )
    if emergency:
        return _result(
            ValidationReasonCode.EMERGENCY_FALLBACK_REQUIRED,
            "emergency PMV requires deterministic correction",
            emergency=True,
        )

    delta = proposal.proposed_setpoint_c - observation.snapshot.current_setpoint_c
    if abs(delta) > policy.normal_rate_limit_c:
        return _result(
            ValidationReasonCode.RATE_LIMIT_EXCEEDED,
            "normal proposal exceeds one-decision rate limit",
        )
    if hot and delta == 0:
        return _result(
            ValidationReasonCode.HOT_CORRECTION_REQUIRED,
            "occupied hot violation requires a lower setpoint correction",
        )
    if cold and delta == 0:
        return _result(
            ValidationReasonCode.COLD_CORRECTION_REQUIRED,
            "occupied cold violation requires a higher setpoint correction",
        )
    if not occupied_pmvs and delta < 0:
        return _result(
            ValidationReasonCode.UNOCCUPIED_ENERGY_DIRECTION,
            "unoccupied optimization cannot increase cooling",
        )
    if delta > 0 and occupied_pmvs and max(occupied_pmvs) >= 0:
        code = (
            ValidationReasonCode.HOT_DIRECTION_WORSENING
            if max(occupied_pmvs) > 0
            else ValidationReasonCode.NEUTRAL_DIRECTION_UNSAFE
        )
        return _result(code, "raising setpoint would move an occupied zone away from neutral")
    if delta < 0 and occupied_pmvs and min(occupied_pmvs) <= 0:
        code = (
            ValidationReasonCode.COLD_DIRECTION_WORSENING
            if min(occupied_pmvs) < 0
            else ValidationReasonCode.NEUTRAL_DIRECTION_UNSAFE
        )
        return _result(code, "lowering setpoint would move an occupied zone away from neutral")

    return _result(
        ValidationReasonCode.APPROVED,
        "identity, freshness, data, evidence, bounds, rate, and direction passed",
        setpoint_c=proposal.proposed_setpoint_c,
    )


def _safe_reference(
    last_safe_setpoint_c: float | None, policy: SafetyPolicy
) -> tuple[float, bool]:
    if (
        last_safe_setpoint_c is not None
        and math.isfinite(last_safe_setpoint_c)
        and policy.setpoint_min_c
        <= last_safe_setpoint_c
        <= policy.setpoint_max_c
    ):
        return last_safe_setpoint_c, False
    return policy.default_safe_setpoint_c, True


def _clamp(value: float, policy: SafetyPolicy) -> float:
    return max(policy.setpoint_min_c, min(policy.setpoint_max_c, value))


def choose_fallback(
    observation: ObservationEnvelope | None,
    *,
    last_safe_setpoint_c: float | None,
    trigger: ValidationReasonCode | None = None,
    policy: SafetyPolicy = DEFAULT_POLICY,
) -> FallbackDecision:
    """Choose a bounded deterministic action; never infer through invalid data."""

    reference, used_default = _safe_reference(last_safe_setpoint_c, policy)
    if trigger in _INVALID_FALLBACK_TRIGGERS or observation is None:
        reason = (
            FallbackReasonCode.DEFAULT_SAFE_INVALID_INPUT
            if used_default
            else FallbackReasonCode.LAST_SAFE_INVALID_INPUT
        )
        return FallbackDecision(
            setpoint_c=reference,
            reason_code=reason,
            emergency_observed=False,
            used_default_reference=used_default,
            evidence=("invalid or stale input cannot authorize a new direction",),
        )
    observation_problem = _zone_data_problem(observation, policy)
    if observation_problem is not None:
        reason = (
            FallbackReasonCode.DEFAULT_SAFE_INVALID_INPUT
            if used_default
            else FallbackReasonCode.LAST_SAFE_INVALID_INPUT
        )
        return FallbackDecision(
            setpoint_c=reference,
            reason_code=reason,
            emergency_observed=False,
            used_default_reference=used_default,
            evidence=(f"observation rejected as {observation_problem.value}",),
        )

    occupied_pmvs = _occupied_pmvs(observation)
    hot, cold, emergency = _pmv_state(occupied_pmvs, policy)
    if hot and cold:
        return FallbackDecision(
            setpoint_c=reference,
            reason_code=FallbackReasonCode.LAST_SAFE_SHARED_CONFLICT,
            emergency_observed=emergency,
            used_default_reference=used_default,
            evidence=("simultaneous hot and cold occupied zones require a shared-setpoint hold",),
        )
    if hot:
        return FallbackDecision(
            setpoint_c=_clamp(reference - policy.fallback_step_c, policy),
            reason_code=FallbackReasonCode.CORRECT_OCCUPIED_HOT,
            emergency_observed=emergency,
            used_default_reference=used_default,
            evidence=(f"maximum occupied PMV={max(occupied_pmvs):.3f}",),
        )
    if cold:
        return FallbackDecision(
            setpoint_c=_clamp(reference + policy.fallback_step_c, policy),
            reason_code=FallbackReasonCode.CORRECT_OCCUPIED_COLD,
            emergency_observed=emergency,
            used_default_reference=used_default,
            evidence=(f"minimum occupied PMV={min(occupied_pmvs):.3f}",),
        )
    if occupied_pmvs:
        return FallbackDecision(
            setpoint_c=reference,
            reason_code=FallbackReasonCode.HOLD_OCCUPIED_COMFORTABLE,
            emergency_observed=False,
            used_default_reference=used_default,
            evidence=("all occupied PMV values are inside the comfort band",),
        )
    return FallbackDecision(
        setpoint_c=_clamp(
            reference + min(policy.normal_rate_limit_c, policy.setpoint_max_c - reference),
            policy,
        ),
        reason_code=FallbackReasonCode.SETBACK_UNOCCUPIED,
        emergency_observed=False,
        used_default_reference=used_default,
        evidence=("no occupied zones; move toward upper setback by at most rate limit",),
    )
