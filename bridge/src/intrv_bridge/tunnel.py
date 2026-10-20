"""Outbound tunnel to the Intrv-embed backend (`wss://…/api/bridge/connect`).

The bridge never listens on the network: it dials out, announces its models, and serves `chat` / `models` /
`cancel` requests by calling the local OpenAI-compatible server for the model (llama-server it started, or a
running Ollama / LM Studio / vLLM). One request runs per model at a time; others wait in order. Reconnects use
exponential backoff with jitter and stop for good when the server says the device was revoked.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx

from intrv_bridge import __version__
from intrv_bridge.catalog import ModelEntry
from intrv_bridge.protocol import FrameError, validate_request

log = logging.getLogger("intrv_bridge")

REVOKED_CODES = {4401, 4403}
REFUSED_STATUS = {401, 403}
BACKOFF_CAP_S = 60.0


class Socket(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...


Connect = Callable[[str, dict[str, str]], Any]  # returns an async context manager yielding a Socket


def default_connect(url: str, headers: dict[str, str]) -> Any:
    import websockets  # local import keeps the test doubles independent of the library

    return websockets.connect(url, additional_headers=headers, max_size=2**20, ping_interval=20)


class Revoked(Exception):
    pass


class Bridge:
    def __init__(
        self,
        server_url: str,
        token: str,
        models: Callable[[], Awaitable[list[ModelEntry]]],
        *,
        http: httpx.AsyncClient | None = None,
        connect: Connect = default_connect,
    ) -> None:
        self.url = server_url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
        self.url += "/api/bridge/connect"
        self.token = token
        self._models_source = models
        self._models: dict[str, ModelEntry] = {}
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=5.0))
        self._connect = connect
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._send_lock = asyncio.Lock()
        self.connected = asyncio.Event()

    async def run_forever(self) -> None:
        attempt = 0
        while True:
            try:
                await self.run_once()
                attempt = 0
            except Revoked:
                log.error("this device was revoked or its token is invalid; pair it again")
                raise
            except (TimeoutError, OSError, ConnectionError) as exc:
                log.warning("tunnel error: %s", exc)
            except Exception as exc:  # websockets' ConnectionClosed and friends
                code = getattr(getattr(exc, "rcvd", None), "code", None)
                status = getattr(getattr(exc, "response", None), "status_code", None)  # refused before upgrade
                if code in REVOKED_CODES or status in REFUSED_STATUS:
                    raise Revoked from exc
                log.warning("tunnel closed: %s", exc)
            self.connected.clear()
            attempt += 1
            delay = min(BACKOFF_CAP_S, 2 ** min(attempt, 6)) * (0.5 + random.random() / 2)  # noqa: S311
            await asyncio.sleep(delay)

    async def run_once(self) -> None:
        self._models = {m.id: m for m in await self._models_source()}
        async with self._connect(self.url, {"Authorization": f"Bearer {self.token}"}) as socket:
            await self._send(
                socket,
                {
                    "type": "hello",
                    "version": __version__,
                    "models": [m.describe() for m in self._models.values()],
                    "caps": {"stream": True, "concurrency_per_model": 1},
                },
            )
            self.connected.set()
            try:
                while True:
                    raw = await socket.recv()
                    await self._dispatch(socket, json.loads(raw))
            finally:
                for task in self._tasks.values():
                    task.cancel()
                await asyncio.gather(*self._tasks.values(), return_exceptions=True)
                self._tasks.clear()

    async def _send(self, socket: Socket, frame: dict[str, Any]) -> None:
        async with self._send_lock:
            await socket.send(json.dumps(frame))

    async def _dispatch(self, socket: Socket, frame: dict[str, Any]) -> None:
        kind = frame.get("type")
        if kind == "ping":
            await self._send(socket, {"type": "pong"})
            return
        if kind == "welcome":
            return
        if kind != "req":
            return
        try:
            request_id, op, payload = validate_request(frame)
        except FrameError as exc:
            await self._send(socket, {"type": "err", "id": frame.get("id"), "kind": exc.kind, "message": exc.message})
            return
        if op == "cancel":
            task = self._tasks.pop(request_id, None)
            if task is not None:
                task.cancel()
            return
        if op == "models":
            await self._send(socket, {"type": "done", "id": request_id, "models": sorted(self._models)})
            return
        task = asyncio.create_task(self._chat(socket, request_id, payload))
        self._tasks[request_id] = task

        def forget(_task: asyncio.Task[None]) -> None:
            self._tasks.pop(request_id, None)

        task.add_done_callback(forget)

    async def _chat(self, socket: Socket, request_id: str, payload: dict[str, Any]) -> None:
        entry = self._models.get(payload["model"])
        if entry is None:
            await self._send(
                socket, {"type": "err", "id": request_id, "kind": "not_found", "message": "model not on this PC"}
            )
            return
        lock = self._locks.setdefault(entry.id, asyncio.Lock())
        if lock.locked():
            await self._send(socket, {"type": "status", "id": request_id, "message": "queued"})
        try:
            async with lock:
                await self._send(socket, {"type": "status", "id": request_id, "message": "preparing the model"})
                base_url, served_name = await entry.endpoint()
                await self._stream(socket, request_id, base_url, {**payload, "model": served_name})
        except asyncio.CancelledError:
            raise
        except httpx.HTTPStatusError as exc:
            await self._send(
                socket,
                {
                    "type": "err",
                    "id": request_id,
                    "kind": "server",
                    "message": f"local model error {exc.response.status_code}",
                },
            )
        except (httpx.HTTPError, RuntimeError, OSError) as exc:
            await self._send(socket, {"type": "err", "id": request_id, "kind": "connection", "message": str(exc)[:300]})

    async def _stream(self, socket: Socket, request_id: str, base_url: str, payload: dict[str, Any]) -> None:
        body = {**payload, "stream": True, "stream_options": {"include_usage": True}}
        usage: dict[str, int] = {}
        async with self._http.stream("POST", f"{base_url}/chat/completions", json=body) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                with contextlib.suppress(json.JSONDecodeError):
                    event = json.loads(data)
                    if isinstance(event.get("usage"), dict):
                        usage = {
                            "prompt_tokens": int(event["usage"].get("prompt_tokens") or 0),
                            "completion_tokens": int(event["usage"].get("completion_tokens") or 0),
                        }
                    for choice in event.get("choices") or []:
                        delta = (choice.get("delta") or {}).get("content")
                        if delta:
                            await self._send(socket, {"type": "chunk", "id": request_id, "delta": delta})
        await self._send(socket, {"type": "done", "id": request_id, "usage": usage})

    async def close(self) -> None:
        await self._http.aclose()
