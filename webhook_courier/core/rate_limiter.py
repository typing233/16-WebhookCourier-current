import time
import threading
from collections import defaultdict


class TokenBucketLimiter:
    """Per-endpoint token bucket rate limiter.

    Each endpoint gets its own bucket that refills at `rate` tokens/sec
    up to `rate` capacity (1-second burst window).
    """

    def __init__(self):
        self._buckets: dict[str, float] = {}
        self._last_refill: dict[str, float] = {}
        self._rates: dict[str, int] = defaultdict(lambda: 50)
        self._lock = threading.Lock()

    def configure(self, endpoint_id: str, rate_per_sec: int):
        with self._lock:
            self._rates[endpoint_id] = rate_per_sec
            if endpoint_id not in self._buckets:
                self._buckets[endpoint_id] = float(rate_per_sec)
                self._last_refill[endpoint_id] = time.monotonic()

    def acquire(self, endpoint_id: str) -> bool:
        with self._lock:
            now = time.monotonic()
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
                return True
            return False

    def remove(self, endpoint_id: str):
        with self._lock:
            self._buckets.pop(endpoint_id, None)
            self._last_refill.pop(endpoint_id, None)
            self._rates.pop(endpoint_id, None)


rate_limiter = TokenBucketLimiter()
