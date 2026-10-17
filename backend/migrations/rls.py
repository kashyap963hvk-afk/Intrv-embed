"""Helpers for Row-Level Security in migrations."""

from __future__ import annotations

from alembic import op


def enable_tenant_rls(table: str) -> None:
    """Enable + FORCE RLS on a tenant table and restrict every row to the current org (app.org_id).

    FORCE applies the policy to the table owner too; only roles with BYPASSRLS (app_owner, used by
    migrations and SECURITY DEFINER auth lookups) skip it.
    """
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (org_id = app_current_org()) WITH CHECK (org_id = app_current_org())"
    )
