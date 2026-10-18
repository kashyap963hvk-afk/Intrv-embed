"""Connector accounts and sources for the API. Every query runs in the caller's tenant context (RLS)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import ClientMeta, record
from app.auth.principal import Principal
from app.connectors.accounts import decrypt_secret, encrypt_secret, open_connector
from app.connectors.base import ScopeError, StorageConnector, within
from app.connectors.dispatch import SyncDispatcher
from app.connectors.oauth import TokenSet
from app.connectors.registry import CREDENTIAL_KINDS, OAUTH_KINDS, ConnectorClients, availability, build_connector
from app.connectors.schemas import AccountOut, AzureAccountIn, ConnectorKindOut, S3AccountIn, SourceIn, SourceOut
from app.core.config import Settings
from app.core.errors import AppError, ForbiddenError, NotFoundError
from app.core.keyring import Keyring
from app.core.ssrf import UnsafeURLError, validate_url
from app.db.deps import after_commit
from app.db.models import (
    AccountStatus,
    ConnectorAccount,
    ConnectorKind,
    ConnectorSource,
    DocType,
    Document,
    KnowledgeSpace,
    Role,
    SourceStatus,
)
from app.knowledge import service as knowledge
from app.knowledge.storage import ObjectStorage
from app.rag.store import VectorStore

LABELS = {
    ConnectorKind.GOOGLE_DRIVE: "Google Drive",
    ConnectorKind.GOOGLE_DRIVE_ORG: "Google Drive (organization)",
    ConnectorKind.S3_COMPATIBLE: "S3 / cloud bucket",
    ConnectorKind.AZURE_BLOB: "Azure Blob Storage",
    ConnectorKind.MS_GRAPH: "OneDrive / SharePoint",
    ConnectorKind.DROPBOX: "Dropbox",
    ConnectorKind.URL: "Web page (URL)",
}


def kinds(settings: Settings) -> list[ConnectorKindOut]:
    reasons = availability(settings)
    out: list[ConnectorKindOut] = []
    for kind, label in LABELS.items():
        auth = (
            "oauth"
            if kind in OAUTH_KINDS
            else "credentials"
            if kind in CREDENTIAL_KINDS
            else "service_account"
            if kind == ConnectorKind.GOOGLE_DRIVE_ORG
            else "none"
        )
        out.append(
            ConnectorKindOut(
                kind=kind,
                label=label,
                auth=auth,
                available=reasons[kind] is None,
                reason=reasons[kind],
                owners_only=kind == ConnectorKind.GOOGLE_DRIVE_ORG,
            )
        )
    return out


def account_out(account: ConnectorAccount) -> AccountOut:
    return AccountOut(
        id=account.id,
        kind=account.kind,
        name=account.name,
        config=account.config,
        status=account.status,
        last_error=account.last_error,
        org_level=account.owner_user_id is None,
        last_validated_at=account.last_validated_at,
        created_at=account.created_at,
    )


async def list_accounts(db: AsyncSession, principal: Principal) -> list[ConnectorAccount]:
    accounts = list(await db.scalars(select(ConnectorAccount).order_by(ConnectorAccount.created_at)))
    # Members never see (or use) org-level accounts: they could pull org storage into a private space.
    return [a for a in accounts if a.owner_user_id is not None or principal.role == Role.OWNER]


async def usable_account(db: AsyncSession, principal: Principal, account_id: uuid.UUID) -> ConnectorAccount:
    account = await db.get(ConnectorAccount, account_id)  # RLS: own accounts and org-level ones
    if account is None or (account.owner_user_id is None and principal.role != Role.OWNER):
        raise NotFoundError("Connected account not found.")
    return account


async def decrypt_account_secret(db: AsyncSession, keyring: Keyring, account: ConnectorAccount) -> dict[str, Any]:
    return await decrypt_secret(db, keyring, account)


async def store_tokens(db: AsyncSession, keyring: Keyring, account: ConnectorAccount, tokens: TokenSet) -> None:
    await encrypt_secret(db, keyring, account, tokens.to_secret())
    await db.flush()


async def connector_for(
    db: AsyncSession, keyring: Keyring, clients: ConnectorClients, account: ConnectorAccount
) -> StorageConnector:
    return await open_connector(db, keyring, clients, account)


def _credential_parts(body: S3AccountIn | AzureAccountIn) -> tuple[ConnectorKind, dict[str, Any], dict[str, Any]]:
    if isinstance(body, S3AccountIn):
        config = {
            "endpoint_url": (body.endpoint_url or "").strip() or None,
            "region": body.region,
            "bucket": body.bucket.strip(),
            "root_prefix": body.root_prefix.strip("/"),
            "force_path_style": body.force_path_style,
        }
        secret = {"access_key": body.access_key, "secret_key": body.secret_key} if body.access_key else {}
        return ConnectorKind.S3_COMPATIBLE, config, secret
    if not body.connection_string and not body.container_sas_url:
        raise AppError("Provide a connection string or a container SAS URL.")
    if body.connection_string and not body.container:
        raise AppError("A container name is needed with a connection string.")
    config = {"container": body.container, "root_prefix": body.root_prefix.strip("/")}
    secret = (
        {"connection_string": body.connection_string}
        if body.connection_string
        else {"container_sas_url": body.container_sas_url}
    )
    return ConnectorKind.AZURE_BLOB, config, secret


async def test_credentials(clients: ConnectorClients, body: S3AccountIn | AzureAccountIn) -> str:
    kind, config, secret = _credential_parts(body)
    health = await build_connector(clients, kind, config, secret).validate()
    return health.detail


async def create_credential_account(
    db: AsyncSession,
    keyring: Keyring,
    clients: ConnectorClients,
    principal: Principal,
    body: S3AccountIn | AzureAccountIn,
    meta: ClientMeta,
) -> ConnectorAccount:
    if body.org_level and principal.role != Role.OWNER:
        raise ForbiddenError("Only owners can add organization-level storage.")
    kind, config, secret = _credential_parts(body)
    await build_connector(clients, kind, config, secret).validate()  # never save credentials that do not work
    account = ConnectorAccount(
        id=uuid.uuid4(),
        org_id=principal.org_id,
        owner_user_id=None if body.org_level else principal.user_id,
        kind=kind,
        name=body.name.strip(),
        config=config,
        status=AccountStatus.OK,
        last_validated_at=datetime.now(UTC),
    )
    await encrypt_secret(db, keyring, account, secret)
    db.add(account)
    await db.flush()
    await _audit(db, principal, "connector.account_connected", account, meta)
    return account


async def save_oauth_account(
    db: AsyncSession,
    keyring: Keyring,
    *,
    principal: Principal,
    kind: ConnectorKind,
    label: str,
    tokens: dict[str, Any],
    connected_as: str,
    meta: ClientMeta,
) -> ConnectorAccount:
    account = ConnectorAccount(
        id=uuid.uuid4(),
        org_id=principal.org_id,
        owner_user_id=principal.user_id,
        kind=kind,
        name=f"{label} ({connected_as})" if connected_as else label,
        config={"connected_as": connected_as},
        status=AccountStatus.OK,
        last_validated_at=datetime.now(UTC),
    )
    await encrypt_secret(db, keyring, account, tokens)
    db.add(account)
    await db.flush()
    await _audit(db, principal, "connector.account_connected", account, meta)
    return account


async def create_google_org_account(
    db: AsyncSession, clients: ConnectorClients, principal: Principal, name: str, roots: list[str], meta: ClientMeta
) -> ConnectorAccount:
    config = {"root_folder_ids": roots}
    connector = build_connector(clients, ConnectorKind.GOOGLE_DRIVE_ORG, config, {})
    await connector.validate()
    for root in roots:
        await connector.browse(root, None)  # proves the folder is shared with the service account
    account = ConnectorAccount(
        id=uuid.uuid4(),
        org_id=principal.org_id,
        owner_user_id=None,
        kind=ConnectorKind.GOOGLE_DRIVE_ORG,
        name=name.strip(),
        config=config,
        status=AccountStatus.OK,
        last_validated_at=datetime.now(UTC),
    )
    db.add(account)
    await db.flush()
    await _audit(db, principal, "connector.account_connected", account, meta)
    return account


async def mark_tested(account: ConnectorAccount, error: str | None) -> None:
    account.status = AccountStatus.ERROR if error else AccountStatus.OK
    account.last_error = error
    account.last_validated_at = datetime.now(UTC)


async def delete_account(db: AsyncSession, principal: Principal, account: ConnectorAccount, meta: ClientMeta) -> None:
    if account.owner_user_id is None and principal.role != Role.OWNER:
        raise ForbiddenError("Only owners can remove organization-level storage.")
    await _audit(db, principal, "connector.account_removed", account, meta)
    await db.delete(account)  # its sources go with it (documents stay, detached)


async def _audit(
    db: AsyncSession, principal: Principal, action: str, account: ConnectorAccount, meta: ClientMeta
) -> None:
    await record(
        db,
        org_id=principal.org_id,
        actor_id=principal.user_id,
        action=action,
        meta=meta,
        target_type="connector_account",
        target_id=account.id,
        detail={"kind": account.kind.value, "name": account.name},
    )


# --- sources -----------------------------------------------------------------------------------------------


def _validate_selection(body: SourceIn, account: ConnectorAccount | None, settings: Settings) -> dict[str, Any]:
    if body.kind == ConnectorKind.URL:
        if not body.url:
            raise AppError("Enter the URL to keep in sync.")
        try:
            url = validate_url(
                body.url, allow_private=settings.allow_private_llm_endpoints, allowed_schemes=("https", "http")
            )
        except UnsafeURLError as exc:
            raise AppError(exc.detail) from exc
        return {"url": url}
    if account is None:
        raise AppError("Choose a connected account.")
    if account.kind != body.kind:
        raise AppError("The account does not match the source type.")
    if not body.items and body.prefix is None:
        raise AppError("Choose at least one file or folder.")
    if account.kind in CREDENTIAL_KINDS:
        root = str(account.config.get("root_prefix", ""))
        for key in [*(i.id for i in body.items), *([body.prefix] if body.prefix else [])]:
            if ".." in key.split("/") or not within(root, key):
                raise ScopeError
    selection: dict[str, Any] = {"items": [i.model_dump() for i in body.items]}
    if body.prefix is not None:
        selection["prefix"] = body.prefix
    return selection


async def source_out(db: AsyncSession, principal: Principal, sources: list[ConnectorSource]) -> list[SourceOut]:
    counts = dict(
        (
            await db.execute(
                select(Document.source_id, func.count())
                .where(Document.source_id.in_([s.id for s in sources]))
                .group_by(Document.source_id)
            )
        ).all()
    )
    return [
        SourceOut(
            id=s.id,
            space_id=s.space_id,
            account_id=s.account_id,
            kind=s.kind,
            name=s.name,
            selection=s.selection,
            doc_type=s.doc_type,
            sync_enabled=s.sync_enabled,
            interval_minutes=s.interval_minutes,
            status=s.status,
            last_sync_at=s.last_sync_at,
            next_sync_at=s.next_sync_at,
            last_error=s.last_error,
            failure_count=s.failure_count,
            last_result=s.last_result,
            document_count=int(counts.get(s.id, 0)),
            created_by_me=s.created_by == principal.user_id,
            created_at=s.created_at,
        )
        for s in sources
    ]


def schedule_now(db: AsyncSession, dispatcher: SyncDispatcher, principal: Principal, source: ConnectorSource) -> None:
    source.next_sync_at = datetime.now(UTC)
    if source.status == SourceStatus.ERROR:
        source.status = SourceStatus.IDLE
    org_id, user_id, source_id = principal.org_id, source.created_by, source.id

    async def enqueue() -> None:
        await dispatcher.dispatch(org_id=org_id, user_id=user_id, source_id=source_id)

    after_commit(db, enqueue)


async def create_source(
    db: AsyncSession,
    valkey: Redis,
    settings: Settings,
    dispatcher: SyncDispatcher,
    principal: Principal,
    space_id: uuid.UUID,
    body: SourceIn,
    meta: ClientMeta,
) -> ConnectorSource:
    space: KnowledgeSpace = await knowledge.require_space(db, valkey, principal, space_id, write=True)
    account = await usable_account(db, principal, body.account_id) if body.account_id else None
    if account is not None and account.owner_user_id is not None and account.owner_user_id != principal.user_id:
        raise NotFoundError("Connected account not found.")
    selection = _validate_selection(body, account, settings)
    source = ConnectorSource(
        id=uuid.uuid4(),
        org_id=principal.org_id,
        space_id=space.id,
        account_id=account.id if account else None,
        created_by=principal.user_id,
        kind=body.kind,
        name=body.name.strip(),
        selection=selection,
        doc_type=None if body.doc_type == "AUTO" else DocType(body.doc_type),
        sync_enabled=body.sync_enabled,
        interval_minutes=body.interval_minutes,
        status=SourceStatus.IDLE,
    )
    db.add(source)
    await db.flush()
    await record(
        db,
        org_id=principal.org_id,
        actor_id=principal.user_id,
        action="source.created",
        meta=meta,
        target_type="connector_source",
        target_id=source.id,
        detail={"space_id": str(space.id), "kind": body.kind.value, "sync": body.sync_enabled},
    )
    schedule_now(db, dispatcher, principal, source)  # first import right away
    return source


async def get_source(db: AsyncSession, source_id: uuid.UUID) -> ConnectorSource:
    source = await db.get(ConnectorSource, source_id)  # RLS: sources in readable spaces
    if source is None:
        raise NotFoundError("Source not found.")
    return source


def require_manager(principal: Principal, source: ConnectorSource) -> None:
    if source.created_by != principal.user_id and principal.role != Role.OWNER:
        raise ForbiddenError("Only the person who added this source (or an owner) can change it.")


async def delete_source(
    db: AsyncSession,
    valkey: Redis,
    storage: ObjectStorage,
    store: VectorStore,
    principal: Principal,
    source: ConnectorSource,
    *,
    delete_documents: bool,
    meta: ClientMeta,
) -> None:
    require_manager(principal, source)
    await knowledge.require_space(db, valkey, principal, source.space_id, write=True)
    if delete_documents:
        for doc in list(await db.scalars(select(Document).where(Document.source_id == source.id))):
            await knowledge.delete_document(db, valkey, storage, store, principal, doc, meta)
    await record(
        db,
        org_id=principal.org_id,
        actor_id=principal.user_id,
        action="source.deleted",
        meta=meta,
        target_type="connector_source",
        target_id=source.id,
        detail={"space_id": str(source.space_id), "documents_deleted": delete_documents},
    )
    await db.delete(source)
