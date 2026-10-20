"""Local Model Bridge end to end over real sockets: pair → the bridge app dials in → chat streams from a local
OpenAI-compatible server through the tunnel (from another process's hub, via Valkey) → cancel propagates →
revoke stops the bridge. Plus GGUF_MANAGED path confinement."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from redis.asyncio import Redis

from app.core.config import Settings
from app.llm.bridge_hub import BridgeHub
from app.llm.gguf import GgufPathError, resolve_model_path
from app.llm.providers.bridge import BridgeProvider
from app.llm.types import ChatRequest, ProviderError
from app.main import create_app
from tests.fakes.openai_server import GOOD_KEY, running_server
from tests.integration.conftest import Infra
from tests.integration.helpers import PASSWORD, knowledge_settings, seed_org, seed_user
from tests.integration.test_knowledge_events import _free_port

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "bridge" / "src"))

from intrv_bridge.catalog import ModelEntry
from intrv_bridge.tunnel import Bridge, Revoked

pytestmark = [pytest.mark.integration, pytest.mark.isolation]


@pytest.fixture
def server(infra: Infra, fresh_valkey: str) -> Iterator[tuple[str, Settings]]:
    settings = knowledge_settings(infra.database_url, fresh_valkey, infra.qdrant_url, infra.s3_endpoint_url)
    port = _free_port()
    instance = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not instance.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}", settings
    instance.should_exit = True
    thread.join(timeout=10)


def login(base: str, email: str) -> httpx.Client:
    client = httpx.Client(base_url=base, timeout=30)
    client.headers["X-CSRF-Token"] = client.get("/api/auth/csrf").json()["csrf_token"]
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    return client


class BridgeThread:
    """The bridge app on 'the member's PC': its own thread and event loop."""

    def __init__(self, base: str, token: str, local_base: str) -> None:
        async def models() -> list[ModelEntry]:
            async def good() -> tuple[str, str]:
                return local_base, "good"

            async def slow() -> tuple[str, str]:
                return local_base, "slow"

            return [
                ModelEntry(id="local:good", name="Good", source="ollama", endpoint=good),
                ModelEntry(id="local:slow", name="Slow", source="ollama", endpoint=slow),
            ]

        self.loop = asyncio.new_event_loop()
        self.bridge: Bridge | None = None
        self.outcome: BaseException | None = None

        def run() -> None:
            asyncio.set_event_loop(self.loop)
            http = httpx.AsyncClient(headers={"Authorization": f"Bearer {GOOD_KEY}"}, timeout=30)
            self.bridge = Bridge(base, token, models, http=http)
            try:
                self.loop.run_until_complete(self.bridge.run_forever())
            except BaseException as exc:  # Revoked ends the thread
                self.outcome = exc

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()


def wait_until(predicate: Any, seconds: float = 15) -> Any:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("condition not reached")


async def chat(settings: Settings, device_id: uuid.UUID, model: str, *, stop_early: bool = False) -> str:
    """Runs in the test's own loop with its own hub: proves the relay works across processes via Valkey."""
    valkey = Redis.from_url(settings.valkey_url)
    try:
        provider = BridgeProvider(BridgeHub(valkey), device_id)
        request = ChatRequest(model=model, messages=[{"role": "user", "content": "Say hello"}], temperature=0.1)
        text = ""
        stream = provider.stream(request)
        async for chunk in stream:
            text += chunk.text
            if stop_early:
                break
        await stream.aclose()  # type: ignore[attr-defined]
        return text
    finally:
        await valkey.aclose()


def test_bridge_pair_stream_cancel_revoke(server: tuple[str, Settings]) -> None:
    base, settings = server
    org = seed_org(settings)
    _, member_email = seed_user(settings, org)
    _, other_email = seed_user(settings, org)
    member, other = login(base, member_email), login(base, other_email)
    try:
        code = member.post("/api/bridge/pair").json()["code"]
        anonymous = httpx.Client(base_url=base, timeout=30)
        claimed = anonymous.post("/api/bridge/claim", json={"code": code, "name": "Lab PC", "platform": "Linux"})
        assert claimed.status_code == 201, claimed.text
        token, device_id = claimed.json()["token"], uuid.UUID(claimed.json()["device_id"])
        assert anonymous.post("/api/bridge/claim", json={"code": code, "name": "again"}).status_code == 404

        with running_server() as (local_base, _app):
            pc = BridgeThread(base, token, local_base)
            device = wait_until(
                lambda: next((d for d in member.get("/api/bridge/devices").json() if d["online"]), None)
            )
            assert [m["id"] for m in device["models"]] == ["local:good", "local:slow"]
            assert other.get("/api/bridge/devices").json() == []  # private to the member

            # A bridge profile: personal, never classified as training on data, only for the member's device.
            body = {"name": "My PC", "kind": "BRIDGE", "model": "local:good", "device_id": str(device_id)}
            created = member.post("/api/llm/profiles", json=body)
            assert created.status_code == 201, created.text
            assert created.json()["trains_on_data"] is False
            assert other.post("/api/llm/profiles", json=body).status_code == 422
            assert member.post("/api/llm/profiles", json={**body, "model": "local:missing"}).status_code == 422
            assert member.post("/api/llm/profiles", json={**body, "scope": "org"}).status_code in {403, 422}

            text = asyncio.run(chat(settings, device_id, "local:good"))
            assert text.strip()

            # Close the stream before the slow model's first token: the bridge cancels the local request.
            asyncio.run(chat(settings, device_id, "local:slow", stop_early=True))
            assert pc.bridge is not None
            wait_until(lambda: not pc.bridge._tasks)

            # The API process itself (probe) also reaches the device through the gateway.
            probed = member.post(f"/api/llm/profiles/{created.json()['id']}/probe")
            assert probed.status_code == 200, probed.text

            assert member.delete(f"/api/bridge/devices/{device_id}").status_code == 204
            pc.thread.join(timeout=15)
            assert isinstance(pc.outcome, Revoked)
            assert member.get("/api/bridge/devices").json() == []

        offline = asyncio.run(_offline_call(settings, device_id))
        assert "offline" in offline
    finally:
        member.close()
        other.close()


async def _offline_call(settings: Settings, device_id: uuid.UUID) -> str:
    try:
        await chat(settings, device_id, "local:good")
    except ProviderError as exc:
        return exc.message
    return ""


def test_gguf_paths_are_confined(tmp_path: Path) -> None:
    allowed = tmp_path / "models"
    allowed.mkdir()
    good = allowed / "tiny.gguf"
    good.write_bytes(b"GGUF" + b"\0" * 32)
    (allowed / "fake.gguf").write_bytes(b"NOPE")
    outside = tmp_path / "secret.gguf"
    outside.write_bytes(b"GGUF" + b"\0" * 32)
    (allowed / "link.gguf").symlink_to(outside)
    assert resolve_model_path(str(good), [str(allowed)]) == good.resolve()
    for bad in (
        str(allowed / ".." / "secret.gguf"),  # traversal
        str(allowed / "link.gguf"),  # symlink escape
        "models/tiny.gguf",  # relative
        str(allowed / "fake.gguf"),  # not a GGUF
        str(allowed / "missing.gguf"),
        "/etc/passwd",
    ):
        with pytest.raises(GgufPathError):
            resolve_model_path(bad, [str(allowed)])
    with pytest.raises(GgufPathError):
        resolve_model_path(str(good), [])
