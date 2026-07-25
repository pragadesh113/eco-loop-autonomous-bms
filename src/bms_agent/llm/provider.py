"""Bounded structured advisory provider with no simulation or actuator authority."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from bms_agent.llm.client import (
    ChatClient,
    ChatMessage,
    ChatModelMissingError,
    ChatReply,
    ChatRequest,
    ChatTimeoutError,
    ChatUnavailableError,
)
from bms_agent.llm.schemas import (
    AdvisoryContract,
    AdvisoryResult,
    AdvisoryRole,
    ComfortAssessment,
    EnergyProposal,
    OutputT,
    ProviderStatus,
    SupervisorDecision,
)

_SYSTEM_PROMPT = (
    "Return only schema JSON. Advice is non-authoritative. "
    "Positive PMV is warm; negative PMV is cold."
)
_CORRECTION_PROMPT = (
    "Prior output was invalid. Return only corrected JSON matching the schema; no prose."
)
_SUPERVISOR_FORMAT_PROMPT = (
    "Supervisor keys exactly: decision,setpoint_c,conflict,energy,comfort. "
    "Keep energy and comfort at most 28 characters."
)
_INFERENCE_LOCK = threading.Lock()
_MAX_ATTEMPTS = 3
_ROLE_SCHEMAS: dict[AdvisoryRole, type[AdvisoryContract]] = {
    AdvisoryRole.ENERGY: EnergyProposal,
    AdvisoryRole.COMFORT: ComfortAssessment,
    AdvisoryRole.SUPERVISOR: SupervisorDecision,
}


class ProviderConfig(BaseModel):
    """Conservative provider configuration for the shared local model."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    host: str = "http://127.0.0.1:11434"
    primary_model: str = "qwen3:4b-instruct"
    fallback_model: str | None = "qwen3:1.7b"
    timeout_seconds: float = Field(default=8.0, gt=0.0, le=30.0)
    temperature: float = Field(default=0.0, ge=0.0, le=0.2)
    max_output_tokens: int = Field(default=64, ge=1, le=64)
    max_prompt_chars: int = Field(default=1_200, ge=32, le=4_000)
    keep_alive: str = "10m"
    audit_path: Path = Path("runs/llm-attempts.jsonl")

    @field_validator("primary_model", "fallback_model")
    @classmethod
    def require_model_tag(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Model tags must not be blank.")
        return normalized

    @field_validator("keep_alive")
    @classmethod
    def require_bounded_keep_alive(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) < 2 or normalized[-1] not in {"s", "m"}:
            raise ValueError("Keep-alive must use a bounded seconds or minutes value.")
        try:
            amount = int(normalized[:-1])
        except ValueError as exc:
            raise ValueError("Keep-alive must contain an integer duration.") from exc
        seconds = amount if normalized[-1] == "s" else amount * 60
        if seconds < 0 or seconds > 1_800:
            raise ValueError("Keep-alive must be between 0 and 30 minutes.")
        return normalized

    @classmethod
    def from_environment(cls) -> ProviderConfig:
        """Load only documented non-secret local settings from the environment."""

        fallback = os.environ.get("OLLAMA_FALLBACK_MODEL", "qwen3:1.7b").strip()
        return cls(
            host=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"),
            primary_model=os.environ.get("OLLAMA_MODEL", "qwen3:4b-instruct"),
            fallback_model=fallback or None,
            timeout_seconds=float(os.environ.get("BMS_LLM_TIMEOUT_SECONDS", "8")),
            keep_alive=os.environ.get("BMS_LLM_KEEP_ALIVE", "10m"),
            audit_path=Path(os.environ.get("BMS_LLM_AUDIT_PATH", "runs/llm-attempts.jsonl")),
        )


class AttemptAudit(BaseModel):
    """Compact append-only metadata; prompts and model thinking are excluded."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    timestamp_utc: str
    role: AdvisoryRole
    schema_name: str
    model: str
    wall_duration_ms: float = Field(ge=0.0)
    api_duration_ms: float | None = Field(default=None, ge=0.0)
    prompt_eval_count: int | None = Field(default=None, ge=0)
    eval_count: int | None = Field(default=None, ge=0)
    correction_index: int = Field(ge=0, le=1)
    outcome: ProviderStatus


class AdvisoryProvider(Protocol):
    """Interchangeable structured provider used later by graph role nodes."""

    def generate(
        self,
        *,
        role: AdvisoryRole,
        output_schema: type[OutputT],
        prompt: str,
        deadline_bound: bool = False,
    ) -> AdvisoryResult[OutputT]:
        """Produce one typed advisory result with bounded failure behavior."""
        ...


class StructuredAdvisoryProvider:
    """Sequential schema-constrained provider over an injectable chat client."""

    def __init__(self, client: ChatClient, config: ProviderConfig) -> None:
        self._client = client
        self.config = config
        self._deadline_model: str | None = None
        self._deadline_uses_fallback = False

    def generate(
        self,
        *,
        role: AdvisoryRole,
        output_schema: type[OutputT],
        prompt: str,
        deadline_bound: bool = False,
    ) -> AdvisoryResult[OutputT]:
        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            raise ValueError("Advisory prompt must not be blank.")
        if len(normalized_prompt) > self.config.max_prompt_chars:
            raise ValueError("Advisory prompt exceeds the configured compact limit.")
        expected_schema = _ROLE_SCHEMAS[role]
        if output_schema is not expected_schema:
            raise ValueError(f"Role {role.value!r} requires schema {expected_schema.__name__}.")
        started = perf_counter()
        with _INFERENCE_LOCK:
            return self._generate_locked(
                role=role,
                output_schema=output_schema,
                prompt=normalized_prompt,
                started=started,
                deadline_bound=deadline_bound,
            )

    def _generate_locked(
        self,
        *,
        role: AdvisoryRole,
        output_schema: type[OutputT],
        prompt: str,
        started: float,
        deadline_bound: bool,
    ) -> AdvisoryResult[OutputT]:
        schema_name = output_schema.__name__
        selection_started = perf_counter()
        if deadline_bound and self._deadline_model is not None:
            model = self._deadline_model
            used_fallback = self._deadline_uses_fallback
            available_models = frozenset({model})
        else:
            try:
                available_models = self._client.list_models()
            except ChatTimeoutError:
                self._audit_failure(
                    role,
                    schema_name,
                    self.config.primary_model,
                    selection_started,
                    ProviderStatus.TIMEOUT,
                )
                return self._failure(
                    output_schema=output_schema,
                    status=ProviderStatus.TIMEOUT,
                    role=role,
                    schema_name=schema_name,
                    model=None,
                    attempts=0,
                    correction_attempted=False,
                    used_fallback=False,
                    started=started,
                    detail="Model discovery timed out.",
                )
            except ChatUnavailableError:
                self._audit_failure(
                    role,
                    schema_name,
                    self.config.primary_model,
                    selection_started,
                    ProviderStatus.UNAVAILABLE,
                )
                return self._failure(
                    output_schema=output_schema,
                    status=ProviderStatus.UNAVAILABLE,
                    role=role,
                    schema_name=schema_name,
                    model=None,
                    attempts=0,
                    correction_attempted=False,
                    used_fallback=False,
                    started=started,
                    detail="Inference service is unavailable.",
                )
            except ChatModelMissingError:
                self._audit_failure(
                    role,
                    schema_name,
                    self.config.primary_model,
                    selection_started,
                    ProviderStatus.MODEL_MISSING,
                )
                return self._failure(
                    output_schema=output_schema,
                    status=ProviderStatus.MODEL_MISSING,
                    role=role,
                    schema_name=schema_name,
                    model=None,
                    attempts=0,
                    correction_attempted=False,
                    used_fallback=False,
                    started=started,
                    detail="Configured model inventory is unavailable.",
                )

            model, used_fallback = self._select_model(available_models)
            if model is None:
                self._audit_failure(
                    role,
                    schema_name,
                    self.config.primary_model,
                    selection_started,
                    ProviderStatus.MODEL_MISSING,
                )
                return self._failure(
                    output_schema=output_schema,
                    status=ProviderStatus.MODEL_MISSING,
                    role=role,
                    schema_name=schema_name,
                    model=None,
                    attempts=0,
                    correction_attempted=False,
                    used_fallback=False,
                    started=started,
                    detail="Neither configured model is installed.",
                )
            if deadline_bound:
                self._deadline_model = model
                self._deadline_uses_fallback = used_fallback

        system_prompt = _SYSTEM_PROMPT
        if role is AdvisoryRole.SUPERVISOR:
            system_prompt = f"{system_prompt} {_SUPERVISOR_FORMAT_PROMPT}"
        base_messages = (
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=prompt),
        )
        correction_index = 0
        correction_attempted = False
        attempts = 0

        while True:
            attempts += 1
            request = ChatRequest(
                model=model,
                messages=(
                    base_messages
                    if correction_index == 0
                    else (*base_messages, ChatMessage(role="user", content=_CORRECTION_PROMPT))
                ),
                schema=output_schema.model_json_schema(by_alias=True),
                options={
                    "temperature": self.config.temperature,
                    "num_predict": self.config.max_output_tokens,
                    "seed": 0,
                },
                keep_alive=self.config.keep_alive,
            )
            attempt_started = perf_counter()
            try:
                reply = self._client.chat(request)
            except ChatTimeoutError:
                self._audit_failure(
                    role,
                    schema_name,
                    model,
                    attempt_started,
                    ProviderStatus.TIMEOUT,
                    correction_index=correction_index,
                )
                return self._failure(
                    output_schema=output_schema,
                    status=ProviderStatus.TIMEOUT,
                    role=role,
                    schema_name=schema_name,
                    model=model,
                    attempts=attempts,
                    correction_attempted=correction_attempted,
                    used_fallback=used_fallback,
                    started=started,
                    detail="Inference attempt timed out.",
                )
            except ChatUnavailableError:
                self._audit_failure(
                    role,
                    schema_name,
                    model,
                    attempt_started,
                    ProviderStatus.UNAVAILABLE,
                    correction_index=correction_index,
                )
                return self._failure(
                    output_schema=output_schema,
                    status=ProviderStatus.UNAVAILABLE,
                    role=role,
                    schema_name=schema_name,
                    model=model,
                    attempts=attempts,
                    correction_attempted=correction_attempted,
                    used_fallback=used_fallback,
                    started=started,
                    detail="Inference service is unavailable.",
                )
            except ChatModelMissingError:
                self._audit_failure(
                    role,
                    schema_name,
                    model,
                    attempt_started,
                    ProviderStatus.MODEL_MISSING,
                    correction_index=correction_index,
                )
                fallback = self.config.fallback_model
                if (
                    not deadline_bound
                    and model == self.config.primary_model
                    and fallback is not None
                    and fallback in available_models
                    and attempts < _MAX_ATTEMPTS
                ):
                    model = fallback
                    used_fallback = True
                    continue
                return self._failure(
                    output_schema=output_schema,
                    status=ProviderStatus.MODEL_MISSING,
                    role=role,
                    schema_name=schema_name,
                    model=model,
                    attempts=attempts,
                    correction_attempted=correction_attempted,
                    used_fallback=used_fallback,
                    started=started,
                    detail="Selected model is not installed.",
                )

            try:
                output = output_schema.model_validate_json(
                    reply.content,
                    by_alias=True,
                    by_name=False,
                )
            except (ValidationError, ValueError):
                self._append_attempt(
                    self._attempt_record(
                        role=role,
                        schema_name=schema_name,
                        model=model,
                        attempt_started=attempt_started,
                        correction_index=correction_index,
                        outcome=ProviderStatus.MALFORMED,
                        reply=reply,
                    )
                )
                if not deadline_bound and not correction_attempted and attempts < _MAX_ATTEMPTS:
                    correction_attempted = True
                    correction_index = 1
                    continue
                return self._failure(
                    output_schema=output_schema,
                    status=ProviderStatus.MALFORMED,
                    role=role,
                    schema_name=schema_name,
                    model=model,
                    attempts=attempts,
                    correction_attempted=correction_attempted,
                    used_fallback=used_fallback,
                    started=started,
                    detail=(
                        "Model output was malformed in deadline-bound mode."
                        if deadline_bound
                        else "Model output remained malformed after one correction."
                    ),
                )

            self._append_attempt(
                self._attempt_record(
                    role=role,
                    schema_name=schema_name,
                    model=model,
                    attempt_started=attempt_started,
                    correction_index=correction_index,
                    outcome=ProviderStatus.SUCCESS,
                    reply=reply,
                )
            )
            return AdvisoryResult[OutputT](
                status=ProviderStatus.SUCCESS,
                role=role,
                schema_name=schema_name,
                model=model,
                output=output,
                attempt_count=attempts,
                correction_attempted=correction_attempted,
                used_fallback=used_fallback,
                wall_duration_ms=_elapsed_ms(started),
            )

    def _select_model(self, available_models: frozenset[str]) -> tuple[str | None, bool]:
        if self.config.primary_model in available_models:
            return self.config.primary_model, False
        fallback = self.config.fallback_model
        if fallback is not None and fallback in available_models:
            return fallback, True
        return None, False

    def _audit_failure(
        self,
        role: AdvisoryRole,
        schema_name: str,
        model: str,
        attempt_started: float,
        outcome: ProviderStatus,
        *,
        correction_index: int = 0,
    ) -> None:
        self._append_attempt(
            self._attempt_record(
                role=role,
                schema_name=schema_name,
                model=model,
                attempt_started=attempt_started,
                correction_index=correction_index,
                outcome=outcome,
            )
        )

    def _attempt_record(
        self,
        *,
        role: AdvisoryRole,
        schema_name: str,
        model: str,
        attempt_started: float,
        correction_index: int,
        outcome: ProviderStatus,
        reply: ChatReply | None = None,
    ) -> AttemptAudit:
        api_duration_ms = None
        if reply is not None and reply.total_duration_ns is not None:
            api_duration_ms = reply.total_duration_ns / 1_000_000
        return AttemptAudit(
            timestamp_utc=datetime.now(UTC).isoformat(),
            role=role,
            schema_name=schema_name,
            model=model,
            wall_duration_ms=_elapsed_ms(attempt_started),
            api_duration_ms=api_duration_ms,
            prompt_eval_count=None if reply is None else reply.prompt_eval_count,
            eval_count=None if reply is None else reply.eval_count,
            correction_index=correction_index,
            outcome=outcome,
        )

    def _append_attempt(self, record: AttemptAudit) -> None:
        path = self.config.audit_path
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record.model_dump(mode="json"), separators=(",", ":"))
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{line}\n")

    @staticmethod
    def _failure(
        *,
        output_schema: type[OutputT],
        status: ProviderStatus,
        role: AdvisoryRole,
        schema_name: str,
        model: str | None,
        attempts: int,
        correction_attempted: bool,
        used_fallback: bool,
        started: float,
        detail: str,
    ) -> AdvisoryResult[OutputT]:
        _ = output_schema
        return AdvisoryResult[OutputT](
            status=status,
            role=role,
            schema_name=schema_name,
            model=model,
            output=None,
            attempt_count=attempts,
            correction_attempted=correction_attempted,
            used_fallback=used_fallback,
            wall_duration_ms=_elapsed_ms(started),
            detail=detail,
        )


def _elapsed_ms(started: float) -> float:
    return max(0.0, (perf_counter() - started) * 1_000)


__all__ = [
    "AdvisoryProvider",
    "AttemptAudit",
    "ProviderConfig",
    "StructuredAdvisoryProvider",
]
