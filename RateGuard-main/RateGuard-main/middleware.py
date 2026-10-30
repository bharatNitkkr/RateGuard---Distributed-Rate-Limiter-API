"""
Attaches X-RateLimit-* headers to every response that passed through a
@rate_limit-decorated route, so clients can see their quota without
needing to hit the limit first.
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RateLimitHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        result = getattr(request.state, "rate_limit_result", None)
        if result is not None:
            response.headers["X-RateLimit-Limit"] = str(result.limit)
            response.headers["X-RateLimit-Remaining"] = str(result.remaining)
            if not result.allowed:
                response.headers["Retry-After"] = str(result.retry_after_seconds)

        return response
