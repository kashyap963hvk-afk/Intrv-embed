from __future__ import annotations

from typing import Any

from app.core.logging import REDACTED, redact_sensitive


def _redact(event: dict[str, Any]) -> dict[str, Any]:
    return dict(redact_sensitive(None, "info", event))


def test_redacts_secret_keys_recursively() -> None:
    event = _redact(
        {
            "event": "llm.profile.saved",
            "api_key": "sk-123",
            "Authorization": "Bearer abc",
            "profile": {"name": "groq", "encrypted_api_key": "zzz", "headers": [{"cookie": "sid=1"}]},
            "model": "llama-3.3-70b",
        }
    )
    assert event["api_key"] == REDACTED
    assert event["Authorization"] == REDACTED
    assert event["profile"]["encrypted_api_key"] == REDACTED
    assert event["profile"]["headers"][0]["cookie"] == REDACTED
    assert event["profile"]["name"] == "groq"
    assert event["model"] == "llama-3.3-70b"
    assert event["event"] == "llm.profile.saved"


def test_non_sensitive_values_untouched() -> None:
    event = _redact({"event": "x", "tokens_in": 10, "max_tokens": 5, "token_count": 3, "items": ("a", "b")})
    assert event == {"event": "x", "tokens_in": 10, "max_tokens": 5, "token_count": 3, "items": ("a", "b")}


def test_token_values_redacted() -> None:
    event = _redact({"event": "x", "access_token": "a", "refresh_token": "b", "csrf_token": "c", "token": "d"})
    assert {event[k] for k in ("access_token", "refresh_token", "csrf_token", "token")} == {REDACTED}
