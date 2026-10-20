"""The voice worker's connection to the interview engine: the same `/ws/interview/{id}` socket the text room uses,
authenticated with the candidate's invite token. The engine does all reasoning, policy, persistence and
integrity; the voice worker only turns audio into answers and replies into audio."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import quote

log = logging.getLogger("intrv_voice")

OnFrame = Callable[[dict[str, Any]], Awaitable[None]]


class Link(Protocol):
    async def send(self, message: dict[str, Any]) -> None: ...

    async def close(self) -> None: ...


class LinkRefusedError(Exception):
    pass


class EngineLink:
    def __init__(self, base_ws_url: str, session_id: uuid.UUID, token: str) -> None:
        self.url = f"{base_ws_url.rstrip('/')}/ws/interview/{session_id}?token={quote(token, safe='')}"
        self._socket: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._on_frame: OnFrame | None = None
        self.first_frame: dict[str, Any] | None = None

    async def connect(self) -> dict[str, Any]:
        """Open the socket and return the engine's first frame. Raises LinkRefusedError for a bad link."""
        import websockets

        try:
            self._socket = await websockets.connect(self.url, max_size=2**20, ping_interval=20)
            self.first_frame = json.loads(await asyncio.wait_for(self._socket.recv(), timeout=10))
        except (OSError, TimeoutError, websockets.WebSocketException) as exc:
            raise LinkRefusedError(str(exc)) from exc
        return self.first_frame

    def listen(self, on_frame: OnFrame) -> None:
        self._on_frame = on_frame
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        assert self._socket is not None
        try:
            async for raw in self._socket:
                if self._on_frame is not None:
                    await self._on_frame(json.loads(raw))
        except Exception as exc:  # the engine went away: tell the bridge so the call ends gracefully
            log.info("engine link closed: %s", exc)
        if self._on_frame is not None:
            await self._on_frame({"type": "link.closed"})

    async def send(self, message: dict[str, Any]) -> None:
        if self._socket is not None:
            await self._socket.send(json.dumps(message))

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader
        if self._socket is not None:
            with contextlib.suppress(Exception):
                await self._socket.close()
