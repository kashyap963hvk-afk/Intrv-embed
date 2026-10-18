"""API models for the "+" connectors: accounts, browsing, and synced sources."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from app.db.models import AccountStatus, ConnectorKind, DocType, SourceStatus
from app.knowledge.schemas import DocTypeChoice


class ConnectorKindOut(BaseModel):
    kind: ConnectorKind
    label: str
    auth: Literal["oauth", "credentials", "service_account", "none"]
    available: bool
    reason: str | None
    owners_only: bool


class AccountOut(BaseModel):
    id: uuid.UUID
    kind: ConnectorKind
    name: str
    config: dict[str, Any]
    status: AccountStatus
    last_error: str | None
    org_level: bool
    last_validated_at: datetime | None
    created_at: datetime


class S3AccountIn(BaseModel):
    kind: Literal["S3_COMPATIBLE"]
    name: str = Field(min_length=1, max_length=120)
    endpoint_url: str | None = Field(default=None, max_length=500, description="Empty for AWS S3")
    region: str = Field(default="us-east-1", max_length=40)
    bucket: str = Field(min_length=3, max_length=255)
    root_prefix: str = Field(default="", max_length=500, description="Only this prefix is ever listed or read")
    force_path_style: bool = True
    access_key: str | None = Field(default=None, max_length=256)
    secret_key: str | None = Field(default=None, max_length=512)
    org_level: bool = False


class AzureAccountIn(BaseModel):
    kind: Literal["AZURE_BLOB"]
    name: str = Field(min_length=1, max_length=120)
    container: str | None = Field(default=None, max_length=63, description="Needed with a connection string")
    root_prefix: str = Field(default="", max_length=500)
    connection_string: str | None = Field(default=None, max_length=2000)
    container_sas_url: str | None = Field(default=None, max_length=2000)
    org_level: bool = False


CredentialAccountIn = Annotated[S3AccountIn | AzureAccountIn, Field(discriminator="kind")]


class GoogleOrgAccountIn(BaseModel):
    name: str = Field(default="Organization Google Drive", min_length=1, max_length=120)
    root_folder_ids: list[Annotated[str, Field(min_length=5, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")]] = Field(
        min_length=1, max_length=20, description="Folders shared with the service account"
    )


class ServiceAccountInfo(BaseModel):
    email: str | None
    configured: bool


class TestResult(BaseModel):
    ok: bool
    detail: str


class OAuthStartOut(BaseModel):
    authorize_url: str


class RemoteItemOut(BaseModel):
    id: str
    name: str
    path: str
    is_folder: bool
    size: int | None
    modified: datetime | None


class BrowseOut(BaseModel):
    items: list[RemoteItemOut]
    next_cursor: str | None


class PickerTokenOut(BaseModel):
    access_token: str
    developer_key: str | None
    client_id: str | None


class SelectedItem(BaseModel):
    id: str = Field(min_length=1, max_length=1000)
    name: str = Field(default="", max_length=500)
    path: str = Field(default="", max_length=2000)
    is_folder: bool = False


class SourceIn(BaseModel):
    kind: ConnectorKind
    account_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    items: list[SelectedItem] = Field(default_factory=list, max_length=200)
    prefix: str | None = Field(default=None, max_length=1000)
    url: str | None = Field(default=None, max_length=2000)
    doc_type: DocTypeChoice = "AUTO"
    sync_enabled: bool = False
    interval_minutes: int = Field(default=360, ge=15, le=10080)


class SourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    sync_enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=15, le=10080)


class SourceOut(BaseModel):
    id: uuid.UUID
    space_id: uuid.UUID
    account_id: uuid.UUID | None
    kind: ConnectorKind
    name: str
    selection: dict[str, Any]
    doc_type: DocType | None
    sync_enabled: bool
    interval_minutes: int
    status: SourceStatus
    last_sync_at: datetime | None
    next_sync_at: datetime | None
    last_error: str | None
    failure_count: int
    last_result: dict[str, Any]
    document_count: int
    created_by_me: bool
    created_at: datetime
