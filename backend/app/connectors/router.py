"""The "+" connectors API: available kinds, connected accounts (credentials or OAuth), browsing, and sources
that import a selection into a space once or keep it in sync."""

from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.audit.service import ClientMeta
from app.auth.deps import ActiveMember, Owner, SettingsDep, ValkeyDep, csrf_protect
from app.connectors import oauth
from app.connectors import service as connectors
from app.connectors.base import ConnectorError
from app.connectors.google_drive import ServiceAccountTokens
from app.connectors.registry import build_connector
from app.connectors.schemas import (
    AccountOut,
    BrowseOut,
    ConnectorKindOut,
    CredentialAccountIn,
    GoogleOrgAccountIn,
    OAuthStartOut,
    PickerTokenOut,
    RemoteItemOut,
    ServiceAccountInfo,
    SourceIn,
    SourceOut,
    SourceUpdate,
    TestResult,
)
from app.core.errors import AppError, NotFoundError
from app.db.deps import DbSession
from app.db.models import ConnectorKind, ConnectorSource
from app.knowledge import service as knowledge
from app.knowledge.deps import ResourcesDep

router = APIRouter(tags=["connectors"], dependencies=[Depends(csrf_protect)])


def _meta(request: Request) -> ClientMeta:
    return ClientMeta.from_request(request)


Meta = Annotated[ClientMeta, Depends(_meta)]


# --- kinds and accounts -------------------------------------------------------------------------------------


@router.get("/api/connectors/kinds", response_model=list[ConnectorKindOut])
async def list_kinds(_member: ActiveMember, settings: SettingsDep) -> list[ConnectorKindOut]:
    return connectors.kinds(settings)


@router.get("/api/connectors/accounts", response_model=list[AccountOut])
async def list_accounts(principal: ActiveMember, db: DbSession) -> list[AccountOut]:
    return [connectors.account_out(a) for a in await connectors.list_accounts(db, principal)]


@router.post("/api/connectors/accounts/test", response_model=TestResult, summary="Try credentials without saving")
async def try_new_account(body: CredentialAccountIn, _member: ActiveMember, resources: ResourcesDep) -> TestResult:
    try:
        return TestResult(ok=True, detail=await connectors.test_credentials(resources.connector_clients, body))
    except ConnectorError as exc:
        return TestResult(ok=False, detail=exc.detail or exc.title)


@router.post("/api/connectors/accounts", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
async def create_account(
    body: CredentialAccountIn, principal: ActiveMember, db: DbSession, resources: ResourcesDep, meta: Meta
) -> AccountOut:
    account = await connectors.create_credential_account(
        db, resources.keyring, resources.connector_clients, principal, body, meta
    )
    return connectors.account_out(account)


@router.get("/api/connectors/service-account", response_model=ServiceAccountInfo)
async def service_account(_owner: Owner, settings: SettingsDep) -> ServiceAccountInfo:
    """The email owners share Drive folders with for organization-wide sync."""
    secret = settings.google_service_account_json_base64
    if secret is None:
        return ServiceAccountInfo(email=None, configured=False)
    try:
        return ServiceAccountInfo(email=ServiceAccountTokens(secret.get_secret_value()).email, configured=True)
    except ConnectorError:
        return ServiceAccountInfo(email=None, configured=False)


@router.post("/api/connectors/accounts/google-org", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
async def create_google_org_account(
    body: GoogleOrgAccountIn, owner: Owner, db: DbSession, resources: ResourcesDep, meta: Meta
) -> AccountOut:
    account = await connectors.create_google_org_account(
        db, resources.connector_clients, owner, body.name, body.root_folder_ids, meta
    )
    return connectors.account_out(account)


@router.post("/api/connectors/accounts/{account_id}/test", response_model=TestResult)
async def check_account(
    account_id: uuid.UUID, principal: ActiveMember, db: DbSession, resources: ResourcesDep
) -> TestResult:
    account = await connectors.usable_account(db, principal, account_id)
    try:
        connector = await connectors.connector_for(db, resources.keyring, resources.connector_clients, account)
        detail = (await connector.validate()).detail
    except ConnectorError as exc:
        await connectors.mark_tested(account, exc.detail)
        return TestResult(ok=False, detail=exc.detail or exc.title)
    await connectors.mark_tested(account, None)
    return TestResult(ok=True, detail=detail)


@router.delete("/api/connectors/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(account_id: uuid.UUID, principal: ActiveMember, db: DbSession, meta: Meta) -> None:
    account = await connectors.usable_account(db, principal, account_id)
    await connectors.delete_account(db, principal, account, meta)


@router.get("/api/connectors/accounts/{account_id}/browse", response_model=BrowseOut)
async def browse(
    account_id: uuid.UUID,
    principal: ActiveMember,
    db: DbSession,
    resources: ResourcesDep,
    path: Annotated[str | None, Query(max_length=1000)] = None,
    cursor: Annotated[str | None, Query(max_length=4000)] = None,
) -> BrowseOut:
    account = await connectors.usable_account(db, principal, account_id)
    connector = await connectors.connector_for(db, resources.keyring, resources.connector_clients, account)
    page = await connector.browse(path, cursor)
    return BrowseOut(
        items=[
            RemoteItemOut(id=i.id, name=i.name, path=i.path, is_folder=i.is_folder, size=i.size, modified=i.modified)
            for i in page.items
        ],
        next_cursor=page.next_cursor,
    )


@router.get("/api/connectors/accounts/{account_id}/picker-token", response_model=PickerTokenOut)
async def picker_token(
    account_id: uuid.UUID, principal: ActiveMember, db: DbSession, resources: ResourcesDep, settings: SettingsDep
) -> PickerTokenOut:
    """A short-lived access token for the Google Picker (drive.file scope, the user's own account only)."""
    account = await connectors.usable_account(db, principal, account_id)
    if account.kind != ConnectorKind.GOOGLE_DRIVE or account.owner_user_id != principal.user_id:
        raise NotFoundError("Google Drive account not found.")
    secret = await connectors.decrypt_account_secret(db, resources.keyring, account)
    provider = oauth.oauth_providers(settings).get("google")
    if provider is None:
        raise AppError("Google Drive is not configured on this server.")

    async def save(tokens: oauth.TokenSet) -> None:
        await connectors.store_tokens(db, resources.keyring, account, tokens)

    tokens = oauth.OAuthTokens(
        resources.connector_clients.provider_http, provider, oauth.TokenSet.from_secret(secret), save
    )
    return PickerTokenOut(
        access_token=await tokens.access_token(),
        developer_key=settings.google_picker_api_key,
        client_id=settings.google_oauth_client_id,
    )


# --- OAuth -------------------------------------------------------------------------------------------------


def _provider(settings: SettingsDep, provider: str) -> oauth.OAuthProvider:
    found = oauth.oauth_providers(settings).get(provider)
    if found is None:
        raise NotFoundError("This storage provider is not configured on this server.")
    return found


@router.post("/api/connectors/oauth/{provider}/start", response_model=OAuthStartOut)
async def oauth_start(
    provider: str, principal: ActiveMember, settings: SettingsDep, valkey: ValkeyDep
) -> OAuthStartOut:
    spec = _provider(settings, provider)
    url = await oauth.start(valkey, settings, spec, org_id=principal.org_id, user_id=principal.user_id)
    return OAuthStartOut(authorize_url=url)


@router.get("/api/connectors/oauth/{provider}/callback", include_in_schema=False)
async def oauth_callback(
    provider: str,
    principal: ActiveMember,
    db: DbSession,
    settings: SettingsDep,
    valkey: ValkeyDep,
    resources: ResourcesDep,
    meta: Meta,
    state: Annotated[str, Query(max_length=200)] = "",
    code: Annotated[str | None, Query(max_length=4000)] = None,
    error: Annotated[str | None, Query(max_length=200)] = None,
) -> RedirectResponse:
    """The provider redirects the popup here; we finish sign-in and send the popup to a page that closes it."""
    spec = _provider(settings, provider)
    done = f"{settings.public_app_url.rstrip('/')}/app/knowledge/connected"

    def finish(outcome: str, detail: str) -> RedirectResponse:
        query = urlencode({"status": outcome, "provider": provider, "detail": detail[:200]})
        return RedirectResponse(f"{done}?{query}", status_code=status.HTTP_303_SEE_OTHER)

    pending = await oauth.consume_state(valkey, state) if state else None
    if pending is None or pending.provider != provider or pending.user_id != principal.user_id:
        return finish("error", "The sign-in link expired or belongs to another session; try again.")
    if error or not code:
        return finish("error", "Access was not granted.")
    try:
        tokens = await oauth.exchange_code(
            resources.connector_clients.provider_http, settings, spec, code, pending.verifier
        )
        health = await build_connector(resources.connector_clients, spec.kind, {}, tokens.to_secret()).validate()
    except ConnectorError as exc:
        return finish("error", exc.detail or "Sign-in failed.")
    await connectors.save_oauth_account(
        db,
        resources.keyring,
        principal=principal,
        kind=spec.kind,
        label=spec.label,
        tokens=tokens.to_secret(),
        connected_as=health.detail.removeprefix("Connected as ").strip(),
        meta=meta,
    )
    return finish("ok", health.detail)


# --- sources -----------------------------------------------------------------------------------------------


@router.get("/api/spaces/{space_id}/sources", response_model=list[SourceOut])
async def list_sources(
    space_id: uuid.UUID, principal: ActiveMember, db: DbSession, valkey: ValkeyDep
) -> list[SourceOut]:
    await knowledge.require_space(db, valkey, principal, space_id, write=False)
    sources = list(
        await db.scalars(
            select(ConnectorSource).where(ConnectorSource.space_id == space_id).order_by(ConnectorSource.created_at)
        )
    )
    return await connectors.source_out(db, principal, sources)


@router.post("/api/spaces/{space_id}/sources", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
async def create_source(
    space_id: uuid.UUID,
    body: SourceIn,
    principal: ActiveMember,
    db: DbSession,
    valkey: ValkeyDep,
    settings: SettingsDep,
    resources: ResourcesDep,
    meta: Meta,
) -> SourceOut:
    source = await connectors.create_source(
        db, valkey, settings, resources.sync_dispatcher, principal, space_id, body, meta
    )
    return (await connectors.source_out(db, principal, [source]))[0]


@router.get("/api/sources/{source_id}", response_model=SourceOut)
async def get_source(source_id: uuid.UUID, principal: ActiveMember, db: DbSession) -> SourceOut:
    return (await connectors.source_out(db, principal, [await connectors.get_source(db, source_id)]))[0]


@router.patch("/api/sources/{source_id}", response_model=SourceOut)
async def update_source(
    source_id: uuid.UUID, body: SourceUpdate, principal: ActiveMember, db: DbSession, resources: ResourcesDep
) -> SourceOut:
    source = await connectors.get_source(db, source_id)
    connectors.require_manager(principal, source)
    if body.name is not None:
        source.name = body.name.strip()
    if body.interval_minutes is not None:
        source.interval_minutes = body.interval_minutes
    if body.sync_enabled is not None and body.sync_enabled != source.sync_enabled:
        source.sync_enabled = body.sync_enabled
        if body.sync_enabled:
            connectors.schedule_now(db, resources.sync_dispatcher, principal, source)
        else:
            source.next_sync_at = None
    await db.flush()
    return (await connectors.source_out(db, principal, [source]))[0]


@router.post("/api/sources/{source_id}/sync", response_model=SourceOut, summary="Sync now")
async def sync_now(source_id: uuid.UUID, principal: ActiveMember, db: DbSession, resources: ResourcesDep) -> SourceOut:
    source = await connectors.get_source(db, source_id)
    connectors.require_manager(principal, source)
    connectors.schedule_now(db, resources.sync_dispatcher, principal, source)
    await db.flush()
    return (await connectors.source_out(db, principal, [source]))[0]


@router.delete("/api/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: uuid.UUID,
    principal: ActiveMember,
    db: DbSession,
    valkey: ValkeyDep,
    resources: ResourcesDep,
    meta: Meta,
    delete_documents: bool = False,
) -> None:
    source = await connectors.get_source(db, source_id)
    await connectors.delete_source(
        db,
        valkey,
        resources.storage,
        await resources.rag.store(),
        principal,
        source,
        delete_documents=delete_documents,
        meta=meta,
    )
