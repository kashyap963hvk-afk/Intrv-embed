"""Connectors end to end against real services: an S3-compatible bucket (SeaweedFS) kept in sync with a space,
root-prefix confinement, credential secrecy, isolation between members, failure backoff and the due-source scan."""

from __future__ import annotations

import http.server
import secrets
import threading
import uuid
from collections.abc import Iterator
from typing import Any

import boto3
import psycopg
import pytest
from botocore.config import Config as BotoConfig

from app.core.config import Settings
from tests.integration.conftest import S3_ACCESS_KEY, S3_SECRET_KEY, Infra, Postgres
from tests.integration.helpers import ApiClient, knowledge_settings, new_client, seed_org, seed_user

pytestmark = pytest.mark.integration

PRIYA_V1 = "# Priya Raman\n\nFirmware engineer. Wrote a CAN bootloader for motor controllers."
BUSES_V1 = "# Bus notes\n\nI2C uses open-drain lines with pull-up resistors and clock stretching."
BUSES_V2 = "# Bus notes\n\nSPI has four modes set by CPOL and CPHA; mode 0 samples on the rising edge."


@pytest.fixture
def settings(infra: Infra, fresh_valkey: str) -> Settings:
    # Self-host mode: the bucket under test lives on 127.0.0.1.
    return knowledge_settings(
        infra.database_url, fresh_valkey, infra.qdrant_url, infra.s3_endpoint_url, allow_private_llm_endpoints=True
    )


@pytest.fixture(scope="module")
def shared_bucket(infra: Infra) -> tuple[str, Any]:
    client = boto3.client(
        "s3",
        endpoint_url=infra.s3_endpoint_url,
        region_name="us-east-1",
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=BotoConfig(s3={"addressing_style": "path"}),
    )
    name = f"conn-{secrets.token_hex(4)}"
    client.create_bucket(Bucket=name)  # one bucket per module: SeaweedFS has few volume slots
    return name, client


@pytest.fixture
def bucket(shared_bucket: tuple[str, Any]) -> tuple[str, Any, str]:
    """(bucket, client, run prefix): each test works under its own prefix of the shared bucket."""
    name, client = shared_bucket
    return name, client, f"run-{secrets.token_hex(4)}"


def put(client: Any, bucket: str, key: str, body: str | bytes) -> None:
    client.put_object(Bucket=bucket, Key=key, Body=body.encode() if isinstance(body, str) else body)


@pytest.fixture
def people(settings: Settings) -> Iterator[dict[str, Any]]:
    org = seed_org(settings)
    _, a_email = seed_user(settings, org)
    _, b_email = seed_user(settings, org)
    clients = {"a": new_client(settings), "b": new_client(settings), "owner": new_client(settings)}
    for key, email in (("a", a_email), ("b", b_email), ("owner", org.owner_email)):
        assert clients[key].login(email).status_code == 200
    yield clients
    for api in clients.values():
        api.close()


def personal_space(api: ApiClient) -> str:
    return next(s["id"] for s in api.get("/api/spaces").json() if s["kind"] == "PERSONAL")


def s3_account(infra: Infra, bucket: str, run: str, **overrides: Any) -> dict[str, Any]:
    return {
        "kind": "S3_COMPATIBLE",
        "name": "Team bucket",
        "endpoint_url": infra.s3_endpoint_url,
        "bucket": bucket,
        "root_prefix": f"{run}/team-a",
        "access_key": S3_ACCESS_KEY,
        "secret_key": S3_SECRET_KEY,
        **overrides,
    }


def search_ids(api: ApiClient, query: str) -> list[str]:
    response = api.post("/api/search", json={"query": query, "k": 10})
    assert response.status_code == 200, response.text
    return [hit["citation"]["document_id"] for hit in response.json()["results"]]


def search_texts(api: ApiClient, query: str) -> list[str]:
    response = api.post("/api/search", json={"query": query, "k": 10})
    assert response.status_code == 200, response.text
    return [hit["text"] for hit in response.json()["results"]]


def test_s3_import_modify_delete_cycle(  # noqa: PLR0915 - one end-to-end story, kept together for readability
    infra: Infra, pg: Postgres, bucket: tuple[str, Any, str], people: dict[str, Any]
) -> None:
    name, client, run = bucket
    put(client, name, f"{run}/team-a/cvs/priya.md", PRIYA_V1)
    put(client, name, f"{run}/team-a/notes/buses.md", BUSES_V1)
    put(client, name, f"{run}/team-a/intro.mp4", b"\x00\x00\x00\x18ftypmp42")
    put(client, name, f"{run}/team-b/secret.md", "# Secret\n\nquarterly layoffs plan")
    a: ApiClient = people["a"]
    space = personal_space(a)

    bad = a.post("/api/connectors/accounts/test", json=s3_account(infra, name, run, secret_key="wrong-secret"))
    assert bad.status_code == 200
    assert bad.json()["ok"] is False
    created = a.post("/api/connectors/accounts", json=s3_account(infra, name, run))
    assert created.status_code == 201, created.text
    account = created.json()
    assert S3_SECRET_KEY not in created.text
    assert "secret_key" not in account["config"]
    with psycopg.connect(pg.dsn("postgres")) as conn:
        stored = conn.execute(
            "SELECT encrypted_secret FROM connector_accounts WHERE id = %s", (account["id"],)
        ).fetchone()
    assert stored is not None
    assert S3_SECRET_KEY.encode() not in bytes(stored[0])

    # Browsing never leaves the root prefix.
    listing = a.get(f"/api/connectors/accounts/{account['id']}/browse").json()
    assert sorted(i["path"] for i in listing["items"]) == [
        f"{run}/team-a/cvs/",
        f"{run}/team-a/intro.mp4",
        f"{run}/team-a/notes/",
    ]
    assert a.get(f"/api/connectors/accounts/{account['id']}/browse", params={"path": "../team-b"}).status_code == 400
    # Folder ids from a listing open that folder (they already include the root).
    inside = a.get(f"/api/connectors/accounts/{account['id']}/browse", params={"path": f"{run}/team-a/notes/"}).json()
    assert [i["path"] for i in inside["items"]] == [f"{run}/team-a/notes/buses.md"]
    outside = a.post(
        f"/api/spaces/{space}/sources",
        json={"kind": "S3_COMPATIBLE", "account_id": account["id"], "name": "x", "prefix": f"{run}/team-b/"},
    )
    assert outside.status_code == 400

    # Import + keep in sync (runs inline in tests).
    created_source = a.post(
        f"/api/spaces/{space}/sources",
        json={
            "kind": "S3_COMPATIBLE",
            "account_id": account["id"],
            "name": "Team A bucket",
            "prefix": f"{run}/team-a/",
            "sync_enabled": True,
            "interval_minutes": 60,
        },
    )
    assert created_source.status_code == 201, created_source.text
    source_id = created_source.json()["id"]
    source = a.get(f"/api/sources/{source_id}").json()
    assert source["status"] == "IDLE", source
    assert source["last_result"]["added"] == 2
    assert source["document_count"] == 2
    assert source["next_sync_at"] is not None
    docs = {d["filename"]: d for d in a.get(f"/api/spaces/{space}/documents").json()}
    assert set(docs) == {"priya.md", "buses.md"}
    assert all(d["status"] == "READY" for d in docs.values())
    assert docs["buses.md"]["id"] in search_ids(a, "I2C clock stretching pull-up")

    # Unchanged remote → nothing re-imported.
    again = a.post(f"/api/sources/{source_id}/sync")
    assert again.status_code == 200
    assert a.get(f"/api/sources/{source_id}").json()["last_result"]["added"] == 0

    # Modify → new version replaces the old one (vectors, chunks and original file).
    put(client, name, f"{run}/team-a/notes/buses.md", BUSES_V2)
    assert a.post(f"/api/sources/{source_id}/sync").status_code == 200
    assert a.get(f"/api/sources/{source_id}").json()["last_result"]["updated"] == 1
    buses = a.get(f"/api/documents/{docs['buses.md']['id']}").json()
    assert buses["status"] == "READY"
    assert buses["id"] == docs["buses.md"]["id"]  # same document, new version
    assert buses["id"] in search_ids(a, "SPI CPOL CPHA modes")
    assert all("I2C" not in hit for hit in search_texts(a, "I2C clock stretching pull-up"))
    with psycopg.connect(pg.dsn("postgres")) as conn:
        version, key = conn.execute(
            "SELECT version, storage_key FROM documents WHERE id = %s", (buses["id"],)
        ).fetchone()  # type: ignore[misc]
        texts = [r[0] for r in conn.execute("SELECT text FROM document_chunks WHERE document_id = %s", (buses["id"],))]
    assert version == 2
    assert all("I2C" not in t for t in texts)
    objects = [o["Key"] for o in _bucket_objects(infra, "org/") if buses["id"] in o["Key"]]
    assert objects == [key]

    # Delete at the source → document, vectors and files go.
    client.delete_object(Bucket=name, Key=f"{run}/team-a/cvs/priya.md")
    assert a.post(f"/api/sources/{source_id}/sync").status_code == 200
    assert a.get(f"/api/sources/{source_id}").json()["last_result"]["deleted"] == 1
    assert a.get(f"/api/documents/{docs['priya.md']['id']}").status_code == 404
    assert all("bootloader" not in hit for hit in search_texts(a, "CAN bootloader motor controllers"))
    assert not [o for o in _bucket_objects(infra, "org/") if docs["priya.md"]["id"] in o["Key"]]

    # Removing the source can keep or delete what it imported.
    assert a.delete(f"/api/sources/{source_id}", params={"delete_documents": "true"}).status_code == 204
    assert a.get(f"/api/documents/{buses['id']}").status_code == 404


def _bucket_objects(infra: Infra, prefix: str) -> list[dict[str, Any]]:
    from tests.integration.conftest import S3_BUCKET  # noqa: PLC0415

    client = boto3.client(
        "s3",
        endpoint_url=infra.s3_endpoint_url,
        region_name="us-east-1",
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=BotoConfig(s3={"addressing_style": "path"}),
    )
    pages = client.get_paginator("list_objects_v2").paginate(Bucket=S3_BUCKET, Prefix=prefix)
    return [obj for page in pages for obj in page.get("Contents", [])]


@pytest.mark.isolation
def test_accounts_and_sources_are_private(infra: Infra, bucket: tuple[str, Any, str], people: dict[str, Any]) -> None:
    name, client, run = bucket
    put(client, name, f"{run}/team-a/cv.md", PRIYA_V1)
    a, b, owner = people["a"], people["b"], people["owner"]
    account = a.post("/api/connectors/accounts", json=s3_account(infra, name, run)).json()
    space = personal_space(a)
    source = a.post(
        f"/api/spaces/{space}/sources",
        json={"kind": "S3_COMPATIBLE", "account_id": account["id"], "name": "mine", "prefix": f"{run}/team-a/"},
    ).json()

    for outsider in (b, owner):
        assert account["id"] not in {x["id"] for x in outsider.get("/api/connectors/accounts").json()}
        assert outsider.get(f"/api/connectors/accounts/{account['id']}/browse").status_code == 404
        assert outsider.post(f"/api/connectors/accounts/{account['id']}/test").status_code == 404
        assert outsider.delete(f"/api/connectors/accounts/{account['id']}").status_code == 404
        assert outsider.get(f"/api/spaces/{space}/sources").status_code == 404
        assert outsider.get(f"/api/sources/{source['id']}").status_code == 404
        assert outsider.post(f"/api/sources/{source['id']}/sync").status_code == 404
        assert outsider.delete(f"/api/sources/{source['id']}").status_code == 404
        own_space = personal_space(outsider)
        stolen = outsider.post(
            f"/api/spaces/{own_space}/sources",
            json={"kind": "S3_COMPATIBLE", "account_id": account["id"], "name": "steal", "prefix": f"{run}/team-a/"},
        )
        assert stolen.status_code == 404

    # Org-level storage: owners only, and members cannot see or use it.
    assert a.post("/api/connectors/accounts", json=s3_account(infra, name, run, org_level=True)).status_code == 403
    org_account = owner.post("/api/connectors/accounts", json=s3_account(infra, name, run, org_level=True, name="Org"))
    assert org_account.status_code == 201
    org_id = org_account.json()["id"]
    assert org_id not in {x["id"] for x in b.get("/api/connectors/accounts").json()}
    assert b.get(f"/api/connectors/accounts/{org_id}/browse").status_code == 404
    leaked = b.post(
        f"/api/spaces/{personal_space(b)}/sources",
        json={"kind": "S3_COMPATIBLE", "account_id": org_id, "name": "leak", "prefix": f"{run}/team-a/"},
    )
    assert leaked.status_code == 404


class _Pages(http.server.BaseHTTPRequestHandler):
    status = 404
    body = b"<html><body><h1>Embedded notes</h1><p>Watchdog timers reset hung firmware.</p></body></html>"

    def do_GET(self) -> None:
        if self.path == "/robots.txt":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(type(self).status)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(type(self).body)

    def log_message(self, *_: object) -> None:
        return


def test_failures_back_off_and_recover(pg: Postgres, people: dict[str, Any]) -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        a = people["a"]
        space = personal_space(a)
        url = f"http://127.0.0.1:{server.server_address[1]}/notes.html"
        created = a.post(
            f"/api/spaces/{space}/sources",
            json={"kind": "URL", "name": "Team wiki", "url": url, "sync_enabled": True, "interval_minutes": 30},
        )
        assert created.status_code == 201, created.text
        source = a.get(f"/api/sources/{created.json()['id']}").json()
        assert source["status"] == "ERROR"
        assert "404" in source["last_error"]
        assert source["failure_count"] == 1
        with psycopg.connect(pg.dsn("postgres")) as conn:
            delay = conn.execute(
                "SELECT extract(epoch FROM next_sync_at - now()) FROM connector_sources WHERE id = %s", (source["id"],)
            ).fetchone()
        assert delay is not None
        assert 25 * 60 < float(delay[0]) <= 30 * 60

        _Pages.status = 200
        assert a.post(f"/api/sources/{source['id']}/sync").status_code == 200
        recovered = a.get(f"/api/sources/{source['id']}").json()
        assert recovered["status"] == "IDLE"
        assert recovered["failure_count"] == 0
        assert recovered["document_count"] == 1
        assert search_ids(a, "watchdog hung firmware")
    finally:
        _Pages.status = 404
        server.shutdown()


@pytest.mark.isolation
def test_due_scan_returns_ids_only_and_crosses_tenants_safely(pg: Postgres, people: dict[str, Any]) -> None:
    a = people["a"]
    space = personal_space(a)
    source = a.post(
        f"/api/spaces/{space}/sources",
        json={"kind": "URL", "name": "x", "url": "http://127.0.0.1:9/nothing", "sync_enabled": True},
    ).json()
    with psycopg.connect(pg.dsn("postgres"), autocommit=True) as admin:
        admin.execute(
            "UPDATE connector_sources SET next_sync_at = now() - interval '1 minute', status = 'IDLE' WHERE id = %s",
            (source["id"],),
        )
    with psycopg.connect(pg.dsn("app_rw")) as conn:
        assert conn.execute("SELECT count(*) FROM connector_sources").fetchone() == (0,)  # RLS: no context, no rows
        due = conn.execute("SELECT * FROM app_due_connector_sources(500)").fetchall()
    assert any(row[0] == uuid.UUID(source["id"]) for row in due)
    assert all(len(row) == 3 for row in due)  # ids only, no selection, names or credentials


def test_scheduled_sync_runs_in_the_worker(
    infra: Infra, pg: Postgres, bucket: tuple[str, Any, str], fresh_valkey: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deployed path: the API enqueues, beat's `sync.due` finds due sources, the worker syncs and ingests."""
    from celery.contrib.testing.worker import start_worker  # noqa: PLC0415

    from app.core.config import get_settings  # noqa: PLC0415
    from tests.integration.helpers import settings_env  # noqa: PLC0415
    from workers import runtime  # noqa: PLC0415
    from workers.config import QUEUES, create_celery  # noqa: PLC0415

    name, client, run = bucket
    put(client, name, f"{run}/team-a/watchdog.md", "# Watchdog\n\nRefresh the independent watchdog from the main loop.")
    settings = knowledge_settings(
        infra.database_url,
        fresh_valkey,
        infra.qdrant_url,
        infra.s3_endpoint_url,
        allow_private_llm_endpoints=True,
        ingest_inline=False,
    )
    for key, value in settings_env(settings).items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    org = seed_org(settings)
    _, email = seed_user(settings, org)
    api = new_client(settings)
    try:
        assert api.login(email).status_code == 200
        space = personal_space(api)
        account = api.post("/api/connectors/accounts", json=s3_account(infra, name, run)).json()
        celery = create_celery(settings)
        celery.loader.import_default_modules()
        with start_worker(celery, pool="solo", perform_ping_check=False, queues=list(QUEUES)):
            source = api.post(
                f"/api/spaces/{space}/sources",
                json={
                    "kind": "S3_COMPATIBLE",
                    "account_id": account["id"],
                    "name": "w",
                    "prefix": f"{run}/team-a/",
                    "sync_enabled": True,
                },
            ).json()
            _wait(lambda: api.get(f"/api/sources/{source['id']}").json()["document_count"] == 1)
            docs = api.get(f"/api/spaces/{space}/documents").json()
            _wait(lambda: api.get(f"/api/documents/{docs[0]['id']}").json()["status"] == "READY")

            # A change plus an overdue schedule: the beat task picks the source up without any API call.
            put(client, name, f"{run}/team-a/dma.md", "# DMA\n\nCircular mode reloads the transfer counter.")
            with psycopg.connect(pg.dsn("postgres"), autocommit=True) as admin:
                admin.execute(
                    "UPDATE connector_sources SET next_sync_at = now() - interval '1 minute' WHERE id = %s",
                    (source["id"],),
                )
            assert celery.send_task("sync.due").get(timeout=30) >= 1
            _wait(lambda: api.get(f"/api/sources/{source['id']}").json()["document_count"] == 2)
    finally:
        api.close()
        get_settings.cache_clear()
        runtime._deps = None
        runtime._sync = None


def _wait(condition: Any, timeout_s: float = 60) -> None:
    import time  # noqa: PLC0415

    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.3)


# Azurite's documented development account (public, not a secret).
AZURITE_ACCOUNT = "devstoreaccount1"
AZURITE_KEY = (
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="  # gitleaks:allow
)


@pytest.fixture(scope="module")
def azurite() -> Iterator[str]:
    from testcontainers.core.container import DockerContainer  # noqa: PLC0415

    from tests.integration.conftest import wait_until  # noqa: PLC0415

    container = (
        DockerContainer("mcr.microsoft.com/azure-storage/azurite:3.35.0")
        .with_command("azurite-blob --blobHost 0.0.0.0 --skipApiVersionCheck --loose")
        .with_exposed_ports(10000)
    )
    with container:
        host, port = container.get_container_host_ip(), int(container.get_exposed_port(10000))
        connection = (
            f"DefaultEndpointsProtocol=http;AccountName={AZURITE_ACCOUNT};AccountKey={AZURITE_KEY};"
            f"BlobEndpoint=http://{host}:{port}/{AZURITE_ACCOUNT};"
        )
        from azure.storage.blob import BlobServiceClient  # noqa: PLC0415

        wait_until(
            lambda: BlobServiceClient.from_connection_string(connection).get_service_properties(), what="azurite"
        )
        yield connection


def test_azure_blob_import_modify_delete(azurite: str, people: dict[str, Any]) -> None:
    from azure.storage.blob import BlobServiceClient  # noqa: PLC0415

    container = BlobServiceClient.from_connection_string(azurite).create_container(f"c{secrets.token_hex(4)}")
    container.upload_blob("team-a/notes/buses.md", BUSES_V1)
    container.upload_blob("team-a/cv.md", PRIYA_V1)
    container.upload_blob("team-b/secret.md", "# Secret\n\nnot for import")
    a: ApiClient = people["a"]
    space = personal_space(a)
    body = {
        "kind": "AZURE_BLOB",
        "name": "Azure team",
        "container": container.container_name,
        "root_prefix": "team-a",
        "connection_string": azurite,
    }
    assert a.post("/api/connectors/accounts/test", json=body).json()["ok"] is True
    account = a.post("/api/connectors/accounts", json=body)
    assert account.status_code == 201, account.text
    account_id = account.json()["id"]
    assert "AccountKey" not in account.text
    listing = a.get(f"/api/connectors/accounts/{account_id}/browse").json()["items"]
    assert sorted(i["path"] for i in listing) == ["team-a/cv.md", "team-a/notes/"]
    assert a.get(f"/api/connectors/accounts/{account_id}/browse", params={"path": "../team-b"}).status_code == 400

    source = a.post(
        f"/api/spaces/{space}/sources",
        json={"kind": "AZURE_BLOB", "account_id": account_id, "name": "Azure", "prefix": "team-a/"},
    ).json()
    assert a.get(f"/api/sources/{source['id']}").json()["last_result"]["added"] == 2

    container.upload_blob("team-a/notes/buses.md", BUSES_V2, overwrite=True)
    container.delete_blob("team-a/cv.md")
    assert a.post(f"/api/sources/{source['id']}/sync").status_code == 200
    result = a.get(f"/api/sources/{source['id']}").json()["last_result"]
    assert (result["updated"], result["deleted"]) == (1, 1)
    assert any("CPOL" in text for text in search_texts(a, "SPI CPOL CPHA modes"))


def test_one_bad_file_does_not_stop_the_sync(infra: Infra, bucket: tuple[str, Any, str], fresh_valkey: str) -> None:
    name, client, run = bucket
    settings = knowledge_settings(
        infra.database_url,
        fresh_valkey,
        infra.qdrant_url,
        infra.s3_endpoint_url,
        allow_private_llm_endpoints=True,
        max_upload_mb=1,
    )
    put(client, name, f"{run}/team-a/big.md", "# Big\n\n" + "register map " * 120_000)  # > 1 MB
    put(client, name, f"{run}/team-a/ok.md", BUSES_V1)
    org = seed_org(settings)
    _, email = seed_user(settings, org)
    api = new_client(settings)
    try:
        assert api.login(email).status_code == 200
        account = api.post("/api/connectors/accounts", json=s3_account(infra, name, run)).json()
        source = api.post(
            f"/api/spaces/{personal_space(api)}/sources",
            json={"kind": "S3_COMPATIBLE", "account_id": account["id"], "name": "s", "prefix": f"{run}/team-a/"},
        ).json()
        state = api.get(f"/api/sources/{source['id']}").json()
        assert state["status"] == "IDLE"
        assert state["last_result"]["added"] == 1
        assert len(state["last_result"]["failed"]) == 1
        assert "big.md" in state["last_result"]["failed"][0]
        assert "upload limit" in state["last_result"]["failed"][0]
    finally:
        api.close()
