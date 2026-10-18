"""Row-Level Security at the SQL level: the runtime role sees only the current tenant, or nothing."""

from __future__ import annotations

import psycopg
import pytest

from app.core.config import Settings
from tests.integration.conftest import Postgres
from tests.integration.helpers import app_settings, seed_org, seed_user

pytestmark = [pytest.mark.integration, pytest.mark.isolation]


@pytest.fixture
def settings(postgres: str, fresh_valkey: str) -> Settings:
    return app_settings(postgres, fresh_valkey)


def test_runtime_role_is_confined_to_the_tenant(pg: Postgres, settings: Settings) -> None:
    org_a, org_b = seed_org(settings), seed_org(settings)
    seed_user(settings, org_a)
    seed_user(settings, org_b)

    with psycopg.connect(pg.dsn("app_rw")) as conn:
        assert conn.execute("SELECT count(*) FROM users").fetchone() == (0,)  # no context → nothing
        assert conn.execute("SELECT count(*) FROM organizations").fetchone() == (0,)

        conn.execute("SELECT set_config('app.org_id', %s, true)", (str(org_a.id),))
        orgs = {row[0] for row in conn.execute("SELECT DISTINCT org_id FROM users").fetchall()}
        assert orgs == {org_a.id}
        assert conn.execute("SELECT count(*) FROM organizations").fetchone() == (1,)

        # Writing into another tenant is rejected by the policy's WITH CHECK.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO groups (id, org_id, name, description) VALUES (gen_random_uuid(), %s, 'x', '')",
                (str(org_b.id),),
            )
        conn.rollback()

        # Context is transaction-local: it is gone after commit.
        conn.execute("SELECT set_config('app.org_id', %s, true)", (str(org_a.id),))
        conn.commit()
        assert conn.execute("SELECT count(*) FROM users").fetchone() == (0,)


def test_runtime_role_cannot_rewrite_the_audit_log(pg: Postgres, settings: Settings) -> None:
    org = seed_org(settings)
    with psycopg.connect(pg.dsn("app_rw")) as conn:
        conn.execute("SELECT set_config('app.org_id', %s, true)", (str(org.id),))
        assert conn.execute("SELECT count(*) FROM audit_logs").fetchone()[0] >= 1  # type: ignore[index]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM audit_logs")
        conn.rollback()
        conn.execute("SELECT set_config('app.org_id', %s, true)", (str(org.id),))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE audit_logs SET action = 'tampered'")


def test_cross_tenant_lookup_functions_expose_only_auth_columns(pg: Postgres, settings: Settings) -> None:
    org = seed_org(settings)
    with psycopg.connect(pg.dsn("app_rw")) as conn:
        row = conn.execute("SELECT * FROM auth_users_by_email(%s)", (org.owner_email,)).fetchone()
        assert row is not None
        columns = [d.name for d in conn.execute("SELECT * FROM auth_users_by_email('x')").description or []]
        assert columns == ["id", "org_id", "org_slug", "org_name", "password_hash", "status", "role"]
