"""Sign in with Google: ID token claim checks and the redirect target after sign-in."""

from __future__ import annotations

import time
from typing import Any

import pytest

from app.auth.google import GoogleSignInError, safe_next, verify_claims
from tests.conftest import make_settings

SETTINGS = make_settings(google_oauth_client_id="client-123", google_oauth_client_secret="secret")


def claims(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": "client-123",
        "exp": time.time() + 300,
        "nonce": "n-1",
        "sub": "1111",
        "email": "Priya.Member@Gmail.com",
        "email_verified": True,
        "name": "Priya",
    }
    return {**base, **overrides}


def test_valid_claims_give_a_lowercased_verified_email() -> None:
    identity = verify_claims(claims(), SETTINGS, "n-1")
    assert (identity.sub, identity.email, identity.name) == ("1111", "priya.member@gmail.com", "Priya")


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"iss": "https://evil.example.com"}, "not issued by Google"),
        ({"aud": "someone-else"}, "different application"),
        ({"exp": time.time() - 1}, "expired"),
        ({"nonce": "other"}, "could not be matched"),
        ({"email_verified": False}, "no verified email"),
        ({"email": ""}, "no verified email"),
    ],
)
def test_bad_claims_are_refused(override: dict[str, Any], message: str) -> None:
    with pytest.raises(GoogleSignInError, match=message):
        verify_claims(claims(**override), SETTINGS, "n-1")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/app/interviews", "/app/interviews"),
        (None, "/app"),
        ("https://evil.example.com", "/app"),
        ("//evil.example.com/x", "/app"),
        ("/\\evil.example.com", "/app"),
    ],
)
def test_next_is_always_a_same_site_path(value: str | None, expected: str) -> None:
    assert safe_next(value) == expected
