"""The connector contract (ARCHITECTURE §6.1) and helpers shared by every external store."""

from __future__ import annotations

import posixpath
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.core.errors import AppError
from app.db.models import ConnectorKind
from app.rag.parse import SUPPORTED_EXTENSIONS

# Google-native documents have no extension; they are exported to one of these.
GOOGLE_EXPORTS: Mapping[str, tuple[str, str]] = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xlsx",
    ),
    "application/vnd.google-apps.presentation": ("application/pdf", "pdf"),
}


class ConnectorError(AppError):
    """A failure talking to an external store. `auth=True` means the credentials need attention."""

    status_code = 502
    slug = "connector-error"
    title = "The external storage could not be reached"

    def __init__(self, detail: str, *, auth: bool = False, http_status: int | None = None) -> None:
        super().__init__(detail)
        self.auth = auth
        self.http_status = http_status


class ScopeError(ConnectorError):
    status_code = 400
    slug = "outside-connector-root"
    title = "That location is outside the configured folder"

    def __init__(self, detail: str = "That location is outside the folder this account is limited to.") -> None:
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class RemoteItem:
    id: str  # stable identifier in the remote store (object key, file id)
    name: str
    path: str  # human-readable location, for display
    is_folder: bool
    size: int | None = None
    modified: datetime | None = None
    etag: str | None = None  # changes whenever the content changes
    mime: str | None = None

    def as_selection(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "path": self.path, "is_folder": self.is_folder}


@dataclass(frozen=True, slots=True)
class Page:
    items: list[RemoteItem]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class FetchedFile:
    data: bytes
    filename: str
    mime: str | None
    etag: str | None


@dataclass(slots=True)
class ChangeSet:
    upserts: list[RemoteItem] = field(default_factory=list)  # new or changed files
    deleted: list[str] = field(default_factory=list)  # ids of files that are gone or left the selection
    cursor: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Health:
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class Selection:
    """What a source imports: chosen items (folders are recursive) or, for buckets, a prefix."""

    items: tuple[dict[str, Any], ...] = ()
    prefix: str | None = None
    url: str | None = None

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Selection:
        return cls(
            items=tuple(dict(i) for i in data.get("items", [])),
            prefix=data.get("prefix"),
            url=data.get("url"),
        )


class StorageConnector(Protocol):
    kind: ConnectorKind

    async def validate(self) -> Health: ...

    async def browse(self, path: str | None, cursor: str | None) -> Page: ...

    async def fetch(self, item: RemoteItem, max_bytes: int) -> FetchedFile: ...

    async def changes(
        self, selection: Selection, cursor: Mapping[str, Any], known: Mapping[str, str | None]
    ) -> ChangeSet:
        """Files to (re)import and ids to delete, given what is already imported (`known`: id → etag)."""
        ...


def importable(name: str, mime: str | None = None) -> bool:
    """Skip videos, archives and other formats the parsers cannot read before downloading them."""
    if mime in GOOGLE_EXPORTS:
        return True
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return extension in SUPPORTED_EXTENSIONS


def diff_snapshot(listed: Sequence[RemoteItem], known: Mapping[str, str | None]) -> ChangeSet:
    """Turn a full listing of the selection into upserts (new or changed etag) and deletions."""
    files = {item.id: item for item in listed if not item.is_folder}
    upserts = [item for item_id, item in files.items() if item_id not in known or known[item_id] != item.etag]
    deleted = [item_id for item_id in known if item_id not in files]
    return ChangeSet(upserts=upserts, deleted=deleted)


def confine(root: str, path: str | None) -> str:
    """Resolve `path` (relative to `root`) and refuse anything that escapes it. Paths use '/' and end in '/'."""
    base = root.strip("/")
    relative = (path or "").strip("/")
    if relative and (relative.startswith(("..", "/")) or "\\" in relative or "\x00" in relative):
        raise ScopeError
    joined = posixpath.normpath(posixpath.join(base, relative)) if relative else base
    joined = "" if joined == "." else joined
    if base and joined != base and not joined.startswith(base + "/"):
        raise ScopeError
    if not base and joined.startswith(".."):
        raise ScopeError
    return f"{joined}/" if joined else ""


def within(root: str, key: str) -> bool:
    base = root.strip("/")
    return not base or key == base or key.startswith(base + "/")
