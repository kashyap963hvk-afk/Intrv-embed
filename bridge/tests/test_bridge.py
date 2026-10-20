"""Bridge app: operation allow-list, tunnel streaming/queueing/cancel, revocation and GGUF scanning."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import gguf
import httpx
import pytest

from intrv_bridge.catalog import ModelEntry, is_gguf, read_gguf_info, scan_folders
from intrv_bridge.protocol import FrameError, validate_request
from intrv_bridge.tunnel import Bridge, Revoked

# --- protocol ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("op", ["exec", "read_file", "shell", "load", "", None])
def test_only_chat_models_cancel_are_allowed(op: Any) -> None:
    with pytest.raises(FrameError) as error:
        validate_request({"id": "r1", "op": op, "payload": {}})
    assert error.value.kind == "unsupported"


def test_chat_payload_is_strict() -> None:
    message = [{"role": "user", "content": "x"}]
    validate_request({"id": "r1", "op": "chat", "payload": {"model": "m", "messages": message}})
    with pytest.raises(FrameError):
        validate_request({"id": "r1", "op": "chat", "payload": {"model": "m", "messages": [], "path": "/etc/passwd"}})
    with pytest.raises(FrameError):
        validate_request({"id": "r1", "op": "chat", "payload": {"messages": [{"role": "user", "content": "x"}]}})


# --- tunnel ------------------------------------------------------------------------------------------------


class FakeSocket:
    def __init__(self) -> None:
        self.inbox: asyncio.Queue[str] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.event = asyncio.Event()

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))
        self.event.set()

    async def recv(self) -> str:
        return await self.inbox.get()

    def push(self, frame: dict[str, Any]) -> None:
        self.inbox.put_nowait(json.dumps(frame))

    async def wait_for(self, predicate: Any, limit_s: float = 5) -> dict[str, Any]:
        async with asyncio.timeout(limit_s):
            while True:
                for frame in self.sent:
                    if predicate(frame):
                        return frame
                self.event.clear()
                await self.event.wait()


def sse(*deltas: str, delay: float = 0.0) -> Any:
    async def body() -> AsyncIterator[bytes]:
        for delta in deltas:
            if delay:
                await asyncio.sleep(delay)
            yield f"data: {json.dumps({'choices': [{'delta': {'content': delta}}]})}\n\n".encode()
        yield b'data: {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2}}\n\n'
        yield b"data: [DONE]\n\n"

    return body()


async def endpoint() -> tuple[str, str]:
    return "http://127.0.0.1:9/v1", "served-name"


MODEL = ModelEntry(id="gguf:tiny:abc", name="Tiny", source="gguf", endpoint=endpoint, size_bytes=10)


def make_bridge(socket: FakeSocket, handler: Any) -> Bridge:
    @contextlib.asynccontextmanager
    async def connect(url: str, headers: dict[str, str]) -> AsyncIterator[FakeSocket]:
        assert url == "wss://api.example.com/api/bridge/connect"
        assert headers == {"Authorization": "Bearer ibr_token"}
        yield socket

    async def models() -> list[ModelEntry]:
        return [MODEL]

    return Bridge(
        "https://api.example.com",
        "ibr_token",
        models,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        connect=connect,
    )


async def test_tunnel_streams_queues_and_cancels() -> None:
    seen: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        slow = body["messages"][0]["content"] == "slow"
        return httpx.Response(200, content=sse("Hel", "lo", delay=0.5 if slow else 0))

    socket = FakeSocket()
    bridge = make_bridge(socket, handler)
    runner = asyncio.create_task(bridge.run_once())
    hello = await socket.wait_for(lambda f: f["type"] == "hello")
    assert hello["models"] == [{"id": "gguf:tiny:abc", "name": "Tiny", "source": "gguf", "size_bytes": 10}]

    chat = {"model": "gguf:tiny:abc", "messages": [{"role": "user", "content": "hi"}], "temperature": 0.2}
    socket.push({"type": "req", "id": "r1", "op": "chat", "payload": chat})
    done = await socket.wait_for(lambda f: f["type"] == "done" and f["id"] == "r1")
    assert done["usage"] == {"prompt_tokens": 7, "completion_tokens": 2}
    assert [f["delta"] for f in socket.sent if f["type"] == "chunk" and f["id"] == "r1"] == ["Hel", "lo"]
    assert seen[0]["model"] == "served-name"  # the local server's own name, not the catalog id
    assert seen[0]["stream"] is True

    # Two requests for one model: the second waits; cancelling the first frees the model.
    slow = {**chat, "messages": [{"role": "user", "content": "slow"}]}
    socket.push({"type": "req", "id": "r2", "op": "chat", "payload": slow})
    await socket.wait_for(lambda f: f.get("id") == "r2" and f["type"] == "status")
    socket.push({"type": "req", "id": "r3", "op": "chat", "payload": chat})
    queued = await socket.wait_for(lambda f: f.get("id") == "r3" and f["type"] == "status")
    assert queued["message"] == "queued"
    socket.push({"type": "req", "id": "r2", "op": "cancel"})
    await socket.wait_for(lambda f: f["type"] == "done" and f["id"] == "r3")
    assert not any(f["type"] == "done" and f["id"] == "r2" for f in socket.sent)

    socket.push({"type": "req", "id": "r4", "op": "exec", "payload": {"cmd": "rm -rf /"}})
    refused = await socket.wait_for(lambda f: f.get("id") == "r4")
    assert (refused["type"], refused["kind"]) == ("err", "unsupported")
    socket.push({"type": "req", "id": "r5", "op": "models"})
    listed = await socket.wait_for(lambda f: f.get("id") == "r5")
    assert listed["models"] == ["gguf:tiny:abc"]
    socket.push({"type": "ping"})
    await socket.wait_for(lambda f: f["type"] == "pong")
    runner.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await runner
    await bridge.close()


async def test_unknown_model_and_local_errors() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    socket = FakeSocket()
    bridge = make_bridge(socket, handler)
    runner = asyncio.create_task(bridge.run_once())
    await socket.wait_for(lambda f: f["type"] == "hello")
    message = [{"role": "user", "content": "hi"}]
    socket.push({"type": "req", "id": "a", "op": "chat", "payload": {"model": "nope", "messages": message}})
    assert (await socket.wait_for(lambda f: f.get("id") == "a" and f["type"] == "err"))["kind"] == "not_found"
    socket.push({"type": "req", "id": "b", "op": "chat", "payload": {"model": MODEL.id, "messages": message}})
    assert (await socket.wait_for(lambda f: f.get("id") == "b" and f["type"] == "err"))["kind"] == "server"
    runner.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await runner
    await bridge.close()


async def test_revoked_device_stops_reconnecting() -> None:
    class Closed(Exception):
        rcvd = type("Close", (), {"code": 4401})()

    @contextlib.asynccontextmanager
    async def connect(_url: str, _headers: dict[str, str]) -> AsyncIterator[Any]:
        raise Closed
        yield  # pragma: no cover

    async def models() -> list[ModelEntry]:
        return []

    bridge = Bridge("https://api.example.com", "t", models, connect=connect)
    with pytest.raises(Revoked):
        await asyncio.wait_for(bridge.run_forever(), timeout=5)
    await bridge.close()


# --- catalog -----------------------------------------------------------------------------------------------


def write_gguf(path: Path) -> None:
    writer = gguf.GGUFWriter(str(path), "llama")
    writer.add_name("Tiny Llama")
    writer.add_context_length(4096)
    writer.add_file_type(15)
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()


def test_scan_reads_gguf_metadata_and_skips_other_files(tmp_path: Path) -> None:
    folder = tmp_path / "models"
    (folder / "nested").mkdir(parents=True)
    write_gguf(folder / "nested" / "tiny.gguf")
    (folder / "fake.gguf").write_bytes(b"NOTGGUF")
    (folder / "notes.txt").write_text("hello")
    assert is_gguf(folder / "nested" / "tiny.gguf")
    info = read_gguf_info(folder / "nested" / "tiny.gguf")
    assert (info.arch, info.context_length, info.quant, info.name) == ("llama", 4096, "Q4_K_M", "Tiny Llama")

    calls: list[tuple[Path, bool]] = []

    def endpoint_for(path: Path, removable: bool) -> Any:
        calls.append((path, removable))
        return endpoint

    entries = scan_folders([folder, tmp_path / "missing"], endpoint_for, lambda _p: True)
    assert [e.name for e in entries] == ["Tiny Llama"]
    assert entries[0].removable is True
    assert entries[0].id.startswith("gguf:tiny:")
    assert calls == [((folder / "nested" / "tiny.gguf").resolve(), True)]


def test_release_asset_choice_and_safe_unpack(tmp_path: Path) -> None:
    import io
    import tarfile

    from intrv_bridge.runtime import _unpack, pick_asset

    assets: list[dict[str, object]] = [
        {"name": "llama-b1-bin-ubuntu-x64.tar.gz"},
        {"name": "llama-b1-bin-ubuntu-vulkan-x64.zip"},
    ]
    assert pick_asset(assets, "bin-ubuntu-x64") == assets[0]
    assert pick_asset(assets, "bin-win-cpu-x64") is None

    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as bundle:
        info = tarfile.TarInfo("../../escape")
        info.size = 2
        bundle.addfile(info, io.BytesIO(b"hi"))
    with pytest.raises(tarfile.TarError):
        _unpack(evil, tmp_path / "out")
    good = tmp_path / "good.tar.gz"
    with tarfile.open(good, "w:gz") as bundle:
        info = tarfile.TarInfo("build/bin/llama-server")
        info.size = 2
        bundle.addfile(info, io.BytesIO(b"#!"))
    binary = _unpack(good, tmp_path / "ok")
    assert binary.name == "llama-server"
    assert not (tmp_path.parent / "escape").exists()
