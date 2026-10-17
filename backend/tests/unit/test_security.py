from __future__ import annotations

import uuid

import jwt

from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_token,
    new_opaque_token,
    password_problems,
    verify_password,
)
from tests.conftest import make_settings


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("a long enough passphrase")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "a long enough passphrase")
    assert not verify_password(hashed, "wrong passphrase!!")
    assert not verify_password(None, "anything at all")
    assert not verify_password("not-a-hash", "anything at all")


def test_password_rules() -> None:
    assert password_problems("correct-horse-battery-9") == []
    assert "must be at least 12 characters" in password_problems("short1!")
    assert "must not contain your email address" in password_problems("ada.lovelace2024!", email="ada.lovelace@x.com")
    assert "is too repetitive" in password_problems("aaaaaaaaaaaabbbb")


def test_opaque_tokens_are_hashed() -> None:
    token, digest = new_opaque_token()
    assert len(token) >= 40
    assert digest == hash_token(token)
    assert token not in digest


def test_access_token_roundtrip_and_tampering() -> None:
    settings = make_settings()
    user, org, family = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    token, _ = create_access_token(settings, user_id=user, org_id=org, family_id=family)
    claims = decode_access_token(settings, token)
    assert claims is not None
    assert (claims.user_id, claims.org_id, claims.family_id) == (user, org, family)

    other_secret = make_settings()
    assert decode_access_token(other_secret, token) is None

    payload = jwt.decode(token, options={"verify_signature": False})
    payload["org"] = str(uuid.uuid4())
    forged = jwt.encode(payload, "attacker-key-" + "x" * 32, algorithm="HS256")
    assert decode_access_token(settings, forged) is None

    none_alg = jwt.encode(payload, None, algorithm="none")
    assert decode_access_token(settings, none_alg) is None


def test_expired_token_rejected() -> None:
    settings = make_settings()
    token, _ = create_access_token(settings, user_id=uuid.uuid4(), org_id=uuid.uuid4(), family_id=uuid.uuid4())
    payload = jwt.decode(token, options={"verify_signature": False})
    payload["exp"] = payload["iat"] - 10
    expired = jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm="HS256")
    assert decode_access_token(settings, expired) is None
