"""Local-loopback Ollama implementation of the model-independent chat boundary."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
from ollama import ChatResponse, Client, RequestError, ResponseError

from bms_agent.llm.client import (
    ChatModelMissingError,
    ChatReply,
    ChatRequest,
    ChatTimeoutError,
    ChatUnavailableError,
)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def normalize_loopback_host(host: str) -> str:
    """Normalize a local Ollama URL and reject remote hosts."""

    candidate = host.strip()
    if not candidate:
        raise ValueError("Ollama host must not be empty.")
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in _LOOPBACK_HOSTS:
        raise ValueError("Ollama host must use an HTTP(S) loopback address.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Ollama host must not embed credentials.")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Ollama host must not contain a path, query, or fragment.")
    return candidate.rstrip("/")


class OllamaChatClient:
    """Bounded synchronous adapter for an already-running local Ollama server."""

    def __init__(
        self,
        *,
        host: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 8.0,
        client: Client | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Ollama timeout must be positive.")
        self.host = normalize_loopback_host(host)
        self.timeout_seconds = timeout_seconds
        self._client = client or Client(host=self.host, timeout=timeout_seconds)

    def list_models(self) -> frozenset[str]:
        try:
            response = self._client.list()
        except Exception as exc:
            raise _map_exception(exc) from exc
        return frozenset(
            model.model
            for model in response.models
            if isinstance(model.model, str) and model.model
        )

    def chat(self, request: ChatRequest) -> ChatReply:
        messages = [
            {"role": message.role, "content": message.content}
            for message in request.messages
        ]
        try:
            response: ChatResponse = self._client.chat(  # pyright: ignore[reportUnknownMemberType]
                model=request.model,
                messages=messages,
                stream=False,
                think=False,
                format=request.schema,
                options=request.options,
                keep_alive=request.keep_alive,
            )
        except Exception as exc:
            raise _map_exception(exc) from exc
        return ChatReply(
            model=response.model or request.model,
            content=response.message.content or "",
            total_duration_ns=response.total_duration,
            prompt_eval_count=response.prompt_eval_count,
            eval_count=response.eval_count,
        )


def _map_exception(
    exc: Exception,
) -> ChatTimeoutError | ChatModelMissingError | ChatUnavailableError:
    if isinstance(exc, httpx.TimeoutException | TimeoutError):
        return ChatTimeoutError("Ollama request timed out.")
    if isinstance(exc, ResponseError):
        message = str(exc.error).lower()
        if exc.status_code == 404 or ("model" in message and "not found" in message):
            return ChatModelMissingError("Requested Ollama model is not installed.")
        if exc.status_code in {408, 504}:
            return ChatTimeoutError("Ollama request timed out.")
        return ChatUnavailableError("Ollama returned an unavailable response.")
    if isinstance(
        exc,
        (
            RequestError,
            httpx.ConnectError,
            httpx.NetworkError,
            ConnectionError,
            OSError,
        ),
    ):
        return ChatUnavailableError("Ollama service is unavailable.")
    return ChatUnavailableError("Ollama request failed.")


__all__ = ["OllamaChatClient", "normalize_loopback_host"]
