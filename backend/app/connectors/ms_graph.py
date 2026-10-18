"""OneDrive and SharePoint through Microsoft Graph (delegated OAuth).

Item ids are "{drive_id}/{item_id}". The first sync lists the selected folders and records a delta link per
drive (`root/delta?token=latest`); later syncs read only the changes since then and keep the files that are
inside the selected folders (delta on non-root folders is not supported for OneDrive for Business and
SharePoint, so changes are read for the whole drive and filtered by path). An expired delta link (410)
triggers a full re-list.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import quote

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
from app.connectors.http import NotFound, ProviderHttp, TokenSource
from app.db.models import ConnectorKind

API = "https://graph.microsoft.com/v1.0"
_SELECT = "id,name,size,file,folder,deleted,lastModifiedDateTime,cTag,eTag,parentReference"
MAX_LISTED = 10_000
_GONE = 410


def _split(item_id: str) -> tuple[str, str]:
    drive, _, item = item_id.partition("/")
    if not drive or not item:
        raise ConnectorError("Invalid OneDrive item reference.")
    return quote(drive, safe="!"), quote(item, safe="!")


def _folder_path(raw: Mapping[str, Any]) -> str:
    """Path of the item's parent without the drive prefix, e.g. "/Hiring/2026"."""
    path = str((raw.get("parentReference") or {}).get("path", ""))
    return path.split(":", 1)[1] if ":" in path else ""


def _item(raw: Mapping[str, Any], drive_id: str | None = None) -> RemoteItem:
    drive = drive_id or str((raw.get("parentReference") or {}).get("driveId", ""))
    modified = raw.get("lastModifiedDateTime")
    parent = _folder_path(raw)
    name = str(raw.get("name", raw["id"]))
    return RemoteItem(
        id=f"{drive}/{raw['id']}",
        name=name,
        path=f"{parent}/{name}" if parent else f"/{name}",
        is_folder="folder" in raw,
        size=int(raw["size"]) if raw.get("size") is not None else None,
        modified=datetime.fromisoformat(modified.replace("Z", "+00:00")) if modified else None,
        etag=raw.get("cTag") or raw.get("eTag"),  # cTag changes only when content changes
        mime=(raw.get("file") or {}).get("mimeType"),
    )


class GraphConnector:
    kind = ConnectorKind.MS_GRAPH

    def __init__(self, http: httpx.AsyncClient, tokens: TokenSource) -> None:
        self._api = ProviderHttp(http, tokens, label="Microsoft 365", hosts=frozenset({"graph.microsoft.com"}))

    async def validate(self) -> Health:
        me = await self._api.json("GET", f"{API}/me", params={"$select": "displayName,mail,userPrincipalName"})
        return Health(True, f"Connected as {me.get('mail') or me.get('userPrincipalName') or me.get('displayName')}")

    async def browse(self, path: str | None, cursor: str | None) -> Page:
        if cursor:
            body = await self._api.json("GET", cursor)
        elif path is None:
            body = await self._api.json(
                "GET", f"{API}/me/drive/root/children", params={"$select": _SELECT, "$top": "200"}
            )
        else:
            drive, item = _split(path)
            body = await self._api.json(
                "GET", f"{API}/drives/{drive}/items/{item}/children", params={"$select": _SELECT, "$top": "200"}
            )
        items = [_item(raw) for raw in body.get("value", [])]
        return Page(items, body.get("@odata.nextLink"))

    async def _list_folder(self, item_id: str, listed: dict[str, RemoteItem]) -> None:
        drive, item = _split(item_id)
        queue = [item]
        while queue:
            current = queue.pop()
            url: str | None = f"{API}/drives/{drive}/items/{current}/children?$select={_SELECT}&$top=200"
            while url:
                body = await self._api.json("GET", url)
                for raw in body.get("value", []):
                    entry = _item(raw, item_id.split("/", 1)[0])
                    if entry.is_folder:
                        queue.append(quote(str(raw["id"]), safe="!"))
                    elif importable(entry.name):
                        listed[entry.id] = entry
                        if len(listed) > MAX_LISTED:
                            raise ConnectorError(f"More than {MAX_LISTED} files; choose a smaller folder.")
                url = body.get("@odata.nextLink")

    async def _latest_delta_link(self, drive_id: str) -> str:
        body = await self._api.json(
            "GET", f"{API}/drives/{quote(drive_id, safe='!')}/root/delta", params={"token": "latest"}
        )
        link = body.get("@odata.deltaLink")
        if not link:
            raise ConnectorError("Microsoft Graph did not return a delta link.")
        return str(link)

    async def _full(self, selection: Selection, known: Mapping[str, str | None]) -> ChangeSet:
        listed: dict[str, RemoteItem] = {}
        drives: set[str] = set()
        for chosen in selection.items:
            item_id = str(chosen["id"])
            drives.add(item_id.split("/", 1)[0])
            if chosen.get("is_folder"):
                await self._list_folder(item_id, listed)
                continue
            drive, item = _split(item_id)
            try:
                raw = await self._api.json("GET", f"{API}/drives/{drive}/items/{item}", params={"$select": _SELECT})
            except NotFound:
                continue
            entry = _item(raw, item_id.split("/", 1)[0])
            if importable(entry.name):
                listed[entry.id] = entry
        changes = diff_snapshot(list(listed.values()), known)
        changes.cursor = {"delta": {drive: await self._latest_delta_link(drive) for drive in drives}}
        return changes

    def _selected(self, entry: RemoteItem, selection: Selection) -> bool:
        for chosen in selection.items:
            chosen_id = str(chosen["id"])
            if entry.id == chosen_id:
                return True
            if chosen.get("is_folder") and entry.id.split("/", 1)[0] == chosen_id.split("/", 1)[0]:
                folder = str(chosen.get("path", "")).rstrip("/")
                if folder and entry.path.startswith(folder + "/"):
                    return True
        return False

    def _apply(
        self,
        raw: Mapping[str, Any],
        drive: str,
        selection: Selection,
        known: Mapping[str, str | None],
        result: ChangeSet,
    ) -> None:
        entry = _item(raw, drive)
        if "deleted" in raw:
            if entry.id in known:
                result.deleted.append(entry.id)
        elif entry.is_folder or not importable(entry.name):
            return
        elif self._selected(entry, selection):
            if known.get(entry.id) != entry.etag:
                result.upserts.append(entry)
        elif entry.id in known:
            result.deleted.append(entry.id)  # moved out of the selected folders

    async def changes(
        self, selection: Selection, cursor: Mapping[str, Any], known: Mapping[str, str | None]
    ) -> ChangeSet:
        links: dict[str, str] = dict(cursor.get("delta", {}))
        drives = {str(c["id"]).split("/", 1)[0] for c in selection.items}
        if not links or set(links) != drives:
            return await self._full(selection, known)
        result = ChangeSet()
        for drive, link in links.items():
            url: str | None = link
            while url:
                try:
                    body = await self._api.json("GET", url)
                except ConnectorError as exc:
                    if exc.http_status == _GONE:
                        return await self._full(selection, known)  # delta token expired: start over
                    raise
                for raw in body.get("value", []):
                    self._apply(raw, drive, selection, known, result)
                url = body.get("@odata.nextLink")
                if body.get("@odata.deltaLink"):
                    links[drive] = str(body["@odata.deltaLink"])
        result.cursor = {"delta": links}
        result.deleted = list(dict.fromkeys(result.deleted))
        return result

    async def fetch(self, item: RemoteItem, max_bytes: int) -> FetchedFile:
        drive, item_id = _split(item.id)
        data, mime = await self._api.download(f"{API}/drives/{drive}/items/{item_id}/content", max_bytes)
        return FetchedFile(data, item.name, item.mime or mime, item.etag)
