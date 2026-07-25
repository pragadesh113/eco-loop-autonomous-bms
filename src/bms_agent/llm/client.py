"""Model-independent synchronous chat boundary for advisory providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Literal["system", "user"]
    content: str


@dataclass(frozen=True, slots=True)
class ChatRequest:
    model: str
    messages: tuple[ChatMessage, ...]
    schema: dict[str, Any]
    options: dict[str, int | float]
    keep_alive: str
    stream: Literal[False] = False
    think: Literal[False] = False


@dataclass(frozen=True, slots=True)
class ChatReply:
    model: str
    content: str
    total_duration_ns: int | None = None
    prompt_eval_count: int | None = None
    eval_count: int | None = None


class ChatClient(Protocol):
    """Minimal boundary implementable by Ollama or a remote open-source service."""

    def list_models(self) -> frozenset[str]:
        """Return exact locally available model tags without installing anything."""
        ...

    def chat(self, request: ChatRequest) -> ChatReply:
        """Return one complete, non-streamed structured response."""
        ...


class ChatClientError(RuntimeError):
    """Base error normalized at the client boundary."""


class ChatTimeoutError(ChatClientError):
    """The bounded inference request timed out."""


class ChatUnavailableError(ChatClientError):
    """The configured inference service cannot serve the request."""


class ChatModelMissingError(ChatClientError):
    """The explicitly requested model is not installed."""


__all__ = [
    "ChatClient",
    "ChatClientError",
    "ChatMessage",
    "ChatModelMissingError",
    "ChatReply",
    "ChatRequest",
    "ChatTimeoutError",
    "ChatUnavailableError",
]
