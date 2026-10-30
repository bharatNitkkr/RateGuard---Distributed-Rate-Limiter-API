# RateGuard — Distributed Rate Limiter API

A rate-limiting service built with **FastAPI** that throttles requests per
user or IP using a **sliding-window algorithm backed by Redis**, so limits
hold correctly even across multiple app instances.

## How it works

- **Algorithm**: sliding-window log, implemented with a Redis sorted set
  (`ZSET`) per `(route, identifier)`. Each request is a timestamped member;
  on every check, entries older than the window are trimmed, the remaining
  count is compared against the limit, and — if there's room — a new entry
  is added.
- **Atomicity**: the trim-count-add sequence runs as a single **Lua script**
  via `EVAL`, so it executes as one indivisible operation on the Redis
  server. This is what prevents the race condition where two concurrent
  requests both read "count < limit" before either has written, which would
  let a caller sneak past the limit under load. See
  `tests/test_rate_limiter.py::test_concurrent_requests_never_exceed_limit`
  for a test that fires 50 concurrent requests at a limit of 10 and asserts
  exactly 10 are admitted.
- **Distributed by construction**: because state lives in Redis rather than
  in-process memory, any number of API replicas behind a load balancer share
  the same limit correctly — that's the "distributed" part.
- **Identity**: a caller is identified by the `X-User-Id` header if present
  (so limits are per authenticated user even behind a shared IP), falling
  back to the client IP (`X-Forwarded-For` if behind a proxy, else the
  socket address) for anonymous traffic.

## One-line opt-in per route

```python
@app.post("/api/expensive")
@rate_limit(limit=3, window_seconds=60)
async def expensive_endpoint(request: Request):
    return {"message": "Expensive operation completed."}
```

Any route without the decorator is completely unaffected — rate limiting is
opt-in per endpoint, with its own limit and window.

## Responses

- Requests within the limit get `X-RateLimit-Limit` and
  `X-RateLimit-Remaining` headers.
- Requests over the limit get a `429 Too Many Requests` with a
  `Retry-After` header (seconds until the oldest request in the current
  window expires), and are logged via the `rateguard` logger for
  monitoring.

## Running it

### With Docker (recommended)

```bash
docker compose up --build
```

This starts the API on `http://localhost:8000` and a Redis instance
alongside it — no local Python or Redis setup needed.

### Locally

```bash
pip install -r requirements.txt
redis-server &          # or point REDIS_URL at any running Redis
uvicorn app.main:app --reload
```

## Try it

```bash
# First 3 succeed, 4th is rejected (limit=3/60s on this route)
for i in 1 2 3 4; do
  curl -i -X POST http://localhost:8000/api/expensive -H "X-User-Id: devesh"
done
```

## Tests

```bash
pytest tests/ -v
```

Tests run against [`fakeredis`](https://github.com/cunla/fakeredis-py) (with
Lua scripting via `lupa`), so no live Redis instance is required. They cover:
per-identifier isolation, per-route isolation, the window actually sliding
open again after it expires, and — the important one — that concurrent
requests never exceed the configured limit.

## Project layout

```
app/
  config.py         # env-driven settings
  redis_client.py    # shared async Redis connection pool
  rate_limiter.py     # the sliding-window Lua-script core
  limiter.py          # the @rate_limit decorator + caller identification
  middleware.py        # attaches X-RateLimit-* response headers
  main.py               # FastAPI app + example routes
tests/
  test_rate_limiter.py  # unit tests incl. concurrency check
Dockerfile
docker-compose.yml
requirements.txt
```

## Configuration (env vars)

| Variable                  | Default                      | Meaning                              |
|---------------------------|-------------------------------|---------------------------------------|
| `REDIS_URL`                | `redis://localhost:6379/0`   | Redis connection string               |
| `RATEGUARD_DEFAULT_LIMIT`  | `60`                          | Default max requests per window       |
| `RATEGUARD_DEFAULT_WINDOW` | `60`                          | Default window size, in seconds       |
| `RATEGUARD_USER_HEADER`    | `X-User-Id`                  | Header used to identify a caller      |
| `RATEGUARD_KEY_PREFIX`     | `rateguard`                  | Redis key namespace                   |

## Possible extensions

- Swap the sliding-window log for a token-bucket if you need to allow
  short bursts above the steady-state rate.
- Add a Redis Cluster-aware key hashing scheme (`{tag}` hash slots) if you
  outgrow a single Redis node.
- Emit rejected-request metrics to Prometheus instead of just logging them.
