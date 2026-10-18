"""Decrypting connector credentials and turning an account into a live connector."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import StorageConnector
from app.connectors.oauth import TokenSet
from app.connectors.registry import ConnectorClients, build_connector
from app.core.keyring import Keyring
from app.db.models import ConnectorAccount


def secret_purpose(account_id: uuid.UUID) -> str:
    return f"connector-account:{account_id}"


async def encrypt_secret(db: AsyncSession, keyring: Keyring, account: ConnectorAccount, secret: dict[str, Any]) -> None:
    account.encrypted_secret = await keyring.encrypt(
        db, account.org_id, json.dumps(secret), purpose=secret_purpose(account.id)
    )


async def decrypt_secret(db: AsyncSession, keyring: Keyring, account: ConnectorAccount) -> dict[str, Any]:
    if account.encrypted_secret is None:
        return {}
    plaintext = await keyring.decrypt(db, account.org_id, account.encrypted_secret, purpose=secret_purpose(account.id))
    data = json.loads(plaintext)
    return data if isinstance(data, dict) else {}


async def open_connector(
    db: AsyncSession, keyring: Keyring, clients: ConnectorClients, account: ConnectorAccount
) -> StorageConnector:
    """A connector for `account`; refreshed OAuth tokens are re-encrypted onto the account (same transaction)."""
    secret = await decrypt_secret(db, keyring, account)

    async def save(tokens: TokenSet) -> None:
        await encrypt_secret(db, keyring, account, tokens.to_secret())
        await db.flush()

    return build_connector(clients, account.kind, account.config, secret, save)
