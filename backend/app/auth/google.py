"""Sign in with Google (OpenID Connect, authorization-code flow with PKCE and a nonce).

Access stays invite-based: Google only proves who someone is. A first sign-in must come through an owner's
invitation link (the invite token travels in the single-use state); later sign-ins match an existing account
by its verified email. The ID token comes straight from Google's token endpoint over TLS, so its claims are
validated (issuer, audience, expiry, nonce, verified email) without a signature check (OIDC Core 3.1.3.7).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from redis.asyncio import Redis

from app.core.config import Settings

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - endpoint, not a secret
ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})
STATE_TTL_S = 600
CALLBACK_PATH = "/api/auth/oidc/google/callback"


class GoogleSignInError(Exception):
    """A sign-in that cannot complete; the message is shown to the person on the login page."""


@dataclass(frozen=True, slots=True)
class PendingSignIn:
    verifier: str
    nonce: str
    next_path: str
    invite: str | None
    org: str | None


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str


def enabled(settings: Settings) -> bool:
    return bool(settings.google_oauth_client_id and settings.google_oauth_client_secret)


def redirect_uri(settings: Settings) -> str:
    return f"{settings.api_public_url.rstrip('/')}{CALLBACK_PATH}"


def safe_next(value: str | None) -> str:
    """Only same-site paths: an absolute URL or `//host` would turn sign-in into an open redirect."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/app"
    return value[:500]


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _state_key(state: str) -> str:
    return f"oidc:google:{state}"


async def start(
    valkey: Redis, settings: Settings, *, next_path: str | None, invite: str | None, org: str | None
) -> str:
    """The Google URL to send the browser to; the state is single-use and expires in 10 minutes."""
    if not enabled(settings):
        raise GoogleSignInError("Sign-in with Google is not configured on this server.")
    state, verifier, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(64), secrets.token_urlsafe(24)
    pending = {"verifier": verifier, "nonce": nonce, "next": safe_next(next_path), "invite": invite, "org": org}
    await valkey.set(_state_key(state), json.dumps(pending), ex=STATE_TTL_S)
    params = {
        "client_id": settings.google_oauth_client_id or "",
        "response_type": "code",
        "redirect_uri": redirect_uri(settings),
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def consume_state(valkey: Redis, state: str) -> PendingSignIn | None:
    raw = await valkey.getdel(_state_key(state))
    if raw is None:
        return None
    data = json.loads(raw)
    return PendingSignIn(data["verifier"], data["nonce"], data["next"], data.get("invite"), data.get("org"))


def _claims(id_token: str) -> dict[str, Any]:
    try:
        payload = id_token.split(".")[1]
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(decoded)
    except (IndexError, ValueError, binascii.Error) as exc:
        raise GoogleSignInError("Google returned an unreadable sign-in token.") from exc
    if not isinstance(claims, dict):
        raise GoogleSignInError("Google returned an unreadable sign-in token.")
    return claims


def verify_claims(
    claims: dict[str, Any], settings: Settings, nonce: str, *, now: float | None = None
) -> GoogleIdentity:
    if claims.get("iss") not in ISSUERS:
        raise GoogleSignInError("The sign-in token was not issued by Google.")
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if settings.google_oauth_client_id not in audiences:
        raise GoogleSignInError("The sign-in token was issued for a different application.")
    if float(claims.get("exp", 0)) <= (now if now is not None else time.time()):
        raise GoogleSignInError("The Google sign-in expired; please try again.")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise GoogleSignInError("The Google sign-in could not be matched to this browser; please try again.")
    email = str(claims.get("email") or "").strip().lower()
    if not email or claims.get("email_verified") is not True:
        raise GoogleSignInError("Your Google account has no verified email address.")
    return GoogleIdentity(sub=str(claims["sub"]), email=email, name=str(claims.get("name") or email.split("@")[0]))


async def exchange(http: httpx.AsyncClient, settings: Settings, code: str, pending: PendingSignIn) -> GoogleIdentity:
    secret = settings.google_oauth_client_secret
    try:
        response = await http.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri(settings),
                "code_verifier": pending.verifier,
                "client_id": settings.google_oauth_client_id or "",
                "client_secret": secret.get_secret_value() if secret else "",
            },
            headers={"Accept": "application/json"},
            timeout=20,
        )
    except httpx.HTTPError as exc:
        raise GoogleSignInError("Google could not be reached; please try again.") from exc
    if response.status_code >= 400:  # noqa: PLR2004
        raise GoogleSignInError("Google refused the sign-in; please try again.")
    id_token = response.json().get("id_token")
    if not isinstance(id_token, str):
        raise GoogleSignInError("Google did not return a sign-in token.")
    return verify_claims(_claims(id_token), settings, pending.nonce)
