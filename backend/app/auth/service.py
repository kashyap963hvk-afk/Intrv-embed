"""Authentication flows: signup, login, refresh rotation, logout, invites, password changes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import ClientMeta, record
from app.auth.schemas import AcceptInviteRequest, InvitePreview, LoginRequest, SignupRequest
from app.core.config import Settings
from app.core.email import EmailSender, OutgoingEmail
from app.core.errors import AppError, ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    new_opaque_token,
    password_needs_rehash,
    password_problems,
    verify_password,
)
from app.db.models import Organization, RefreshToken, Role, User, UserInvite, UserPolicy, UserStatus
from app.db.tenant import TenantContext, set_tenant_context

log = get_logger("app.auth")

# Two tabs refreshing at the same moment both present the same token; within this window a second
# use is treated as a benign race rather than theft.
REFRESH_REUSE_GRACE = timedelta(seconds=15)


class WeakPasswordError(AppError):
    status_code = 422
    slug = "weak-password"
    title = "Password does not meet the requirements"


class OrgChoiceRequiredError(AppError):
    status_code = 409
    slug = "organization-required"
    title = "This email belongs to several organizations; choose one"


@dataclass(frozen=True, slots=True)
class IssuedSession:
    user: User
    access_token: str
    access_expires: datetime
    refresh_token: str
    refresh_expires: datetime
    family_id: uuid.UUID


def ensure_strong_password(password: str, *, email: str, name: str) -> None:
    problems = password_problems(password, email=email, name=name)
    if problems:
        raise WeakPasswordError("Password " + "; ".join(problems) + ".", extra={"problems": problems})


async def bind_org(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID | None = None, role: str = "") -> None:
    await set_tenant_context(db, TenantContext(org_id=org_id, user_id=user_id, role=role))


async def issue_session(
    db: AsyncSession, settings: Settings, user: User, meta: ClientMeta, *, family_id: uuid.UUID | None = None
) -> IssuedSession:
    family = family_id or uuid.uuid4()
    refresh_token, refresh_hash = new_opaque_token()
    refresh_expires = datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days)
    db.add(
        RefreshToken(
            org_id=user.org_id,
            user_id=user.id,
            family_id=family,
            token_hash=refresh_hash,
            expires_at=refresh_expires,
            user_agent=meta.user_agent,
            ip=meta.ip,
        )
    )
    access_token, access_expires = create_access_token(settings, user_id=user.id, org_id=user.org_id, family_id=family)
    await db.flush()
    return IssuedSession(user, access_token, access_expires, refresh_token, refresh_expires, family)


async def revoke_families(db: AsyncSession, *, user_id: uuid.UUID, family_ids: list[uuid.UUID] | None = None) -> None:
    """Revoke the given session families of a user, or all of them when `family_ids` is None."""
    stmt = update(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
    if family_ids is not None:
        stmt = stmt.where(RefreshToken.family_id.in_(family_ids))
    await db.execute(stmt.values(revoked_at=datetime.now(UTC)))


async def _active_owner_emails(db: AsyncSession) -> list[str]:
    rows = await db.scalars(select(User.email).where(User.role == Role.OWNER, User.status == UserStatus.ACTIVE))
    return list(rows)


# --- Signup / login -------------------------------------------------------------------------------


async def signup(
    db: AsyncSession, settings: Settings, mailer: EmailSender, body: SignupRequest, meta: ClientMeta
) -> None:
    """Request membership of an org. The account stays PENDING until an owner approves it.

    Responds identically whether or not the email is already registered (no account enumeration).
    """
    ensure_strong_password(body.password, email=body.email, name=body.name)
    org = (await db.execute(text("SELECT * FROM auth_org_by_slug(:s)"), {"s": body.org_slug})).mappings().first()
    if org is None:
        raise NotFoundError("No organization with that ID. Ask your administrator for the correct one.")
    await bind_org(db, org["id"])
    if await db.scalar(select(User.id).where(User.email == body.email)) is not None:
        log.info("auth.signup_duplicate", org_id=str(org["id"]))
        return

    user = User(
        org_id=org["id"],
        email=body.email,
        name=body.name.strip(),
        password_hash=hash_password(body.password),
        role=Role.MEMBER,
        status=UserStatus.PENDING,
    )
    db.add(user)
    await db.flush()
    db.add(UserPolicy(user_id=user.id, org_id=user.org_id))
    await record(
        db, org_id=user.org_id, actor_id=user.id, action="user.signup", meta=meta, target_type="user", target_id=user.id
    )
    for owner_email in await _active_owner_emails(db):
        await mailer.send(
            OutgoingEmail(
                to=owner_email,
                subject=f"New member request for {org['name']}",
                body=f"{user.name} ({user.email}) asked to join {org['name']}.\n"
                f"Review pending members: {settings.public_app_url}/admin\n",
            )
        )


async def login(db: AsyncSession, settings: Settings, body: LoginRequest, meta: ClientMeta) -> IssuedSession:
    rows = (await db.execute(text("SELECT * FROM auth_users_by_email(:e)"), {"e": body.email})).mappings().all()
    if body.org_slug:
        rows = [r for r in rows if r["org_slug"] == body.org_slug.strip().lower()]
    if not rows:
        verify_password(None, body.password)  # same work as a real check
        raise UnauthorizedError("Invalid email or password.")

    matches = [r for r in rows if verify_password(r["password_hash"], body.password)]
    if not matches:
        raise UnauthorizedError("Invalid email or password.")
    if len(matches) > 1:
        raise OrgChoiceRequiredError(
            extra={"organizations": [{"slug": r["org_slug"], "name": r["org_name"]} for r in matches]}
        )
    row = matches[0]
    if row["status"] == UserStatus.SUSPENDED:
        raise ForbiddenError("This account is suspended. Contact your organization owner.")

    await bind_org(db, row["org_id"], row["id"], row["role"])
    user = await db.get(User, row["id"])
    if user is None:  # deleted between lookup and load
        raise UnauthorizedError("Invalid email or password.")
    if user.password_hash and password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.last_login_at = datetime.now(UTC)
    session = await issue_session(db, settings, user, meta)
    await record(
        db, org_id=user.org_id, actor_id=user.id, action="auth.login", meta=meta, target_type="user", target_id=user.id
    )
    return session


# --- Refresh / logout -----------------------------------------------------------------------------


async def refresh(db: AsyncSession, settings: Settings, refresh_token: str, meta: ClientMeta) -> IssuedSession:
    row = (
        (await db.execute(text("SELECT * FROM auth_refresh_token(:h)"), {"h": hash_token(refresh_token)}))
        .mappings()
        .first()
    )
    if row is None or row["revoked_at"] is not None:
        raise UnauthorizedError("Session has ended; please sign in again.")
    now = datetime.now(UTC)
    await bind_org(db, row["org_id"], row["user_id"])

    if row["used_at"] is not None:
        if now - row["used_at"] <= REFRESH_REUSE_GRACE:
            raise UnauthorizedError("Session was just refreshed; retry with the new session.")
        # A rotated-out token came back: assume it was stolen and end every session in the family.
        await revoke_families(db, user_id=row["user_id"], family_ids=[row["family_id"]])
        await record(
            db,
            org_id=row["org_id"],
            actor_id=row["user_id"],
            action="auth.refresh_reuse_detected",
            meta=meta,
            target_type="session",
            target_id=row["family_id"],
        )
        await db.commit()
        log.warning("auth.refresh_reuse_detected", org_id=str(row["org_id"]), user_id=str(row["user_id"]))
        raise UnauthorizedError("Session has ended; please sign in again.")
    if row["expires_at"] <= now:
        raise UnauthorizedError("Session has expired; please sign in again.")

    user = await db.get(User, row["user_id"])
    if user is None or user.status == UserStatus.SUSPENDED:
        await revoke_families(db, user_id=row["user_id"], family_ids=[row["family_id"]])
        await db.commit()
        raise UnauthorizedError("Session has ended; please sign in again.")
    await bind_org(db, user.org_id, user.id, user.role.value)
    await db.execute(update(RefreshToken).where(RefreshToken.id == row["id"]).values(used_at=now))
    return await issue_session(db, settings, user, meta, family_id=row["family_id"])


async def logout(db: AsyncSession, refresh_token: str | None) -> None:
    if not refresh_token:
        return
    row = (
        (await db.execute(text("SELECT * FROM auth_refresh_token(:h)"), {"h": hash_token(refresh_token)}))
        .mappings()
        .first()
    )
    if row is None:
        return
    await bind_org(db, row["org_id"], row["user_id"])
    await revoke_families(db, user_id=row["user_id"], family_ids=[row["family_id"]])


# --- Invites ----------------------------------------------------------------------------------------


def _invite_problem(row: dict[str, object], now: datetime) -> str | None:
    if row["revoked_at"] is not None:
        return "This invitation was revoked."
    if row["accepted_at"] is not None:
        return "This invitation was already used."
    expires = row["expires_at"]
    if isinstance(expires, datetime) and expires <= now:
        return "This invitation has expired. Ask your organization owner for a new one."
    return None


async def _load_invite(db: AsyncSession, token: str) -> dict[str, object]:
    row = (await db.execute(text("SELECT * FROM auth_invite(:h)"), {"h": hash_token(token)})).mappings().first()
    if row is None:
        raise NotFoundError("Invitation not found.")
    return dict(row)


async def preview_invite(db: AsyncSession, token: str) -> InvitePreview:
    row = await _load_invite(db, token)
    problem = _invite_problem(row, datetime.now(UTC))
    return InvitePreview(
        org_name=str(row["org_name"]),
        email=str(row["email"]),
        role=Role(str(row["role"])),
        usable=problem is None,
        reason=problem,
    )


async def accept_invite(
    db: AsyncSession, settings: Settings, body: AcceptInviteRequest, meta: ClientMeta
) -> IssuedSession:
    row = await _load_invite(db, body.token)
    problem = _invite_problem(row, datetime.now(UTC))
    if problem:
        raise ConflictError(problem)
    org_id = row["org_id"]
    email = str(row["email"])
    if not isinstance(org_id, uuid.UUID):
        raise TypeError("invalid org id")
    ensure_strong_password(body.password, email=email, name=body.name)
    await bind_org(db, org_id)

    user = await db.scalar(select(User).where(User.email == email))
    if user is not None and user.status != UserStatus.PENDING:
        raise ConflictError("An account with this email already exists; sign in instead.")
    if user is None:
        user = User(org_id=org_id, email=email, name=body.name.strip())
        db.add(user)
        await db.flush()
        db.add(UserPolicy(user_id=user.id, org_id=org_id))
    user.name = body.name.strip()
    user.password_hash = hash_password(body.password)
    user.role = Role(str(row["role"]))
    user.status = UserStatus.ACTIVE
    user.last_login_at = datetime.now(UTC)
    await db.execute(update(UserInvite).where(UserInvite.id == row["id"]).values(accepted_at=datetime.now(UTC)))
    await bind_org(db, org_id, user.id, user.role.value)
    await record(
        db,
        org_id=org_id,
        actor_id=user.id,
        action="invite.accepted",
        meta=meta,
        target_type="invite",
        target_id=str(row["id"]),
        detail={"role": user.role.value},
    )
    return await issue_session(db, settings, user, meta)


# --- Account ------------------------------------------------------------------------------------------


async def change_password(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    current_family: uuid.UUID,
    current_password: str,
    new_password: str,
    meta: ClientMeta,
) -> None:
    user = await db.get(User, user_id)
    if user is None:
        raise UnauthorizedError()
    if not verify_password(user.password_hash, current_password):
        raise ForbiddenError("Current password is incorrect.")
    ensure_strong_password(new_password, email=user.email, name=user.name)
    user.password_hash = hash_password(new_password)
    other_families = await db.scalars(
        select(RefreshToken.family_id).where(
            RefreshToken.user_id == user.id,
            RefreshToken.family_id != current_family,
            RefreshToken.revoked_at.is_(None),
        )
    )
    await revoke_families(db, user_id=user.id, family_ids=list(set(other_families)))
    await record(
        db,
        org_id=user.org_id,
        actor_id=user.id,
        action="auth.password_changed",
        meta=meta,
        target_type="user",
        target_id=user.id,
    )


async def load_me(db: AsyncSession, user_id: uuid.UUID) -> tuple[User, Organization, UserPolicy]:
    user = await db.get(User, user_id)
    if user is None:
        raise UnauthorizedError()
    org = await db.get(Organization, user.org_id)
    # Explicit query (not the relationship): a user created in this transaction has no loaded policy,
    # and lazy loading is not possible in async code.
    policy = await db.scalar(select(UserPolicy).where(UserPolicy.user_id == user.id))
    if org is None or policy is None:
        raise UnauthorizedError()
    return user, org, policy
