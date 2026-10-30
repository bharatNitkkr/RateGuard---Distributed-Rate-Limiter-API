"""
Sliding-window rate limiter backed by Redis sorted sets.

Why a sorted set + Lua script instead of a simple INCR counter?
-----------------------------------------------------------------
A fixed-window counter (INCR + EXPIRE) lets a client burst up to
2x the limit right at the window boundary (e.g. 60 requests in the
last second of one window, then another 60 in the first second of
the next). A sliding window log fixes this by keeping a timestamped
entry per request and only counting the ones inside the trailing
`window_seconds`.

The catch is that "count entries, then decide whether to add one"
is a read-then-write sequence. Under concurrency, two requests can
both read a count just under the limit and both get admitted,
letting the caller sneak past the limit. Redis is single-threaded
per command, but our sequence is multiple commands — so we ship the
whole sequence as one Lua script. Redis runs Lua scripts atomically,
so the read-count-and-maybe-write happens as one indivisible step
no matter how many requests arrive at the same instant.
"""
import time
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis

from app.config import settings

# KEYS[1] = the sorted-set key for this identifier+route
# ARGV[1] = current unix time in milliseconds
# ARGV[2] = window size in milliseconds
# ARGV[3] = max requests allowed in the window
# ARGV[4] = unique member id for this request (only used if admitted)
#
# Returns: {allowed (0/1), current_count, oldest_timestamp_or_-1}
_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

local window_start = now - window

-- Drop entries that have already fallen out of the window.
redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)

local current = redis.call('ZCARD', key)

if current < limit then
    redis.call('ZADD', key, now, member)
    -- Key only needs to live as long as the window itself.
    redis.call('PEXPIRE', key, window)
    return {1, current + 1, -1}
else
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local oldest_ts = -1
    if oldest[2] ~= nil then
        oldest_ts = oldest[2]
    end
    return {0, current, oldest_ts}
end
"""


@dataclass
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int


class SlidingWindowRateLimiter:
    """Thin wrapper that registers the Lua script once and calls it per request."""

    def __init__(self, redis: Redis):
        self._redis = redis
        self._script = redis.register_script(_SLIDING_WINDOW_LUA)

    async def check(
        self,
        identifier: str,
        route: str,
        limit: int = settings.DEFAULT_LIMIT,
        window_seconds: int = settings.DEFAULT_WINDOW_SECONDS,
    ) -> RateLimitResult:
        key = f"{settings.KEY_PREFIX}:{route}:{identifier}"
        now_ms = int(time.time() * 1000)
        window_ms = window_seconds * 1000
        member = f"{now_ms}-{uuid.uuid4().hex[:8]}"

        allowed, current_count, oldest_ts = await self._script(
            keys=[key], args=[now_ms, window_ms, limit, member]
        )

        if allowed:
            remaining = max(limit - int(current_count), 0)
            return RateLimitResult(
                allowed=True,
                limit=limit,
                remaining=remaining,
                retry_after_seconds=0,
            )

        # Rejected — tell the caller when the oldest request in the
        # window will expire, since that's the earliest moment a new
        # request could be admitted.
        oldest_ts = int(oldest_ts)
        if oldest_ts == -1:
            retry_after = window_seconds
        else:
            retry_after_ms = (oldest_ts + window_ms) - now_ms
            retry_after = max(1, (retry_after_ms + 999) // 1000)  # round up

        return RateLimitResult(
            allowed=False,
            limit=limit,
            remaining=0,
            retry_after_seconds=retry_after,
        )
