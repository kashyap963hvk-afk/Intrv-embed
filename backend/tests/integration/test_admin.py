"""Owner administration, invites and cross-organization isolation."""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import urlparse

import pytest

from app.core.config import Settings
from app.db.models import UserStatus
from tests.integration.helpers import PASSWORD, ApiClient, app_settings, new_client, seed_org, seed_user

pytestmark = pytest.mark.integration


@pytest.fixture
def settings(postgres: str, fresh_valkey: str) -> Settings:
    return app_settings(postgres, fresh_valkey)


@pytest.fixture
def owner_client(settings: Settings) -> Iterator[tuple[ApiClient, object]]:
    org = seed_org(settings)
    api = new_client(settings)
    assert api.login(org.owner_email).status_code == 200
    yield api, org
    api.close()


def _actions(api: ApiClient, target_id: str) -> list[str]:
    return [item["action"] for item in api.get("/api/admin/audit", params={"target_id": target_id}).json()["items"]]


def test_member_cannot_use_admin_api(settings: Settings) -> None:
    org = seed_org(settings)
    _, email = seed_user(settings, org)
    api = new_client(settings)
    try:
        api.login(email)
        for method, url in [
            ("GET", "/api/admin/users"),
            ("GET", "/api/admin/audit"),
            ("POST", "/api/admin/invites"),
            ("GET", "/api/admin/groups"),
        ]:
            response = api.request(method, url, json={"email": "x@y.example.com"})
            assert response.status_code == 403, (method, url, response.text)
    finally:
        api.close()


def test_lifecycle_is_audited(settings: Settings, owner_client: tuple[ApiClient, object]) -> None:
    api, org = owner_client
    user_id, _ = seed_user(settings, org, status=UserStatus.PENDING)  # type: ignore[arg-type]
    uid = str(user_id)

    assert api.post(f"/api/admin/users/{uid}/approve").json()["status"] == "ACTIVE"
    policy = api.patch(
        f"/api/admin/users/{uid}/policy", json={"can_use_voice": False, "monthly_interview_quota": 25}
    ).json()["policy"]
    assert policy["can_use_voice"] is False
    assert policy["monthly_interview_quota"] == 25
    assert api.post(f"/api/admin/users/{uid}/suspend").json()["status"] == "SUSPENDED"
    assert api.post(f"/api/admin/users/{uid}/reactivate").json()["status"] == "ACTIVE"

    actions = _actions(api, uid)
    for expected in ("user.approved", "user.policy_changed", "user.suspended", "user.reactivated"):
        assert expected in actions


def test_suspension_ends_active_sessions(settings: Settings, owner_client: tuple[ApiClient, object]) -> None:
    api, org = owner_client
    user_id, email = seed_user(settings, org)  # type: ignore[arg-type]
    member = new_client(settings)
    try:
        member.login(email)
        assert member.get("/api/auth/me").status_code == 200
        api.post(f"/api/admin/users/{user_id}/suspend")
        assert member.get("/api/auth/me").status_code == 401
        assert member.post("/api/auth/refresh").status_code == 401
    finally:
        member.close()


def test_last_owner_is_protected(settings: Settings, owner_client: tuple[ApiClient, object]) -> None:
    api, org = owner_client
    me = api.get("/api/auth/me").json()["user"]["id"]
    assert api.post(f"/api/admin/users/{me}/suspend").status_code == 409
    assert api.put(f"/api/admin/users/{me}/role", json={"role": "MEMBER"}).status_code == 409

    second_id, _ = seed_user(settings, org)  # type: ignore[arg-type]
    assert api.put(f"/api/admin/users/{second_id}/role", json={"role": "OWNER"}).status_code == 200
    assert api.put(f"/api/admin/users/{me}/role", json={"role": "MEMBER"}).status_code == 200


def test_owner_cannot_touch_another_org(settings: Settings, owner_client: tuple[ApiClient, object]) -> None:
    api, _ = owner_client
    other = seed_org(settings)
    victim_id, victim_email = seed_user(settings, other, status=UserStatus.PENDING)
    assert victim_email not in {u["email"] for u in api.get("/api/admin/users").json()}
    assert api.get(f"/api/admin/users/{victim_id}").status_code == 404
    assert api.post(f"/api/admin/users/{victim_id}/approve").status_code == 404
    assert api.delete(f"/api/admin/users/{victim_id}").status_code == 404
    group = api.post("/api/admin/groups", json={"name": "Panel"}).json()
    assert api.put(f"/api/admin/groups/{group['id']}/members/{victim_id}").status_code == 404


def test_groups(settings: Settings, owner_client: tuple[ApiClient, object]) -> None:
    api, org = owner_client
    user_id, _ = seed_user(settings, org)  # type: ignore[arg-type]
    created = api.post("/api/admin/groups", json={"name": "Firmware panel", "description": "MCU interviews"})
    assert created.status_code == 201
    group_id = created.json()["id"]
    assert api.post("/api/admin/groups", json={"name": "Firmware panel"}).status_code == 409
    members = api.put(f"/api/admin/groups/{group_id}/members/{user_id}").json()["member_ids"]
    assert members == [str(user_id)]
    assert api.get(f"/api/admin/users/{user_id}").json()["group_ids"] == [group_id]
    assert api.delete(f"/api/admin/groups/{group_id}/members/{user_id}").json()["member_ids"] == []
    assert api.delete(f"/api/admin/groups/{group_id}").status_code == 204


def test_invite_flow(settings: Settings, owner_client: tuple[ApiClient, object]) -> None:
    api, _ = owner_client
    created = api.post("/api/admin/invites", json={"email": "Invitee@Example.COM", "role": "MEMBER"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["invite"]["email"] == "invitee@example.com"
    assert body["email_sent"] is True  # dev outbox
    token = urlparse(body["link"]).path.rsplit("/", 1)[-1]

    guest = new_client(settings)
    try:
        preview = guest.get(f"/api/auth/invites/{token}").json()
        assert preview["usable"] is True
        assert preview["email"] == "invitee@example.com"
        accepted = guest.post(
            "/api/auth/invites/accept", json={"token": token, "name": "Ivy Invitee", "password": PASSWORD}
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["user"]["status"] == "ACTIVE"
        reused = guest.post("/api/auth/invites/accept", json={"token": token, "name": "Ivy", "password": PASSWORD})
        assert reused.status_code == 409
    finally:
        guest.close()
    assert api.get("/api/admin/invites").json() == []


def test_revoked_invite_cannot_be_used(settings: Settings, owner_client: tuple[ApiClient, object]) -> None:
    api, _ = owner_client
    body = api.post("/api/admin/invites", json={"email": "revoked@example.com"}).json()
    token = urlparse(body["link"]).path.rsplit("/", 1)[-1]
    assert api.delete(f"/api/admin/invites/{body['invite']['id']}").status_code == 204
    guest = new_client(settings)
    try:
        assert guest.get(f"/api/auth/invites/{token}").json()["usable"] is False
        response = guest.post("/api/auth/invites/accept", json={"token": token, "name": "R", "password": PASSWORD})
        assert response.status_code == 409
    finally:
        guest.close()


def test_audit_pagination(settings: Settings, owner_client: tuple[ApiClient, object]) -> None:
    api, _ = owner_client
    for i in range(5):
        api.post("/api/admin/groups", json={"name": f"g{i}"})
    page1 = api.get("/api/admin/audit", params={"limit": 2, "action": "group."}).json()
    page2 = api.get("/api/admin/audit", params={"limit": 2, "action": "group.", "cursor": page1["next_cursor"]}).json()
    ids1 = {i["id"] for i in page1["items"]}
    ids2 = {i["id"] for i in page2["items"]}
    assert len(ids1) == len(ids2) == 2
    assert not ids1 & ids2
