"""Chat provider for a model on a member's PC or pendrive, reached through the Local Model Bridge."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.llm.bridge_hub import BridgeHub, BridgeOfflineError, BridgeRequestError
from app.llm.types import ChatChunk, ChatRequest, ProviderError, Usage

_KINDS = {"timeout", "connection", "server", "bad_request", "not_found", "unsupported", "rate_limited"}


def _error(exc: BridgeRequestError) -> ProviderError:
    kind = exc.kind if exc.kind in _KINDS else "server"
    return ProviderError(kind, f"bridge: {exc.message}")  # type: ignore[arg-type]


class BridgeProvider:
    def __init__(self, hub: BridgeHub, device_id: uuid.UUID) -> None:
        self._hub = hub
        self._device_id = device_id

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        payload: dict[str, Any] = {"model": request.model, "messages": request.messages}
        for key in ("temperature", "max_tokens", "response_format"):
            value = getattr(request, key)
            if value is not None:
                payload[key] = value
        try:
            async for frame in self._hub.request(self._device_id, "chat", payload):
                kind = frame.get("type")
                if kind == "chunk":
                    yield ChatChunk(text=str(frame.get("delta", "")))
                elif kind == "status":
                    yield ChatChunk()  # e.g. "loading the model from USB": keeps the first-token timer alive
                elif kind == "done":
                    raw_usage = frame.get("usage")
                    usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
                    yield ChatChunk(
                        usage=Usage(int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))),
                        finish_reason="stop",
                    )
        except BridgeOfflineError as exc:
            raise ProviderError("connection", str(exc)) from exc
        except BridgeRequestError as exc:
            raise _error(exc) from exc

    async def list_models(self) -> list[str]:
        try:
            async for frame in self._hub.request(self._device_id, "models", {}, first_timeout_s=15):
                if frame.get("type") == "done":
                    return sorted(str(m) for m in frame.get("models", []) if isinstance(m, str))
        except BridgeOfflineError as exc:
            raise ProviderError("connection", str(exc)) from exc
        except BridgeRequestError as exc:
            raise _error(exc) from exc
        return []
