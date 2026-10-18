"""Original files in S3-compatible object storage, keyed by org/space/document/version."""

from __future__ import annotations

import uuid
from typing import BinaryIO

import anyio
from mypy_boto3_s3 import S3Client


def object_key(org_id: uuid.UUID, space_id: uuid.UUID, document_id: uuid.UUID, version: int, extension: str) -> str:
    safe_ext = "".join(c for c in extension.lower() if c.isalnum())[:10] or "bin"
    return f"org/{org_id}/space/{space_id}/doc/{document_id}/v{version}/original.{safe_ext}"


def space_prefix(org_id: uuid.UUID, space_id: uuid.UUID) -> str:
    return f"org/{org_id}/space/{space_id}/"


def document_prefix(org_id: uuid.UUID, space_id: uuid.UUID, document_id: uuid.UUID) -> str:
    return f"org/{org_id}/space/{space_id}/doc/{document_id}/"


class ObjectStorage:
    def __init__(self, client: S3Client, bucket: str, *, presign_client: S3Client | None = None) -> None:
        self._client = client
        self._bucket = bucket
        # Signing is offline; a separate client only changes the host written into links for browsers.
        self._presign_client = presign_client or client

    async def put(self, key: str, body: BinaryIO, content_type: str) -> None:
        await anyio.to_thread.run_sync(
            lambda: self._client.upload_fileobj(body, self._bucket, key, ExtraArgs={"ContentType": content_type})
        )

    async def get(self, key: str) -> bytes:
        def read() -> bytes:
            return self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()

        return await anyio.to_thread.run_sync(read)

    async def presign_get(self, key: str, *, filename: str, expires_s: int = 300) -> str:
        safe = "".join(c for c in filename if c.isalnum() or c in "._- ")[:150] or "download"
        return await anyio.to_thread.run_sync(
            lambda: self._presign_client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                    "ResponseContentDisposition": f'attachment; filename="{safe}"',
                },
                ExpiresIn=expires_s,
            )
        )

    async def delete_prefix(self, prefix: str, *, keep: frozenset[str] = frozenset()) -> int:
        """Delete every object under `prefix` except the keys in `keep`."""

        def run() -> int:
            deleted = 0
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                keys = [{"Key": obj["Key"]} for obj in page.get("Contents", []) if obj["Key"] not in keep]
                if keys:
                    self._client.delete_objects(Bucket=self._bucket, Delete={"Objects": keys})  # type: ignore[typeddict-item]
                    deleted += len(keys)
            return deleted

        return await anyio.to_thread.run_sync(run)
