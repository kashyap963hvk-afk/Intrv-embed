"""Provider for any OpenAI-compatible endpoint (cloud APIs, vLLM, Ollama, LM Studio, llama-server...)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx2
import openai

from app.core.ssrf import UnsafeURLError
from app.llm.types import ChatChunk, ChatRequest, ProviderError, Usage

_NO_KEY = "sk-no-key-required"  # the SDK insists on a key; local servers ignore it


def _map_error(exc: Exception) -> ProviderError:  # noqa: PLR0911 - one branch per SDK error class
    if isinstance(exc, openai.APIConnectionError):
        cause = exc.__cause__
        while cause is not None:
            if isinstance(cause, UnsafeURLError):
                return ProviderError("blocked", cause.detail or "destination not allowed")
            cause = cause.__cause__
        if isinstance(exc, openai.APITimeoutError):
            return ProviderError("timeout", "the provider did not respond in time")
        return ProviderError("connection", f"could not reach the provider: {exc}")
    if isinstance(exc, openai.RateLimitError):
        return ProviderError("rate_limited", "the provider rate-limited this request", status_code=429)
    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
        return ProviderError("auth", "the API key was rejected", status_code=exc.status_code)
    if isinstance(exc, openai.NotFoundError):
        return ProviderError("not_found", "model or endpoint not found", status_code=404)
    if isinstance(exc, openai.BadRequestError | openai.UnprocessableEntityError):
        return ProviderError("bad_request", f"the provider rejected the request: {exc.message}", status_code=400)
    if isinstance(exc, openai.APIStatusError):
        return ProviderError("server", f"provider error {exc.status_code}", status_code=exc.status_code)
    if isinstance(exc, UnsafeURLError):
        return ProviderError("blocked", exc.detail or "destination not allowed")
    if isinstance(exc, httpx2.TimeoutException):
        return ProviderError("timeout", "the provider did not respond in time")
    return ProviderError("connection", f"unexpected provider failure: {type(exc).__name__}")


class OpenAICompatibleProvider:
    def __init__(self, *, base_url: str, api_key: str | None, http_client: httpx2.AsyncClient) -> None:
        self._client = openai.AsyncOpenAI(
            base_url=base_url, api_key=api_key or _NO_KEY, http_client=http_client, max_retries=0
        )

    def _payload(self, request: ChatRequest, *, include_usage: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": request.model, "messages": request.messages, "stream": True}
        if include_usage:
            payload["stream_options"] = {"include_usage": True}
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.response_format is not None:
            payload["response_format"] = request.response_format
        if request.tools:
            payload["tools"] = request.tools
        return payload

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        try:
            try:
                stream = await self._client.chat.completions.create(
                    **self._payload(request, include_usage=request.include_usage)
                )
            except openai.BadRequestError as exc:
                # Some servers reject stream_options; retry once without usage reporting.
                if not request.include_usage or "stream_options" not in str(exc):
                    raise
                stream = await self._client.chat.completions.create(**self._payload(request, include_usage=False))
            async with stream:
                async for chunk in stream:
                    usage = (
                        Usage(chunk.usage.prompt_tokens or 0, chunk.usage.completion_tokens or 0)
                        if chunk.usage
                        else None
                    )
                    if not chunk.choices:
                        if usage:
                            yield ChatChunk(usage=usage)
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta
                    names = tuple(
                        call.function.name
                        for call in (delta.tool_calls or [])
                        if call.function is not None and call.function.name
                    )
                    text = delta.content or ""
                    if text or names or choice.finish_reason or usage:
                        yield ChatChunk(text=text, tool_calls=names, usage=usage, finish_reason=choice.finish_reason)
        except ProviderError:
            raise
        except Exception as exc:
            raise _map_error(exc) from exc

    async def list_models(self) -> list[str]:
        try:
            page = await self._client.models.list()
            return sorted({model.id async for model in page})
        except Exception as exc:
            raise _map_error(exc) from exc
