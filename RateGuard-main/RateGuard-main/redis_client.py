"""
Single shared async Redis connection pool for the whole app.

FastAPI apps typically live for a long time and serve many concurrent
requests, so we open one pool at startup and reuse it everywhere instead
of opening a connection per-request.
"""
import redis.asyncio as redis

from app.config import settings

_pool: redis.ConnectionPool | None = None
_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    """Return the shared Redis client, creating it lazily on first use."""
    global _pool, _client
    if _client is None:
        _pool = redis.ConnectionPool.from_url(
            settings.REDIS_URL, decode_responses=True
        )
        _client = redis.Redis(connection_pool=_pool)
    return _client


async def close_redis() -> None:
    """Close the pool cleanly on app shutdown."""
    global _pool, _client
    if _client is not None:
        await _client.aclose()
        _client = None
        _pool = None
