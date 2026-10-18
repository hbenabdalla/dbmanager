"""Deterministic signal generator for demo servers.

Values are a pure function of (server seed, metric, timestamp) so historical backfill and
live collection produce one continuous, reproducible series. Nothing here is ever used for
real servers.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime


def _unit(seed: str, *parts: object) -> float:
    """Deterministic pseudo-random float in [0, 1)."""
    h = hashlib.blake2b(("|".join([seed, *map(str, parts)])).encode(), digest_size=8).digest()
    return int.from_bytes(h, "big") / 2**64


def noise(seed: str, metric: str, ts: datetime, bucket_s: int = 300) -> float:
    """Smooth noise in [-1, 1] interpolated between buckets."""
    t = ts.timestamp() / bucket_s
    a, b = math.floor(t), math.floor(t) + 1
    fa, fb = _unit(seed, metric, a) * 2 - 1, _unit(seed, metric, b) * 2 - 1
    w = t - a
    w = w * w * (3 - 2 * w)
    return fa * (1 - w) + fb * w


def diurnal(ts: datetime, peak_hour: float = 14.0) -> float:
    """Business-hours load curve in [0, 1] (UTC based)."""
    hour = ts.hour + ts.minute / 60
    base = 0.5 + 0.5 * math.cos((hour - peak_hour) / 24 * 2 * math.pi)
    weekend = ts.weekday() >= 5
    return base * (0.55 if weekend else 1.0)


def signal(seed: str, metric: str, ts: datetime, base: float, amp: float = 0.4, jitter: float = 0.08) -> float:
    load = diurnal(ts)
    v = base * (1 - amp + 2 * amp * load) * (1 + jitter * noise(seed, metric, ts))
    return max(v, 0.0)


def pick(seed: str, key: str, options: list[object]) -> object:
    return options[int(_unit(seed, key) * len(options)) % len(options)]


def rnd(seed: str, key: str, lo: float, hi: float) -> float:
    return lo + (hi - lo) * _unit(seed, key)
