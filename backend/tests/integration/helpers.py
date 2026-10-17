"""Helpers for API-level integration tests: a CSRF-aware client and data factories."""

from __future__ import annotations

import asyncio
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.audit.service import SYSTEM_META
from app.core.config import Settings
from app.core.security import hash_password
from app.db.models import Organization, Role, User, UserPolicy, UserStatus
from app.db.session import create_engine, create_session_factory
from app.db.tenant import TenantContext, set_tenant_context
from app.main import create_app
from app.orgs.service import add_active_owner, create_org
from tests.conftest import make_settings

PASSWORD = "correct-horse-battery-9"


def app_settings(database_url: str, valkey_url: str, **overrides: Any) -> Settings:
    return make_settings(database_url=database_url, valkey_url=valkey_url, **overrides)


class ApiClient:
    """TestClient wrapper that fetches a CSRF token and sends it on every request (like the frontend)."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self._client = TestClient(app, raise_server_exceptions=False)
        self._client.__enter__()
        self.csrf = self._client.get("/api/auth/csrf").json()["csrf_token"]

    def close(self) -> None:
        self._client.__exit__(None, None, None)

    @property
    def cookies(self) -> httpx.Cookies:
        return self._client.cookies

    def request(self, method: str, url: str, *, csrf: bool = True, **kwargs: Any) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        if csrf:
            headers.setdefault("X-CSRF-Token", self.csrf)
        return self._client.request(method, url, headers=headers, **kwargs)

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", url, **kwargs)

    def login(self, email: str, password: str = PASSWORD, org_slug: str | None = None) -> httpx.Response:
        body: dict[str, Any] = {"email": email, "password": password}
        if org_slug:
            body["org_slug"] = org_slug
        return self.post("/api/auth/login", json=body)


@dataclass(frozen=True)
class SeededOrg:
    id: uuid.UUID
    slug: str
    owner_id: uuid.UUID
    owner_email: str


def unique_slug(prefix: str = "org") -> str:
    return f"{prefix}-{secrets.token_hex(4)}"


async def _seed_org(settings: Settings, slug: str) -> SeededOrg:
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as db:
            org = await create_org(db, name=f"Org {slug}", slug=slug)
            owner = await add_active_owner(
                db, org=org, email=f"owner@{slug}.example.com", name="Olivia Owner", password=PASSWORD, meta=SYSTEM_META
            )
            await db.commit()
            return SeededOrg(org.id, org.slug, owner.id, owner.email)
    finally:
        await engine.dispose()


def seed_org(settings: Settings, slug: str | None = None) -> SeededOrg:
    return asyncio.run(_seed_org(settings, slug or unique_slug()))


async def _seed_user(settings: Settings, org_id: uuid.UUID, email: str, status: UserStatus, role: Role) -> uuid.UUID:
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as db:
            await set_tenant_context(db, TenantContext(org_id=org_id))
            user = User(
                org_id=org_id,
                email=email,
                name="Member " + email.split("@", maxsplit=1)[0],
                password_hash=hash_password(PASSWORD),
                role=role,
                status=status,
            )
            db.add(user)
            await db.flush()
            db.add(UserPolicy(user_id=user.id, org_id=org_id))
            await db.commit()
            return user.id
    finally:
        await engine.dispose()


def seed_user(
    settings: Settings,
    org: SeededOrg,
    email: str | None = None,
    *,
    status: UserStatus = UserStatus.ACTIVE,
    role: Role = Role.MEMBER,
) -> tuple[uuid.UUID, str]:
    address = email or f"member-{secrets.token_hex(3)}@{org.slug}.example.com"
    return asyncio.run(_seed_user(settings, org.id, address, status, role)), address


def new_client(settings: Settings) -> ApiClient:
    return ApiClient(create_app(settings))


__all__ = ["PASSWORD", "ApiClient", "Organization", "SeededOrg", "app_settings", "new_client", "seed_org", "seed_user"]
