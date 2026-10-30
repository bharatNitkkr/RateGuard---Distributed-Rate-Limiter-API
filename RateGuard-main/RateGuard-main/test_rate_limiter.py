import asyncio

import fakeredis.aioredis
import pytest
import pytest_asyncio

from app.rate_limiter import SlidingWindowRateLimiter


@pytest_asyncio.fixture
async def redis():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.mark.asyncio
async def test_allows_requests_within_limit(redis):
    limiter = SlidingWindowRateLimiter(redis)
    for _ in range(5):
        result = await limiter.check("user:1", "/api/test", limit=5, window_seconds=60)
        assert result.allowed

    # 6th request in the same window should be rejected.
    result = await limiter.check("user:1", "/api/test", limit=5, window_seconds=60)
    assert not result.allowed
    assert result.retry_after_seconds > 0


@pytest.mark.asyncio
async def test_different_identifiers_have_independent_limits(redis):
    limiter = SlidingWindowRateLimiter(redis)
    for _ in range(3):
        await limiter.check("user:a", "/api/test", limit=3, window_seconds=60)

    # user:a is now exhausted, user:b should be unaffected.
    result_a = await limiter.check("user:a", "/api/test", limit=3, window_seconds=60)
    result_b = await limiter.check("user:b", "/api/test", limit=3, window_seconds=60)

    assert not result_a.allowed
    assert result_b.allowed


@pytest.mark.asyncio
async def test_different_routes_have_independent_limits(redis):
    limiter = SlidingWindowRateLimiter(redis)
    for _ in range(2):
        await limiter.check("user:1", "/api/a", limit=2, window_seconds=60)

    result_a = await limiter.check("user:1", "/api/a", limit=2, window_seconds=60)
    result_b = await limiter.check("user:1", "/api/b", limit=2, window_seconds=60)

    assert not result_a.allowed
    assert result_b.allowed


@pytest.mark.asyncio
async def test_window_slides_and_frees_capacity(redis):
    limiter = SlidingWindowRateLimiter(redis)
    # Use a tiny 1-second window so the test runs fast.
    for _ in range(2):
        await limiter.check("user:1", "/api/test", limit=2, window_seconds=1)

    blocked = await limiter.check("user:1", "/api/test", limit=2, window_seconds=1)
    assert not blocked.allowed

    await asyncio.sleep(1.1)

    freed = await limiter.check("user:1", "/api/test", limit=2, window_seconds=1)
    assert freed.allowed


@pytest.mark.asyncio
async def test_concurrent_requests_never_exceed_limit(redis):
    """
    Fires 50 concurrent requests at a limit of 10 and asserts that
    exactly 10 get admitted — this is the race-condition check that a
    naive read-then-write (non-atomic) implementation would fail,
    since concurrent readers could all see "count < limit" before any
    of them writes.
    """
    limiter = SlidingWindowRateLimiter(redis)
    results = await asyncio.gather(
        *[
            limiter.check("user:1", "/api/burst", limit=10, window_seconds=60)
            for _ in range(50)
        ]
    )
    allowed_count = sum(1 for r in results if r.allowed)
    assert allowed_count == 10
