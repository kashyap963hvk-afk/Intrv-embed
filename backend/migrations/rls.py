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


def enable_private_or_shared_rls(table: str, owner_column: str) -> None:
    """Tenant RLS plus per-user privacy for tables mixing personal rows and org-shared rows.

    Rows with `owner_column = NULL` are org-shared: everyone in the org reads them, only owners
    (app.role = 'OWNER') write them. Other rows are visible and writable only by their owner.
    """
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_private_or_shared ON {table} "
        f"USING (org_id = app_current_org() AND ({owner_column} IS NULL OR {owner_column} = app_current_user())) "
        f"WITH CHECK (org_id = app_current_org() AND ("
        f"{owner_column} = app_current_user() "
        f"OR ({owner_column} IS NULL AND current_setting('app.role', true) = 'OWNER')))"
    )
