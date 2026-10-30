import logging

from fastapi import FastAPI, Request

from app.limiter import rate_limit
from app.middleware import RateLimitHeadersMiddleware
from app.redis_client import close_redis, get_redis

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="RateGuard",
    description="Distributed sliding-window rate limiter for FastAPI, backed by Redis.",
    version="1.0.0",
)
app.add_middleware(RateLimitHeadersMiddleware)


@app.on_event("startup")
async def startup() -> None:
    # Touch Redis once at boot so a bad REDIS_URL fails fast instead of
    # on the first request.
    await get_redis().ping()


@app.on_event("shutdown")
async def shutdown() -> None:
    await close_redis()


@app.get("/health")
async def health():
    return {"status": "ok"}


# --- Example routes showing per-endpoint limits ---------------------------

@app.get("/api/public")
@rate_limit(limit=100, window_seconds=60)
async def public_endpoint(request: Request):
    """Generous limit — cheap, read-only data."""
    return {"message": "This is a lightly-throttled public endpoint."}


@app.get("/api/search")
@rate_limit(limit=10, window_seconds=60)
async def search_endpoint(request: Request, q: str = ""):
    """Tighter limit — simulates an expensive search/query operation."""
    return {"query": q, "results": []}


@app.post("/api/expensive")
@rate_limit(limit=3, window_seconds=60)
async def expensive_endpoint(request: Request):
    """Very tight limit — simulates something like sending an email or OTP."""
    return {"message": "Expensive operation completed."}


@app.get("/api/default")
async def default_endpoint(request: Request):
    """No decorator at all — unaffected, proving the decorator is opt-in per route."""
    return {"message": "Not rate-limited."}
