"""FastAPI dependencies — shared across routers.

Provides:
  • ``verify_api_key``   — validates the ``X-API-Key`` header.
  • ``RateLimiter``      — in-memory sliding-window rate limiter keyed by
                           API key.  No Redis dependency required for the
                           Docker Compose demo.  Redis integration notes are
                           in the README.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from .config import settings

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# API-key authentication
# ────────────────────────────────────────────────────────────────────────────

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(
    api_key: Annotated[str | None, Security(_api_key_header)],
) -> str:
    """FastAPI dependency that validates the ``X-API-Key`` request header.

    Returns the validated key (used as the rate-limiter key).
    Raises HTTP 401 if missing or wrong.

    Security note
    ─────────────
    This is a shared-secret scheme suitable for a bootcamp/demo deployment
    where the Flutter app is the only client.  For production:
      • Issue per-device keys stored in a database.
      • Migrate to OAuth 2.0 / JWT for richer identity and revocation.
    """
    if api_key is None or api_key != settings.proxy_api_key:
        logger.warning("auth_failed", extra={"reason": "INVALID_API_KEY"})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key header.",
        )
    return api_key


# ────────────────────────────────────────────────────────────────────────────
# In-memory rate limiter
# ────────────────────────────────────────────────────────────────────────────

# State: api_key → deque of request timestamps (epoch seconds).
_rate_limit_state: dict[str, deque[float]] = defaultdict(deque)

WINDOW_SECONDS = 60  # sliding window size


def _check_rate_limit(api_key: str, limit: int) -> None:
    """Enforce a sliding-window rate limit.

    Raises HTTP 429 if the key has exceeded ``limit`` requests in the last
    ``WINDOW_SECONDS`` seconds.

    In-memory trade-offs
    ────────────────────
    • Zero external dependencies — works out of the box in the Docker Compose
      demo.
    • State is per-process; under multi-worker deployments each worker has its
      own counter, effectively multiplying the limit by the worker count.
    • For production, replace with a Redis-backed counter (e.g. ``slowapi``
      with a Redis store, or ``fastapi-limiter``).  The ``settings`` object
      already exposes ``rate_limit_per_minute`` so no code changes are needed
      beyond swapping the storage backend.
    """
    now = time.monotonic()
    window_start = now - WINDOW_SECONDS
    bucket = _rate_limit_state[api_key]

    # Remove timestamps outside the current window.
    while bucket and bucket[0] < window_start:
        bucket.popleft()

    if len(bucket) >= limit:
        logger.warning("rate_limit_exceeded", extra={"key_prefix": api_key[:6]})
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: max {limit} requests per {WINDOW_SECONDS}s.",
        )

    bucket.append(now)


async def rate_limit(
    request: Request,
    api_key: Annotated[str, Depends(verify_api_key)],
) -> None:
    """FastAPI dependency that combines auth + rate limiting.

    Usage in a router::

        @router.post("/endpoint")
        async def handler(
            _: Annotated[None, Depends(rate_limit)],
            ...
        ):
            ...
    """
    _check_rate_limit(api_key, settings.rate_limit_per_minute)
