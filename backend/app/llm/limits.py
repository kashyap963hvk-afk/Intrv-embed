"""Per-profile request budgets and circuit breaker, shared across processes through Valkey."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis

BREAKER_FAILURES = 5
BREAKER_WINDOW_S = 60
BREAKER_OPEN_S = 30


class ProfileLimiter:
    def __init__(self, valkey: Redis) -> None:
        self._valkey = valkey

    async def try_acquire(self, profile_id: uuid.UUID, *, rpm: int | None, rpd: int | None) -> bool:
        """Count one request against the profile's per-minute/per-day budgets; False when over budget."""
        if not rpm and not rpd:
            return True
        now = datetime.now(UTC)
        keys: list[tuple[str, int, int]] = []
        if rpm:
            keys.append((f"llm:rpm:{profile_id}:{now:%Y%m%d%H%M}", rpm, 60))
        if rpd:
            keys.append((f"llm:rpd:{profile_id}:{now:%Y%m%d}", rpd, 86_400))
        async with self._valkey.pipeline(transaction=True) as pipe:
            for key, _, ttl in keys:
                pipe.incr(key)
                pipe.expire(key, ttl, nx=True)
            results = await pipe.execute()
        counts = results[0::2]
        return all(int(count) <= limit for count, (_, limit, _) in zip(counts, keys, strict=True))

    async def is_open(self, profile_id: uuid.UUID) -> bool:
        return bool(await self._valkey.exists(f"llm:cb:open:{profile_id}"))

    async def record_failure(self, profile_id: uuid.UUID) -> None:
        key = f"llm:cb:fail:{profile_id}"
        async with self._valkey.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, BREAKER_WINDOW_S, nx=True)
            failures, _ = await pipe.execute()
        if int(failures) >= BREAKER_FAILURES:
            await self._valkey.set(f"llm:cb:open:{profile_id}", "1", ex=BREAKER_OPEN_S)
            await self._valkey.delete(key)

    async def record_success(self, profile_id: uuid.UUID) -> None:
        await self._valkey.delete(f"llm:cb:fail:{profile_id}")
