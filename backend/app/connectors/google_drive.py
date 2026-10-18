"""Google Drive.

- GOOGLE_DRIVE: a member's own Drive through OAuth with the `drive.file` scope; files and folders are chosen in
  the Google Picker, so the app only ever sees what the user picked.
- GOOGLE_DRIVE_ORG: the platform service account (GOOGLE_SERVICE_ACCOUNT_JSON_BASE64). The owner shares folders
  with the service account's email and registers their ids as roots; browsing never leaves those roots.

Google Docs/Sheets/Slides are exported to DOCX/XLSX/PDF. Change detection compares md5 (binary files) or the
revision number (Google-native files) on each sync.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import anyio
import httpx
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import service_account

from app.connectors.base import (
    GOOGLE_EXPORTS,
    ChangeSet,
    ConnectorError,
    FetchedFile,
    Health,
    Page,
    RemoteItem,
    ScopeError,
    Selection,
    diff_snapshot,
    importable,
)
from app.connectors.http import NotFound, ProviderHttp, TokenSource
from app.db.models import ConnectorKind

API = "https://www.googleapis.com/drive/v3"
FOLDER = "application/vnd.google-apps.folder"
_FIELDS = "id,name,mimeType,size,modifiedTime,md5Checksum,version,parents,trashed"
_COMMON = {"supportsAllDrives": "true"}
MAX_LISTED = 10_000
MAX_DEPTH = 25


class ServiceAccountTokens:
    """Access tokens for the platform service account (read-only Drive scope)."""

    def __init__(self, info_base64: str) -> None:
        try:
            info = json.loads(base64.b64decode(info_base64))
        except (ValueError, json.JSONDecodeError) as exc:
            raise ConnectorError("GOOGLE_SERVICE_ACCOUNT_JSON_BASE64 is not valid base64-encoded JSON.") from exc
        self.email: str = info.get("client_email", "")
        self._credentials = service_account.Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
            info, scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
        self._lock = asyncio.Lock()
        self._expires = 0.0

    async def access_token(self, *, force_refresh: bool = False) -> str:
        async with self._lock:
            if force_refresh or not self._credentials.token or self._expires - 60 <= time.time():
                try:
                    await anyio.to_thread.run_sync(self._credentials.refresh, GoogleRequest())
                except Exception as exc:
                    raise ConnectorError(
                        f"The service account could not sign in ({type(exc).__name__}).", auth=True
                    ) from exc
                expiry = self._credentials.expiry
                self._expires = expiry.timestamp() if expiry else time.time() + 3000
            return str(self._credentials.token)


def _item(raw: Mapping[str, Any], parent_path: str = "") -> RemoteItem:
    mime = str(raw.get("mimeType", ""))
    name = str(raw.get("name", raw["id"]))
    export = GOOGLE_EXPORTS.get(mime)
    if export and not name.lower().endswith(f".{export[1]}"):
        name = f"{name}.{export[1]}"
    modified = raw.get("modifiedTime")
    return RemoteItem(
        id=str(raw["id"]),
        name=name,
        path=f"{parent_path}/{name}" if parent_path else name,
        is_folder=mime == FOLDER,
        size=int(raw["size"]) if raw.get("size") else None,
        modified=datetime.fromisoformat(modified.replace("Z", "+00:00")) if modified else None,
        etag=raw.get("md5Checksum") or (f"v{raw['version']}" if raw.get("version") else modified),
        mime=mime,
    )


class GoogleDriveConnector:
    def __init__(
        self,
        kind: ConnectorKind,
        http: httpx.AsyncClient,
        tokens: TokenSource,
        config: Mapping[str, Any],
    ) -> None:
        self.kind = kind
        self._api = ProviderHttp(http, tokens, label="Google Drive", hosts=frozenset({"www.googleapis.com"}))
        # Org mode: browsing and imports are limited to these shared folders.
        self.roots: tuple[str, ...] = (
            tuple(config.get("root_folder_ids", ())) if kind == ConnectorKind.GOOGLE_DRIVE_ORG else ()
        )

    async def validate(self) -> Health:
        about = await self._api.json("GET", f"{API}/about", params={"fields": "user(emailAddress,displayName)"})
        user = about.get("user", {})
        return Health(True, f"Connected as {user.get('emailAddress') or user.get('displayName') or 'Google account'}")

    async def _get(self, file_id: str) -> dict[str, Any]:
        return await self._api.json("GET", f"{API}/files/{file_id}", params={"fields": _FIELDS, **_COMMON})

    async def _inside_roots(self, file_id: str) -> bool:
        """Org mode: walk parents up to a registered root (bounded depth)."""
        if not self.roots:
            return True
        current = file_id
        for _ in range(MAX_DEPTH):
            if current in self.roots:
                return True
            parents = (await self._get(current)).get("parents") or []
            if not parents:
                return False
            current = str(parents[0])
        return False

    async def _children(self, folder_id: str, page_token: str | None) -> dict[str, Any]:
        params = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": f"nextPageToken,files({_FIELDS})",
            "pageSize": "500",
            "includeItemsFromAllDrives": "true",
            "orderBy": "folder,name",
            **_COMMON,
        }
        if page_token:
            params["pageToken"] = page_token
        return await self._api.json("GET", f"{API}/files", params=params)

    async def browse(self, path: str | None, cursor: str | None) -> Page:
        if path is None:
            if self.kind == ConnectorKind.GOOGLE_DRIVE_ORG:
                return Page([_item(await self._get(root)) for root in self.roots])
            # drive.file: lists exactly the files and folders this user has opened with the app.
            params = {"q": "trashed = false", "fields": f"nextPageToken,files({_FIELDS})", "pageSize": "500"}
            if cursor:
                params["pageToken"] = cursor
            body = await self._api.json("GET", f"{API}/files", params=params)
            return Page([_item(f) for f in body.get("files", [])], body.get("nextPageToken"))
        if not await self._inside_roots(path):
            raise ScopeError
        body = await self._children(path, cursor)
        return Page([_item(f) for f in body.get("files", [])], body.get("nextPageToken"))

    async def _walk(self, folder: Mapping[str, Any], listed: dict[str, RemoteItem]) -> None:
        queue: list[tuple[str, str, int]] = [(str(folder["id"]), str(folder.get("name", "")), 0)]
        while queue:
            folder_id, folder_path, depth = queue.pop()
            token: str | None = None
            while True:
                body = await self._children(folder_id, token)
                for raw in body.get("files", []):
                    item = _item(raw, folder_path)
                    if item.is_folder and depth < MAX_DEPTH:
                        queue.append((item.id, item.path, depth + 1))
                    elif not item.is_folder and importable(item.name, item.mime):
                        listed[item.id] = item
                        if len(listed) > MAX_LISTED:
                            raise ConnectorError(f"More than {MAX_LISTED} files; choose a smaller folder.")
                token = body.get("nextPageToken")
                if not token:
                    break

    async def changes(
        self, selection: Selection, cursor: Mapping[str, Any], known: Mapping[str, str | None]
    ) -> ChangeSet:
        listed: dict[str, RemoteItem] = {}
        for chosen in selection.items:
            file_id = str(chosen["id"])
            if not await self._inside_roots(file_id):
                raise ScopeError
            try:
                raw = await self._get(file_id)
            except NotFound:
                continue  # deleted or access revoked: its documents are removed below
            if raw.get("trashed"):
                continue
            item = _item(raw)
            if item.is_folder:
                await self._walk({"id": item.id, "name": item.name}, listed)
            elif importable(item.name, item.mime):
                listed[item.id] = item
        return diff_snapshot(list(listed.values()), known)

    async def fetch(self, item: RemoteItem, max_bytes: int) -> FetchedFile:
        export = GOOGLE_EXPORTS.get(item.mime or "")
        if export:
            data, _ = await self._api.download(
                f"{API}/files/{item.id}/export", max_bytes, params={"mimeType": export[0]}
            )
            return FetchedFile(data, item.name, export[0], item.etag)
        data, mime = await self._api.download(f"{API}/files/{item.id}", max_bytes, params={"alt": "media", **_COMMON})
        return FetchedFile(data, item.name, mime, item.etag)


def selection_from_picker(docs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalise Google Picker results ({id, name, mimeType}) into selection items."""
    return [
        {
            "id": str(d["id"]),
            "name": str(d.get("name", d["id"])),
            "path": str(d.get("name", "")),
            "is_folder": d.get("mimeType") == FOLDER,
        }
        for d in docs
    ]
