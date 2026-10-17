"""Auth cookies: httpOnly access + refresh tokens, readable CSRF token (double-submit)."""

from __future__ import annotations

import secrets
from datetime import datetime

from fastapi import Response

from app.core.config import Settings

ACCESS_COOKIE = "intrv_at"
REFRESH_COOKIE = "intrv_rt"
CSRF_COOKIE = "intrv_csrf"
CSRF_HEADER = "x-csrf-token"
REFRESH_COOKIE_PATH = "/api/auth"
_CSRF_MAX_AGE_S = 60 * 60 * 24 * 7


def _set(
    response: Response,
    settings: Settings,
    key: str,
    value: str,
    *,
    path: str,
    httponly: bool,
    expires: datetime | None = None,
    max_age: int | None = None,
) -> None:
    response.set_cookie(
        key,
        value,
        expires=expires,
        max_age=max_age,
        path=path,
        domain=settings.cookie_domain or None,
        secure=settings.secure_cookies,
        httponly=httponly,
        samesite="lax",
    )


def _delete(response: Response, settings: Settings, key: str, *, path: str) -> None:
    response.delete_cookie(
        key, path=path, domain=settings.cookie_domain or None, secure=settings.secure_cookies, samesite="lax"
    )


def set_session_cookies(
    response: Response,
    settings: Settings,
    *,
    access_token: str,
    access_expires: datetime,
    refresh_token: str,
    refresh_expires: datetime,
) -> None:
    _set(response, settings, ACCESS_COOKIE, access_token, path="/", httponly=True, expires=access_expires)
    _set(
        response,
        settings,
        REFRESH_COOKIE,
        refresh_token,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        expires=refresh_expires,
    )


def clear_session_cookies(response: Response, settings: Settings) -> None:
    _delete(response, settings, ACCESS_COOKIE, path="/")
    _delete(response, settings, REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)


def issue_csrf_cookie(response: Response, settings: Settings) -> str:
    """Set a fresh CSRF cookie and return its value.

    Not httpOnly by design (double-submit). The value is also returned in the response body so a
    frontend on another subdomain can send it in the X-CSRF-Token header without reading cookies.
    """
    token = secrets.token_urlsafe(32)
    _set(response, settings, CSRF_COOKIE, token, path="/", httponly=False, max_age=_CSRF_MAX_AGE_S)
    return token
