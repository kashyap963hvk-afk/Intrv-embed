"""Tunnel frames (ARCHITECTURE §10). The server may only ask for `chat`, `models` and `cancel`.

Server → bridge:  {"type": "req", "id", "op": "chat"|"models"|"cancel", "payload"}  |  {"type": "ping"}
Bridge → server:  {"type": "hello", "models", "caps", "version"}
                  {"type": "status", "id", "message"}      # e.g. loading the model, queued
                  {"type": "chunk", "id", "delta"}
                  {"type": "done", "id", "usage"?, "models"?}
                  {"type": "err", "id", "kind", "message"}
                  {"type": "pong"}
"""

from __future__ import annotations

from typing import Any

ALLOWED_OPS = frozenset({"chat", "models", "cancel"})
CHAT_KEYS = frozenset({"model", "messages", "temperature", "max_tokens", "response_format"})
MAX_MESSAGES = 200


class FrameError(Exception):
    """A request the bridge refuses (unknown operation, malformed payload)."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def validate_request(frame: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Returns (id, op, payload) or raises FrameError. No file-system or shell operation exists at all."""
    request_id = frame.get("id")
    op = frame.get("op")
    if not isinstance(request_id, str) or not request_id:
        raise FrameError("bad_request", "request without an id")
    if op not in ALLOWED_OPS:
        raise FrameError("unsupported", f"operation {op!r} is not allowed")
    payload = frame.get("payload") or {}
    if not isinstance(payload, dict):
        raise FrameError("bad_request", "payload must be an object")
    if op == "chat":
        unknown = set(payload) - CHAT_KEYS
        if unknown:
            raise FrameError("bad_request", f"unexpected chat fields: {', '.join(sorted(unknown))}")
        messages = payload.get("messages")
        if not isinstance(payload.get("model"), str) or not isinstance(messages, list) or not messages:
            raise FrameError("bad_request", "chat needs a model and messages")
        if len(messages) > MAX_MESSAGES:
            raise FrameError("bad_request", "too many messages")
    return request_id, str(op), payload
