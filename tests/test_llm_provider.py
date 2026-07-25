from __future__ import annotations

import ast
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from ollama import ChatResponse, Client, Message, RequestError, ResponseError
from pydantic import ValidationError

from bms_agent.llm import (
    AdvisoryResult,
    AdvisoryRole,
    ChatModelMissingError,
    ChatReply,
    ChatRequest,
    ChatTimeoutError,
    ChatUnavailableError,
    ComfortAssessment,
    EnergyProposal,
    OllamaChatClient,
    ProviderConfig,
    ProviderStatus,
    StructuredAdvisoryProvider,
    SupervisorDecision,
    normalize_loopback_host,
)
from bms_agent.llm.client import ChatClient
from bms_agent.llm.schemas import AdvisoryContract, SupervisorDisposition

PRIMARY = "qwen3:4b-instruct"
FALLBACK = "qwen3:1.7b"

ENERGY_JSON = json.dumps(
    {
        "proposed_setpoint_c": 25.0,
        "expected_energy_effect": "reduce",
        "confidence": 0.8,
        "reason": "Higher setpoint reduces cooling demand.",
    }
)
COMFORT_JSON = json.dumps(
    {
        "comfort_state": "cold",
        "recommended_direction": "raise",
        "risk": "target_violation",
        "reason": "Occupied PMV is below the target band.",
    }
)
SUPERVISOR_JSON = json.dumps(
    {
        "decision": "accept",
        "setpoint_c": 25.0,
        "conflict": False,
        "energy": "Less cooling demand",
        "comfort": "Cold PMV supports raise",
    }
)


class FakeClient:
    def __init__(
        self,
        *,
        models: frozenset[str] = frozenset({PRIMARY}),
        replies: list[ChatReply | Exception] | None = None,
        list_error: Exception | None = None,
    ) -> None:
        self.models = models
        self.replies = list(replies or [])
        self.list_error = list_error
        self.requests: list[ChatRequest] = []
        self.list_calls = 0

    def list_models(self) -> frozenset[str]:
        self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
        return self.models

    def chat(self, request: ChatRequest) -> ChatReply:
        self.requests.append(request)
        if not self.replies:
            raise AssertionError("Fake response queue exhausted.")
        response = self.replies.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _reply(content: str, *, model: str = PRIMARY) -> ChatReply:
    return ChatReply(
        model=model,
        content=content,
        total_duration_ns=1_250_000_000,
        prompt_eval_count=17,
        eval_count=23,
    )


def _config(tmp_path: Path, **updates: object) -> ProviderConfig:
    values: dict[str, object] = {
        "audit_path": tmp_path / "nested" / "attempts.jsonl",
    }
    values.update(updates)
    return ProviderConfig.model_validate(values)


def _audit_rows(config: ProviderConfig) -> list[dict[str, object]]:
    return [
        cast(dict[str, object], json.loads(line))
        for line in config.audit_path.read_text(encoding="utf-8").splitlines()
    ]


@pytest.mark.parametrize(
    ("role", "schema", "content"),
    [
        (AdvisoryRole.ENERGY, EnergyProposal, ENERGY_JSON),
        (AdvisoryRole.COMFORT, ComfortAssessment, COMFORT_JSON),
        (AdvisoryRole.SUPERVISOR, SupervisorDecision, SUPERVISOR_JSON),
    ],
)
def test_all_role_schemas_parse_through_exact_bounded_request(
    tmp_path: Path,
    role: AdvisoryRole,
    schema: type[AdvisoryContract],
    content: str,
) -> None:
    client = FakeClient(replies=[_reply(content)])
    config = _config(tmp_path)
    provider = StructuredAdvisoryProvider(client, config)

    result = provider.generate(
        role=role,
        output_schema=schema,
        prompt="Compact observation evidence.",
    )

    assert result.status is ProviderStatus.SUCCESS
    assert isinstance(result.output, schema)
    assert result.model == PRIMARY
    assert result.attempt_count == 1
    assert result.correction_attempted is False
    assert result.used_fallback is False
    assert result.wall_duration_ms >= 0
    request = client.requests[0]
    assert request.model == PRIMARY
    assert request.stream is False
    assert request.think is False
    assert request.options == {"temperature": 0.0, "num_predict": 64, "seed": 0}
    assert request.keep_alive == "10m"
    assert request.schema["additionalProperties"] is False
    assert tuple(message.role for message in request.messages) == ("system", "user")
    if role is AdvisoryRole.SUPERVISOR:
        properties = cast(dict[str, object], request.schema["properties"])
        assert set(properties) == {
            "decision",
            "setpoint_c",
            "conflict",
            "energy",
            "comfort",
        }
        assert "decision,setpoint_c,conflict,energy,comfort" in request.messages[0].content
        assert "at most 28 characters" in request.messages[0].content

    rows = _audit_rows(config)
    assert len(rows) == 1
    assert rows[0]["role"] == role.value
    assert rows[0]["schema_name"] == schema.__name__
    assert rows[0]["model"] == PRIMARY
    assert rows[0]["api_duration_ms"] == 1250.0
    assert rows[0]["prompt_eval_count"] == 17
    assert rows[0]["eval_count"] == 23
    assert rows[0]["correction_index"] == 0
    assert rows[0]["outcome"] == "success"
    datetime.fromisoformat(cast(str, rows[0]["timestamp_utc"]))
    audit_text = config.audit_path.read_text(encoding="utf-8")
    assert "Compact observation evidence" not in audit_text
    assert "Higher setpoint" not in audit_text
    assert "thinking" not in audit_text.lower()


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (
            EnergyProposal,
            {
                **json.loads(ENERGY_JSON),
                "untrusted_override": True,
            },
        ),
        (
            ComfortAssessment,
            {
                **json.loads(COMFORT_JSON),
                "untrusted_override": True,
            },
        ),
        (
            SupervisorDecision,
            {
                **json.loads(SUPERVISOR_JSON),
                "untrusted_override": True,
            },
        ),
    ],
)
def test_representative_schemas_are_extra_forbidden_and_frozen(
    schema: type[AdvisoryContract],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        schema.model_validate(payload)

    valid_payload = {key: value for key, value in payload.items() if key != "untrusted_override"}
    valid = schema.model_validate_json(json.dumps(valid_payload))
    with pytest.raises(ValidationError, match="frozen"):
        valid.__setattr__("untrusted_override", True)


def test_supervisor_abstention_and_setpoint_are_consistent() -> None:
    abstain = SupervisorDecision(
        disposition=SupervisorDisposition.ABSTAIN,
        proposed_setpoint_c=None,
        conflict=True,
        energy_evidence="Potential energy reduction",
        comfort_evidence="Mixed PMV is ambiguous",
    )
    assert abstain.proposed_setpoint_c is None

    with pytest.raises(ValidationError, match="abstention"):
        abstain.model_copy(update={"proposed_setpoint_c": 24.0}).model_validate(
            {
                **abstain.model_dump(),
                "proposed_setpoint_c": 24.0,
            }
        )
    with pytest.raises(ValidationError, match="require a proposed"):
        SupervisorDecision(
            disposition=SupervisorDisposition.ACCEPT,
            proposed_setpoint_c=None,
            conflict=False,
            energy_evidence="Energy evidence.",
            comfort_evidence="Comfort evidence.",
        )


def test_supervisor_alias_schema_and_round_trip_preserve_internal_names() -> None:
    schema = SupervisorDecision.model_json_schema(by_alias=True)
    properties = cast(dict[str, object], schema["properties"])
    required = cast(list[str], schema["required"])
    wire_keys = {
        "decision",
        "setpoint_c",
        "conflict",
        "energy",
        "comfort",
    }

    assert set(properties) == wire_keys
    assert set(required) == wire_keys
    assert "disposition" not in properties
    assert cast(dict[str, object], properties["energy"])["maxLength"] == 28
    assert cast(dict[str, object], properties["comfort"])["maxLength"] == 28

    parsed = SupervisorDecision.model_validate_json(
        SUPERVISOR_JSON,
        by_alias=True,
        by_name=False,
    )
    assert parsed.disposition is SupervisorDisposition.ACCEPT
    assert parsed.proposed_setpoint_c == 25.0
    assert parsed.energy_evidence == "Less cooling demand"
    assert parsed.comfort_evidence == "Cold PMV supports raise"
    assert set(parsed.model_dump()) == {
        "disposition",
        "proposed_setpoint_c",
        "conflict",
        "energy_evidence",
        "comfort_evidence",
    }
    assert set(parsed.model_dump(by_alias=True)) == wire_keys

    populated_by_name = SupervisorDecision(
        disposition=SupervisorDisposition.REVISE,
        proposed_setpoint_c=24.5,
        conflict=True,
        energy_evidence="Energy case",
        comfort_evidence="Comfort case",
    )
    assert populated_by_name.model_dump()["disposition"] is SupervisorDisposition.REVISE


@pytest.mark.parametrize(
    "payload",
    [
        {
            "decision": "accept",
            "setpoint_c": 25.0,
            "conflict": False,
            "energy": "Energy evidence",
        },
        {
            "decision": "accept",
            "setpoint_c": 25.0,
            "conflict": False,
            "energy": "Energy evidence",
            "comfort": "Comfort evidence",
            "extra": "untrusted",
        },
        {
            "disposition": "accept",
            "proposed_setpoint_c": 25.0,
            "conflict": False,
            "energy_evidence": "Energy evidence",
            "comfort_evidence": "Comfort evidence",
        },
        {
            "decision": "accept",
            "setpoint_c": 25.0,
            "conflict": False,
            "energy": "x" * 29,
            "comfort": "Comfort evidence",
        },
    ],
)
def test_supervisor_wire_rejects_missing_extra_internal_or_long_keys(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        SupervisorDecision.model_validate_json(
            json.dumps(payload),
            by_alias=True,
            by_name=False,
        )


def test_prior_supervisor_malformed_path_corrects_once_with_compact_wire(
    tmp_path: Path,
) -> None:
    prior_truncated = (
        '{"decision":"accept","setpoint_c":25,"conflict":false,"energy":"reduced cooling"'
    )
    client = FakeClient(replies=[_reply(prior_truncated), _reply(SUPERVISOR_JSON)])
    config = _config(tmp_path)

    result = StructuredAdvisoryProvider(client, config).generate(
        role=AdvisoryRole.SUPERVISOR,
        output_schema=SupervisorDecision,
        prompt="Reconcile compact evidence.",
    )

    assert result.status is ProviderStatus.SUCCESS
    assert result.attempt_count == 2
    assert result.correction_attempted is True
    assert isinstance(result.output, SupervisorDecision)
    assert [row["outcome"] for row in _audit_rows(config)] == [
        "malformed",
        "success",
    ]
    assert len(client.requests) == 2
    for request in client.requests:
        assert set(cast(dict[str, object], request.schema["properties"])) == {
            "decision",
            "setpoint_c",
            "conflict",
            "energy",
            "comfort",
        }


def test_prior_supervisor_malformed_twice_still_fails_closed(tmp_path: Path) -> None:
    client = FakeClient(replies=[_reply('{"decision":"accept"}'), _reply("{}")])
    config = _config(tmp_path)

    result = StructuredAdvisoryProvider(client, config).generate(
        role=AdvisoryRole.SUPERVISOR,
        output_schema=SupervisorDecision,
        prompt="Reconcile compact evidence.",
    )

    assert result.status is ProviderStatus.MALFORMED
    assert result.output is None
    assert result.attempt_count == 2
    assert result.correction_attempted is True
    assert len(client.requests) == 2


def test_one_schema_correction_succeeds_without_replaying_raw_output(tmp_path: Path) -> None:
    raw_bad_output = "SECRET malformed non-JSON output"
    client = FakeClient(replies=[_reply(raw_bad_output), _reply(ENERGY_JSON)])
    config = _config(tmp_path)
    provider = StructuredAdvisoryProvider(client, config)

    result = provider.generate(
        role=AdvisoryRole.ENERGY,
        output_schema=EnergyProposal,
        prompt="Setpoint 24 C; occupied PMV -0.6.",
    )

    assert result.status is ProviderStatus.SUCCESS
    assert result.attempt_count == 2
    assert result.correction_attempted is True
    assert len(client.requests) == 2
    assert len(client.requests[1].messages) == 3
    assert "Prior output was invalid" in client.requests[1].messages[-1].content
    assert raw_bad_output not in repr(client.requests[1].messages)
    rows = _audit_rows(config)
    assert [row["outcome"] for row in rows] == ["malformed", "success"]
    assert [row["correction_index"] for row in rows] == [0, 1]
    assert raw_bad_output not in config.audit_path.read_text(encoding="utf-8")


def test_malformed_output_stops_after_exactly_one_correction(tmp_path: Path) -> None:
    client = FakeClient(replies=[_reply("{}"), _reply('{"still":"wrong"}')])
    config = _config(tmp_path)

    result = StructuredAdvisoryProvider(client, config).generate(
        role=AdvisoryRole.COMFORT,
        output_schema=ComfortAssessment,
        prompt="PMV evidence.",
    )

    assert result.status is ProviderStatus.MALFORMED
    assert result.output is None
    assert result.attempt_count == 2
    assert result.correction_attempted is True
    assert len(client.requests) == 2
    assert [row["outcome"] for row in _audit_rows(config)] == [
        "malformed",
        "malformed",
    ]


def test_deadline_bound_calls_cache_model_and_make_one_chat_per_role(
    tmp_path: Path,
) -> None:
    client = FakeClient(
        replies=[
            _reply(ENERGY_JSON),
            _reply(COMFORT_JSON),
            _reply(SUPERVISOR_JSON),
        ]
    )
    provider = StructuredAdvisoryProvider(client, _config(tmp_path))

    results = [
        provider.generate(
            role=role,
            output_schema=schema,
            prompt="Bounded role evidence.",
            deadline_bound=True,
        )
        for role, schema in (
            (AdvisoryRole.ENERGY, EnergyProposal),
            (AdvisoryRole.COMFORT, ComfortAssessment),
            (AdvisoryRole.SUPERVISOR, SupervisorDecision),
        )
    ]

    assert all(result.status is ProviderStatus.SUCCESS for result in results)
    assert all(result.attempt_count == 1 for result in results)
    assert all(result.correction_attempted is False for result in results)
    assert client.list_calls == 1
    assert len(client.requests) == 3
    assert [request.model for request in client.requests] == [PRIMARY] * 3


def test_deadline_bound_cache_preserves_exact_installed_fallback_selection(
    tmp_path: Path,
) -> None:
    client = FakeClient(
        models=frozenset({FALLBACK}),
        replies=[
            _reply(ENERGY_JSON, model=FALLBACK),
            _reply(COMFORT_JSON, model=FALLBACK),
        ],
    )
    provider = StructuredAdvisoryProvider(client, _config(tmp_path))

    energy = provider.generate(
        role=AdvisoryRole.ENERGY,
        output_schema=EnergyProposal,
        prompt="Bounded energy evidence.",
        deadline_bound=True,
    )
    comfort = provider.generate(
        role=AdvisoryRole.COMFORT,
        output_schema=ComfortAssessment,
        prompt="Bounded comfort evidence.",
        deadline_bound=True,
    )

    assert energy.status is ProviderStatus.SUCCESS
    assert comfort.status is ProviderStatus.SUCCESS
    assert energy.model == FALLBACK and comfort.model == FALLBACK
    assert energy.used_fallback is True and comfort.used_fallback is True
    assert client.list_calls == 1
    assert [request.model for request in client.requests] == [FALLBACK, FALLBACK]


def test_deadline_bound_malformed_returns_after_one_chat_without_correction(
    tmp_path: Path,
) -> None:
    client = FakeClient(replies=[_reply("{}"), _reply(ENERGY_JSON)])
    provider = StructuredAdvisoryProvider(client, _config(tmp_path))

    result = provider.generate(
        role=AdvisoryRole.ENERGY,
        output_schema=EnergyProposal,
        prompt="Bounded evidence.",
        deadline_bound=True,
    )

    assert result.status is ProviderStatus.MALFORMED
    assert result.attempt_count == 1
    assert result.correction_attempted is False
    assert len(client.requests) == 1
    assert len(client.requests[0].messages) == 2
    assert len(client.replies) == 1


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (ChatTimeoutError("deadline timeout"), ProviderStatus.TIMEOUT),
        (ChatUnavailableError("deadline unavailable"), ProviderStatus.UNAVAILABLE),
        (
            ChatModelMissingError("selected model disappeared"),
            ProviderStatus.MODEL_MISSING,
        ),
    ],
)
def test_deadline_bound_chat_failure_returns_without_second_chat(
    tmp_path: Path,
    failure: Exception,
    status: ProviderStatus,
) -> None:
    client = FakeClient(
        models=frozenset({PRIMARY, FALLBACK}),
        replies=[
            failure,
            _reply(ENERGY_JSON, model=FALLBACK),
        ],
    )
    provider = StructuredAdvisoryProvider(client, _config(tmp_path))

    result = provider.generate(
        role=AdvisoryRole.ENERGY,
        output_schema=EnergyProposal,
        prompt="Bounded evidence.",
        deadline_bound=True,
    )

    assert result.status is status
    assert result.attempt_count == 1
    assert result.correction_attempted is False
    assert result.used_fallback is False
    assert len(client.requests) == 1
    assert client.requests[0].model == PRIMARY


def test_regular_mode_still_discovers_and_corrects_after_deadline_cache(
    tmp_path: Path,
) -> None:
    client = FakeClient(
        replies=[
            _reply(ENERGY_JSON),
            _reply("{}"),
            _reply(ENERGY_JSON),
        ]
    )
    provider = StructuredAdvisoryProvider(client, _config(tmp_path))
    bounded = provider.generate(
        role=AdvisoryRole.ENERGY,
        output_schema=EnergyProposal,
        prompt="Bounded evidence.",
        deadline_bound=True,
    )

    regular = provider.generate(
        role=AdvisoryRole.ENERGY,
        output_schema=EnergyProposal,
        prompt="Regular evidence.",
    )

    assert bounded.status is ProviderStatus.SUCCESS
    assert regular.status is ProviderStatus.SUCCESS
    assert regular.attempt_count == 2
    assert regular.correction_attempted is True
    assert client.list_calls == 2
    assert len(client.requests) == 3


def test_already_installed_fallback_is_selected_without_installing(tmp_path: Path) -> None:
    client = FakeClient(models=frozenset({FALLBACK}), replies=[_reply(ENERGY_JSON, model=FALLBACK)])
    config = _config(tmp_path)

    result = StructuredAdvisoryProvider(client, config).generate(
        role=AdvisoryRole.ENERGY,
        output_schema=EnergyProposal,
        prompt="Energy evidence.",
    )

    assert result.status is ProviderStatus.SUCCESS
    assert result.model == FALLBACK
    assert result.used_fallback is True
    assert client.requests[0].model == FALLBACK
    assert not hasattr(client, "pull")


def test_primary_disappearing_after_listing_uses_available_fallback(tmp_path: Path) -> None:
    client = FakeClient(
        models=frozenset({PRIMARY, FALLBACK}),
        replies=[
            ChatModelMissingError("gone"),
            _reply(SUPERVISOR_JSON, model=FALLBACK),
        ],
    )
    config = _config(tmp_path)

    result = StructuredAdvisoryProvider(client, config).generate(
        role=AdvisoryRole.SUPERVISOR,
        output_schema=SupervisorDecision,
        prompt="Reconcile evidence.",
    )

    assert result.status is ProviderStatus.SUCCESS
    assert result.model == FALLBACK
    assert result.used_fallback is True
    assert result.attempt_count == 2
    assert [request.model for request in client.requests] == [PRIMARY, FALLBACK]
    assert [row["outcome"] for row in _audit_rows(config)] == [
        "model_missing",
        "success",
    ]


@pytest.mark.parametrize(
    ("fallback_reply", "expected_status"),
    [
        (_reply(ENERGY_JSON, model=FALLBACK), ProviderStatus.SUCCESS),
        (_reply('{"still":"malformed"}', model=FALLBACK), ProviderStatus.MALFORMED),
    ],
)
def test_correction_is_global_across_primary_to_fallback_transition(
    tmp_path: Path,
    fallback_reply: ChatReply,
    expected_status: ProviderStatus,
) -> None:
    client = FakeClient(
        models=frozenset({PRIMARY, FALLBACK}),
        replies=[
            _reply("{}"),
            ChatModelMissingError("primary disappeared during correction"),
            fallback_reply,
        ],
    )
    config = _config(tmp_path)

    result = StructuredAdvisoryProvider(client, config).generate(
        role=AdvisoryRole.ENERGY,
        output_schema=EnergyProposal,
        prompt="Energy evidence.",
    )

    assert result.status is expected_status
    assert result.attempt_count == 3
    assert result.correction_attempted is True
    assert result.used_fallback is True
    assert len(client.requests) == 3
    assert [request.model for request in client.requests] == [
        PRIMARY,
        PRIMARY,
        FALLBACK,
    ]
    assert [len(request.messages) for request in client.requests] == [2, 3, 3]
    assert [row["outcome"] for row in _audit_rows(config)] == [
        "malformed",
        "model_missing",
        expected_status.value,
    ]


def test_missing_primary_and_fallback_returns_controlled_signal(tmp_path: Path) -> None:
    client = FakeClient(models=frozenset({"other:model"}))
    config = _config(tmp_path)

    result = StructuredAdvisoryProvider(client, config).generate(
        role=AdvisoryRole.ENERGY,
        output_schema=EnergyProposal,
        prompt="Evidence.",
    )

    assert result.status is ProviderStatus.MODEL_MISSING
    assert result.model is None
    assert result.output is None
    assert result.attempt_count == 0
    assert client.requests == []
    assert _audit_rows(config)[0]["outcome"] == "model_missing"


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (ChatTimeoutError("timeout"), ProviderStatus.TIMEOUT),
        (ChatUnavailableError("offline"), ProviderStatus.UNAVAILABLE),
        (ChatModelMissingError("inventory failure"), ProviderStatus.MODEL_MISSING),
    ],
)
def test_model_discovery_failures_are_controlled(
    tmp_path: Path,
    failure: Exception,
    status: ProviderStatus,
) -> None:
    config = _config(tmp_path)
    client = FakeClient(list_error=failure)

    result = StructuredAdvisoryProvider(client, config).generate(
        role=AdvisoryRole.ENERGY,
        output_schema=EnergyProposal,
        prompt="Evidence.",
    )

    assert result.status is status
    assert result.attempt_count == 0
    assert result.output is None
    assert _audit_rows(config)[0]["outcome"] == status.value


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (ChatTimeoutError("timeout"), ProviderStatus.TIMEOUT),
        (ChatUnavailableError("offline"), ProviderStatus.UNAVAILABLE),
        (ChatModelMissingError("missing"), ProviderStatus.MODEL_MISSING),
    ],
)
def test_chat_failures_are_controlled_and_not_retried(
    tmp_path: Path,
    failure: Exception,
    status: ProviderStatus,
) -> None:
    config = _config(tmp_path, fallback_model=None)
    client = FakeClient(replies=[failure])

    result = StructuredAdvisoryProvider(client, config).generate(
        role=AdvisoryRole.COMFORT,
        output_schema=ComfortAssessment,
        prompt="Evidence.",
    )

    assert result.status is status
    assert result.attempt_count == 1
    assert result.output is None
    assert len(client.requests) == 1
    assert _audit_rows(config)[0]["outcome"] == status.value


def test_prompt_and_configuration_bounds_fail_fast(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        _config(tmp_path, timeout_seconds=30.1)
    with pytest.raises(ValidationError):
        _config(tmp_path, max_output_tokens=65)
    with pytest.raises(ValidationError):
        _config(tmp_path, temperature=0.3)
    with pytest.raises(ValidationError):
        _config(tmp_path, keep_alive="31m")

    provider = StructuredAdvisoryProvider(FakeClient(), _config(tmp_path))
    with pytest.raises(ValueError, match="blank"):
        provider.generate(
            role=AdvisoryRole.ENERGY,
            output_schema=EnergyProposal,
            prompt=" ",
        )
    with pytest.raises(ValueError, match="compact"):
        provider.generate(
            role=AdvisoryRole.ENERGY,
            output_schema=EnergyProposal,
            prompt="x" * 1_201,
        )
    with pytest.raises(ValueError, match="requires schema"):
        provider.generate(
            role=AdvisoryRole.ENERGY,
            output_schema=ComfortAssessment,
            prompt="Evidence.",
        )


def test_environment_configuration_uses_conservative_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BMS_LLM_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", PRIMARY)
    monkeypatch.setenv("OLLAMA_FALLBACK_MODEL", "")
    monkeypatch.setenv("BMS_LLM_AUDIT_PATH", "runs/test-attempts.jsonl")
    monkeypatch.setenv("BMS_LLM_KEEP_ALIVE", "20s")

    config = ProviderConfig.from_environment()

    assert config.timeout_seconds == 12.0
    assert config.fallback_model is None
    assert config.audit_path == Path("runs/test-attempts.jsonl")
    assert config.keep_alive == "20s"
    assert ProviderConfig().timeout_seconds == 8.0


class ConcurrentFakeClient:
    def __init__(self) -> None:
        self.active = 0
        self.maximum_active = 0
        self.guard = threading.Lock()

    def list_models(self) -> frozenset[str]:
        return frozenset({PRIMARY})

    def chat(self, request: ChatRequest) -> ChatReply:
        with self.guard:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        time.sleep(0.02)
        with self.guard:
            self.active -= 1
        return _reply(ENERGY_JSON)


def test_shared_model_calls_are_strictly_sequential(tmp_path: Path) -> None:
    client = ConcurrentFakeClient()
    provider = StructuredAdvisoryProvider(cast(ChatClient, client), _config(tmp_path))

    def call(index: int) -> AdvisoryResult[EnergyProposal]:
        return provider.generate(
            role=AdvisoryRole.ENERGY,
            output_schema=EnergyProposal,
            prompt=f"Evidence {index}.",
        )

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(call, range(3)))

    assert all(result.status is ProviderStatus.SUCCESS for result in results)
    assert client.maximum_active == 1
    assert len(_audit_rows(provider.config)) == 3


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1:11434",
        "http://127.0.0.1:11434/",
        "http://localhost:11434",
        "https://[::1]:11434",
    ],
)
def test_ollama_host_accepts_only_loopback(host: str) -> None:
    normalized = normalize_loopback_host(host)
    assert normalized.startswith(("http://", "https://"))


@pytest.mark.parametrize(
    "host",
    [
        "",
        "http://192.168.1.10:11434",
        "https://example.com",
        "http://user:pass@127.0.0.1:11434",
        "http://127.0.0.1:11434/api",
    ],
)
def test_ollama_host_rejects_remote_or_credentialed_urls(host: str) -> None:
    with pytest.raises(ValueError):
        normalize_loopback_host(host)


class FakeOllamaClient:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.chat_kwargs: dict[str, object] | None = None

    def list(self) -> object:
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(
            models=[
                SimpleNamespace(model=PRIMARY),
                SimpleNamespace(model=None),
            ]
        )

    def chat(self, **kwargs: object) -> ChatResponse:
        if self.failure is not None:
            raise self.failure
        self.chat_kwargs = kwargs
        return ChatResponse(
            model=PRIMARY,
            message=Message(role="assistant", content=ENERGY_JSON),
            total_duration=2_000_000,
            prompt_eval_count=5,
            eval_count=7,
        )


def test_ollama_adapter_preserves_nonstreaming_structured_contract() -> None:
    raw = FakeOllamaClient()
    adapter = OllamaChatClient(client=cast(Client, raw))
    request = ChatRequest(
        model=PRIMARY,
        messages=(),
        schema=EnergyProposal.model_json_schema(),
        options={"temperature": 0.0, "num_predict": 64},
        keep_alive="10m",
    )

    assert adapter.list_models() == frozenset({PRIMARY})
    reply = adapter.chat(request)

    assert reply.content == ENERGY_JSON
    assert reply.total_duration_ns == 2_000_000
    assert raw.chat_kwargs is not None
    assert raw.chat_kwargs["stream"] is False
    assert raw.chat_kwargs["think"] is False
    assert raw.chat_kwargs["format"] == request.schema
    assert raw.chat_kwargs["keep_alive"] == "10m"


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (httpx.ReadTimeout("slow"), ChatTimeoutError),
        (ResponseError("model not found", 404), ChatModelMissingError),
        (ResponseError("server failed", 500), ChatUnavailableError),
        (RequestError("bad request"), ChatUnavailableError),
        (ConnectionError("offline"), ChatUnavailableError),
        (RuntimeError("unexpected"), ChatUnavailableError),
    ],
)
def test_ollama_adapter_normalizes_dependency_errors(
    failure: Exception,
    expected: type[Exception],
) -> None:
    adapter = OllamaChatClient(client=cast(Client, FakeOllamaClient(failure=failure)))
    with pytest.raises(expected):
        adapter.list_models()


def test_llm_package_has_no_actuator_or_control_imports() -> None:
    package = Path(__file__).parents[1] / "src" / "bms_agent" / "llm"
    forbidden = (
        "bms_agent.control",
        "bms_agent.mcp_server",
        "bms_agent.simulation",
        "langgraph",
    )
    imported: set[str] = set()
    for source_path in package.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)
    assert not any(name.startswith(forbidden) for name in imported)
