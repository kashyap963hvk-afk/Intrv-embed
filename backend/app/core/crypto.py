"""Envelope encryption for secrets at rest (API keys, OAuth tokens, candidate PII).

  KEK (MASTER_KEY_BASE64, 32 bytes) --wraps--> per-org DEK (stored in org_keys) --encrypts--> values

Both layers use AES-256-GCM with associated data, so a ciphertext only decrypts for the org (and the purpose)
it was written for: copying an encrypted API key into another row or org fails authentication.
Format: b"v1" + 12-byte nonce + ciphertext||tag.
"""

from __future__ import annotations

import os
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = b"v1"
_NONCE_BYTES = 12
_KEY_BYTES = 32


class DecryptionError(Exception):
    """Ciphertext is corrupt, was encrypted under another key, or for another purpose."""


def _seal(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(_NONCE_BYTES)
    return _VERSION + nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def _open(key: bytes, blob: bytes, aad: bytes) -> bytes:
    if len(blob) < len(_VERSION) + _NONCE_BYTES + 16 or not blob.startswith(_VERSION):
        raise DecryptionError("unsupported ciphertext format")
    nonce = blob[len(_VERSION) : len(_VERSION) + _NONCE_BYTES]
    try:
        return AESGCM(key).decrypt(nonce, blob[len(_VERSION) + _NONCE_BYTES :], aad)
    except InvalidTag as exc:
        raise DecryptionError("authentication failed") from exc


def new_data_key() -> bytes:
    return os.urandom(_KEY_BYTES)


def _dek_aad(org_id: uuid.UUID) -> bytes:
    return b"intrv:dek:" + org_id.bytes


def wrap_data_key(kek: bytes, org_id: uuid.UUID, dek: bytes) -> bytes:
    return _seal(kek, dek, _dek_aad(org_id))


def unwrap_data_key(kek: bytes, org_id: uuid.UUID, wrapped: bytes) -> bytes:
    dek = _open(kek, wrapped, _dek_aad(org_id))
    if len(dek) != _KEY_BYTES:
        raise DecryptionError("invalid data key length")
    return dek


def encrypt_value(dek: bytes, plaintext: str, *, purpose: str) -> bytes:
    return _seal(dek, plaintext.encode(), b"intrv:value:" + purpose.encode())


def decrypt_value(dek: bytes, blob: bytes, *, purpose: str) -> str:
    return _open(dek, blob, b"intrv:value:" + purpose.encode()).decode()
