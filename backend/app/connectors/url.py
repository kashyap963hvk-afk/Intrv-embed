"""A single web page or file kept in sync by URL. Downloads go through the SSRF-guarded client (no redirects,
25 MB cap) and respect robots.txt. Change detection is by content hash."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any
from urllib import robotparser
from urllib.parse import urlsplit

import httpx2

from app.connectors.base import ChangeSet, ConnectorError, FetchedFile, Health, Page, RemoteItem, Selection
from app.core.ssrf import UnsafeURLError
from app.db.models import ConnectorKind

MAX_URL_BYTES = 25 * 1024 * 1024
USER_AGENT = "Intrv-embed/1.0 (+knowledge import)"
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_ERROR = 400


async def download_url(
    client: httpx2.AsyncClient, url: str, max_bytes: int = MAX_URL_BYTES
) -> tuple[bytes, str | None]:
    try:
        async with client.stream(
            "GET", url, follow_redirects=False, timeout=30, headers={"User-Agent": USER_AGENT}
        ) as response:
            if response.status_code in _REDIRECTS:
                raise ConnectorError("The URL redirects; use the final address instead.")
            if response.status_code >= _ERROR:
                raise ConnectorError(f"The URL returned HTTP {response.status_code}.", http_status=response.status_code)
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise ConnectorError(f"The download is larger than {max_bytes // (1024 * 1024)} MB.")
                chunks.append(chunk)
            return b"".join(chunks), response.headers.get("content-type")
    except UnsafeURLError as exc:
        raise ConnectorError(exc.detail or "This URL is not allowed.") from exc
    except httpx2.HTTPError as exc:
        raise ConnectorError(f"Could not download the URL ({type(exc).__name__}).") from exc


async def robots_allows(client: httpx2.AsyncClient, url: str) -> bool:
    parts = urlsplit(url)
    try:
        body, _ = await download_url(client, f"{parts.scheme}://{parts.netloc}/robots.txt", 512 * 1024)
    except ConnectorError:
        return True  # no robots.txt (or unreadable): crawling a single page is allowed
    parser = robotparser.RobotFileParser()
    parser.parse(body.decode("utf-8", errors="replace").splitlines())
    return parser.can_fetch(USER_AGENT, url)


def _filename(url: str) -> str:
    path = urlsplit(url).path.rstrip("/")
    last = path.rsplit("/", 1)[-1] if path else ""
    return last if "." in last else f"{urlsplit(url).hostname or 'page'}.html"


class UrlConnector:
    kind = ConnectorKind.URL

    def __init__(self, client: httpx2.AsyncClient) -> None:
        self._client = client
        self._cache: dict[str, tuple[bytes, str | None]] = {}

    async def validate(self) -> Health:
        return Health(True, "Web pages are fetched directly")

    async def browse(self, path: str | None, cursor: str | None) -> Page:
        return Page([])

    async def _get(self, url: str, max_bytes: int) -> tuple[bytes, str | None]:
        if url not in self._cache:
            if not await robots_allows(self._client, url):
                raise ConnectorError("The site's robots.txt does not allow fetching this page.")
            self._cache[url] = await download_url(self._client, url, max_bytes)
        return self._cache[url]

    async def changes(
        self, selection: Selection, cursor: Mapping[str, Any], known: Mapping[str, str | None]
    ) -> ChangeSet:
        if not selection.url:
            raise ConnectorError("No URL configured.")
        data, mime = await self._get(selection.url, MAX_URL_BYTES)
        etag = hashlib.sha256(data).hexdigest()
        item = RemoteItem(
            id=selection.url,
            name=_filename(selection.url),
            path=selection.url,
            is_folder=False,
            size=len(data),
            etag=etag,
            mime=mime,
        )
        result = ChangeSet(upserts=[item] if known.get(item.id) != etag else [])
        result.deleted = [k for k in known if k != item.id]
        return result

    async def fetch(self, item: RemoteItem, max_bytes: int) -> FetchedFile:
        data, mime = await self._get(item.id, min(max_bytes, MAX_URL_BYTES))
        return FetchedFile(data, item.name, mime, item.etag)
