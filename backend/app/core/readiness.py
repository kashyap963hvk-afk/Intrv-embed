"""Dependency readiness checks used by `GET /readyz`."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal

import anyio
from sqlalchemy import text

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.metrics import READINESS_CHECK_FAILURES
from app.core.resources import Resources

log = get_logger("app.readiness")

CheckFn = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class DependencyCheck:
    name: str
    run: CheckFn


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    status: Literal["ok", "error"]
    latency_ms: float
    error: str | None = None


def build_checks(resources: Resources, settings: Settings) -> list[DependencyCheck]:
    async def postgres() -> None:
        async with resources.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def valkey() -> None:
        await resources.valkey.ping()

    async def qdrant() -> None:
        headers = {"api-key": settings.qdrant_api_key.get_secret_value()} if settings.qdrant_api_key else {}
        response = await resources.http.get(f"{settings.qdrant_url.rstrip('/')}/readyz", headers=headers)
        response.raise_for_status()

    async def s3() -> None:
        await anyio.to_thread.run_sync(lambda: resources.s3.head_bucket(Bucket=settings.s3_bucket))

    return [
        DependencyCheck("postgres", postgres),
        DependencyCheck("valkey", valkey),
        DependencyCheck("qdrant", qdrant),
        DependencyCheck("s3", s3),
    ]


async def _run_one(check: DependencyCheck, timeout_s: float, expose_errors: bool) -> CheckResult:
    started = time.perf_counter()
    try:
        await asyncio.wait_for(check.run(), timeout=timeout_s)
    except Exception as exc:
        READINESS_CHECK_FAILURES.labels(dependency=check.name).inc()
        log.warning("readiness_check_failed", dependency=check.name, exc_type=type(exc).__name__, error=str(exc))
        if isinstance(exc, TimeoutError):
            message = f"timed out after {timeout_s}s"
        elif expose_errors:
            message = f"{type(exc).__name__}: {exc}"
        else:
            # Production: never leak hostnames or connection strings to unauthenticated callers.
            message = type(exc).__name__
        return CheckResult(check.name, "error", round((time.perf_counter() - started) * 1000, 2), message)
    return CheckResult(check.name, "ok", round((time.perf_counter() - started) * 1000, 2))


async def run_checks(checks: Sequence[DependencyCheck], *, timeout_s: float, expose_errors: bool) -> list[CheckResult]:
    return list(await asyncio.gather(*(_run_one(c, timeout_s, expose_errors) for c in checks)))
