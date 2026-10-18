"""Azure Blob Storage container, by connection string or container SAS URL, confined to a root prefix."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import anyio
from azure.core.exceptions import AzureError, ClientAuthenticationError, HttpResponseError, ResourceNotFoundError
from azure.storage.blob import BlobPrefix, BlobProperties, ContainerClient

from app.connectors.base import (
    ChangeSet,
    ConnectorError,
    FetchedFile,
    Health,
    Page,
    RemoteItem,
    ScopeError,
    Selection,
    confine,
    diff_snapshot,
    importable,
    within,
)
from app.connectors.net import check_endpoint
from app.db.models import ConnectorKind

MAX_LISTED = 10_000


def _error(exc: Exception) -> ConnectorError:
    if isinstance(exc, ClientAuthenticationError) or (
        isinstance(exc, HttpResponseError) and exc.status_code in {401, 403}
    ):
        return ConnectorError("Azure rejected the credentials (check the connection string or SAS token).", auth=True)
    if isinstance(exc, ResourceNotFoundError):
        return ConnectorError("The Azure container was not found.")
    return ConnectorError(f"Azure Blob Storage error ({type(exc).__name__}).")


class AzureBlobConnector:
    kind = ConnectorKind.AZURE_BLOB

    def __init__(self, config: Mapping[str, Any], secret: Mapping[str, Any], *, allow_private: bool) -> None:
        self.root: str = (config.get("root_prefix") or "").strip("/")
        connection_string = secret.get("connection_string")
        sas_url = secret.get("container_sas_url")
        if connection_string:
            self._client = ContainerClient.from_connection_string(connection_string, config["container"])
        elif sas_url:
            self._client = ContainerClient.from_container_url(sas_url)
        else:
            raise ConnectorError("Provide a connection string or a container SAS URL.")
        self._allow_private = allow_private

    async def _container(self) -> ContainerClient:
        await check_endpoint(self._client.url, allow_private=self._allow_private)
        return self._client

    @staticmethod
    def _item(blob: BlobProperties) -> RemoteItem:
        name = str(blob.name)
        return RemoteItem(
            id=name,
            name=name.rsplit("/", 1)[-1],
            path=name,
            is_folder=False,
            size=int(blob.size or 0),
            modified=blob.last_modified,
            etag=str(blob.etag).strip('"') if blob.etag else None,
            mime=blob.content_settings.content_type if blob.content_settings else None,
        )

    async def validate(self) -> Health:
        container = await self._container()
        try:
            await anyio.to_thread.run_sync(container.get_container_properties)
        except AzureError as exc:
            raise _error(exc) from exc
        return Health(True, f"Connected to container {container.container_name}")

    async def browse(self, path: str | None, cursor: str | None) -> Page:
        prefix = confine(self.root, path)
        container = await self._container()

        def run() -> Page:
            pages = container.walk_blobs(name_starts_with=prefix or None, delimiter="/").by_page(cursor)
            page = next(pages, None)
            items: list[RemoteItem] = []
            for entry in page or []:
                if isinstance(entry, BlobPrefix):
                    name = str(entry.name)
                    if within(self.root, name):
                        items.append(
                            RemoteItem(id=name, name=name.rstrip("/").rsplit("/", 1)[-1], path=name, is_folder=True)
                        )
                elif within(self.root, str(entry.name)):
                    items.append(self._item(entry))
            # by_page() is typed as a plain iterator but is a pager carrying the next continuation token.
            return Page(items, getattr(pages, "continuation_token", None))

        try:
            return await anyio.to_thread.run_sync(run)
        except AzureError as exc:
            raise _error(exc) from exc

    async def changes(
        self, selection: Selection, cursor: Mapping[str, Any], known: Mapping[str, str | None]
    ) -> ChangeSet:
        prefixes = [selection.prefix] if selection.prefix is not None else []
        prefixes += [str(i["id"]) for i in selection.items if i.get("is_folder")]
        wanted_files = {str(i["id"]) for i in selection.items if not i.get("is_folder")}
        for key in wanted_files:
            if not within(self.root, key):
                raise ScopeError
        container = await self._container()

        def run() -> list[RemoteItem]:
            listed: dict[str, RemoteItem] = {}
            for prefix in prefixes:
                start = confine(self.root, prefix.removeprefix(self.root).lstrip("/"))
                for blob in container.list_blobs(name_starts_with=start or None):
                    if within(self.root, blob.name) and importable(blob.name):
                        listed[blob.name] = self._item(blob)
                        if len(listed) > MAX_LISTED:
                            raise ConnectorError(f"More than {MAX_LISTED} files; choose a narrower folder.")
            for key in wanted_files - set(listed):
                try:
                    listed[key] = self._item(container.get_blob_client(key).get_blob_properties())
                except ResourceNotFoundError:
                    continue
            return list(listed.values())

        try:
            return diff_snapshot(await anyio.to_thread.run_sync(run), known)
        except AzureError as exc:
            raise _error(exc) from exc

    async def fetch(self, item: RemoteItem, max_bytes: int) -> FetchedFile:
        if not within(self.root, item.id):
            raise ScopeError
        if item.size is not None and item.size > max_bytes:
            raise ConnectorError(f"{item.name} is larger than the upload limit.")
        container = await self._container()

        def run() -> FetchedFile:
            downloader = container.download_blob(item.id, offset=0, length=max_bytes + 1)
            data = downloader.readall()
            if len(data) > max_bytes:
                raise ConnectorError(f"{item.name} is larger than the upload limit.")
            props = downloader.properties
            mime = props.content_settings.content_type if props.content_settings else None
            return FetchedFile(data, item.name, mime, str(props.etag).strip('"') if props.etag else None)

        try:
            return await anyio.to_thread.run_sync(run)
        except AzureError as exc:
            raise _error(exc) from exc
