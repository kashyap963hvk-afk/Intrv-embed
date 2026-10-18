"""Live status events over a real HTTP server: readers of a space see its events, nobody else does."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn

from app.core.config import Settings
from app.main import create_app
from tests.fixtures.corpus import BUS_PROTOCOLS_MD
from tests.integration.conftest import Infra
from tests.integration.helpers import PASSWORD, knowledge_settings, seed_org, seed_user

pytestmark = [pytest.mark.integration, pytest.mark.isolation]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def server(infra: Infra, fresh_valkey: str) -> Iterator[tuple[str, Settings]]:
    settings = knowledge_settings(infra.database_url, fresh_valkey, infra.qdrant_url, infra.s3_endpoint_url)
    port = _free_port()
    config = uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning")
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while not instance.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.1)
    yield base, settings
    instance.should_exit = True
    thread.join(timeout=10)


def _login(base: str, email: str) -> httpx.Client:
    client = httpx.Client(base_url=base, timeout=30)
    token = client.get("/api/auth/csrf").json()["csrf_token"]
    client.headers["X-CSRF-Token"] = token
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    return client


def _listen(client: httpx.Client, sink: list[str], stop: threading.Event) -> None:
    try:
        with client.stream("GET", "/api/events", timeout=httpx.Timeout(60, read=60)) as response:
            sink.append(f"status: {response.status_code} {response.headers['content-type']}")
            for line in response.iter_lines():
                sink.append(line)
                if stop.is_set():
                    return
    except (httpx.HTTPError, httpx.StreamError, RuntimeError):
        if not stop.is_set():  # the test closing the client ends the stream; anything earlier is a failure
            raise


def test_events_reach_only_readers_of_the_space(server: tuple[str, Settings]) -> None:
    base, settings = server
    org = seed_org(settings)
    _, a_email = seed_user(settings, org)
    _, b_email = seed_user(settings, org)
    a, b, owner = _login(base, a_email), _login(base, b_email), _login(base, org.owner_email)
    a_lines: list[str] = []
    b_lines: list[str] = []
    owner_lines: list[str] = []
    stop = threading.Event()
    listeners = [
        threading.Thread(target=_listen, args=(client, sink, stop), daemon=True)
        for client, sink in ((a, a_lines), (b, b_lines), (owner, owner_lines))
    ]
    for listener in listeners:
        listener.start()
    time.sleep(1.0)  # let all three subscribe

    space = next(s["id"] for s in a.get("/api/spaces").json() if s["kind"] == "PERSONAL")
    uploaded = a.post(f"/api/spaces/{space}/documents", files=[("files", ("buses.md", BUS_PROTOCOLS_MD.encode()))])
    assert uploaded.status_code == 201, uploaded.text
    doc_id = uploaded.json()["documents"][0]["id"]

    deadline = time.monotonic() + 30
    while not any('"READY"' in line and doc_id in line for line in a_lines):
        assert time.monotonic() < deadline, a_lines
        time.sleep(0.2)
    time.sleep(1.0)
    stop.set()

    for sink in (a_lines, b_lines, owner_lines):
        assert sink[0] == "status: 200 text/event-stream; charset=utf-8", sink[:2]
    statuses = [line for line in a_lines if line.startswith("data:") and doc_id in line]
    assert any('"PARSING"' in s for s in statuses)
    assert any(line == "event: document.status" for line in a_lines)
    # Neither a colleague nor the org owner may learn that a private document exists.
    assert not any(doc_id in line for line in b_lines)
    assert not any(doc_id in line for line in owner_lines)
    for client in (a, b, owner):
        client.close()
