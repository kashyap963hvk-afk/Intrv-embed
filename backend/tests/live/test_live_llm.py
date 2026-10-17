"""Live checks against a real provider. Skipped unless these environment variables are set:

  LIVE_OPENAI_COMPAT_BASE_URL  e.g. https://api.groq.com/openai/v1
  LIVE_OPENAI_COMPAT_API_KEY
  LIVE_OPENAI_COMPAT_MODEL     e.g. llama-3.3-70b-versatile

Run: uv run pytest -m live tests/live
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from app.core.ssrf import create_http_client
from app.db.models import AgentRole, ProviderKind
from app.llm.factory import make_provider_factory
from app.llm.probe import Prober
from app.llm.types import CallCtx, ResolvedProfile
from tests.conftest import make_settings

pytestmark = pytest.mark.live

BASE_URL = os.environ.get("LIVE_OPENAI_COMPAT_BASE_URL")
API_KEY = os.environ.get("LIVE_OPENAI_COMPAT_API_KEY")
MODEL = os.environ.get("LIVE_OPENAI_COMPAT_MODEL")


@pytest.mark.skipif(not (BASE_URL and MODEL), reason="LIVE_OPENAI_COMPAT_* not set")
def test_probe_real_provider() -> None:
    async def run() -> None:
        settings = make_settings()
        async with create_http_client(allow_private=False) as public, create_http_client(allow_private=True) as private:
            factory = make_provider_factory(settings, public, private)
            profile = ResolvedProfile(
                id=uuid.uuid4(),
                name="live",
                kind=ProviderKind.OPENAI_COMPATIBLE,
                model=str(MODEL),
                base_url=BASE_URL,
                api_key=API_KEY,
            )
            report = await Prober(factory, CallCtx(org_id=uuid.uuid4(), user_id=None, role=AgentRole.PLANNER)).run(
                profile
            )
            print(report)
            assert report.ok, report.error
            assert report.capabilities.get("stream")

    asyncio.run(run())
