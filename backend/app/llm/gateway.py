"""LLM gateway: runs a request over an ordered chain of profiles with budgets, retries, a circuit breaker,
time-to-first-token and idle timeouts, cancellation, structured output, and usage reporting.

The gateway does not touch the database; `app.llm.service` resolves profiles and records usage.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.core.logging import get_logger
from app.llm.json_output import parse_model, response_format_for, schema_instruction
from app.llm.limits import ProfileLimiter
from app.llm.providers.base import ChatProvider
from app.llm.types import (
    CallCtx,
    ChatChunk,
    ChatRequest,
    Completion,
    LLMUnavailableError,
    Message,
    ProviderError,
    ResolvedProfile,
    StructuredOutputError,
    Usage,
    UsageRecord,
)

log = get_logger("app.llm")

ProviderFactory = Callable[[ResolvedProfile], ChatProvider]
UsageSink = Callable[[UsageRecord], Awaitable[None]]

_FAILURES_THAT_TRIP_BREAKER = frozenset({"rate_limited", "timeout", "connection", "server"})


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    max_retries: int = 2
    ttft_timeout_s: float = 20.0
    idle_timeout_s: float = 60.0
    backoff_base_s: float = 0.5
    backoff_cap_s: float = 4.0


@dataclass(slots=True)
class CallTrace:
    """Filled in by the gateway: which profile answered, what was tried, token usage."""

    profile: ResolvedProfile | None = None
    attempts: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    tool_calls: tuple[str, ...] = ()
    ttft_ms: int | None = None


@dataclass(slots=True)
class _AttemptState:
    started: float
    emitted: bool = False
    usage: Usage = field(default_factory=Usage)
    ttft_ms: int | None = None
    tool_names: list[str] = field(default_factory=list)


def _merge_usage(current: Usage, update: Usage | None) -> Usage:
    if update is None:
        return current
    return Usage(max(current.tokens_in, update.tokens_in), max(current.tokens_out, update.tokens_out))


def with_system_suffix(messages: Sequence[Message], suffix: str) -> list[Message]:
    """Append text to the system prompt (or add one) without mutating the caller's list."""
    result = [dict(m) for m in messages]
    for message in result:
        if message["role"] == "system" and isinstance(message.get("content"), str):
            message["content"] = f"{message['content']}\n\n{suffix}"
            return result
    return [{"role": "system", "content": suffix}, *result]


class LLMGateway:
    def __init__(
        self, provider_factory: ProviderFactory, limiter: ProfileLimiter | None, config: GatewayConfig | None = None
    ) -> None:
        self._factory = provider_factory
        self._limiter = limiter
        self.config = config or GatewayConfig()

    async def _admit(self, profile: ResolvedProfile, reasons: list[str]) -> bool:
        if self._limiter is None:
            return True
        if await self._limiter.is_open(profile.id):
            reasons.append(f"{profile.name}: paused after repeated failures")
            return False
        if not await self._limiter.try_acquire(profile.id, rpm=profile.rpm_limit, rpd=profile.rpd_limit):
            reasons.append(f"{profile.name}: request budget used up")
            return False
        return True

    def _backoff(self, attempt: int) -> float:
        return random.uniform(0, min(self.config.backoff_cap_s, self.config.backoff_base_s * 2**attempt))  # noqa: S311

    async def _run_attempt(
        self, provider: ChatProvider, request: ChatRequest, ttft_limit: float, state: _AttemptState
    ) -> AsyncIterator[str]:
        iterator = provider.stream(request)
        try:
            timeout = ttft_limit
            while True:
                try:
                    chunk: ChatChunk = await asyncio.wait_for(anext(iterator), timeout=timeout)
                except StopAsyncIteration:
                    return
                except TimeoutError as exc:
                    raise ProviderError("timeout", "no response within the time limit") from exc
                state.usage = _merge_usage(state.usage, chunk.usage)
                state.tool_names.extend(chunk.tool_calls)
                if (chunk.text or chunk.tool_calls) and state.ttft_ms is None:
                    state.ttft_ms = int((time.monotonic() - state.started) * 1000)
                    timeout = self.config.idle_timeout_s
                if chunk.text:
                    state.emitted = True
                    yield chunk.text
        finally:
            await iterator.aclose()  # type: ignore[attr-defined]

    async def _handle_failure(
        self,
        exc: ProviderError,
        profile: ResolvedProfile,
        ctx: CallCtx,
        attempt: int,
        state: _AttemptState,
        on_usage: UsageSink | None,
    ) -> bool:
        """Record a failed attempt; True means retry the same profile, False means move to the next one."""
        await self._report(on_usage, profile, ctx, state, ok=False, error=exc.kind)
        if self._limiter and exc.kind in _FAILURES_THAT_TRIP_BREAKER:
            await self._limiter.record_failure(profile.id)
        log.warning(
            "llm.attempt_failed",
            profile=profile.name,
            attempt=attempt + 1,
            kind=exc.kind,
            error=exc.message,
            role=ctx.role.value,
        )
        if state.emitted:
            raise exc
        if exc.retryable and attempt < self.config.max_retries:
            await asyncio.sleep(self._backoff(attempt))
            return True
        return False

    def _request(self, profile: ResolvedProfile, messages: Sequence[Message], options: dict[str, Any]) -> ChatRequest:
        temperature = options.get("temperature")
        max_tokens = options.get("max_tokens")
        return ChatRequest(
            model=profile.model,
            messages=list(messages),
            temperature=temperature if temperature is not None else profile.params.get("temperature"),
            max_tokens=max_tokens if max_tokens is not None else profile.params.get("max_tokens"),
            response_format=options.get("response_format"),
            tools=options.get("tools"),
        )

    async def stream(
        self,
        chain: Sequence[ResolvedProfile],
        messages: Sequence[Message],
        ctx: CallCtx,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        ttft_timeout_s: float | None = None,
        on_usage: UsageSink | None = None,
        trace: CallTrace | None = None,
    ) -> AsyncIterator[str]:
        """Yield text deltas from the first profile that answers.

        Retries and fallbacks only happen before the first delta is emitted; an error mid-stream propagates.
        Closing this generator (e.g. the candidate interrupted) closes the upstream HTTP stream.
        """
        trace = trace if trace is not None else CallTrace()
        options = {
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": response_format,
            "tools": tools,
        }
        reasons: list[str] = []
        for profile in chain:
            if not await self._admit(profile, reasons):
                continue
            try:
                provider = self._factory(profile)
            except ProviderError as exc:
                reasons.append(f"{profile.name}: {exc.message}")
                continue
            request = self._request(profile, messages, options)
            for attempt in range(self.config.max_retries + 1):
                trace.attempts.append(f"{profile.name}#{attempt + 1}")
                state = _AttemptState(started=time.monotonic())
                attempt_stream = self._run_attempt(
                    provider, request, ttft_timeout_s or self.config.ttft_timeout_s, state
                )
                try:
                    async for text in attempt_stream:
                        yield text
                except ProviderError as exc:
                    if await self._handle_failure(exc, profile, ctx, attempt, state, on_usage):
                        continue
                    reasons.append(f"{profile.name}: {exc.message}")
                    break
                finally:
                    await attempt_stream.aclose()  # type: ignore[attr-defined]
                if self._limiter:
                    await self._limiter.record_success(profile.id)
                await self._report(on_usage, profile, ctx, state, ok=True, error=None)
                trace.profile, trace.usage, trace.ttft_ms = profile, state.usage, state.ttft_ms
                trace.tool_calls = tuple(state.tool_names)
                return
        raise LLMUnavailableError(reasons or ["no model is configured for this task"])

    async def _report(
        self,
        sink: UsageSink | None,
        profile: ResolvedProfile,
        ctx: CallCtx,
        state: _AttemptState,
        *,
        ok: bool,
        error: str | None,
    ) -> None:
        if sink is None:
            return
        await sink(
            UsageRecord(
                profile=profile,
                ctx=ctx,
                usage=state.usage,
                latency_ms=int((time.monotonic() - state.started) * 1000),
                ttft_ms=state.ttft_ms,
                ok=ok,
                error_kind=error,
            )
        )

    async def complete(
        self,
        chain: Sequence[ResolvedProfile],
        messages: Sequence[Message],
        ctx: CallCtx,
        **kwargs: Any,
    ) -> Completion:
        trace = CallTrace()
        parts = [delta async for delta in self.stream(chain, messages, ctx, trace=trace, **kwargs)]
        if trace.profile is None:
            raise LLMUnavailableError(["the model returned no output"])
        return Completion(
            text="".join(parts),
            profile=trace.profile,
            usage=trace.usage,
            tool_calls=trace.tool_calls,
            attempts=tuple(trace.attempts),
        )

    async def complete_json[T: BaseModel](
        self,
        chain: Sequence[ResolvedProfile],
        messages: Sequence[Message],
        schema: type[T],
        ctx: CallCtx,
        *,
        on_usage: UsageSink | None = None,
        temperature: float | None = 0.0,
        max_tokens: int | None = None,
    ) -> T:
        """Structured output ladder, per profile: native json_schema → json_object → prompt-only; then
        local repair + validation; then one corrective retry with the validation error; then the next profile."""
        reasons: list[str] = []
        guided = with_system_suffix(messages, schema_instruction(schema))
        for profile in chain:
            fmt = response_format_for(schema, profile.capabilities)
            conversation = list(guided)
            for attempt in range(2):
                try:
                    completion = await self.complete(
                        [profile],
                        conversation,
                        ctx,
                        response_format=fmt,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        on_usage=on_usage,
                    )
                except LLMUnavailableError as exc:
                    reasons.extend(exc.reasons)
                    break
                try:
                    return parse_model(completion.text, schema)
                except ValueError as exc:
                    log.info("llm.invalid_structured_output", profile=profile.name, attempt=attempt + 1, error=str(exc))
                    if attempt == 0:
                        conversation = [
                            *conversation,
                            {"role": "assistant", "content": completion.text[:4000]},
                            {
                                "role": "user",
                                "content": f"That output was invalid ({exc}). Reply with only the corrected JSON.",
                            },
                        ]
                        continue
                    reasons.append(f"{profile.name}: {exc}")
        raise StructuredOutputError("; ".join(reasons) or "no model is configured for this task")
