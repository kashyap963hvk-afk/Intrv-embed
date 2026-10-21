"""Local Model Bridge hub (ARCHITECTURE §10).

A member's PC keeps one outbound WebSocket to `/api/bridge/connect`. Whichever API replica holds that socket
relays requests to it; any process (other replicas, Celery workers) reaches the device through Valkey:

    caller ──publish req──▶ bridge:dev:{device_id} ──▶ socket holder ──▶ device
    caller ◀──subscribe──── bridge:res:{request_id} ◀── socket holder ◀── device (chunk / status / done / err)

Presence is a short-lived key refreshed by the holder, so "Your PC bridge is offline" is known before a request
is sent. Only `chat`, `models` and `cancel` operations exist; the bridge refuses anything else on its side too.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from redis.asyncio import Redis

from app.core.logging import get_logger

log = get_logger("app.llm.bridge")

PRESENCE_TTL_S = 30
HEARTBEAT_S = 10
FIRST_FRAME_TIMEOUT_S = 300.0  # a 7B model loading from a USB 2.0 stick takes minutes; the device sends status
IDLE_TIMEOUT_S = 120.0
FORWARD_POLL_S = 30.0  # longer than the Valkey socket timeout on purpose
MAX_FRAME_BYTES = 256_000
OPS = frozenset({"chat", "models", "cancel"})
DEVICE_FRAMES = frozenset({"hello", "chunk", "status", "done", "err", "pong"})


class Tunnel(Protocol):
    async def send_json(self, data: Any) -> None: ...

    async def receive_text(self) -> str: ...


class BridgeOfflineError(Exception):
    pass


class BridgeRequestError(Exception):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def _device_channel(device_id: uuid.UUID) -> str:
    return f"bridge:dev:{device_id}"


def _response_channel(request_id: str) -> str:
    return f"bridge:res:{request_id}"


def _presence_key(device_id: uuid.UUID) -> str:
    return f"bridge:online:{device_id}"


@dataclass(frozen=True, slots=True)
class Hello:
    models: list[dict[str, Any]]
    capabilities: dict[str, Any]
    version: str


def parse_hello(frame: dict[str, Any]) -> Hello:
    models = [
        {k: m[k] for k in ("id", "name", "size_bytes", "quant", "context_length", "source", "arch") if k in m}
        for m in frame.get("models", [])
        if isinstance(m, dict) and isinstance(m.get("id"), str)
    ][:100]
    raw_caps = frame.get("caps")
    caps: dict[str, Any] = raw_caps if isinstance(raw_caps, dict) else {}
    return Hello(models=models, capabilities=dict(caps), version=str(frame.get("version", ""))[:40])


async def _next_message(pubsub: Any, wait_s: float) -> dict[str, Any] | None:
    """get_message returns early for skipped subscribe confirmations; wait the whole period instead."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_s
    while (remaining := deadline - loop.time()) > 0:
        message: dict[str, Any] | None = await pubsub.get_message(ignore_subscribe_messages=True, timeout=remaining)
        if message is not None:
            return message
    return None


class BridgeHub:
    """One per process. `serve` runs for the lifetime of a device's socket; `request` works from anywhere."""

    def __init__(self, valkey: Redis) -> None:
        self.valkey = valkey

    async def online(self, device_id: uuid.UUID) -> bool:
        return bool(await self.valkey.exists(_presence_key(device_id)))

    async def serve(
        self,
        device_id: uuid.UUID,
        tunnel: Tunnel,
        on_hello: Callable[[Hello], Awaitable[None]],
    ) -> None:
        """Relay between Valkey and the device until the socket closes (the caller handles the disconnect)."""
        first = json.loads(await asyncio.wait_for(tunnel.receive_text(), timeout=30))
        if not isinstance(first, dict) or first.get("type") != "hello":
            raise BridgeRequestError("bad_request", "the bridge must start with a hello frame")
        await on_hello(parse_hello(first))
        await self.valkey.set(_presence_key(device_id), "1", ex=PRESENCE_TTL_S)
        pubsub = self.valkey.pubsub()
        await pubsub.subscribe(_device_channel(device_id))
        tasks = {
            asyncio.create_task(self._forward_requests(pubsub, tunnel)),
            asyncio.create_task(self._heartbeat(device_id, tunnel)),
            asyncio.create_task(self._relay_responses(tunnel)),
        }
        try:
            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()  # re-raise the reason the tunnel ended (disconnect, revoke)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe()
                await pubsub.aclose()  # type: ignore[no-untyped-call]
            await self.valkey.delete(_presence_key(device_id))

    async def disconnect(self, device_id: uuid.UUID) -> None:
        """Close the device's tunnel wherever it is held (after a revoke)."""
        await self.valkey.publish(_device_channel(device_id), json.dumps({"type": "close"}))

    async def _forward_requests(self, pubsub: Any, tunnel: Tunnel) -> None:
        # Not `pubsub.listen()`: that blocks with the client's 5 s socket timeout, so an idle device's tunnel
        # died every few seconds. get_message(timeout=...) waits as long as asked.
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=FORWARD_POLL_S)
            if message is None or message.get("type") != "message":
                continue
            frame = json.loads(message["data"])
            if frame.get("type") == "close":
                raise BridgeRequestError("revoked", "this device was revoked")
            if frame.get("op") in OPS:
                await tunnel.send_json(frame)

    async def _heartbeat(self, device_id: uuid.UUID, tunnel: Tunnel) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            await self.valkey.set(_presence_key(device_id), "1", ex=PRESENCE_TTL_S)
            await tunnel.send_json({"type": "ping"})

    async def _relay_responses(self, tunnel: Tunnel) -> None:
        while True:
            raw = await tunnel.receive_text()
            if len(raw) > MAX_FRAME_BYTES:
                continue
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(frame, dict) or frame.get("type") not in DEVICE_FRAMES:
                continue
            request_id = frame.get("id")
            if frame["type"] in {"chunk", "status", "done", "err"} and isinstance(request_id, str):
                await self.valkey.publish(_response_channel(request_id), raw)

    async def request(
        self,
        device_id: uuid.UUID,
        op: str,
        payload: dict[str, Any],
        *,
        first_timeout_s: float = FIRST_FRAME_TIMEOUT_S,
        idle_timeout_s: float = IDLE_TIMEOUT_S,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield the device's frames for one request until `done`. Closing the iterator cancels the request."""
        if op not in OPS - {"cancel"}:
            raise BridgeRequestError("bad_request", f"unsupported bridge operation {op!r}")
        if not await self.online(device_id):
            raise BridgeOfflineError("Your PC bridge is offline.")
        request_id = uuid.uuid4().hex
        pubsub = self.valkey.pubsub()
        await pubsub.subscribe(_response_channel(request_id))
        finished = False
        try:
            delivered = await self.valkey.publish(
                _device_channel(device_id), json.dumps({"type": "req", "id": request_id, "op": op, "payload": payload})
            )
            if not delivered:
                raise BridgeOfflineError("Your PC bridge is offline.")
            timeout = first_timeout_s
            while True:
                message = await _next_message(pubsub, timeout)
                if message is None:
                    raise BridgeRequestError("timeout", "the bridge did not answer in time")
                timeout = idle_timeout_s
                frame = json.loads(message["data"])
                if frame.get("type") == "err":
                    finished = True
                    raise BridgeRequestError(str(frame.get("kind", "server")), str(frame.get("message", "error")))
                yield frame
                if frame.get("type") == "done":
                    finished = True
                    return
        finally:
            if not finished:
                with contextlib.suppress(Exception):
                    await self.valkey.publish(
                        _device_channel(device_id), json.dumps({"type": "req", "id": request_id, "op": "cancel"})
                    )
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe()
                await pubsub.aclose()  # type: ignore[no-untyped-call]
