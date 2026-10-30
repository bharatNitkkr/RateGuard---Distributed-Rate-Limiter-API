"""
Public decorator: `@rate_limit(...)`.

Drop this on any FastAPI path function and it's rate-limited — no
change to the route body, no separate registration step. It works by
wrapping the handler, checking Redis before the handler runs, and
either calling through or raising a 429.

The route function must accept `request: Request` as a parameter,
which is the standard way FastAPI exposes the incoming request object.
"""
import functools
import logging
from typing import Callable

from fastapi import HTTPException, Request

from app.config import settings
from app.rate_limiter import SlidingWindowRateLimiter
from app.redis_client import get_redis

logger = logging.getLogger("rateguard")


def _identify_caller(request: Request) -> str:
    """
    Prefer an authenticated/user-supplied identity header so limits are
    per-user even behind a shared NAT or proxy. Fall back to the
    connecting IP for anonymous traffic.
    """
    user_id = request.headers.get(settings.USER_HEADER)
    if user_id:
        return f"user:{user_id}"

    client_ip = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # First entry is the original client when behind a proxy chain.
        client_ip = forwarded.split(",")[0].strip()
    return f"ip:{client_ip}"


def rate_limit(limit: int | None = None, window_seconds: int | None = None) -> Callable:
    """
    Rate-limit a single route.

    Usage:
        @app.get("/search")
        @rate_limit(limit=10, window_seconds=60)
        async def search(request: Request):
            ...

    Omit `limit`/`window_seconds` to fall back to the service-wide
    defaults in `app.config.settings`.
    """
    effective_limit = limit or settings.DEFAULT_LIMIT
    effective_window = window_seconds or settings.DEFAULT_WINDOW_SECONDS

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            request: Request | None = kwargs.get("request")
            if request is None:
                for arg in args:
                    if isinstance(arg, Request):
                        request = arg
                        break
            if request is None:
                raise RuntimeError(
                    f"@rate_limit requires '{func.__name__}' to declare a "
                    "'request: Request' parameter."
                )

            limiter = SlidingWindowRateLimiter(get_redis())
            identifier = _identify_caller(request)
            route_key = request.url.path

            result = await limiter.check(
                identifier=identifier,
                route=route_key,
                limit=effective_limit,
                window_seconds=effective_window,
            )

            # Stash the result so a response-header middleware (or the
            # handler itself) can surface X-RateLimit-* headers.
            request.state.rate_limit_result = result

            if not result.allowed:
                logger.warning(
                    "rate_limit_exceeded identifier=%s route=%s limit=%s window=%ss",
                    identifier, route_key, effective_limit, effective_window,
                )
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests. Please slow down.",
                    headers={"Retry-After": str(result.retry_after_seconds)},
                )

            return await func(*args, **kwargs)

        return wrapper

    return decorator
