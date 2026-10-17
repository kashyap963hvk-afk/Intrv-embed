from __future__ import annotations

import os
import uuid

import pytest

from app.core.crypto import (
    DecryptionError,
    decrypt_value,
    encrypt_value,
    new_data_key,
    unwrap_data_key,
    wrap_data_key,
)


def test_value_roundtrip_and_binding() -> None:
    dek = new_data_key()
    blob = encrypt_value(dek, "sk-live-123", purpose="llm_profile.api_key:a")
    assert b"sk-live-123" not in blob
    assert decrypt_value(dek, blob, purpose="llm_profile.api_key:a") == "sk-live-123"
    # Same key, different purpose (e.g. copied into another row): rejected.
    with pytest.raises(DecryptionError):
        decrypt_value(dek, blob, purpose="llm_profile.api_key:b")
    with pytest.raises(DecryptionError):
        decrypt_value(new_data_key(), blob, purpose="llm_profile.api_key:a")


def test_tampering_detected() -> None:
    dek = new_data_key()
    blob = bytearray(encrypt_value(dek, "secret", purpose="p"))
    blob[-1] ^= 0x01
    with pytest.raises(DecryptionError):
        decrypt_value(dek, bytes(blob), purpose="p")
    with pytest.raises(DecryptionError):
        decrypt_value(dek, b"garbage", purpose="p")


def test_data_keys_are_bound_to_their_org() -> None:
    kek = os.urandom(32)
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    dek = new_data_key()
    wrapped = wrap_data_key(kek, org_a, dek)
    assert unwrap_data_key(kek, org_a, wrapped) == dek
    with pytest.raises(DecryptionError):
        unwrap_data_key(kek, org_b, wrapped)
    with pytest.raises(DecryptionError):
        unwrap_data_key(os.urandom(32), org_a, wrapped)
    assert wrap_data_key(kek, org_a, dek) != wrapped  # random nonce
