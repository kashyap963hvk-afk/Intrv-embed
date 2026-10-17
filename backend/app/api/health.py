"""Liveness, readiness and metrics endpoints."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from app.core.config import Settings
from app.core.readiness import DependencyCheck, run_checks

router = APIRouter(tags=["ops"])


class HealthResponse(BaseModel):
    status: Literal["ok"]


class DependencyStatus(BaseModel):
    status: Literal["ok", "error"]
    latency_ms: float
    error: str | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, DependencyStatus]


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_readiness_checks(request: Request) -> list[DependencyCheck]:
    checks: list[DependencyCheck] = request.app.state.readiness_checks
    return checks


@router.get("/healthz", response_model=HealthResponse, summary="Liveness probe")
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    summary="Readiness probe: checks Postgres, Valkey, Qdrant and S3",
    responses={503: {"model": ReadinessResponse, "description": "At least one dependency is unavailable"}},
)
async def readyz(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    checks: Annotated[list[DependencyCheck], Depends(get_readiness_checks)],
) -> ReadinessResponse:
    results = await run_checks(checks, timeout_s=settings.readiness_timeout_s, expose_errors=not settings.is_prod)
    healthy = all(r.status == "ok" for r in results)
    response.status_code = 200 if healthy else 503
    return ReadinessResponse(
        status="ok" if healthy else "degraded",
        checks={r.name: DependencyStatus(status=r.status, latency_ms=r.latency_ms, error=r.error) for r in results},
    )


metrics_router = APIRouter()


@metrics_router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
