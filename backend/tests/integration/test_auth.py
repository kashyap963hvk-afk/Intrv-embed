"""Authentication flows against real Postgres (RLS) and Valkey."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

import pytest
from fastapi import Depends

from app.auth import service as auth_service
from app.auth.deps import require_active_member
from app.auth.principal import Principal
from app.core.config import Settings
from app.db.models import UserStatus
from tests.integration.helpers import PASSWORD, ApiClient, app_settings, new_client, seed_org, seed_user

pytestmark = pytest.mark.integration


@pytest.fixture
def settings(postgres: str, fresh_valkey: str) -> Settings:
    return app_settings(postgres, fresh_valkey)


@pytest.fixture
def client(settings: Settings) -> Iterator[ApiClient]:
    api = new_client(settings)

    @api.app.get("/api/test/member-only")
    async def member_only(principal: Annotated[Principal, Depends(require_active_member)]) -> dict[str, str]:
        return {"user_id": str(principal.user_id)}

    yield api
    api.close()


def test_login_me_logout(settings: Settings, client: ApiClient) -> None:
    org = seed_org(settings)
    response = client.login(org.owner_email)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["email"] == org.owner_email
    assert body["user"]["role"] == "OWNER"
    assert body["org"]["slug"] == org.slug
    assert "intrv_at" in client.cookies
    assert "intrv_rt" in client.cookies

    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_logout_invalidates_a_copied_access_token(settings: Settings, client: ApiClient) -> None:
    org = seed_org(settings)
    client.login(org.owner_email)
    stolen = client.cookies.get("intrv_at")
    client.post("/api/auth/logout")
    other = new_client(settings)
    try:
        other.cookies.set("intrv_at", stolen)
        assert other.get("/api/auth/me").status_code == 401
    finally:
        other.close()


def test_wrong_password_and_unknown_email_look_identical(settings: Settings, client: ApiClient) -> None:
    org = seed_org(settings)
    wrong = client.login(org.owner_email, "not-the-password-123")
    unknown = client.login("nobody@example.com", "not-the-password-123")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


def test_csrf_required_on_mutations(settings: Settings, client: ApiClient) -> None:
    org = seed_org(settings)
    response = client.post("/api/auth/login", json={"email": org.owner_email, "password": PASSWORD}, csrf=False)
    assert response.status_code == 403
    assert "CSRF" in response.json()["detail"]
    bad = client.post(
        "/api/auth/login",
        json={"email": org.owner_email, "password": PASSWORD},
        headers={"X-CSRF-Token": "forged"},
    )
    assert bad.status_code == 403


def test_signup_pending_until_approved(settings: Settings, client: ApiClient) -> None:
    org = seed_org(settings)
    email = f"newbie@{org.slug}.example.com"
    response = client.post(
        "/api/auth/signup",
        json={"org_slug": org.slug, "email": email, "name": "New Bie", "password": PASSWORD},
    )
    assert response.status_code == 202, response.text

    # Duplicate signup gives the same answer (no account enumeration).
    again = client.post(
        "/api/auth/signup",
        json={"org_slug": org.slug, "email": email, "name": "New Bie", "password": PASSWORD},
    )
    assert again.status_code == 202

    assert client.login(email).status_code == 200
    me = client.get("/api/auth/me").json()
    assert me["user"]["status"] == "PENDING"
    assert client.get("/api/test/member-only").status_code == 403
    assert client.get("/api/admin/users").status_code == 403

    owner = new_client(settings)
    try:
        owner.login(org.owner_email)
        pending = owner.get("/api/admin/users", params={"status": "PENDING"}).json()
        target = next(u for u in pending if u["email"] == email)
        approved = owner.post(f"/api/admin/users/{target['id']}/approve")
        assert approved.status_code == 200
        assert approved.json()["status"] == "ACTIVE"
    finally:
        owner.close()

    assert client.get("/api/test/member-only").status_code == 200


def test_signup_rejects_weak_password_and_unknown_org(settings: Settings, client: ApiClient) -> None:
    org = seed_org(settings)
    weak = client.post(
        "/api/auth/signup",
        json={"org_slug": org.slug, "email": f"w@{org.slug}.example.com", "name": "W", "password": "short"},
    )
    assert weak.status_code == 422
    assert weak.json()["type"] == "urn:intrv:problem:weak-password"
    unknown = client.post(
        "/api/auth/signup",
        json={"org_slug": "no-such-org-zz", "email": "x@y.example.com", "name": "X", "password": PASSWORD},
    )
    assert unknown.status_code == 404


def test_suspended_user_cannot_login(settings: Settings, client: ApiClient) -> None:
    org = seed_org(settings)
    _, email = seed_user(settings, org, status=UserStatus.SUSPENDED)
    response = client.login(email)
    assert response.status_code == 403


def test_same_email_in_two_orgs_requires_choice(settings: Settings, client: ApiClient) -> None:
    org_a, org_b = seed_org(settings), seed_org(settings)
    email = "shared-person@multi.example.com"
    seed_user(settings, org_a, email)
    seed_user(settings, org_b, email)
    ambiguous = client.login(email)
    assert ambiguous.status_code == 409
    slugs = {o["slug"] for o in ambiguous.json()["organizations"]}
    assert slugs == {org_a.slug, org_b.slug}
    chosen = client.login(email, org_slug=org_b.slug)
    assert chosen.status_code == 200
    assert chosen.json()["org"]["slug"] == org_b.slug


def test_login_rate_limited_per_email(settings: Settings, client: ApiClient) -> None:
    email = "ratelimit-target@nowhere.example.com"
    codes = [client.login(email, "wrong-password-xyz").status_code for _ in range(11)]
    assert codes[:10] == [401] * 10
    assert codes[10] == 429


def test_refresh_rotates_and_reuse_revokes_family(
    settings: Settings, client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta  # noqa: PLC0415

    monkeypatch.setattr(auth_service, "REFRESH_REUSE_GRACE", timedelta(0))
    org = seed_org(settings)
    client.login(org.owner_email)
    first_refresh = client.cookies.get("intrv_rt", path="/api/auth")
    assert client.post("/api/auth/refresh").status_code == 204
    second_refresh = client.cookies.get("intrv_rt", path="/api/auth")
    assert second_refresh != first_refresh
    assert client.get("/api/auth/me").status_code == 200

    attacker = new_client(settings)
    try:
        attacker.cookies.set("intrv_rt", first_refresh, path="/api/auth")
        assert attacker.post("/api/auth/refresh").status_code == 401  # reuse detected
    finally:
        attacker.close()

    # The legitimate session in the same family is now dead too.
    assert client.post("/api/auth/refresh").status_code == 401
    assert client.get("/api/auth/me").status_code == 401

    owner = new_client(settings)
    try:
        owner.login(org.owner_email)
        audit = owner.get("/api/admin/audit", params={"action": "auth.refresh_reuse"}).json()
        assert len(audit["items"]) == 1
    finally:
        owner.close()


def test_change_password_ends_other_sessions(settings: Settings, client: ApiClient) -> None:
    org = seed_org(settings)
    _, email = seed_user(settings, org)
    other_device = new_client(settings)
    try:
        assert other_device.login(email).status_code == 200
        assert client.login(email).status_code == 200
        new_password = "a-brand-new-passphrase-42"
        response = client.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": new_password})
        assert response.status_code == 204, response.text
        assert client.get("/api/auth/me").status_code == 200
        assert other_device.get("/api/auth/me").status_code == 401
        assert client.login(email, new_password).status_code == 200
    finally:
        other_device.close()
