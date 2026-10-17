"""Deterministic provider for tests, offline demos and CI (never available in production).

`script` rules are tried in order; the first whose `match` regex matches the last user message (or, with
`on: "system"`, the system prompt) wins. Its `reply` is streamed word by word. Rules may also set
`fail` (an ErrorKind) to simulate provider failures. Without a match, the reply is `default`.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from typing import Any

from app.llm.types import ChatChunk, ChatRequest, ProviderError, Usage


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


class FakeProvider:
    def __init__(self, script: list[dict[str, Any]] | None = None, *, default: str = "OK", delay_s: float = 0.0):
        self.script = script or []
        self.default = default
        self.delay_s = delay_s
        self.calls: list[ChatRequest] = []

    def _reply(self, request: ChatRequest) -> str:
        user = next((_text(m["content"]) for m in reversed(request.messages) if m["role"] == "user"), "")
        system = next((_text(m["content"]) for m in request.messages if m["role"] == "system"), "")
        for rule in self.script:
            haystack = system if rule.get("on") == "system" else user
            if re.search(str(rule.get("match", "")), haystack, flags=re.IGNORECASE | re.DOTALL):
                if rule.get("fail"):
                    raise ProviderError(rule["fail"], f"scripted failure: {rule['fail']}")
                return str(rule.get("reply", ""))
        return self.default

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        self.calls.append(request)
        reply = self._reply(request)
        words = re.findall(r"\S+\s*", reply) or [reply]
        for word in words:
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            yield ChatChunk(text=word)
        prompt_chars = sum(len(_text(m["content"])) for m in request.messages)
        yield ChatChunk(usage=Usage(tokens_in=max(1, prompt_chars // 4), tokens_out=len(words)), finish_reason="stop")

    async def list_models(self) -> list[str]:
        return ["fake-model"]
