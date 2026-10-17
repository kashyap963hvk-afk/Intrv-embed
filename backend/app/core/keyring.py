"""Per-organization data keys: created on first use, wrapped by the master key, cached in memory."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_value, encrypt_value, new_data_key, unwrap_data_key, wrap_data_key
from app.db.models import OrgKey


class Keyring:
    def __init__(self, master_key: bytes) -> None:
        self._master_key = master_key
        self._cache: dict[uuid.UUID, bytes] = {}

    async def data_key(self, db: AsyncSession, org_id: uuid.UUID) -> bytes:
        """The org's DEK (requires the org's tenant context on `db`)."""
        cached = self._cache.get(org_id)
        if cached is not None:
            return cached
        wrapped = await db.scalar(select(OrgKey.wrapped_dek).where(OrgKey.org_id == org_id))
        if wrapped is None:
            candidate = wrap_data_key(self._master_key, org_id, new_data_key())
            # Concurrent first uses race safely: exactly one insert wins and everyone reads the winner.
            await db.execute(
                insert(OrgKey)
                .values(org_id=org_id, wrapped_dek=candidate)
                .on_conflict_do_nothing(index_elements=["org_id"])
            )
            wrapped = await db.scalar(select(OrgKey.wrapped_dek).where(OrgKey.org_id == org_id))
            if wrapped is None:
                raise RuntimeError("org data key could not be created")
        dek = unwrap_data_key(self._master_key, org_id, wrapped)
        self._cache[org_id] = dek
        return dek

    async def encrypt(self, db: AsyncSession, org_id: uuid.UUID, plaintext: str, *, purpose: str) -> bytes:
        return encrypt_value(await self.data_key(db, org_id), plaintext, purpose=purpose)

    async def decrypt(self, db: AsyncSession, org_id: uuid.UUID, blob: bytes, *, purpose: str) -> str:
        return decrypt_value(await self.data_key(db, org_id), blob, purpose=purpose)

    def forget(self) -> None:
        self._cache.clear()
