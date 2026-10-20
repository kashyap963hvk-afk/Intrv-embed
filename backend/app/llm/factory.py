"""Builds a provider client for a resolved profile."""

from __future__ import annotations

import uuid

import httpx2

from app.core.config import Settings
from app.db.models import ProviderKind
from app.llm.bridge_hub import BridgeHub
from app.llm.gateway import ProviderFactory
from app.llm.gguf import GgufProvider, GgufRuntime
from app.llm.providers.base import ChatProvider
from app.llm.providers.bridge import BridgeProvider
from app.llm.providers.fake import FakeProvider
from app.llm.providers.openai_compat import OpenAICompatibleProvider
from app.llm.types import ProviderError, ResolvedProfile


def make_provider_factory(
    settings: Settings,
    public_client: httpx2.AsyncClient,
    private_client: httpx2.AsyncClient,
    *,
    bridge: BridgeHub | None = None,
    gguf: GgufRuntime | None = None,
) -> ProviderFactory:
    def factory(profile: ResolvedProfile) -> ChatProvider:
        match profile.kind:
            case ProviderKind.FAKE:
                if settings.is_prod:
                    raise ProviderError("unsupported", "the scripted test provider is disabled in production")
                return FakeProvider(profile.params.get("script"), default=str(profile.params.get("default", "OK")))
            case ProviderKind.OPENAI_COMPATIBLE:
                if not profile.base_url:
                    raise ProviderError("bad_request", "profile has no base URL")
                client = private_client if settings.allow_private_llm_endpoints else public_client
                return OpenAICompatibleProvider(base_url=profile.base_url, api_key=profile.api_key, http_client=client)
            case ProviderKind.SERVER_LOCAL:
                if not settings.allow_private_llm_endpoints:
                    raise ProviderError(
                        "blocked",
                        "self-hosted endpoints are disabled on this server (set ALLOW_PRIVATE_LLM_ENDPOINTS=true "
                        "in self-host mode, or connect your PC through the Local Model Bridge)",
                    )
                if not profile.base_url:
                    raise ProviderError("bad_request", "profile has no base URL")
                return OpenAICompatibleProvider(
                    base_url=profile.base_url, api_key=profile.api_key, http_client=private_client
                )
            case ProviderKind.BRIDGE:
                device = profile.params.get("device_id")
                if bridge is None or not isinstance(device, str):
                    raise ProviderError("unsupported", "the Local Model Bridge is not available on this server")
                return BridgeProvider(bridge, uuid.UUID(device))
            case ProviderKind.GGUF_MANAGED:
                path = profile.params.get("path")
                if gguf is None or not settings.gguf_allowed_dirs or not isinstance(path, str):
                    raise ProviderError("unsupported", "local GGUF models are disabled on this server")
                return GgufProvider(gguf, path, private_client)
        raise ProviderError("unsupported", f"unknown provider type {profile.kind}")

    return factory
