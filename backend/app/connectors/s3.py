"""S3-compatible buckets: AWS S3, Cloudflare R2, Backblaze B2, Wasabi, DigitalOcean Spaces, Ceph, GCS (HMAC),
SeaweedFS, MinIO. Change detection by ETag; listing is confined to the account's root prefix."""

from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from typing import Any

import anyio
import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from mypy_boto3_s3 import S3Client

from app.connectors.base import (
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
    under_root,
    within,
)
from app.connectors.net import check_endpoint
from app.db.models import ConnectorKind

MAX_LISTED = 10_000  # objects per source; larger buckets should be split into several prefixes
_AUTH_CODES = frozenset({"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch", "ExpiredToken"})


def _error(exc: Exception) -> ConnectorError:
    if isinstance(exc, ClientError):
        code = str(exc.response.get("Error", {}).get("Code", ""))
        message = str(exc.response.get("Error", {}).get("Message", "")) or code
        return ConnectorError(f"S3 error {code}: {message}", auth=code in _AUTH_CODES)
    return ConnectorError(f"S3 is unreachable ({type(exc).__name__}).")


class S3Connector:
    kind = ConnectorKind.S3_COMPATIBLE

    def __init__(self, config: Mapping[str, Any], secret: Mapping[str, Any], *, allow_private: bool) -> None:
        self.endpoint: str | None = config.get("endpoint_url") or None
        self.region: str = config.get("region") or "us-east-1"
        self.bucket: str = config["bucket"]
        self.root: str = (config.get("root_prefix") or "").strip("/")
        self.path_style: bool = bool(config.get("force_path_style", True))
        self._access = secret.get("access_key")
        self._secret = secret.get("secret_key")
        self._allow_private = allow_private
        self._client: S3Client | None = None

    async def _s3(self) -> S3Client:
        if self.endpoint:
            await check_endpoint(self.endpoint, allow_private=self._allow_private)
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint,
                region_name=self.region,
                aws_access_key_id=self._access,
                aws_secret_access_key=self._secret,
                config=BotoConfig(
                    signature_version="s3v4",
                    s3={"addressing_style": "path" if self.path_style else "auto"},
                    connect_timeout=5,
                    read_timeout=30,
                    retries={"max_attempts": 3, "mode": "standard"},
                ),
            )
        return self._client

    def _item(self, obj: Mapping[str, Any]) -> RemoteItem:
        key = str(obj["Key"])
        return RemoteItem(
            id=key,
            name=key.rsplit("/", 1)[-1],
            path=key,
            is_folder=False,
            size=int(obj.get("Size", 0)),
            modified=obj.get("LastModified"),
            etag=str(obj.get("ETag", "")).strip('"') or None,
        )

    async def validate(self) -> Health:
        client = await self._s3()
        try:
            await anyio.to_thread.run_sync(
                lambda: client.list_objects_v2(Bucket=self.bucket, Prefix=under_root(self.root, None), MaxKeys=1)
            )
        except (ClientError, BotoCoreError) as exc:
            raise _error(exc) from exc
        where = f"s3://{self.bucket}/{self.root}" if self.root else f"s3://{self.bucket}"
        return Health(True, f"Connected to {where}")

    async def browse(self, path: str | None, cursor: str | None) -> Page:
        prefix = under_root(self.root, path)
        client = await self._s3()
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix, "Delimiter": "/", "MaxKeys": 500}
        if cursor:
            kwargs["ContinuationToken"] = cursor
        try:
            page = await anyio.to_thread.run_sync(lambda: client.list_objects_v2(**kwargs))
        except (ClientError, BotoCoreError) as exc:
            raise _error(exc) from exc
        folders = [
            RemoteItem(
                id=p["Prefix"], name=p["Prefix"].rstrip("/").rsplit("/", 1)[-1], path=p["Prefix"], is_folder=True
            )
            for p in page.get("CommonPrefixes", [])
            if within(self.root, p["Prefix"])
        ]
        files = [
            self._item(obj)
            for obj in page.get("Contents", [])
            if within(self.root, str(obj["Key"])) and not str(obj["Key"]).endswith("/")
        ]
        return Page(folders + files, page.get("NextContinuationToken"))

    async def _list_all(self, prefix: str) -> list[RemoteItem]:
        client = await self._s3()

        def run() -> list[RemoteItem]:
            items: list[RemoteItem] = []
            for page in client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = str(obj["Key"])
                    if key.endswith("/") or not within(self.root, key) or not importable(key):
                        continue
                    items.append(self._item(obj))
                    if len(items) > MAX_LISTED:
                        raise ConnectorError(
                            f"More than {MAX_LISTED} files under {prefix!r}; choose a narrower prefix."
                        )
            return items

        try:
            return await anyio.to_thread.run_sync(run)
        except (ClientError, BotoCoreError) as exc:
            raise _error(exc) from exc

    async def changes(
        self, selection: Selection, cursor: Mapping[str, Any], known: Mapping[str, str | None]
    ) -> ChangeSet:
        prefixes = [selection.prefix] if selection.prefix is not None else []
        prefixes += [str(i["id"]) for i in selection.items if i.get("is_folder")]
        files = [str(i["id"]) for i in selection.items if not i.get("is_folder")]
        listed: dict[str, RemoteItem] = {}
        for prefix in prefixes:
            for item in await self._list_all(under_root(self.root, prefix)):
                listed[item.id] = item
        client = await self._s3()
        for key in files:
            if not within(self.root, key):
                raise ScopeError
            try:
                head = await anyio.to_thread.run_sync(partial(client.head_object, Bucket=self.bucket, Key=key))
            except ClientError as exc:
                if str(exc.response.get("Error", {}).get("Code")) in {"404", "NoSuchKey", "NotFound"}:
                    continue  # deleted remotely
                raise _error(exc) from exc
            listed[key] = self._item(
                {"Key": key, "Size": head["ContentLength"], "ETag": head["ETag"], "LastModified": head["LastModified"]}
            )
        return diff_snapshot(list(listed.values()), known)

    async def fetch(self, item: RemoteItem, max_bytes: int) -> FetchedFile:
        if not within(self.root, item.id):
            raise ScopeError
        client = await self._s3()

        def run() -> FetchedFile:
            obj = client.get_object(Bucket=self.bucket, Key=item.id)
            if int(obj.get("ContentLength", 0)) > max_bytes:
                raise ConnectorError(f"{item.name} is larger than the upload limit.")
            data = obj["Body"].read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ConnectorError(f"{item.name} is larger than the upload limit.")
            return FetchedFile(data, item.name, obj.get("ContentType"), str(obj.get("ETag", "")).strip('"') or None)

        try:
            return await anyio.to_thread.run_sync(run)
        except (ClientError, BotoCoreError) as exc:
            raise _error(exc) from exc
