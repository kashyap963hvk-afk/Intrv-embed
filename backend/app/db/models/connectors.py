"""External storage accounts ("+" connectors) and the sources that keep spaces in sync with them."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, LargeBinary, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, str_enum
from app.db.models.knowledge import DocType


class ConnectorKind(StrEnum):
    GOOGLE_DRIVE = "GOOGLE_DRIVE"  # a member's own Drive, files chosen with the Picker (drive.file scope)
    GOOGLE_DRIVE_ORG = "GOOGLE_DRIVE_ORG"  # owner-level: folders shared with the platform service account
    S3_COMPATIBLE = "S3_COMPATIBLE"
    AZURE_BLOB = "AZURE_BLOB"
    MS_GRAPH = "MS_GRAPH"  # OneDrive / SharePoint
    DROPBOX = "DROPBOX"
    URL = "URL"


class AccountStatus(StrEnum):
    OK = "OK"
    ERROR = "ERROR"


class SourceStatus(StrEnum):
    IDLE = "IDLE"
    SYNCING = "SYNCING"
    ERROR = "ERROR"


class ConnectorAccount(IdMixin, TimestampMixin, Base):
    """Credentials for one external store. `owner_user_id` NULL means an org-level account only owners may use."""

    __tablename__ = "connector_accounts"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[ConnectorKind] = mapped_column(str_enum(ConnectorKind, "connector_kind"))
    name: Mapped[str] = mapped_column(String(120))
    # Non-secret settings (endpoint, bucket, root prefix, account email). Secrets live only in `encrypted_secret`.
    config: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"), default=dict)
    encrypted_secret: Mapped[bytes | None] = mapped_column(LargeBinary)
    status: Mapped[AccountStatus] = mapped_column(
        str_enum(AccountStatus, "connector_account_status"), default=AccountStatus.OK
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    last_validated_at: Mapped[datetime | None]


class ConnectorSource(IdMixin, TimestampMixin, Base):
    """A selection in an external store (folders, files, a prefix or a URL) imported into one space."""

    __tablename__ = "connector_sources"
    __table_args__ = (Index("ix_connector_sources_due", "sync_enabled", "next_sync_at"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    space_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_spaces.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("connector_accounts.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[ConnectorKind] = mapped_column(str_enum(ConnectorKind, "source_connector_kind"))
    name: Mapped[str] = mapped_column(String(200))
    # {"items": [{"id", "name", "path", "is_folder"}]} or {"prefix": "..."} or {"url": "..."}
    selection: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"), default=dict)
    doc_type: Mapped[DocType | None] = mapped_column(str_enum(DocType, "source_doc_type"))
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    interval_minutes: Mapped[int] = mapped_column(Integer, default=360, server_default=text("360"))
    cursor: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"), default=dict)
    status: Mapped[SourceStatus] = mapped_column(
        str_enum(SourceStatus, "connector_source_status"), default=SourceStatus.IDLE
    )
    last_sync_at: Mapped[datetime | None]
    next_sync_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    last_result: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"), default=dict)
