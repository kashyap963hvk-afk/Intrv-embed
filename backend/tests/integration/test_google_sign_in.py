"""Sign in with Google: owners invite any Gmail address, the invitation creates the account on the first Google
sign-in, later sign-ins find it by email, and with `member_sign_in = google` only owners may use a password."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import psycopg
import pytest
import respx

from app.core.config import Settings
from tests.integration.conftest import Infra, Postgres
from tests.integration.helpers import PASSWORD, ApiClient, knowledge_settings, new_client, seed_org, seed_user

pytestmark = [pytest.mark.integration, pytest.mark.isolation]

TOKEN_URL = "https://oauth2.googleapis.com/token"
MEMBER = "priya.member@gmail.com"


@pytest.fixture
def settings(infra: Infra, fresh_valkey: str) -> Settings:
    return knowledge_settings(
        infra.database_url,
        fresh_valkey,
        infra.qdrant_url,
        infra.s3_endpoint_url,
        google_oauth_client_id="client-123",
        google_oauth_client_secret="google-secret",
        public_app_url="http://app.example.com",
        api_public_url="http://api.example.com",
    )


@pytest.fixture
def owner(settings: Settings) -> Iterator[tuple[ApiClient, Any]]:
    org = seed_org(settings)
    api = new_client(settings)
    assert api.login(org.owner_email).status_code == 200
    yield api, org
    api.close()


def id_token(nonce: str, email: str, sub: str = "google-sub-1") -> str:
    def part(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    claims = {
        "iss": "https://accounts.google.com",
        "aud": "client-123",
        "exp": time.time() + 300,
        "nonce": nonce,
        "sub": sub,
        "email": email,
        "email_verified": True,
        "name": "Priya Member",
    }
    return f"{part({'alg': 'RS256'})}.{part(claims)}.signature"


def google_round_trip(
    api: ApiClient, respx_mock: respx.MockRouter, email: str, *, sub: str = "google-sub-1", **query: str
) -> httpx.Response:
    start = api.get("/api/auth/google/start", params=query, follow_redirects=False)
    assert start.status_code == 302
    url = urlsplit(start.headers["location"])
    params = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == "accounts.google.com"
    assert params["redirect_uri"] == "http://api.example.com/api/auth/oidc/google/callback"
    assert params["code_challenge_method"] == "S256"
    respx_mock.post(TOKEN_URL).respond(json={"id_token": id_token(params["nonce"], email, sub)})
    return api.get(
        "/api/auth/oidc/google/callback", params={"code": "c-1", "state": params["state"]}, follow_redirects=False
    )


def login_error(response: httpx.Response) -> str:
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("http://app.example.com/login?error=")
    return unquote(location.split("error=", 1)[1])


def test_google_members_and_password_owners(
    pg: Postgres, settings: Settings, owner: tuple[ApiClient, Any], respx_mock: respx.MockRouter
) -> None:
    api, org = owner
    respx_mock.route(host="testserver").pass_through()
    assert api.get("/api/auth/google").json() == {"enabled": True}
    policy = api.get("/api/admin/org-policy").json()
    assert api.put("/api/admin/org-policy", json={**policy, "member_sign_in": "google"}).status_code == 200

    # The owner gives access to a Gmail address; the invitation asks for Google, not a password.
    link = api.post("/api/admin/invites", json={"email": MEMBER, "role": "MEMBER"}).json()["link"]
    token = link.rsplit("/", 1)[1]
    assert api.get(f"/api/auth/invites/{token}").json()["sign_in"] == "google"
    guest = new_client(settings)
    try:
        refused = guest.post("/api/auth/invites/accept", json={"token": token, "name": "P", "password": PASSWORD})
        assert refused.status_code == 403

        # Signing in with a different Google account than the invited one is refused and leaves nothing behind.
        wrong = google_round_trip(guest, respx_mock, "someone.else@gmail.com", invite=token)
        assert "This invitation is for priya.member@gmail.com" in login_error(wrong)

        # The invited Google account: account created, session cookies set, back to the app.
        done = google_round_trip(guest, respx_mock, MEMBER, invite=token, next="/app/interviews")
        assert done.headers["location"] == "http://app.example.com/app/interviews"
        me = guest.get("/api/auth/me").json()
        assert (me["user"]["email"], me["user"]["role"]) == (MEMBER, "MEMBER")

        # A later sign-in needs no invitation; a state can only be used once.
        guest.cookies.clear()
        again = google_round_trip(guest, respx_mock, MEMBER)
        assert again.headers["location"] == "http://app.example.com/app"
        replay = guest.get(
            "/api/auth/oidc/google/callback", params={"code": "c-1", "state": "used"}, follow_redirects=False
        )
        assert "cancelled or expired" in login_error(replay)

        # A different Google account claiming the same email cannot take the account over.
        guest.cookies.clear()
        takeover = google_round_trip(guest, respx_mock, MEMBER, sub="google-sub-2")
        assert "different Google account" in login_error(takeover)

        # Nobody else gets in without an invitation.
        stranger = google_round_trip(guest, respx_mock, "stranger@gmail.com")
        assert "Ask an owner to invite you" in login_error(stranger)
    finally:
        guest.close()

    # Members with a password are told to use Google; owners keep their password.
    _, member_email = seed_user(settings, org)
    member = new_client(settings)
    try:
        assert member.login(member_email).status_code == 403
        assert new_client(settings).login(org.owner_email).status_code == 200
    finally:
        member.close()

    with psycopg.connect(pg.dsn("postgres")) as conn:
        rows = conn.execute(
            "SELECT email FROM users WHERE org_id = %s AND email LIKE %s", (str(org.id), "%gmail.com")
        ).fetchall()
    assert rows == [(MEMBER,)]  # the refused sign-ins created nothing
