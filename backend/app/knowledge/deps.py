"""Shared dependencies for knowledge endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.auth.deps import CurrentPrincipal, ValkeyDep
from app.core.resources import Resources
from app.db.deps import DbSession, after_commit
from app.knowledge.acl import bump_acl_version


def get_resources(request: Request) -> Resources:
    resources: Resources = request.app.state.resources
    return resources


ResourcesDep = Annotated[Resources, Depends(get_resources)]


async def bump_acl_after_mutation(
    request: Request, db: DbSession, valkey: ValkeyDep, principal: CurrentPrincipal
) -> None:
    """Any successful owner change (grants, groups, roles, spaces, members) invalidates cached permissions."""
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        org_id = principal.org_id

        async def bump() -> None:
            await bump_acl_version(valkey, org_id)

        after_commit(db, bump)
