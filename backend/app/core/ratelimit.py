"""Fixed-window rate limiting backed by Valkey."""

from __future__ import annotations

from redis.asyncio import Redis

from app.core.errors import RateLimitedError


async def enforce_rate_limit(valkey: Redis, key: str, *, limit: int, window_s: int) -> None:
    """Count one hit on `key`; raise RateLimitedError when more than `limit` hits fall in the window."""
    redis_key = f"rl:{key}"
    async with valkey.pipeline(transaction=True) as pipe:
        pipe.incr(redis_key)
        pipe.expire(redis_key, window_s, nx=True)
        pipe.ttl(redis_key)
        count, _, ttl = await pipe.execute()
    if int(count) > limit:
        raise RateLimitedError(retry_after_s=max(int(ttl), 1))
