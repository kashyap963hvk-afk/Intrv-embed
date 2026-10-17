"""RFC 7807 problem+json error responses.

Raise `AppError` (or a subclass) from domain code. Unexpected exceptions become a generic 500 that
never echoes internal details; the request id lets operators find the full traceback in the logs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

PROBLEM_CONTENT_TYPE = "application/problem+json"
_PROBLEM_TYPE_PREFIX = "urn:intrv:problem:"

log = get_logger("app.errors")


class AppError(Exception):
    status_code: int = 400
    slug: str = "bad-request"
    title: str = "Bad request"

    def __init__(self, detail: str | None = None, *, extra: dict[str, Any] | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        self.extra = extra or {}


class NotFoundError(AppError):
    status_code = 404
    slug = "not-found"
    title = "Not found"


class ForbiddenError(AppError):
    status_code = 403
    slug = "forbidden"
    title = "Forbidden"


class ConflictError(AppError):
    status_code = 409
    slug = "conflict"
    title = "Conflict"


def _request_id(request: Request) -> str | None:
    value = request.scope.get("state", {}).get("request_id")
    return value if isinstance(value, str) else None


def problem_response(
    request: Request,
    *,
    status: int,
    slug: str,
    title: str,
    detail: str | None = None,
    extra: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"{_PROBLEM_TYPE_PREFIX}{slug}",
        "title": title,
        "status": status,
        "instance": request.url.path,
        "request_id": _request_id(request),
    }
    if detail:
        body["detail"] = detail
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_CONTENT_TYPE, headers=headers)


_HTTP_SLUGS = {
    400: ("bad-request", "Bad request"),
    401: ("unauthorized", "Unauthorized"),
    403: ("forbidden", "Forbidden"),
    404: ("not-found", "Not found"),
    405: ("method-not-allowed", "Method not allowed"),
    409: ("conflict", "Conflict"),
    413: ("payload-too-large", "Payload too large"),
    429: ("rate-limited", "Too many requests"),
}


async def _app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppError):
        raise TypeError(type(exc).__name__)
    return problem_response(
        request, status=exc.status_code, slug=exc.slug, title=exc.title, detail=exc.detail, extra=exc.extra
    )


async def _http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise TypeError(type(exc).__name__)
    slug, title = _HTTP_SLUGS.get(exc.status_code, ("http-error", "HTTP error"))
    detail = exc.detail if isinstance(exc.detail, str) and exc.detail != title else None
    return problem_response(request, status=exc.status_code, slug=slug, title=title, detail=detail, headers=exc.headers)


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise TypeError(type(exc).__name__)
    # Never echo the submitted input back: it may contain passwords or API keys.
    errors = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in exc.errors()
    ]
    return problem_response(
        request,
        status=422,
        slug="validation-error",
        title="Request validation failed",
        extra={"errors": errors},
    )


def internal_error_response(request: Request) -> JSONResponse:
    return problem_response(request, status=500, slug="internal-error", title="Internal server error")


async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Backstop only: RequestContextMiddleware normally converts unhandled errors first.
    log.exception("unhandled_exception", path=request.url.path, exc_type=type(exc).__name__)
    return internal_error_response(request)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
