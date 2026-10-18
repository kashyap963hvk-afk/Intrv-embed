"""Bearer-token HTTP calls to fixed provider APIs (Google, Microsoft Graph, Dropbox) with refresh-on-401,
rate-limit handling and size-capped downloads."""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from app.connectors.base import ConnectorError

_UNAUTHORIZED = 401
_FORBIDDEN = 403
_NOT_FOUND = 404
_TOO_MANY = 429
_ERROR = 400


class TokenSource(Protocol):
    async def access_token(self, *, force_refresh: bool = False) -> str: ...


class NotFound(ConnectorError):  # noqa: N818 - reads as a result, e.g. `except NotFound`
    status_code = 404


class ProviderHttp:
    def __init__(self, http: httpx.AsyncClient, tokens: TokenSource, *, label: str, hosts: frozenset[str]) -> None:
        self._http = http
        self._tokens = tokens
        self._label = label
        self._hosts = hosts  # pagination links must stay on the provider's API hosts

    def _check_host(self, url: str) -> None:
        if urlsplit(url).hostname not in self._hosts:
            raise ConnectorError(f"{self._label} returned a link to an unexpected host.")

    def _raise_for(self, response: httpx.Response) -> None:
        if response.status_code < _ERROR:
            return
        if response.status_code == _NOT_FOUND:
            raise NotFound(f"{self._label}: not found.", http_status=_NOT_FOUND)
        if response.status_code == _TOO_MANY:
            retry = response.headers.get("Retry-After", "a while")
            raise ConnectorError(
                f"{self._label} is rate limiting requests; retrying after {retry} seconds.", http_status=_TOO_MANY
            )
        if response.status_code in {_UNAUTHORIZED, _FORBIDDEN}:
            raise ConnectorError(
                f"{self._label} denied access ({response.status_code}).", auth=True, http_status=response.status_code
            )
        raise ConnectorError(
            f"{self._label} returned HTTP {response.status_code}: {response.text[:200]}",
            http_status=response.status_code,
        )

    async def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self._check_host(url)
        headers = dict(kwargs.pop("headers", {}) or {})
        for attempt in range(2):
            headers["Authorization"] = f"Bearer {await self._tokens.access_token(force_refresh=attempt > 0)}"
            try:
                response = await self._http.request(method, url, headers=headers, **kwargs)
            except httpx.HTTPError as exc:
                raise ConnectorError(f"{self._label} is unreachable ({type(exc).__name__}).") from exc
            if response.status_code != _UNAUTHORIZED or attempt == 1:
                break
        self._raise_for(response)
        return response

    async def json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        response = await self._send(method, url, **kwargs)
        body = response.json()
        if not isinstance(body, dict):
            raise ConnectorError(f"{self._label} returned an unexpected response.")
        return body

    async def download(
        self, url: str, max_bytes: int, *, method: str = "GET", **kwargs: Any
    ) -> tuple[bytes, str | None]:
        """Stream a file, refusing anything larger than `max_bytes`. Redirects (signed download URLs on other
        hosts) are followed; httpx drops the Authorization header when the redirect leaves the origin."""
        self._check_host(url)
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {await self._tokens.access_token()}"
        try:
            async with self._http.stream(method, url, headers=headers, follow_redirects=True, **kwargs) as response:
                self._raise_for(response)
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ConnectorError("The file is larger than the upload limit.")
                    chunks.append(chunk)
                return b"".join(chunks), response.headers.get("content-type")
        except httpx.HTTPError as exc:
            raise ConnectorError(f"{self._label} download failed ({type(exc).__name__}).") from exc
