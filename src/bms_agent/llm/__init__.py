"""Schema-constrained advisory LLM provider with no actuator authority."""

from bms_agent.llm.client import (
    ChatClient,
    ChatMessage,
    ChatModelMissingError,
    ChatReply,
    ChatRequest,
    ChatTimeoutError,
    ChatUnavailableError,
)
from bms_agent.llm.ollama_client import OllamaChatClient, normalize_loopback_host
from bms_agent.llm.provider import (
    AdvisoryProvider,
    AttemptAudit,
    ProviderConfig,
    StructuredAdvisoryProvider,
)
from bms_agent.llm.schemas import (
    AdvisoryResult,
    AdvisoryRole,
    ComfortAssessment,
    ComfortRisk,
    ComfortState,
    EnergyEffect,
    EnergyProposal,
    ProviderStatus,
    SetpointDirection,
    SupervisorDecision,
    SupervisorDisposition,
)


def build_local_provider(
    config: ProviderConfig | None = None,
) -> StructuredAdvisoryProvider:
    """Build the bounded local provider without installing or pulling models."""

    resolved = config or ProviderConfig.from_environment()
    client = OllamaChatClient(
        host=resolved.host,
        timeout_seconds=resolved.timeout_seconds,
    )
    return StructuredAdvisoryProvider(client, resolved)


__all__ = [
    "AdvisoryProvider",
    "AdvisoryResult",
    "AdvisoryRole",
    "AttemptAudit",
    "ChatClient",
    "ChatMessage",
    "ChatModelMissingError",
    "ChatReply",
    "ChatRequest",
    "ChatTimeoutError",
    "ChatUnavailableError",
    "ComfortAssessment",
    "ComfortRisk",
    "ComfortState",
    "EnergyEffect",
    "EnergyProposal",
    "OllamaChatClient",
    "ProviderConfig",
    "ProviderStatus",
    "SetpointDirection",
    "StructuredAdvisoryProvider",
    "SupervisorDecision",
    "SupervisorDisposition",
    "build_local_provider",
    "normalize_loopback_host",
]
