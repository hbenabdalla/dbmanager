"""Small sliding-window rate limiter.

Per-process and in-memory: it protects each replica from bursts. Account lockout (the
security-relevant control) is stored in the application database and therefore holds
across replicas. A shared limiter (e.g. Redis) can replace this class without API changes.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def hit(self, key: str) -> bool:
        """Record a hit. Returns False when the key is over its limit."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self._hits) > 50_000:  # bound memory under key-spray attacks
                for k in list(self._hits)[:10_000]:
                    self._hits.pop(k, None)
            return True

    def blocked(self, key: str) -> bool:
        """True when the key is at its limit (does not record a hit)."""
        now = time.monotonic()
        with self._lock:
            q = self._hits.get(key)
            if not q:
                return False
            while q and now - q[0] > self.window:
                q.popleft()
            return len(q) >= self.limit

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
