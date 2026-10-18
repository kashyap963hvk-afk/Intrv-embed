"""OAuth 2.0 authorization-code flow with PKCE for Google Drive, Microsoft Graph and Dropbox.

State (with the PKCE verifier and who started the flow) lives in Valkey for 10 minutes and is single-use.
Token sets are stored only encrypted (see connectors.service); refresh happens transparently.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from redis.asyncio import Redis

from app.connectors.base import ConnectorError
from app.core.config import Settings
from app.db.models import ConnectorKind

STATE_TTL_S = 600
_REFRESH_MARGIN_S = 60


@dataclass(frozen=True, slots=True)
class OAuthProvider:
    key: str  # URL segment: google | microsoft | dropbox
    kind: ConnectorKind
    label: str
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]
    client_id: str
    client_secret: str
    extra_params: tuple[tuple[str, str], ...] = ()


def oauth_providers(settings: Settings) -> dict[str, OAuthProvider]:
    """Providers whose app credentials are configured (the others are shown as unavailable)."""
    found: dict[str, OAuthProvider] = {}
    if settings.google_oauth_client_id and settings.google_oauth_client_secret:
        found["google"] = OAuthProvider(
            key="google",
            kind=ConnectorKind.GOOGLE_DRIVE,
            label="Google Drive",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",  # noqa: S106 - endpoint, not a secret
            # drive.file: only files the user picks; not a restricted scope, so no security assessment.
            scopes=("openid", "email", "https://www.googleapis.com/auth/drive.file"),
            client_id=settings.google_oauth_client_id,
            client_secret=settings.google_oauth_client_secret.get_secret_value(),
            extra_params=(("access_type", "offline"), ("prompt", "consent"), ("include_granted_scopes", "true")),
        )
    if settings.ms_client_id and settings.ms_client_secret:
        tenant = settings.ms_tenant or "common"
        found["microsoft"] = OAuthProvider(
            key="microsoft",
            kind=ConnectorKind.MS_GRAPH,
            label="OneDrive / SharePoint",
            authorize_url=f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
            token_url=f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            scopes=("offline_access", "User.Read", "Files.Read.All", "Sites.Read.All"),
            client_id=settings.ms_client_id,
            client_secret=settings.ms_client_secret.get_secret_value(),
        )
    if settings.dropbox_app_key and settings.dropbox_app_secret:
        found["dropbox"] = OAuthProvider(
            key="dropbox",
            kind=ConnectorKind.DROPBOX,
            label="Dropbox",
            authorize_url="https://www.dropbox.com/oauth2/authorize",
            token_url="https://api.dropboxapi.com/oauth2/token",  # noqa: S106 - endpoint, not a secret
            scopes=("account_info.read", "files.metadata.read", "files.content.read"),
            client_id=settings.dropbox_app_key,
            client_secret=settings.dropbox_app_secret.get_secret_value(),
            extra_params=(("token_access_type", "offline"),),
        )
    return found


def redirect_uri(settings: Settings, provider: OAuthProvider) -> str:
    return f"{settings.api_public_url.rstrip('/')}/api/connectors/oauth/{provider.key}/callback"


@dataclass(frozen=True, slots=True)
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: float  # unix time

    def to_secret(self) -> dict[str, Any]:
        return {"access_token": self.access_token, "refresh_token": self.refresh_token, "expires_at": self.expires_at}

    @classmethod
    def from_secret(cls, data: dict[str, Any]) -> TokenSet:
        return cls(str(data["access_token"]), data.get("refresh_token"), float(data.get("expires_at", 0)))


@dataclass(frozen=True, slots=True)
class PendingAuth:
    provider: str
    org_id: uuid.UUID
    user_id: uuid.UUID
    verifier: str


def _state_key(state: str) -> str:
    return f"oauth:state:{state}"


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def start(
    valkey: Redis, settings: Settings, provider: OAuthProvider, *, org_id: uuid.UUID, user_id: uuid.UUID
) -> str:
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    pending = {"provider": provider.key, "org_id": str(org_id), "user_id": str(user_id), "verifier": verifier}
    await valkey.set(_state_key(state), json.dumps(pending), ex=STATE_TTL_S)
    params = [
        ("client_id", provider.client_id),
        ("response_type", "code"),
        ("redirect_uri", redirect_uri(settings, provider)),
        ("scope", " ".join(provider.scopes)),
        ("state", state),
        ("code_challenge", _challenge(verifier)),
        ("code_challenge_method", "S256"),
        *provider.extra_params,
    ]
    return f"{provider.authorize_url}?{urlencode(params)}"


async def consume_state(valkey: Redis, state: str) -> PendingAuth | None:
    """Single use: the state is deleted as it is read."""
    raw = await valkey.getdel(_state_key(state))
    if raw is None:
        return None
    data = json.loads(raw)
    return PendingAuth(data["provider"], uuid.UUID(data["org_id"]), uuid.UUID(data["user_id"]), data["verifier"])


async def _token_request(http: httpx.AsyncClient, provider: OAuthProvider, form: dict[str, str]) -> TokenSet:
    try:
        response = await http.post(
            provider.token_url,
            data={**form, "client_id": provider.client_id, "client_secret": provider.client_secret},
            headers={"Accept": "application/json"},
            timeout=20,
        )
    except httpx.HTTPError as exc:
        raise ConnectorError(f"{provider.label} sign-in service is unreachable ({type(exc).__name__}).") from exc
    if response.status_code >= 400:  # noqa: PLR2004
        try:
            error = response.json().get("error_description") or response.json().get("error")
        except ValueError:
            error = response.text[:200]
        raise ConnectorError(f"{provider.label} refused the sign-in: {error}", auth=True)
    body = response.json()
    return TokenSet(
        access_token=str(body["access_token"]),
        refresh_token=body.get("refresh_token") or form.get("refresh_token"),
        expires_at=time.time() + float(body.get("expires_in", 3600)),
    )


async def exchange_code(
    http: httpx.AsyncClient, settings: Settings, provider: OAuthProvider, code: str, verifier: str
) -> TokenSet:
    return await _token_request(
        http,
        provider,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri(settings, provider),
            "code_verifier": verifier,
        },
    )


TokenSaver = Callable[[TokenSet], Awaitable[None]]


class OAuthTokens:
    """Hands out a valid access token, refreshing (and persisting through `save`) when it is about to expire."""

    def __init__(
        self, http: httpx.AsyncClient, provider: OAuthProvider, tokens: TokenSet, save: TokenSaver | None
    ) -> None:
        self._http = http
        self._provider = provider
        self._tokens = tokens
        self._save = save

    async def access_token(self, *, force_refresh: bool = False) -> str:
        if force_refresh or self._tokens.expires_at - _REFRESH_MARGIN_S <= time.time():
            if not self._tokens.refresh_token:
                raise ConnectorError(f"The {self._provider.label} connection expired; connect it again.", auth=True)
            self._tokens = await _token_request(
                self._http, self._provider, {"grant_type": "refresh_token", "refresh_token": self._tokens.refresh_token}
            )
            if self._save is not None:
                await self._save(self._tokens)
        return self._tokens.access_token
