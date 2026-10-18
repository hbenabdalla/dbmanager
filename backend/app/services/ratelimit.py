"""Rate limiting shared by every backend replica.

Algorithm: *sliding-window counter*. Hits are counted in fixed windows aligned to the epoch
(60 s); the rate at time ``t`` is estimated as ``previous × (1 − elapsed / window) + current``.
A hit is accepted when the estimate including it stays within the limit. Refused hits are not
counted, so a client that backs off for ``Retry-After`` seconds is served again.

Backends (``DBM_RATE_LIMIT_BACKEND``):

* ``database`` (default) — counters in the ``rate_limit_windows`` table of the control-plane
  PostgreSQL database, written through a separate small connection pool. Every replica sees the same counts; an atomic
  ``INSERT … ON CONFLICT DO UPDATE … WHERE hits < max`` makes concurrent hits safe. Each bucket
  keeps the current and the previous window only; idle buckets are removed by the retention job.
* ``memory`` — the same algorithm per process (single-instance installations, tests).

Policies and their settings:

======================  ======================================  ==================================
policy                  applies to                              setting (per minute, 0 = off)
======================  ======================================  ==================================
``api``                 every authenticated request             ``DBM_API_RATE_PER_USER_PER_MINUTE``
``target``              live queries against a managed database ``DBM_API_RATE_TARGET_PER_MINUTE``
``expensive``           SQL execution, plans, administration,   ``DBM_API_RATE_EXPENSIVE_PER_MINUTE``
                        exports, connection and channel tests
``login``               login attempts per source IP            ``DBM_LOGIN_RATE_PER_IP_PER_MINUTE``
``token_failures``      failed Bearer authentications per IP    ``DBM_API_TOKEN_AUTH_FAILURES_PER_MINUTE``
======================  ======================================  ==================================

API policies are counted per *principal*: an interactive session counts against its user, an
API token against that token. If the limiter's own storage fails, the request is allowed (the
failure is logged) — rate limiting protects capacity, it is not an authorization control.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Protocol

from sqlalchemy import text

from app.core.config import get_settings
from app.core.metrics import RATE_LIMITED

log = logging.getLogger(__name__)

WINDOW_SECONDS = 60
#: tolerance for float rounding at exact window/decay boundaries
_EPS = 1e-6

_SETTING = {
    "api": "api_rate_per_user_per_minute",
    "target": "api_rate_target_per_minute",
    "expensive": "api_rate_expensive_per_minute",
    "login": "login_rate_per_ip_per_minute",
    "token_failures": "api_token_auth_failures_per_minute",
}
POLICIES = tuple(_SETTING)


@dataclass(frozen=True)
class Policy:
    name: str
    limit: int
    window: int = WINDOW_SECONDS

    @property
    def enabled(self) -> bool:
        return self.limit > 0


def policy(name: str) -> Policy:
    """Current limits (read on every call, so changed settings apply without a restart)."""
    return Policy(name, int(getattr(get_settings(), _SETTING[name])))


@dataclass(frozen=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    #: seconds until a new request would be accepted (0 when allowed)
    retry_after: int
    #: seconds until the current window ends
    reset: int


# --------------------------------------------------------------------------------------------
# pure functions (unit-tested)
def max_current(prev: int, elapsed: float, limit: int, window: int) -> int:
    """Largest count the current window may reach given the weighted previous window."""
    weight = max(0.0, 1.0 - elapsed / window)
    return math.floor(limit - prev * weight + _EPS)


def retry_after(prev: int, curr: int, elapsed: float, limit: int, window: int) -> int:
    """Seconds until one more hit would be accepted (estimate + 1 ≤ limit)."""
    room = limit - curr - 1
    if room >= 0 and prev > 0:
        # still in this window: wait until the previous window's weight has decayed enough
        t = window * (1.0 - room / prev) - elapsed
        if elapsed + t < window:
            return max(1, math.ceil(t - _EPS))
    elif room >= 0:
        return 1
    # next window: the current count becomes the decaying "previous" one
    to_next = window - elapsed
    if curr <= limit - 1:
        return max(1, math.ceil(to_next - _EPS))
    t_next = window * (1.0 - (limit - 1) / curr) if curr > 0 else 0.0
    return max(1, min(2 * window, math.ceil(to_next + t_next - _EPS)))


def decide(prev: int, curr: int, elapsed: float, p: Policy, *, accepted: bool) -> Decision:
    """Decision after the backend tried to record a hit (``curr`` includes it when accepted)."""
    reset = max(1, math.ceil(p.window - elapsed))
    if accepted:
        est = prev * max(0.0, 1.0 - elapsed / p.window) + curr
        return Decision(True, p.limit, max(0, math.floor(p.limit - est)), 0, reset)
    return Decision(False, p.limit, 0, retry_after(prev, curr, elapsed, p.limit, p.window), reset)


# --------------------------------------------------------------------------------------------
class Backend(Protocol):
    async def hit(self, bucket: str, p: Policy, *, record: bool = True) -> Decision: ...

    async def reset(self) -> None: ...


class MemoryBackend:
    """Per-process counters (same algorithm as the database backend)."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._counts: dict[tuple[str, int], int] = {}
        self._lock = Lock()

    async def hit(self, bucket: str, p: Policy, *, record: bool = True) -> Decision:
        now = self.clock()
        start = int(now // p.window) * p.window
        elapsed = now - start
        with self._lock:
            prev = self._counts.get((bucket, start - p.window), 0)
            curr = self._counts.get((bucket, start), 0)
            accepted = curr + 1 <= max_current(prev, elapsed, p.limit, p.window)
            if accepted and record:
                curr += 1
                self._counts[(bucket, start)] = curr
                if curr == 1:
                    self._prune(start - p.window)
            elif accepted:
                # peek: report as if the hit were recorded, without recording it
                return decide(prev, curr + 1, elapsed, p, accepted=True)
            return decide(prev, curr, elapsed, p, accepted=accepted)

    def _prune(self, oldest: int) -> None:
        if len(self._counts) > 50_000:  # bound memory under key-spray
            for k in [k for k in self._counts if k[1] < oldest]:
                self._counts.pop(k, None)

    async def reset(self) -> None:
        with self._lock:
            self._counts.clear()


_SELECT = text("SELECT window_start, hits FROM rate_limit_windows WHERE bucket = :b AND window_start IN (:prev, :cur)")
_UPSERT = text(
    "INSERT INTO rate_limit_windows (bucket, window_start, hits) VALUES (:b, :cur, 1) "
    "ON CONFLICT (bucket, window_start) DO UPDATE SET hits = rate_limit_windows.hits + 1 "
    "WHERE rate_limit_windows.hits < :max RETURNING hits"
)
_PRUNE = text("DELETE FROM rate_limit_windows WHERE bucket = :b AND window_start < :prev")


class DatabaseBackend:
    """Counters in PostgreSQL, shared across replicas. Each hit is a short transaction on a
    small pool of its own (``DBM_RATE_LIMIT_POOL_SIZE``), independent of the request's session:
    a refused or failed request does not roll back the count, the bucket row is not locked
    for the duration of the request, and busy request sessions cannot starve the limiter.
    When no limiter connection is free within 5 s the request is allowed (fail open)."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock

    async def hit(self, bucket: str, p: Policy, *, record: bool = True) -> Decision:
        from app.db.session import get_limiter_engine as get_engine

        now = self.clock()
        start = int(now // p.window) * p.window
        elapsed = now - start
        params = {"b": bucket[:200], "prev": start - p.window, "cur": start}
        async with get_engine().begin() as conn:
            rows = {int(ws): int(h) for ws, h in (await conn.execute(_SELECT, params)).all()}
            prev, curr = rows.get(start - p.window, 0), rows.get(start, 0)
            allowed_max = max_current(prev, elapsed, p.limit, p.window)
            if curr + 1 > allowed_max:
                return decide(prev, curr, elapsed, p, accepted=False)
            if not record:
                return decide(prev, curr + 1, elapsed, p, accepted=True)
            got = (await conn.execute(_UPSERT, {**params, "max": allowed_max})).scalar()
            if got is None:  # another replica took the last slot in between
                return decide(prev, allowed_max, elapsed, p, accepted=False)
            if got == 1:
                await conn.execute(_PRUNE, params)
            return decide(prev, int(got), elapsed, p, accepted=True)

    async def reset(self) -> None:
        from app.db.session import get_limiter_engine as get_engine

        async with get_engine().begin() as conn:
            await conn.execute(text("DELETE FROM rate_limit_windows"))


_backend: Backend | None = None
_backend_kind: str | None = None


def backend() -> Backend:
    global _backend, _backend_kind
    kind = get_settings().rate_limit_backend
    if _backend is None or _backend_kind != kind:
        _backend = DatabaseBackend() if kind == "database" else MemoryBackend()
        _backend_kind = kind
    return _backend


async def check(policy_name: str, key: str, *, record: bool = True) -> Decision:
    """Record a hit for ``key`` under ``policy_name`` (or only look, ``record=False``)."""
    p = policy(policy_name)
    if not p.enabled:
        return Decision(True, 0, 0, 0, 0)
    try:
        d = await backend().hit(f"{p.name}:{key}", p, record=record)
    except Exception:  # noqa: BLE001 - fail open, see module docstring
        log.warning("rate limiter unavailable; request allowed", exc_info=True, extra={"policy": p.name})
        return Decision(True, p.limit, p.limit, 0, p.window)
    if not d.allowed:
        RATE_LIMITED.labels(p.name).inc()
    return d


def headers(d: Decision, p_name: str) -> dict[str, str]:
    """``Retry-After`` (RFC 9110) and the IETF draft ``RateLimit-*`` fields for a refusal."""
    h = {
        "RateLimit-Limit": str(d.limit),
        "RateLimit-Remaining": str(d.remaining),
        "RateLimit-Reset": str(d.retry_after or d.reset),
        "RateLimit-Policy": f'"{p_name}";q={d.limit};w={WINDOW_SECONDS}',
    }
    if not d.allowed:
        h["Retry-After"] = str(d.retry_after)
    return h


async def purge_idle(older_than_seconds: int = 3600) -> int:
    """Remove windows of idle buckets (retention job; database backend only)."""
    if get_settings().rate_limit_backend != "database":
        return 0
    from app.db.session import get_limiter_engine as get_engine

    cutoff = int(time.time()) - older_than_seconds
    async with get_engine().begin() as conn:
        res = await conn.execute(text("DELETE FROM rate_limit_windows WHERE window_start < :c"), {"c": cutoff})
    return res.rowcount or 0
