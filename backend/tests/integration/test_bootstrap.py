"""First-run owner bootstrap and the operator CLI."""

from __future__ import annotations

import asyncio
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.email import EmailSender
from app.db.session import create_engine, create_session_factory
from app.main import create_app
from app.orgs.service import bootstrap_first_owner
from tests.integration.conftest import BACKEND_ROOT, Postgres, create_empty_database
from tests.integration.helpers import PASSWORD, ApiClient, app_settings

pytestmark = pytest.mark.integration


async def _org_count(url: str) -> int:
    engine = create_engine(app_settings(url, "redis://127.0.0.1:1/0"))
    try:
        async with create_session_factory(engine)() as db:
            return int(await db.scalar(text("SELECT auth_org_count()")) or 0)
    finally:
        await engine.dispose()


def test_bootstrap_creates_org_and_owner_invite_once(pg: Postgres, fresh_valkey: str) -> None:
    url = create_empty_database(pg)
    settings = app_settings(
        url, fresh_valkey, owner_bootstrap_email="Boss@Example.COM", owner_bootstrap_org_slug="acme"
    )

    with TestClient(create_app(settings)):
        pass
    assert asyncio.run(_org_count(url)) == 1

    # Running it again (another replica, a restart) does nothing.
    async def again() -> None:
        engine = create_engine(settings)
        try:
            await bootstrap_first_owner(create_session_factory(engine), settings, EmailSender(settings))
        finally:
            await engine.dispose()

    asyncio.run(again())
    assert asyncio.run(_org_count(url)) == 1


def test_cli_create_owner_with_password(pg: Postgres, fresh_valkey: str) -> None:
    url = create_empty_database(pg)
    settings = app_settings(url, fresh_valkey)
    env = {
        "DATABASE_URL": url,
        "VALKEY_URL": fresh_valkey,
        "QDRANT_URL": "http://127.0.0.1:1",
        "S3_BUCKET": "unused",
        "MASTER_KEY_BASE64": settings.master_key_base64.get_secret_value(),
        "JWT_SECRET": settings.jwt_secret.get_secret_value(),
        "APP_ENV": "test",
        "PATH": "/usr/bin:/bin",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.cli",
            "create-owner",
            "--org-slug",
            "cli-org",
            "--org-name",
            "CLI Org",
            "--email",
            "cli-owner@example.com",
            "--name",
            "Cli Owner",
            "--password-stdin",
        ],
        cwd=BACKEND_ROOT,
        env=env,
        input=PASSWORD + "\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    api = ApiClient(create_app(settings))
    try:
        response = api.login("cli-owner@example.com")
        assert response.status_code == 200
        assert response.json()["user"]["role"] == "OWNER"
    finally:
        api.close()
