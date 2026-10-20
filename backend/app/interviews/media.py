"""Voice interview plumbing on the API side: where the voice service is, and ICE servers with short-lived TURN
credentials (coturn's REST scheme: username `<expiry>:<session>`, password = base64(HMAC-SHA1(secret, username)))."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid
from typing import Any

from app.core.config import Settings


def ice_servers(settings: Settings, session_id: uuid.UUID, *, now: float | None = None) -> list[dict[str, Any]]:
    servers: list[dict[str, Any]] = [{"urls": settings.stun_urls}] if settings.stun_urls else []
    if not settings.turn_urls:
        return servers
    if settings.turn_shared_secret is not None:
        expiry = int((now or time.time()) + settings.turn_credential_ttl_s)
        username = f"{expiry}:{session_id}"
        digest = hmac.new(
            settings.turn_shared_secret.get_secret_value().encode(), username.encode(), hashlib.sha1
        ).digest()
        servers.append(
            {"urls": settings.turn_urls, "username": username, "credential": base64.b64encode(digest).decode()}
        )
    elif settings.turn_username and settings.turn_credential is not None:
        servers.append(
            {
                "urls": settings.turn_urls,
                "username": settings.turn_username,
                "credential": settings.turn_credential.get_secret_value(),
            }
        )
    return servers


def voice_info(settings: Settings, session_id: uuid.UUID) -> dict[str, Any]:
    url = settings.voice_public_url.rstrip("/") if settings.voice_public_url else None
    return {
        "available": url is not None,
        "offer_url": f"{url}/voice/{session_id}/offer" if url else None,
        "ice_servers": ice_servers(settings, session_id) if url else [],
    }
