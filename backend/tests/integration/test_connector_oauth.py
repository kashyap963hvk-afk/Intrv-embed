"""OAuth connect flow against a recorded provider (respx): PKCE, single-use state bound to the user,
encrypted token storage and the redirect back to the app."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import psycopg
import pytest
import respx

from app.core.config import Settings
from tests.integration.conftest import Infra, Postgres
from tests.integration.helpers import ApiClient, knowledge_settings, new_client, seed_org, seed_user

pytestmark = [pytest.mark.integration, pytest.mark.isolation]

ACCESS = "sl.access-token-abcdefghijklmnop"
REFRESH = "refresh-token-qrstuvwxyz0123456789"


@pytest.fixture
def settings(infra: Infra, fresh_valkey: str) -> Settings:
    return knowledge_settings(
        infra.database_url,
        fresh_valkey,
        infra.qdrant_url,
        infra.s3_endpoint_url,
        dropbox_app_key="dbx-app-key",
        dropbox_app_secret="dbx-app-secret",
        public_app_url="http://app.example.com",
        api_public_url="http://api.example.com",
    )


@pytest.fixture
def members(settings: Settings) -> Iterator[tuple[ApiClient, ApiClient]]:
    org = seed_org(settings)
    _, a_email = seed_user(settings, org)
    _, b_email = seed_user(settings, org)
    a, b = new_client(settings), new_client(settings)
    assert a.login(a_email).status_code == 200
    assert b.login(b_email).status_code == 200
    yield a, b
    a.close()
    b.close()


def _start(api: ApiClient) -> dict[str, Any]:
    response = api.post("/api/connectors/oauth/dropbox/start")
    assert response.status_code == 200, response.text
    url = urlsplit(response.json()["authorize_url"])
    params = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == "www.dropbox.com"
    assert params["code_challenge_method"] == "S256"
    assert params["redirect_uri"] == "http://api.example.com/api/connectors/oauth/dropbox/callback"
    assert params["token_access_type"] == "offline"
    return params


def _callback(api: ApiClient, state: str, code: str = "auth-code") -> httpx.Response:
    return api.get(
        "/api/connectors/oauth/dropbox/callback", params={"state": state, "code": code}, follow_redirects=False
    )


def test_oauth_connect_flow(pg: Postgres, members: tuple[ApiClient, ApiClient], respx_mock: respx.MockRouter) -> None:
    a, b = members
    token = respx_mock.post("https://api.dropboxapi.com/oauth2/token").respond(
        json={"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 14400}
    )
    respx_mock.post("https://api.dropboxapi.com/2/users/get_current_account").respond(
        json={"email": "mira@example.com"}
    )

    kinds = {k["kind"]: k for k in a.get("/api/connectors/kinds").json()}
    assert kinds["DROPBOX"]["available"] is True
    assert kinds["MS_GRAPH"]["available"] is False

    params = _start(a)
    # Another member cannot complete someone else's sign-in.
    stolen = _callback(b, params["state"])
    assert stolen.status_code == 303
    assert "status=error" in stolen.headers["location"]

    params = _start(a)
    done = _callback(a, params["state"])
    assert done.status_code == 303
    assert done.headers["location"].startswith("http://app.example.com/app/knowledge/connected?status=ok")
    form = parse_qs(token.calls.last.request.content.decode())
    assert form["code_verifier"][0]
    assert form["grant_type"] == ["authorization_code"]

    # State is single use.
    replay = _callback(a, params["state"])
    assert "status=error" in replay.headers["location"]

    accounts = a.get("/api/connectors/accounts").json()
    assert [x["name"] for x in accounts] == ["Dropbox (mira@example.com)"]
    assert ACCESS not in str(accounts)
    assert b.get("/api/connectors/accounts").json() == []
    with psycopg.connect(pg.dsn("postgres")) as conn:
        (blob,) = conn.execute(
            "SELECT encrypted_secret FROM connector_accounts WHERE id = %s", (accounts[0]["id"],)
        ).fetchone()  # type: ignore[misc]
    assert ACCESS.encode() not in bytes(blob)
    assert REFRESH.encode() not in bytes(blob)


def test_unconfigured_provider_is_not_found(members: tuple[ApiClient, ApiClient]) -> None:
    a, _ = members
    assert a.post("/api/connectors/oauth/microsoft/start").status_code == 404
