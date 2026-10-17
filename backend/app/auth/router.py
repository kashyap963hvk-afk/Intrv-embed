"""Authentication endpoints (/api/auth)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.audit.service import ClientMeta
from app.auth import service
from app.auth.cookies import (
    REFRESH_COOKIE,
    clear_session_cookies,
    issue_csrf_cookie,
    set_session_cookies,
)
from app.auth.deps import CurrentPrincipal, SettingsDep, ValkeyDep, csrf_protect
from app.auth.schemas import (
    AcceptInviteRequest,
    ChangePasswordRequest,
    CsrfResponse,
    InvitePreview,
    LoginRequest,
    MeResponse,
    OrgOut,
    PolicyOut,
    RegisterOrgRequest,
    SignupAccepted,
    SignupRequest,
    UserOut,
)
from app.core.email import EmailSender
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.ratelimit import enforce_rate_limit
from app.db.deps import DbSession
from app.orgs.service import add_active_owner, create_org

router = APIRouter(prefix="/api/auth", tags=["auth"], dependencies=[Depends(csrf_protect)])

_LOGIN_WINDOW_S = 15 * 60


def get_mailer(request: Request) -> EmailSender:
    mailer: EmailSender = request.app.state.mailer
    return mailer


MailerDep = Annotated[EmailSender, Depends(get_mailer)]


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def _me(db: DbSession, user_id: uuid.UUID) -> MeResponse:
    user, org, policy = await service.load_me(db, user_id)
    return MeResponse(
        user=UserOut.model_validate(user),
        org=OrgOut.model_validate(org),
        policy=PolicyOut.model_validate(policy),
    )


def _apply_session(response: Response, settings: SettingsDep, session: service.IssuedSession) -> None:
    set_session_cookies(
        response,
        settings,
        access_token=session.access_token,
        access_expires=session.access_expires,
        refresh_token=session.refresh_token,
        refresh_expires=session.refresh_expires,
    )


@router.get("/csrf", response_model=CsrfResponse, summary="Issue a CSRF token (cookie + body)")
async def csrf(response: Response, settings: SettingsDep) -> CsrfResponse:
    return CsrfResponse(csrf_token=issue_csrf_cookie(response, settings))


@router.post(
    "/signup", response_model=SignupAccepted, status_code=status.HTTP_202_ACCEPTED, summary="Request to join an org"
)
async def signup(
    body: SignupRequest,
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    valkey: ValkeyDep,
    mailer: MailerDep,
) -> SignupAccepted:
    await enforce_rate_limit(valkey, f"signup:ip:{_client_key(request)}", limit=10, window_s=3600)
    await service.signup(db, settings, mailer, body, ClientMeta.from_request(request))
    return SignupAccepted(
        message="Request received. An organization owner must approve your account before you can use the app."
    )


@router.post("/register-org", response_model=MeResponse, status_code=status.HTTP_201_CREATED)
async def register_org(
    body: RegisterOrgRequest,
    request: Request,
    response: Response,
    db: DbSession,
    settings: SettingsDep,
    valkey: ValkeyDep,
) -> MeResponse:
    """Self-serve organization creation (only when ALLOW_ORG_SIGNUP=true). The creator becomes its owner."""
    if not settings.allow_org_signup:
        raise ForbiddenError("Self-serve organization signup is disabled on this server.")
    await enforce_rate_limit(valkey, f"register-org:ip:{_client_key(request)}", limit=5, window_s=3600)
    service.ensure_strong_password(body.password, email=body.email, name=body.name)
    meta = ClientMeta.from_request(request)
    org = await create_org(db, name=body.org_name, slug=body.org_slug)
    user = await add_active_owner(db, org=org, email=body.email, name=body.name, password=body.password, meta=meta)
    await service.bind_org(db, org.id, user.id, user.role.value)
    session = await service.issue_session(db, settings, user, meta)
    _apply_session(response, settings, session)
    return await _me(db, user.id)


@router.post("/login", response_model=MeResponse, summary="Sign in (sets httpOnly session cookies)")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession,
    settings: SettingsDep,
    valkey: ValkeyDep,
) -> MeResponse:
    await enforce_rate_limit(valkey, f"login:ip:{_client_key(request)}", limit=30, window_s=_LOGIN_WINDOW_S)
    await enforce_rate_limit(valkey, f"login:email:{body.email}", limit=10, window_s=_LOGIN_WINDOW_S)
    session = await service.login(db, settings, body, ClientMeta.from_request(request))
    _apply_session(response, settings, session)
    return await _me(db, session.user.id)


@router.post("/refresh", status_code=status.HTTP_204_NO_CONTENT, summary="Rotate the session cookies")
async def refresh(request: Request, response: Response, db: DbSession, settings: SettingsDep) -> None:
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise UnauthorizedError("No session; please sign in.")
    session = await service.refresh(db, settings, token, ClientMeta.from_request(request))
    _apply_session(response, settings, session)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: DbSession, settings: SettingsDep) -> None:
    await service.logout(db, request.cookies.get(REFRESH_COOKIE))
    clear_session_cookies(response, settings)


@router.get("/me", response_model=MeResponse)
async def me(principal: CurrentPrincipal, db: DbSession) -> MeResponse:
    return await _me(db, principal.user_id)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT, summary="Change password (ends other sessions)")
async def change_password(
    body: ChangePasswordRequest, request: Request, principal: CurrentPrincipal, db: DbSession, valkey: ValkeyDep
) -> None:
    await enforce_rate_limit(valkey, f"password:user:{principal.user_id}", limit=10, window_s=3600)
    await service.change_password(
        db,
        user_id=principal.user_id,
        current_family=principal.family_id,
        current_password=body.current_password,
        new_password=body.new_password,
        meta=ClientMeta.from_request(request),
    )


@router.get("/invites/{token}", response_model=InvitePreview, summary="Show who an invitation is for")
async def preview_invite(token: str, request: Request, db: DbSession, valkey: ValkeyDep) -> InvitePreview:
    await enforce_rate_limit(valkey, f"invite-preview:ip:{_client_key(request)}", limit=60, window_s=3600)
    return await service.preview_invite(db, token)


@router.post("/invites/accept", response_model=MeResponse, summary="Accept an invitation and sign in")
async def accept_invite(
    body: AcceptInviteRequest,
    request: Request,
    response: Response,
    db: DbSession,
    settings: SettingsDep,
    valkey: ValkeyDep,
) -> MeResponse:
    await enforce_rate_limit(valkey, f"invite-accept:ip:{_client_key(request)}", limit=20, window_s=3600)
    session = await service.accept_invite(db, settings, body, ClientMeta.from_request(request))
    _apply_session(response, settings, session)
    return await _me(db, session.user.id)
