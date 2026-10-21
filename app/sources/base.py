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

# Structured Logging
logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger("geoai.sources")

class SourceResult(BaseModel, Generic[T]):
    source: str
    ok: bool
    fetched_at: float = Field(default_factory=time.time)
    params: dict[str, Any]
    data: T | None = None
    error: str | None = None
    cached: bool = False
    latency_ms: float = 0.0

class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures = 0
        self.last_failure_time = 0.0

    def is_open(self) -> bool:
        if self.failures >= self.failure_threshold:
            if time.time() - self.last_failure_time < self.recovery_timeout:
                return True
            # Reset after timeout
            self.failures = 0
        return False

    def record_failure(self):
        self.failures += 1
        self.last_failure_time = time.time()

    def record_success(self):
        self.failures = 0

# Global state for rate limiters and circuit breakers
GLOBAL_LIMITERS: dict[str, asyncio.Semaphore] = {}
GLOBAL_BREAKERS: dict[str, CircuitBreaker] = {}
app_cache = Cache(settings.CACHE_DIR)

class BaseSource:
    def __init__(self, name: str, rate_limit: int | None = None):
        self.name = name
        if rate_limit:
            GLOBAL_LIMITERS[name] = asyncio.Semaphore(rate_limit)
        GLOBAL_BREAKERS[name] = CircuitBreaker()

    async def _request(
        self,
        method: str,
        url: str,
        params: dict | None = None,
        json_data: dict | None = None,
        timeout: float = settings.DEFAULT_TIMEOUT,
        retries: int = 2
    ) -> httpx.Response:
        # Circuit Breaker Check
        breaker = GLOBAL_BREAKERS[self.name]
        if breaker.is_open():
            raise RuntimeError(f"Circuit breaker open for {self.name}")

        # Rate Limiter Check
        limiter = GLOBAL_LIMITERS.get(self.name)

        async with httpx.AsyncClient(
            headers={"User-Agent": settings.user_agent},
            timeout=timeout
        ) as client:
            for attempt in range(retries + 1):
                try:
                    if limiter:
                        async with limiter:
                            resp = await client.request(
                                method, url, params=params, json=json_data
                            )
                    else:
                        resp = await client.request(
                            method, url, params=params, json=json_data
                        )

                    resp.raise_for_status()
                    breaker.record_success()
                    return resp
                except (httpx.HTTPStatusError, httpx.RequestError) as e:
                    if attempt == retries:
                        breaker.record_failure()
                        raise e

                    # Exponential backoff: 1s, 2s, 4s...
                    wait_time = (2 ** attempt)
                    logger.warning(f"Retry {attempt+1}/{retries} for {self.name} after {wait_time}s due to {e}")
                    await asyncio.sleep(wait_time)

    async def fetch(
        self,
        cache_key: str,
        ttl: int,
        fetch_fn: Callable[[httpx.AsyncClient], Awaitable[T]],
        params: dict[str, Any]
    ) -> SourceResult[T]:
        # 1. Check Cache
        cached_val = app_cache.get(cache_key)
        if cached_val:
            return SourceResult(
                source=self.name, ok=True, params=params,
                data=cached_val, cached=True, latency_ms=0.0
            )

        # 2. Circuit Breaker check (inside fetch_fn via _request)
        start_time = time.perf_counter()
        try:
            # We pass a client created by _request or similar;
            # to keep it clean, fetch_fn will call self._request
            data = await fetch_fn(self)
            latency = (time.perf_counter() - start_time) * 1000

            app_cache.set(cache_key, data, expire=ttl)

            return SourceResult(
                source=self.name, ok=True, params=params,
                data=data, cached=False, latency_ms=latency
            )
        except Exception as e:
            latency = (time.perf_counter() - start_time) * 1000
            logger.error(f"Source {self.name} failed: {e}")
            return SourceResult(
                source=self.name, ok=False, params=params,
                error=str(e), cached=False, latency_ms=latency
            )
