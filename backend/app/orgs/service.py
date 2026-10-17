"""Organization creation and first-owner bootstrap."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.service import SYSTEM_META, ClientMeta, record
from app.core.config import Settings
from app.core.email import EmailSender, OutgoingEmail
from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.core.security import hash_password, new_opaque_token
from app.db.models import Organization, Role, User, UserInvite, UserPolicy, UserStatus
from app.db.tenant import TenantContext, set_tenant_context

log = get_logger("app.orgs")


@dataclass(frozen=True, slots=True)
class CreatedInvite:
    invite: UserInvite
    link: str
    email_sent: bool


def invite_link(settings: Settings, token: str) -> str:
    return f"{settings.public_app_url.rstrip('/')}/invite/{token}"


async def create_invite(
    db: AsyncSession,
    settings: Settings,
    mailer: EmailSender,
    *,
    org: Organization,
    email: str,
    role: Role,
    invited_by: uuid.UUID | None,
    meta: ClientMeta,
) -> CreatedInvite:
    """Create (or replace) the pending invite for `email` and email the link. Requires the org's tenant context."""
    now = datetime.now(UTC)
    await db.execute(
        update(UserInvite)
        .where(UserInvite.email == email, UserInvite.accepted_at.is_(None), UserInvite.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    token, token_hash = new_opaque_token()
    invite = UserInvite(
        org_id=org.id,
        email=email,
        role=role,
        token_hash=token_hash,
        invited_by=invited_by,
        expires_at=now + timedelta(hours=settings.invite_ttl_hours),
    )
    db.add(invite)
    await db.flush()
    link = invite_link(settings, token)
    await record(
        db,
        org_id=org.id,
        actor_id=invited_by,
        action="invite.created",
        meta=meta,
        target_type="invite",
        target_id=invite.id,
        detail={"role": role.value},
    )
    sent = await mailer.send(
        OutgoingEmail(
            to=email,
            subject=f"You're invited to {org.name} on Intrv-embed",
            body=f"You have been invited to join {org.name} as {role.value.lower()}.\n\n"
            f"Accept the invitation (valid for {settings.invite_ttl_hours} hours):\n{link}\n",
        )
    )
    return CreatedInvite(invite=invite, link=link, email_sent=sent)


async def org_slug_taken(db: AsyncSession, slug: str) -> bool:
    row = (await db.execute(text("SELECT id FROM auth_org_by_slug(:s)"), {"s": slug})).first()
    return row is not None


async def create_org(db: AsyncSession, *, name: str, slug: str) -> Organization:
    """Create an organization and bind the session to it. Raises ConflictError if the slug is taken."""
    if await org_slug_taken(db, slug):
        raise ConflictError("That organization ID is already taken.")
    org = Organization(id=uuid.uuid4(), name=name.strip(), slug=slug)
    await set_tenant_context(db, TenantContext(org_id=org.id))
    db.add(org)
    await db.flush()
    return org


async def add_active_owner(
    db: AsyncSession, *, org: Organization, email: str, name: str, password: str, meta: ClientMeta
) -> User:
    """Create an ACTIVE owner with a password (CLI / self-serve org registration). Requires tenant context."""
    if await db.scalar(select(User.id).where(User.email == email)) is not None:
        raise ConflictError("A user with this email already exists in the organization.")
    user = User(
        org_id=org.id,
        email=email,
        name=name.strip(),
        password_hash=hash_password(password),
        role=Role.OWNER,
        status=UserStatus.ACTIVE,
    )
    db.add(user)
    await db.flush()
    db.add(UserPolicy(user_id=user.id, org_id=org.id))
    await record(
        db,
        org_id=org.id,
        actor_id=user.id,
        action="org.owner_created",
        meta=meta,
        target_type="user",
        target_id=user.id,
    )
    return user


async def bootstrap_first_owner(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings, mailer: EmailSender
) -> None:
    """On an empty database, create the bootstrap org and email an owner invite to OWNER_BOOTSTRAP_EMAIL.

    Safe to run from several processes at once: the unique org slug lets exactly one of them win.
    """
    email = (settings.owner_bootstrap_email or "").strip().lower()
    if not email:
        return
    async with session_factory() as db:
        count = await db.scalar(text("SELECT auth_org_count()"))
        if count:
            return
        try:
            org = await create_org(db, name=settings.owner_bootstrap_org_name, slug=settings.owner_bootstrap_org_slug)
            await record(
                db,
                org_id=org.id,
                actor_id=None,
                action="org.bootstrapped",
                meta=SYSTEM_META,
                target_type="org",
                target_id=org.id,
            )
            created = await create_invite(
                db, settings, mailer, org=org, email=email, role=Role.OWNER, invited_by=None, meta=SYSTEM_META
            )
            await db.commit()
        except (IntegrityError, ConflictError):
            await db.rollback()
            return
    # The link is the only way in on a fresh install without SMTP, so it is logged deliberately.
    log.warning(
        "org.bootstrap_owner_invite",
        org_slug=settings.owner_bootstrap_org_slug,
        email=email,
        invite_link=created.link,
        email_sent=created.email_sent,
    )
