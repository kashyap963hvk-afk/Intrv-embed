"""Password hashing, access tokens (JWT) and opaque tokens (refresh, invite, CSRF)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings

_JWT_ALGORITHM = "HS256"
_JWT_AUDIENCE = "intrv-api"
_hasher = PasswordHasher()  # argon2id with the library's current recommended parameters
# Verified against when the user does not exist, so failed logins take the same time either way.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256
_MIN_IDENTIFIER_LENGTH = 4  # shorter email local parts / names are too common to forbid
_MIN_DISTINCT_CHARACTERS = 5


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-work verification: a missing hash still costs one argon2 verification."""
    try:
        if password_hash is None:
            _hasher.verify(_DUMMY_HASH, password)
            return False
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def password_problems(password: str, *, email: str = "", name: str = "") -> list[str]:
    """Return human-readable reasons a password is unacceptable (empty list = OK)."""
    problems: list[str] = []
    if len(password) < PASSWORD_MIN_LENGTH:
        problems.append(f"must be at least {PASSWORD_MIN_LENGTH} characters")
    if len(password) > PASSWORD_MAX_LENGTH:
        problems.append(f"must be at most {PASSWORD_MAX_LENGTH} characters")
    lowered = password.lower()
    local_part = email.split("@", 1)[0].lower()
    if len(local_part) >= _MIN_IDENTIFIER_LENGTH and local_part in lowered:
        problems.append("must not contain your email address")
    if name and len(name) >= _MIN_IDENTIFIER_LENGTH and name.lower() in lowered:
        problems.append("must not contain your name")
    if len(set(password)) < _MIN_DISTINCT_CHARACTERS:
        problems.append("is too repetitive")
    return problems


# --- Opaque tokens -------------------------------------------------------------------------------


def new_opaque_token() -> tuple[str, str]:
    """Return (token for the client, sha256 hex digest for storage)."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_match(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


# --- Access tokens -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccessClaims:
    user_id: uuid.UUID
    org_id: uuid.UUID
    family_id: uuid.UUID
    expires_at: datetime


def create_access_token(
    settings: Settings, *, user_id: uuid.UUID, org_id: uuid.UUID, family_id: uuid.UUID
) -> tuple[str, datetime]:
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=settings.access_token_ttl_s)
    payload = {
        "sub": str(user_id),
        "org": str(org_id),
        "fam": str(family_id),
        "typ": "access",
        "aud": _JWT_AUDIENCE,
        "iss": settings.api_public_url,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=_JWT_ALGORITHM)
    return token, expires


def decode_access_token(settings: Settings, token: str) -> AccessClaims | None:
    """Return the claims, or None for any invalid/expired/foreign token."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[_JWT_ALGORITHM],
            audience=_JWT_AUDIENCE,
            issuer=settings.api_public_url,
            options={"require": ["sub", "org", "fam", "exp", "iat", "typ"]},
        )
        if payload.get("typ") != "access":
            return None
        return AccessClaims(
            user_id=uuid.UUID(payload["sub"]),
            org_id=uuid.UUID(payload["org"]),
            family_id=uuid.UUID(payload["fam"]),
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        )
    except (jwt.PyJWTError, ValueError, KeyError):
        return None
