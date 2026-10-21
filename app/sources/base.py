"""Shared plumbing for every live data source.

Every source returns a `SourceResult` envelope, honours per-source rate limits,
retries with exponential backoff, trips a circuit breaker after repeated
failures, and caches responses with a TTL. Failures are cached for a short
period so a flapping upstream does not get hammered on every request.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeVar

import httpx
from diskcache import Cache
from pydantic import BaseModel, Field

from app.config import settings

T = TypeVar("T")

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger("geoai.sources")


class SourceResult(BaseModel, Generic[T]):
    source: str
    ok: bool
    fetched_at: float = Field(default_factory=time.time)
    params: dict[str, Any] = Field(default_factory=dict)
    data: T | None = None
    error: str | None = None
    cached: bool = False
    latency_ms: float = 0.0
    degraded: bool = False


class CircuitBreaker:
    """Trip after N consecutive failures, recover after a cool-down."""

    def __init__(self, failure_threshold: int = 3, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures = 0
        self.last_failure_time = 0.0

    def is_open(self) -> bool:
        if self.failures >= self.failure_threshold:
            if time.time() - self.last_failure_time < self.recovery_timeout:
                return True
            self.failures = 0
        return False

    def record_failure(self) -> None:
        self.failures += 1
        self.last_failure_time = time.time()

    def record_success(self) -> None:
        self.failures = 0


GLOBAL_LIMITERS: dict[str, asyncio.Semaphore] = {}
GLOBAL_BREAKERS: dict[str, CircuitBreaker] = {}
app_cache = Cache(settings.CACHE_DIR)

FAILURE_CACHE_TTL = 60


class BaseSource:
    def __init__(self, name: str, rate_limit: int | None = None):
        self.name = name
        if rate_limit:
            GLOBAL_LIMITERS[name] = asyncio.Semaphore(rate_limit)
        GLOBAL_BREAKERS.setdefault(name, CircuitBreaker())

    @property
    def headers(self) -> dict[str, str]:
        return {
            "User-Agent": settings.user_agent_for(self.name),
            "Accept": "application/json",
        }

    async def _request(
        self,
        method: str,
        url: str,
        params: dict | None = None,
        json_data: dict | None = None,
        data: dict | None = None,
        headers: dict | None = None,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> httpx.Response:
        breaker = GLOBAL_BREAKERS[self.name]
        if breaker.is_open():
            raise RuntimeError(f"Circuit breaker open for {self.name}")

        limiter = GLOBAL_LIMITERS.get(self.name)
        timeout = timeout if timeout is not None else settings.DEFAULT_TIMEOUT
        retries = settings.MAX_RETRIES if retries is None else retries
        merged_headers = {**self.headers, **(headers or {})}

        async with httpx.AsyncClient(
            headers=merged_headers, timeout=timeout, follow_redirects=True
        ) as client:
            last_exc: Exception | None = None
            for attempt in range(retries + 1):
                try:
                    if limiter:
                        async with limiter:
                            resp = await client.request(
                                method,
                                url,
                                params=params,
                                json=json_data,
                                data=data,
                                headers=merged_headers,
                            )
                    else:
                        resp = await client.request(
                            method,
                            url,
                            params=params,
                            json=json_data,
                            data=data,
                            headers=merged_headers,
                        )
                    resp.raise_for_status()
                    breaker.record_success()
                    return resp
                except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                    last_exc = exc
                    if attempt == retries:
                        break
                    wait = 2**attempt
                    logger.warning(
                        "Retry %s/%s for %s after %ss (%s)",
                        attempt + 1,
                        retries,
                        self.name,
                        wait,
                        exc,
                    )
                    await asyncio.sleep(wait)
            breaker.record_failure()
            raise last_exc if last_exc else RuntimeError("request failed")

    async def fetch(
        self,
        cache_key: str,
        ttl: int,
        fetch_fn: Callable[[BaseSource], Awaitable[T]],
        params: dict[str, Any],
    ) -> SourceResult[T]:
        cached_val = app_cache.get(f"{settings.CACHE_VERSION}:{cache_key}")
        if cached_val is not None:
            return SourceResult(
                source=self.name,
                ok=True,
                params=params,
                data=cached_val,
                cached=True,
                latency_ms=0.0,
            )

        # A source that just failed is skipped for a moment instead of being
        # retried on every request, which is what made a flaky Overpass cost
        # 15 seconds each time.
        fail_key = f"fail:{settings.CACHE_VERSION}:{cache_key}"
        if app_cache.get(fail_key) is not None:
            return SourceResult(
                source=self.name,
                ok=False,
                params=params,
                error=str(app_cache.get(fail_key)),
                cached=True,
                latency_ms=0.0,
            )

        start = time.perf_counter()
        try:
            data = await fetch_fn(self)
        except Exception as exc:  # noqa: BLE001 - a source failure must never crash the request
            latency = (time.perf_counter() - start) * 1000
            logger.error("Source %s failed: %s", self.name, exc)
            # Cache the failure briefly so a dead upstream is not retried on
            # every single request, but the caller still gets a clean error.
            app_cache.set(
                f"fail:{settings.CACHE_VERSION}:{cache_key}", str(exc), expire=FAILURE_CACHE_TTL
            )
            return SourceResult(
                source=self.name,
                ok=False,
                params=params,
                error=str(exc),
                cached=False,
                latency_ms=latency,
            )

        latency = (time.perf_counter() - start) * 1000
        app_cache.set(f"{settings.CACHE_VERSION}:{cache_key}", data, expire=ttl)
        return SourceResult(
            source=self.name,
            ok=True,
            params=params,
            data=data,
            cached=False,
            latency_ms=latency,
        )
