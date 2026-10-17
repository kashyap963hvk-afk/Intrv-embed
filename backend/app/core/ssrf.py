"""Server-side request forgery protection for user-supplied URLs (LLM endpoints, URL imports, connectors).

Two layers:
  1. `validate_url` rejects bad schemes, credentials in URLs and literal internal addresses up front.
  2. `GuardedNetworkBackend` resolves the hostname at *connect* time, rejects the connection if any resolved
     address is internal, and connects to the address it validated. A DNS answer that changes between check and
     use (DNS rebinding) therefore cannot reach an internal service. TLS still verifies the original hostname.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

import httpcore2
import httpx2
from httpcore2._backends.auto import AutoBackend

from app.core.errors import AppError

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_METADATA_HOSTS = frozenset({"metadata.google.internal", "metadata", "instance-data"})


class UnsafeURLError(AppError):
    status_code = 400
    slug = "unsafe-url"
    title = "This URL is not allowed"


def is_public_address(ip: IPAddress) -> bool:
    """True only for globally routable unicast addresses (handles IPv4-mapped and NAT64 IPv6 forms)."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_public_address(ip.ipv4_mapped)
        if ip in _NAT64:
            return is_public_address(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return ip.is_global and not ip.is_multicast


def _literal_ip(host: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def validate_url(url: str, *, allow_private: bool, allowed_schemes: Iterable[str] = ("https",)) -> str:
    """Static checks; returns the normalized URL. Resolution is checked again at connect time."""
    parts = urlsplit(url.strip())
    schemes = set(allowed_schemes) | ({"http"} if allow_private else set())
    if parts.scheme not in schemes:
        raise UnsafeURLError(f"URL scheme must be one of: {', '.join(sorted(schemes))}.")
    if not parts.hostname:
        raise UnsafeURLError("URL has no host.")
    if parts.username or parts.password:
        raise UnsafeURLError("Credentials in URLs are not allowed; use the API key field.")
    host = parts.hostname.lower()
    if not allow_private:
        if host in _METADATA_HOSTS or host == "localhost" or host.endswith((".localhost", ".internal", ".local")):
            raise UnsafeURLError("Internal hostnames are not allowed.")
        ip = _literal_ip(host)
        if ip is not None and not is_public_address(ip):
            raise UnsafeURLError("Private, loopback and link-local addresses are not allowed.")
    return parts.geturl()


async def resolve_public(host: str, port: int) -> list[str]:
    """Resolve `host` and return its addresses, raising if any of them is not public."""
    literal = _literal_ip(host)
    if literal is not None:
        addresses = [str(literal)]
    else:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise UnsafeURLError(f"Cannot resolve host {host!r}.") from exc
        addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))
    for address in addresses:
        if not is_public_address(ipaddress.ip_address(address.split("%", 1)[0])):
            raise UnsafeURLError(f"Host {host!r} resolves to a non-public address.")
    if not addresses:
        raise UnsafeURLError(f"Host {host!r} has no addresses.")
    return addresses


class GuardedNetworkBackend(httpcore2.AsyncNetworkBackend):
    """httpcore backend that only opens TCP connections to validated public addresses."""

    def __init__(self) -> None:
        self._inner = AutoBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - signature defined by httpcore2.AsyncNetworkBackend
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        last_error: Exception | None = None
        for address in await resolve_public(host, port):
            try:
                return await self._inner.connect_tcp(
                    address, port, timeout=timeout, local_address=local_address, socket_options=socket_options
                )
            except (httpcore2.ConnectError, httpcore2.ConnectTimeout) as exc:
                last_error = exc
        raise httpcore2.ConnectError(f"Could not connect to {host}:{port}") from last_error

    async def connect_unix_socket(self, *args: Any, **kwargs: Any) -> httpcore2.AsyncNetworkStream:
        raise httpcore2.ConnectError("Unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class GuardedTransport(httpx2.AsyncHTTPTransport):
    """httpx2 transport using GuardedNetworkBackend (no proxies: the guard must see the real destination)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        limits = kwargs.get("limits", httpx2.Limits(max_connections=100, max_keepalive_connections=20))
        self._pool = httpcore2.AsyncConnectionPool(
            ssl_context=httpx2.create_ssl_context(),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            http1=True,
            http2=False,
            retries=0,
            network_backend=GuardedNetworkBackend(),
        )

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.scheme not in {"http", "https"}:
            raise UnsafeURLError("Only http(s) requests are allowed.")
        return await super().handle_async_request(request)


def create_http_client(*, allow_private: bool, timeout: httpx2.Timeout | None = None) -> httpx2.AsyncClient:
    """HTTP client for user-supplied destinations. With allow_private (self-host mode) the guard is off."""
    transport: httpx2.AsyncBaseTransport = (
        httpx2.AsyncHTTPTransport(retries=0) if allow_private else GuardedTransport(retries=0)
    )
    return httpx2.AsyncClient(
        transport=transport,
        timeout=timeout or httpx2.Timeout(60.0, connect=5.0),
        follow_redirects=False,
        trust_env=False,
    )
