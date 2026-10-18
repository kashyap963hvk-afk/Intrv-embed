"""Dropbox (OAuth with PKCE, offline refresh tokens).

Items are identified by their lower-cased path, because deletions in `list_folder/continue` carry only a
path. A selected folder keeps a list_folder cursor, so later syncs read only the changes; a reset cursor
(409 `reset`) triggers a full re-list. Selected single files are re-checked by metadata on each sync.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import httpx

from app.connectors.base import (
    ChangeSet,
    ConnectorError,
    FetchedFile,
    Health,
    Page,
    RemoteItem,
    Selection,
    diff_snapshot,
    importable,
)
from app.connectors.http import ProviderHttp, TokenSource
from app.db.models import ConnectorKind

API = "https://api.dropboxapi.com/2"
CONTENT = "https://content.dropboxapi.com/2"
MAX_LISTED = 10_000
_CONFLICT = 409


def _item(raw: Mapping[str, Any]) -> RemoteItem:
    modified = raw.get("server_modified")
    return RemoteItem(
        id=str(raw.get("path_lower") or raw.get("id")),
        name=str(raw.get("name", "")),
        path=str(raw.get("path_display") or raw.get("path_lower") or ""),
        is_folder=raw.get(".tag") == "folder",
        size=int(raw["size"]) if raw.get("size") is not None else None,
        modified=datetime.fromisoformat(modified.replace("Z", "+00:00")) if modified else None,
        etag=raw.get("content_hash") or raw.get("rev"),
    )


class DropboxConnector:
    kind = ConnectorKind.DROPBOX

    def __init__(self, http: httpx.AsyncClient, tokens: TokenSource) -> None:
        self._api = ProviderHttp(
            http, tokens, label="Dropbox", hosts=frozenset({"api.dropboxapi.com", "content.dropboxapi.com"})
        )

    async def validate(self) -> Health:
        account = await self._api.json(
            "POST", f"{API}/users/get_current_account", content=b"null", headers={"Content-Type": "application/json"}
        )
        return Health(True, f"Connected as {account.get('email') or (account.get('name') or {}).get('display_name')}")

    async def browse(self, path: str | None, cursor: str | None) -> Page:
        if cursor:
            body = await self._api.json("POST", f"{API}/files/list_folder/continue", json={"cursor": cursor})
        else:
            body = await self._api.json("POST", f"{API}/files/list_folder", json={"path": path or "", "limit": 500})
        items = [_item(e) for e in body.get("entries", []) if e.get(".tag") in {"file", "folder"}]
        return Page(items, body.get("cursor") if body.get("has_more") else None)

    async def _drain(self, body: dict[str, Any], entries: list[dict[str, Any]]) -> str:
        entries.extend(body.get("entries", []))
        while body.get("has_more"):
            body = await self._api.json("POST", f"{API}/files/list_folder/continue", json={"cursor": body["cursor"]})
            entries.extend(body.get("entries", []))
            if len(entries) > MAX_LISTED * 2:
                raise ConnectorError(f"More than {MAX_LISTED} files; choose a smaller folder.")
        return str(body["cursor"])

    async def _full_folder(self, path: str) -> tuple[list[RemoteItem], str]:
        entries: list[dict[str, Any]] = []
        body = await self._api.json(
            "POST", f"{API}/files/list_folder", json={"path": path, "recursive": True, "limit": 2000}
        )
        cursor = await self._drain(body, entries)
        files = [_item(e) for e in entries if e.get(".tag") == "file" and importable(str(e.get("name", "")))]
        return files, cursor

    async def _file_changes(self, path: str, known: Mapping[str, str | None], result: ChangeSet) -> None:
        try:
            raw = await self._api.json("POST", f"{API}/files/get_metadata", json={"path": path})
        except ConnectorError as exc:
            if exc.http_status != _CONFLICT:  # 409 = path/not_found: the file is gone
                raise
            if path in known:
                result.deleted.append(path)
            return
        entry = _item(raw)
        if known.get(entry.id) != entry.etag:
            result.upserts.append(entry)

    async def _incremental(self, previous: str, in_folder: Mapping[str, str | None], result: ChangeSet) -> str | None:
        """Apply changes since `previous`; None when Dropbox reset the cursor and a full listing is needed."""
        entries: list[dict[str, Any]] = []
        try:
            body = await self._api.json("POST", f"{API}/files/list_folder/continue", json={"cursor": previous})
        except ConnectorError as exc:
            if exc.http_status == _CONFLICT:
                return None
            raise
        new_cursor = await self._drain(body, entries)
        for entry in entries:
            tag = entry.get(".tag")
            key = str(entry.get("path_lower", ""))
            if tag == "deleted":
                # A deleted folder removes everything below it.
                result.deleted.extend(k for k in in_folder if k == key or k.startswith(key + "/"))
            elif tag == "file" and importable(str(entry.get("name", ""))):
                item = _item(entry)
                if in_folder.get(item.id) != item.etag:
                    result.upserts.append(item)
        return new_cursor

    async def changes(
        self, selection: Selection, cursor: Mapping[str, Any], known: Mapping[str, str | None]
    ) -> ChangeSet:
        cursors: dict[str, str] = dict(cursor.get("folders", {}))
        result = ChangeSet()
        new_cursors: dict[str, str] = {}
        for chosen in selection.items:
            path = str(chosen["id"])
            if not chosen.get("is_folder"):
                await self._file_changes(path, known, result)
                continue
            in_folder = {k: v for k, v in known.items() if k.startswith(path.rstrip("/") + "/")}
            previous = cursors.get(path)
            if previous and (updated := await self._incremental(previous, in_folder, result)):
                new_cursors[path] = updated
                continue
            files, new_cursors[path] = await self._full_folder(path)
            folder_changes = diff_snapshot(files, in_folder)
            result.upserts.extend(folder_changes.upserts)
            result.deleted.extend(folder_changes.deleted)
        result.cursor = {"folders": new_cursors}
        result.deleted = list(dict.fromkeys(result.deleted))
        return result

    async def fetch(self, item: RemoteItem, max_bytes: int) -> FetchedFile:
        data, _ = await self._api.download(
            f"{CONTENT}/files/download",
            max_bytes,
            method="POST",
            headers={"Dropbox-API-Arg": json.dumps({"path": item.id}, ensure_ascii=True)},
        )
        return FetchedFile(data, item.name, None, item.etag)
