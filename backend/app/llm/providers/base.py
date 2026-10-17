"""Provider interface: anything that can stream chat completions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from app.llm.types import ChatChunk, ChatRequest


class ChatProvider(Protocol):
    def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]: ...

    async def list_models(self) -> list[str]: ...
