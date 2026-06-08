import time
import threading
from collections import defaultdict

from webhook_courier.config import settings


class TokenBucketLimiter:
    """Per-endpoint + global token bucket rate limiter.

    Each endpoint gets its own bucket that refills at `rate` tokens/sec.
    A global bucket caps total throughput across all endpoints.
    """

    def __init__(self):
        self._buckets: dict[str, float] = {}
        self._last_refill: dict[str, float] = {}
        self._rates: dict[str, int] = defaultdict(lambda: 50)
        self._lock = threading.Lock()
        self._global_tokens: float = float(settings.GLOBAL_RATE_LIMIT_PER_SEC)
        self._global_last_refill: float = time.monotonic()
        self._global_rate: int = settings.GLOBAL_RATE_LIMIT_PER_SEC

    def configure(self, endpoint_id: str, rate_per_sec: int):
        with self._lock:
            self._rates[endpoint_id] = rate_per_sec
            if endpoint_id not in self._buckets:
                self._buckets[endpoint_id] = float(rate_per_sec)
                self._last_refill[endpoint_id] = time.monotonic()

    def acquire(self, endpoint_id: str) -> bool:
        with self._lock:
            now = time.monotonic()

            # Global rate limit
            elapsed_global = now - self._global_last_refill
            self._global_tokens = min(
                float(self._global_rate),
                self._global_tokens + elapsed_global * self._global_rate
            )
            self._global_last_refill = now
            if self._global_tokens < 1.0:
                from webhook_courier.metrics.collector import metrics
                metrics.counter_inc("rate_limit_rejections_total", labels={"scope": "global"})
                return False

            # Per-endpoint rate limit
            rate = self._rates[endpoint_id]
            if endpoint_id not in self._buckets:
                self._buckets[endpoint_id] = float(rate)
                self._last_refill[endpoint_id] = now

            elapsed = now - self._last_refill[endpoint_id]
            self._buckets[endpoint_id] = min(
                float(rate), self._buckets[endpoint_id] + elapsed * rate
            )
            self._last_refill[endpoint_id] = now

            if self._buckets[endpoint_id] >= 1.0:
                self._buckets[endpoint_id] -= 1.0
                self._global_tokens -= 1.0
                return True

            from webhook_courier.metrics.collector import metrics
            metrics.counter_inc("rate_limit_rejections_total", labels={"scope": "endpoint", "endpoint_id": endpoint_id})
            return False

    def remove(self, endpoint_id: str):
        with self._lock:
            self._buckets.pop(endpoint_id, None)
            self._last_refill.pop(endpoint_id, None)
            self._rates.pop(endpoint_id, None)

    def update_global_rate(self, rate: int):
        with self._lock:
            self._global_rate = rate


rate_limiter = TokenBucketLimiter()
