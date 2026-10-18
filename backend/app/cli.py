"""Operator commands.

python -m app.cli create-owner --org-slug acme --org-name "Acme" --email owner@acme.com --name "Ada"
    Creates the organization if needed and an owner. With --password-stdin the owner is created
    active with that password; otherwise an owner invitation link is printed (and emailed).

python -m app.cli selftest
    Loads the embedding/reranking models, checks the vector store and parsers; exit code 1 on any failure.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import SYSTEM_META
from app.auth.schemas import SLUG_PATTERN
from app.auth.service import ensure_strong_password
from app.core.config import get_settings
from app.core.email import EmailSender
from app.core.errors import AppError
from app.db.models import Organization, Role
from app.db.session import create_engine, create_session_factory
from app.db.tenant import TenantContext, set_tenant_context
from app.orgs.service import add_active_owner, create_invite, create_org


async def _load_org(db: AsyncSession, org_id: uuid.UUID) -> Organization:
    org = await db.get(Organization, org_id)
    if org is None:
        raise AppError("organization disappeared while loading")
    return org


async def _create_owner(args: argparse.Namespace) -> int:
    settings = get_settings()
    slug = args.org_slug.strip().lower()
    email = args.email.strip().lower()
    if not SLUG_PATTERN.match(slug):
        print("org slug must be 3-64 chars: lowercase letters, digits, hyphens", file=sys.stderr)
        return 2
    password: str | None = None
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n") if not sys.stdin.isatty() else getpass.getpass("Password: ")
        ensure_strong_password(password, email=email, name=args.name)

    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as db:
            existing = (await db.execute(text("SELECT * FROM auth_org_by_slug(:s)"), {"s": slug})).mappings().first()
            if existing is None:
                org = await create_org(db, name=args.org_name or slug, slug=slug)
                print(f"created organization {org.name!r} ({org.slug})")
            else:
                await set_tenant_context(db, TenantContext(org_id=existing["id"]))
                org = await _load_org(db, existing["id"])
                print(f"using existing organization {org.name!r} ({org.slug})")
            if password is not None:
                await add_active_owner(db, org=org, email=email, name=args.name, password=password, meta=SYSTEM_META)
                print(f"owner {email} created; sign in at {settings.public_app_url}/login")
            else:
                created = await create_invite(
                    db,
                    settings,
                    EmailSender(settings),
                    org=org,
                    email=email,
                    role=Role.OWNER,
                    invited_by=None,
                    meta=SYSTEM_META,
                )
                print(f"owner invitation for {email} (emailed: {created.email_sent}):\n{created.link}")
            await db.commit()
    except AppError as exc:
        print(f"error: {exc.detail or exc.title}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    return 0


async def _selftest() -> int:
    import httpx  # noqa: PLC0415

    from app.rag.engine import RagEngine  # noqa: PLC0415
    from app.rag.selftest import run_selftest  # noqa: PLC0415

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0)) as http:
        rag = RagEngine(get_settings(), http)
        try:
            checks = await run_selftest(rag)
        finally:
            await rag.close()
    for check in checks:
        print(f"[{'ok' if check.ok else 'FAIL'}] {check.name}: {check.detail}")
    return 0 if all(c.ok for c in checks) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    owner = sub.add_parser("create-owner", help="create an organization owner")
    owner.add_argument("--org-slug", required=True)
    owner.add_argument("--org-name", default="")
    owner.add_argument("--email", required=True)
    owner.add_argument("--name", required=True)
    owner.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    sub.add_parser("selftest", help="check models, vector store and parsers")
    args = parser.parse_args(argv)
    if args.command == "create-owner":
        return asyncio.run(_create_owner(args))
    if args.command == "selftest":
        return asyncio.run(_selftest())
    return 2


if __name__ == "__main__":
    sys.exit(main())
