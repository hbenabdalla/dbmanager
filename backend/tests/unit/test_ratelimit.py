"""Sliding-window counter: limits, Retry-After accuracy and the in-memory backend."""

from __future__ import annotations

import random

import pytest

from app.services.ratelimit import MemoryBackend, Policy, headers, max_current, retry_after


class Clock:
    def __init__(self, t: float = 999_980.0):  # 20 s into a window
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_previous_window_weight_decays_linearly() -> None:
    assert max_current(0, 0, 10, 60) == 10
    assert max_current(10, 0, 10, 60) == 0  # previous window still counts fully
    assert max_current(10, 30, 10, 60) == 5
    assert max_current(10, 59.9, 10, 60) == 9
    assert max_current(4, 45, 10, 60) == 9


def test_retry_after_examples() -> None:
    # 10 hits at the start of a window, limit 10: next window + 6 s of decay (10 × 0.9 = 9)
    assert retry_after(0, 10, 0, 10, 60) == 66
    # refused only because the previous window still weighs too much: wait inside this window
    assert retry_after(10, 2, 10, 10, 60) == 8  # at 18 s: 10 × (1 − 18/60) + 2 + 1 = 10
    assert 1 <= retry_after(0, 1, 59.5, 1, 60) <= 61


@pytest.mark.asyncio
async def test_memory_backend_enforces_limit_and_recovers_after_retry_after() -> None:
    clock = Clock(999_960.0)  # window boundary (16666 × 60)
    b, p = MemoryBackend(clock), Policy("expensive", 10)
    for _ in range(10):
        assert (await b.hit("k", p)).allowed
    refused = await b.hit("k", p)
    assert not refused.allowed and refused.remaining == 0 and refused.retry_after == 66
    clock.t += refused.retry_after - 1
    assert not (await b.hit("k", p)).allowed  # one second early is still refused
    clock.t += 1
    assert (await b.hit("k", p)).allowed
    assert (await b.hit("other", p)).allowed  # buckets are independent


@pytest.mark.asyncio
async def test_refused_hits_are_not_counted() -> None:
    clock = Clock(999_960.0)
    b, p = MemoryBackend(clock), Policy("api", 3)
    for _ in range(3):
        await b.hit("k", p)
    for _ in range(50):  # a client hammering while refused does not extend its own ban
        assert not (await b.hit("k", p)).allowed
    assert (await b.hit("k", p)).retry_after == retry_after(0, 3, 0, 3, 60)


@pytest.mark.asyncio
async def test_peek_does_not_record() -> None:
    b, p = MemoryBackend(Clock()), Policy("token_failures", 1)
    assert (await b.hit("ip", p, record=False)).allowed
    assert (await b.hit("ip", p, record=False)).allowed
    assert (await b.hit("ip", p)).allowed
    assert not (await b.hit("ip", p, record=False)).allowed


@pytest.mark.asyncio
async def test_retry_after_is_exact_for_random_traffic() -> None:
    """Whenever a hit is refused, the same hit is refused one second before Retry-After and
    accepted at Retry-After (no other traffic in between)."""
    rnd = random.Random(7)  # noqa: S311 - deterministic test traffic, not cryptography
    for _ in range(200):
        limit = rnd.randint(1, 20)
        clock = Clock(1_000_000.0 + rnd.uniform(0, 60))
        b, p = MemoryBackend(clock), Policy("api", limit)
        for _ in range(rnd.randint(0, 3 * limit)):
            clock.t += rnd.uniform(0, 4)
            await b.hit("k", p)
        d = await b.hit("k", p, record=False)
        if d.allowed:
            continue
        start = clock.t
        if d.retry_after > 1:
            clock.t = start + d.retry_after - 1
            assert not (await b.hit("k", p, record=False)).allowed
        clock.t = start + d.retry_after
        assert (await b.hit("k", p, record=False)).allowed


@pytest.mark.asyncio
async def test_refusal_headers() -> None:
    b, p = MemoryBackend(Clock()), Policy("expensive", 1)
    await b.hit("k", p)
    d = await b.hit("k", p)
    h = headers(d, "expensive")
    assert h["Retry-After"] == str(d.retry_after) and int(h["Retry-After"]) >= 1
    assert h["RateLimit-Remaining"] == "0" and h["RateLimit-Limit"] == "1"
    assert h["RateLimit-Policy"] == '"expensive";q=1;w=60'
