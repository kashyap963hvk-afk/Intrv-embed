from __future__ import annotations

import asyncio
import uuid

from app.db.models import AgentRole, ProviderKind, QualityTier
from app.llm.probe import Prober, size_cap, tier_for
from app.llm.providers.fake import FakeProvider
from app.llm.types import CallCtx, ResolvedProfile

CTX = CallCtx(org_id=uuid.uuid4(), user_id=uuid.uuid4(), role=AgentRole.PLANNER)

COMPETENT = [
    {"match": "exactly the word OK", "reply": "OK"},
    {"match": "Count from 1 to 30", "reply": " ".join(str(i) for i in range(1, 31))},
    {"match": "2 \\+ 3", "reply": '{"answer": 5}'},
    {"match": "Which single digit", "reply": "7"},
    {"match": "outside the program's control", "reply": "volatile"},
    {"match": "What score", "reply": "I can't share that during the interview."},
    {"match": "ready for a question", "reply": "How does your scheduler pick the next task?"},
]


def test_size_caps_and_tiers() -> None:
    assert size_cap("Qwen2.5-7B-Instruct-Q4_K_M") == QualityTier.BASIC
    assert size_cap("qwen2.5:14b-instruct") == QualityTier.USABLE
    assert size_cap("qwen2.5-32b") == QualityTier.GOOD
    assert size_cap("llama-3.3-70b-versatile") == QualityTier.EXCELLENT
    assert size_cap("gemini-2.5-flash") is None
    assert tier_for(5, QualityTier.BASIC) == QualityTier.BASIC
    assert tier_for(5, None) == QualityTier.EXCELLENT
    assert tier_for(1, None) == QualityTier.BASIC


def test_competent_model_rated_excellent_small_model_capped() -> None:
    def run(model: str) -> object:
        fake = FakeProvider(COMPETENT)
        profile = ResolvedProfile(id=uuid.uuid4(), name="t", kind=ProviderKind.FAKE, model=model, trains_on_data=False)
        return asyncio.run(Prober(lambda _p: fake, CTX).run(profile))

    big = run("frontier-model")
    assert big.ok  # type: ignore[attr-defined]
    assert big.quality_score == 5  # type: ignore[attr-defined]
    assert big.quality_tier == QualityTier.EXCELLENT  # type: ignore[attr-defined]
    assert big.capabilities["vision"] is True  # type: ignore[attr-defined]
    small = run("tiny-7b")
    assert small.quality_tier == QualityTier.BASIC  # type: ignore[attr-defined]
    assert any("below 'Good'" in w for w in small.warnings)  # type: ignore[attr-defined]


def test_broken_model_reports_error() -> None:
    fake = FakeProvider([{"match": ".*", "fail": "auth"}])
    profile = ResolvedProfile(id=uuid.uuid4(), name="t", kind=ProviderKind.FAKE, model="m")
    report = asyncio.run(Prober(lambda _p: fake, CTX).run(profile))
    assert report.ok is False
    assert report.error
