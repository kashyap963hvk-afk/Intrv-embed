from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any

import httpx2
import pytest

from app.core.ssrf import UnsafeURLError, create_http_client, is_public_address, resolve_public, validate_url


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.10",
        "169.254.169.254",  # cloud metadata
        "100.64.0.1",  # CGNAT
        "0.0.0.0",  # noqa: S104 - test data, nothing binds to it
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:169.254.169.254",
        "64:ff9b::7f00:1",  # NAT64-embedded 127.0.0.1
        "64:ff9b::a9fe:a9fe",  # NAT64-embedded metadata
    ],
)
def test_internal_addresses_rejected(address: str) -> None:
    assert not is_public_address(ipaddress.ip_address(address))


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "64:ff9b::808:808"])
def test_public_addresses_allowed(address: str) -> None:
    assert is_public_address(ipaddress.ip_address(address))


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com/v1",  # http not allowed in cloud mode
        "ftp://example.com",
        "https://user:pass@example.com/v1",
        "https://localhost/v1",
        "https://127.0.0.1/v1",
        "https://[::1]/v1",
        "https://169.254.169.254/latest/meta-data",
        "https://metadata.google.internal/computeMetadata",
        "https://my-service.internal/v1",
        "https://printer.local/",
        "file:///etc/passwd",
    ],
)
def test_validate_url_rejects(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        validate_url(url, allow_private=False)


def test_validate_url_allows_public_https_and_private_in_self_host_mode() -> None:
    assert validate_url("https://api.groq.com/openai/v1", allow_private=False)
    assert validate_url("http://ollama:11434/v1", allow_private=True)
    assert validate_url("http://127.0.0.1:8080/v1", allow_private=True)


def test_resolution_is_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    """A public-looking hostname that resolves to an internal address (DNS rebinding) is refused."""

    async def fake_getaddrinfo(host: str, port: int, **_: Any) -> list[Any]:
        address = "10.0.0.5" if host == "rebind.example.com" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    async def run() -> None:
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
        assert await resolve_public("good.example.com", 443) == ["93.184.216.34"]
        with pytest.raises(UnsafeURLError):
            await resolve_public("rebind.example.com", 443)

    asyncio.run(run())


def test_decimal_ip_trick_is_caught_at_connect_time() -> None:
    """'2130706433' passes static checks but resolves to 127.0.0.1; the guarded client must refuse to connect."""

    async def run() -> None:
        async with create_http_client(allow_private=False) as client:
            for url in ("https://2130706433:9/", "https://0x7f000001:9/", "https://169.254.169.254/"):
                with pytest.raises(UnsafeURLError, match="non-public"):
                    await client.get(url)

    asyncio.run(run())


def test_guarded_client_refuses_localhost_but_private_client_connects() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    async def run() -> None:
        async with create_http_client(allow_private=False) as client:
            with pytest.raises(UnsafeURLError, match="non-public"):
                await client.get(f"http://localhost:{port}/")
        async with create_http_client(allow_private=True) as client:
            with pytest.raises(httpx2.HTTPError):  # connects (self-host mode), then the socket closes without HTTP
                await client.get(f"http://localhost:{port}/", timeout=1)

    try:
        asyncio.run(run())
    finally:
        server.close()
