"""Real dependency containers for integration tests (testcontainers).

Tests here skip when no Docker daemon is reachable, unless REQUIRE_DOCKER=1 (set in CI), in which
case they fail: CI must never silently skip infrastructure tests.
"""

from __future__ import annotations

import os
import secrets
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import boto3
import docker
import httpx
import pytest
import redis
from botocore.config import Config as BotoConfig
from docker.errors import DockerException
from testcontainers.core.container import DockerContainer

REPO_ROOT = Path(__file__).resolve().parents[3]
# Ephemeral credentials for the throwaway SeaweedFS container, generated per test session.
S3_ACCESS_KEY = f"itest{secrets.token_hex(4)}"
S3_SECRET_KEY = secrets.token_hex(20)
S3_BUCKET = "intrv-itest"


def _docker_available() -> bool:
    try:
        docker.from_env().ping()
    except DockerException:
        return False
    return True


@pytest.fixture(scope="session", autouse=True)
def _require_docker() -> None:
    if _docker_available():
        return
    if os.environ.get("REQUIRE_DOCKER") == "1":
        pytest.fail("Docker is required for integration tests (REQUIRE_DOCKER=1) but is not reachable")
    pytest.skip("Docker daemon not reachable; integration tests skipped")


def wait_until(probe: Callable[[], object], *, timeout_s: float = 60.0, what: str) -> None:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            probe()
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
        else:
            return
    raise TimeoutError(f"{what} not ready after {timeout_s}s: {last_error!r}")


@dataclass(frozen=True)
class Infra:
    database_url: str
    valkey_url: str
    qdrant_url: str
    s3_endpoint_url: str


def _url(container: DockerContainer, port: int) -> tuple[str, int]:
    return container.get_container_host_ip(), int(container.get_exposed_port(port))


@pytest.fixture(scope="session")
def postgres() -> Iterator[str]:
    container = (
        DockerContainer("postgres:16-alpine")
        .with_env("POSTGRES_USER", "intrv")
        .with_env("POSTGRES_PASSWORD", "intrv")
        .with_env("POSTGRES_DB", "intrv")
        .with_exposed_ports(5432)
    )
    with container:
        host, port = _url(container, 5432)

        def probe() -> None:
            exit_code, _ = container.exec(["pg_isready", "-U", "intrv", "-d", "intrv", "-h", "127.0.0.1"])
            if exit_code != 0:
                raise RuntimeError("pg_isready failed")

        wait_until(probe, what="postgres")
        yield f"postgresql+asyncpg://intrv:intrv@{host}:{port}/intrv"


@pytest.fixture(scope="session")
def valkey() -> Iterator[str]:
    with DockerContainer("valkey/valkey:8-alpine").with_exposed_ports(6379) as container:
        host, port = _url(container, 6379)
        url = f"redis://{host}:{port}/0"
        wait_until(lambda: redis.Redis.from_url(url).ping(), what="valkey")
        yield url


@pytest.fixture(scope="session")
def qdrant() -> Iterator[str]:
    with DockerContainer("qdrant/qdrant:v1.19.2").with_exposed_ports(6333) as container:
        host, port = _url(container, 6333)
        url = f"http://{host}:{port}"
        wait_until(lambda: httpx.get(f"{url}/readyz").raise_for_status(), what="qdrant")
        yield url


@pytest.fixture(scope="session")
def s3() -> Iterator[str]:
    container = (
        DockerContainer("chrislusf/seaweedfs:4.48")
        .with_env("S3_ACCESS_KEY", S3_ACCESS_KEY)
        .with_env("S3_SECRET_KEY", S3_SECRET_KEY)
        .with_volume_mapping(str(REPO_ROOT / "infra/seaweedfs/entrypoint.sh"), "/entrypoint.sh", "ro")
        .with_kwargs(entrypoint=["/bin/sh", "/entrypoint.sh"])
        .with_exposed_ports(8333)
    )
    with container:
        host, port = _url(container, 8333)
        endpoint = f"http://{host}:{port}"
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name="us-east-1",
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY,
            config=BotoConfig(s3={"addressing_style": "path"}),
        )
        wait_until(lambda: client.create_bucket(Bucket=S3_BUCKET), what="seaweedfs s3")
        yield endpoint


@pytest.fixture(scope="session")
def infra(postgres: str, valkey: str, qdrant: str, s3: str) -> Infra:
    return Infra(database_url=postgres, valkey_url=valkey, qdrant_url=qdrant, s3_endpoint_url=s3)
