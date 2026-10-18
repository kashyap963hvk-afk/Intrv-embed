"""Live document status events: workers publish to Valkey, the SSE endpoint relays to readers of the space.

Payloads carry identifiers and status only (no content), and the SSE endpoint still filters every event by
the subscriber's readable spaces.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from redis.asyncio import Redis


def org_channel(org_id: uuid.UUID) -> str:
    return f"events:org:{org_id}"


async def publish(valkey: Redis, org_id: uuid.UUID, event: dict[str, Any]) -> None:
    await valkey.publish(org_channel(org_id), json.dumps(event, default=str))


async def publish_document_status(
    valkey: Redis,
    *,
    org_id: uuid.UUID,
    space_id: uuid.UUID,
    document_id: uuid.UUID,
    status: str,
    error: str | None = None,
) -> None:
    await publish(
        valkey,
        org_id,
        {"type": "document.status", "space_id": space_id, "document_id": document_id, "status": status, "error": error},
    )
