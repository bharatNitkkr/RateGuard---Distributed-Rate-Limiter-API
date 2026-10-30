"""
Central configuration for RateGuard.

Pulls everything from environment variables so the service behaves
the same whether you run it with `uvicorn` locally or inside Docker.
"""
import os


class Settings:
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # Default limit applied to any route that doesn't specify its own.
    DEFAULT_LIMIT: int = int(os.getenv("RATEGUARD_DEFAULT_LIMIT", "60"))
    DEFAULT_WINDOW_SECONDS: int = int(os.getenv("RATEGUARD_DEFAULT_WINDOW", "60"))

    # Header used to identify a caller when no authenticated user is present.
    # Falls back to the connecting IP if this header is absent.
    USER_HEADER: str = os.getenv("RATEGUARD_USER_HEADER", "X-User-Id")

    # Prefix for every key RateGuard writes to Redis, so it never collides
    # with other data you might be storing in the same Redis instance.
    KEY_PREFIX: str = os.getenv("RATEGUARD_KEY_PREFIX", "rateguard")


settings = Settings()
