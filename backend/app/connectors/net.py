"""Destination checks for connectors whose endpoint the user types (S3-compatible, Azure)."""

from __future__ import annotations

from urllib.parse import urlsplit

from app.connectors.base import ConnectorError
from app.core.ssrf import UnsafeURLError, resolve_public, validate_url


async def check_endpoint(url: str, *, allow_private: bool) -> str:
    """Static URL checks plus DNS resolution to public addresses only (unless self-host mode allows private).

    SDK clients (boto3, azure) open their own sockets, so this runs before every use, not just on save.
    """
    try:
        normalized = validate_url(url, allow_private=allow_private, allowed_schemes=("https",))
        if not allow_private:
            parts = urlsplit(normalized)
            await resolve_public(parts.hostname or "", parts.port or 443)
    except UnsafeURLError as exc:
        raise ConnectorError(exc.detail or "This endpoint is not allowed.") from exc
    return normalized
