"""Gateway data types and the provider error taxonomy."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from app.db.models import AgentRole, ProviderKind, QualityTier

Message = dict[str, Any]  # OpenAI chat message: {"role": ..., "content": str | list[content parts]}

ErrorKind = Literal[
    "rate_limited",
    "timeout",
    "connection",
    "server",
    "auth",
    "not_found",
    "bad_request",
    "blocked",
    "unsupported",
    "invalid_output",
]
RETRYABLE: frozenset[str] = frozenset({"rate_limited", "timeout", "connection", "server"})


class ProviderError(Exception):
    def __init__(self, kind: ErrorKind, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.kind: ErrorKind = kind
        self.message = message
        self.status_code = status_code

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE


@dataclass(frozen=True, slots=True)
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass(slots=True)
class ChatRequest:
    model: str
    messages: list[Message]
    temperature: float | None = None
    max_tokens: int | None = None
    response_format: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    include_usage: bool = True


@dataclass(frozen=True, slots=True)
class ChatChunk:
    text: str = ""
    tool_calls: tuple[str, ...] = ()
    usage: Usage | None = None
    finish_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    """A profile ready to call: decrypted key, effective limits and capabilities."""

    id: uuid.UUID
    name: str
    kind: ProviderKind
    model: str
    base_url: str | None = None
    api_key: str | None = None
    preset_key: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    trains_on_data: bool | None = None
    rpm_limit: int | None = None
    rpd_limit: int | None = None
    quality_tier: QualityTier | None = None

    @property
    def provider_label(self) -> str:
        return self.preset_key or self.kind.value.lower()


@dataclass(frozen=True, slots=True)
class CallCtx:
    org_id: uuid.UUID
    user_id: uuid.UUID | None
    role: AgentRole
    session_id: uuid.UUID | None = None
    contains_pii: bool = False


@dataclass(frozen=True, slots=True)
class UsageRecord:
    profile: ResolvedProfile
    ctx: CallCtx
    usage: Usage
    latency_ms: int
    ttft_ms: int | None
    ok: bool
    error_kind: str | None = None


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    profile: ResolvedProfile
    usage: Usage
    tool_calls: tuple[str, ...] = ()
    attempts: tuple[str, ...] = ()


class LLMUnavailableError(Exception):
    """Every profile in the chain failed or was skipped."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("No model could serve this request: " + "; ".join(reasons))
        self.reasons = reasons


class StructuredOutputError(Exception):
    """The model(s) did not produce output matching the required schema."""
