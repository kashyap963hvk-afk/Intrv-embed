"""Gateway behaviour with in-process providers (no network)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel

from app.db.models import AgentRole, ProviderKind
from app.llm.gateway import CallTrace, GatewayConfig, LLMGateway
from app.llm.providers.fake import FakeProvider
from app.llm.types import (
    CallCtx,
    ChatChunk,
    ChatRequest,
    LLMUnavailableError,
    ProviderError,
    ResolvedProfile,
    StructuredOutputError,
    Usage,
    UsageRecord,
)

CTX = CallCtx(org_id=uuid.uuid4(), user_id=uuid.uuid4(), role=AgentRole.INTERVIEWER)
FAST = GatewayConfig(max_retries=2, ttft_timeout_s=0.3, idle_timeout_s=1, backoff_base_s=0.0, backoff_cap_s=0.0)


def profile(name: str, **kwargs: Any) -> ResolvedProfile:
    return ResolvedProfile(id=uuid.uuid4(), name=name, kind=ProviderKind.FAKE, model=name, **kwargs)


class Scripted:
    """Provider whose behaviour per call is taken from a list: 'ok', 'fail:<kind>', 'slow', 'emit-then-fail'."""

    def __init__(self, plan: list[str], text: str = "hello world") -> None:
        self.plan = plan
        self.text = text
        self.calls = 0
        self.closed = 0

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        step = self.plan[min(self.calls, len(self.plan) - 1)]
        self.calls += 1
        try:
            if step.startswith("fail:"):
                raise ProviderError(step.split(":", 1)[1], "scripted")  # type: ignore[arg-type]
            if step == "slow":
                await asyncio.sleep(5)
            for word in self.text.split():
                yield ChatChunk(text=word + " ")
                if step == "emit-then-fail":
                    raise ProviderError("connection", "dropped")
                await asyncio.sleep(0)
            yield ChatChunk(usage=Usage(10, 2), finish_reason="stop")
        finally:
            self.closed += 1

    async def list_models(self) -> list[str]:
        return []


def gateway_for(providers: dict[str, Any], config: GatewayConfig = FAST, limiter: Any = None) -> LLMGateway:
    return LLMGateway(lambda p: providers[p.name], limiter, config)


async def collect(gateway: LLMGateway, chain: list[ResolvedProfile], **kwargs: Any) -> str:
    return "".join([d async for d in gateway.stream(chain, [{"role": "user", "content": "hi"}], CTX, **kwargs)])


def test_retries_retryable_errors_then_succeeds() -> None:
    primary = Scripted(["fail:rate_limited", "fail:server", "ok"])
    usage: list[UsageRecord] = []

    async def sink(record: UsageRecord) -> None:
        usage.append(record)

    text = asyncio.run(collect(gateway_for({"a": primary}), [profile("a")], on_usage=sink))
    assert text.strip() == "hello world"
    assert primary.calls == 3
    assert [u.ok for u in usage] == [False, False, True]
    assert usage[-1].usage == Usage(10, 2)


def test_falls_back_to_next_profile_without_retrying_auth_errors() -> None:
    broken, backup = Scripted(["fail:auth"]), Scripted(["ok"], text="from backup")
    trace = CallTrace()
    text = asyncio.run(collect(gateway_for({"a": broken, "b": backup}), [profile("a"), profile("b")], trace=trace))
    assert text.strip() == "from backup"
    assert broken.calls == 1  # auth errors are not retried
    assert trace.profile is not None
    assert trace.profile.name == "b"
    assert trace.attempts == ["a#1", "b#1"]


def test_ttft_timeout_triggers_fallback_and_closes_upstream() -> None:
    slow, fast = Scripted(["slow"]), Scripted(["ok"], text="fast answer")
    config = GatewayConfig(max_retries=0, ttft_timeout_s=0.2, backoff_base_s=0, backoff_cap_s=0)
    text = asyncio.run(collect(gateway_for({"slow": slow, "fast": fast}, config), [profile("slow"), profile("fast")]))
    assert text.strip() == "fast answer"
    assert slow.closed == 1


def test_no_retry_after_text_was_emitted() -> None:
    flaky, backup = Scripted(["emit-then-fail"]), Scripted(["ok"])
    with pytest.raises(ProviderError):
        asyncio.run(collect(gateway_for({"a": flaky, "b": backup}), [profile("a"), profile("b")]))
    assert flaky.calls == 1
    assert backup.calls == 0


def test_all_profiles_failing_reports_every_reason() -> None:
    with pytest.raises(LLMUnavailableError) as info:
        asyncio.run(
            collect(
                gateway_for({"a": Scripted(["fail:auth"]), "b": Scripted(["fail:not_found"])}),
                [profile("a"), profile("b")],
            )
        )
    assert len(info.value.reasons) == 2
    with pytest.raises(LLMUnavailableError, match="no model is configured"):
        asyncio.run(collect(gateway_for({}), []))


def test_consumer_cancellation_closes_the_upstream_stream() -> None:
    provider = Scripted(["ok"], text=" ".join(["word"] * 50))
    gateway = gateway_for({"a": provider})

    async def run() -> None:
        stream = gateway.stream([profile("a")], [{"role": "user", "content": "hi"}], CTX)
        await anext(stream)  # first token, then the candidate interrupts
        await stream.aclose()

    asyncio.run(run())
    assert provider.closed == 1


def test_limiter_skips_over_budget_and_open_breaker_profiles() -> None:
    class Limiter:
        def __init__(self) -> None:
            self.failures: list[uuid.UUID] = []

        async def is_open(self, profile_id: uuid.UUID) -> bool:
            return profile_id == open_profile.id

        async def try_acquire(self, profile_id: uuid.UUID, *, rpm: int | None, rpd: int | None) -> bool:
            return profile_id != exhausted.id

        async def record_failure(self, profile_id: uuid.UUID) -> None:
            self.failures.append(profile_id)

        async def record_success(self, profile_id: uuid.UUID) -> None:
            return None

    open_profile, exhausted, healthy = profile("open"), profile("exhausted"), profile("healthy")
    providers = {"open": Scripted(["ok"]), "exhausted": Scripted(["ok"]), "healthy": Scripted(["ok"], text="served")}
    trace = CallTrace()
    text = asyncio.run(
        collect(gateway_for(providers, limiter=Limiter()), [open_profile, exhausted, healthy], trace=trace)
    )
    assert text.strip() == "served"
    assert providers["open"].calls == providers["exhausted"].calls == 0


class Verdict(BaseModel):
    score: int
    reason: str


def test_complete_json_corrects_invalid_output_once() -> None:
    fake = FakeProvider(
        [
            {"match": "invalid", "reply": '{"score": 7, "reason": "fixed"}'},
            {"match": ".*", "reply": "Score: seven"},
        ]
    )
    gateway = LLMGateway(lambda _p: fake, None, FAST)
    result = asyncio.run(gateway.complete_json([profile("a")], [{"role": "user", "content": "Grade it"}], Verdict, CTX))
    assert result == Verdict(score=7, reason="fixed")
    assert len(fake.calls) == 2
    assert "JSON Schema" in fake.calls[0].messages[0]["content"]


def test_complete_json_moves_to_next_profile_and_finally_fails() -> None:
    bad = FakeProvider(default="not json at all")
    good = FakeProvider(default='{"score": 3, "reason": "ok"}')
    gateway = LLMGateway(lambda p: bad if p.name == "bad" else good, None, FAST)
    chain = [profile("bad"), profile("good")]
    assert asyncio.run(gateway.complete_json(chain, [{"role": "user", "content": "x"}], Verdict, CTX)).score == 3
    with pytest.raises(StructuredOutputError):
        asyncio.run(gateway.complete_json([profile("bad")], [{"role": "user", "content": "x"}], Verdict, CTX))


def test_json_schema_mode_requested_when_capable() -> None:
    fake = FakeProvider(default='{"score": 1, "reason": "r"}')
    gateway = LLMGateway(lambda _p: fake, None, FAST)
    capable = profile("a", capabilities={"json_schema": True})
    asyncio.run(gateway.complete_json([capable], [{"role": "user", "content": "x"}], Verdict, CTX))
    assert fake.calls[0].response_format is not None
    assert fake.calls[0].response_format["type"] == "json_schema"
