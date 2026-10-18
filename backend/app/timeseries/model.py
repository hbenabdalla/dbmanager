"""Engine-neutral time-series data model shared by every storage implementation.

A *series* is identified by its metric name plus its label set; a *sample* is one
(series, timestamp, value). Timestamps are always UTC (epoch milliseconds on the wire).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal


class MetricType(StrEnum):
    GAUGE = "gauge"  # a level that goes up and down (connections, CPU %, lag)
    COUNTER = "counter"  # monotonically increasing total since an origin (resets allowed)
    RATE = "rate"  # per-second value already derived by the collector from counters
    DISTRIBUTION = "distribution"  # a statistic of a distribution as reported (mean / max latency)


# --- label policy -------------------------------------------------------------------------
#: The only label keys any sample may carry. Everything else (environment, site, owner,
#: application …) is inventory metadata in PostgreSQL and is joined at query time, so moving a
#: server between environments never forks its history and labels stay low-cardinality.
ALLOWED_LABELS: frozenset[str] = frozenset(
    {
        "server_id",  # UUID of the server row (scope/RBAC key)
        "engine",  # postgresql | mysql | … (bounded)
        "data_source",  # real | simulated (deletion key for simulated telemetry)
        "database",  # database name on the server (bounded per server)
        "wait_type",  # engine wait class (bounded by the engine)
        "fingerprint",  # query fingerprint (bounded by the per-server cap)
        "tablespace",  # tablespace / filegroup name (bounded per server)
    }
)
REQUIRED_LABELS: frozenset[str] = frozenset({"server_id", "engine", "data_source"})
MAX_LABEL_VALUE_LENGTH = 128
_NAME_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")


class LabelPolicyError(ValueError):
    pass


def check_labels(labels: dict[str, str]) -> None:
    """Reject labels that would create unbounded cardinality (SQL text, session ids, errors…)."""
    unknown = set(labels) - ALLOWED_LABELS
    if unknown:
        raise LabelPolicyError(f"label(s) not allowed: {sorted(unknown)}")
    missing = REQUIRED_LABELS - set(labels)
    if missing:
        raise LabelPolicyError(f"missing required label(s): {sorted(missing)}")
    for k, v in labels.items():
        if len(v) > MAX_LABEL_VALUE_LENGTH or "\n" in v:
            raise LabelPolicyError(f"label {k!r} value too long or multi-line")


@dataclass(frozen=True, slots=True)
class Sample:
    name: str
    labels: tuple[tuple[str, str], ...]
    value: float
    ts_ms: int

    @staticmethod
    def make(name: str, labels: dict[str, str], value: float, ts: datetime | int) -> Sample:
        if not _NAME_RE.match(name):
            raise LabelPolicyError(f"invalid metric name {name!r}")
        check_labels(labels)
        ts_ms = ts if isinstance(ts, int) else to_ms(ts)
        return Sample(name, tuple(sorted(labels.items())), float(value), ts_ms)

    @property
    def label_dict(self) -> dict[str, str]:
        return dict(self.labels)

    @property
    def finite(self) -> bool:
        return math.isfinite(self.value)


def to_ms(ts: datetime) -> int:
    if ts.tzinfo is None:
        raise ValueError("naive datetimes are not allowed: metrics are stored in UTC")
    return int(ts.timestamp() * 1000)


def from_ms(ms: float) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


@dataclass(frozen=True)
class Selector:
    """Which series to read. Translated by each implementation into its own query language."""

    metrics: tuple[str, ...]
    server_ids: tuple[str, ...] | None = None  # None = no server restriction (caller checked RBAC)
    match: tuple[tuple[str, str], ...] = ()  # exact label matches
    match_in: tuple[tuple[str, tuple[str, ...]], ...] = ()  # label value in set

    @staticmethod
    def of(
        metrics: list[str] | tuple[str, ...] | str,
        server_ids: list[str] | None = None,
        **match: str | list[str] | tuple[str, ...] | None,
    ) -> Selector:
        names = (metrics,) if isinstance(metrics, str) else tuple(metrics)
        exact = tuple(sorted((k, v) for k, v in match.items() if isinstance(v, str)))
        multi = tuple(sorted((k, tuple(v)) for k, v in match.items() if isinstance(v, (list, tuple))))
        return Selector(names, tuple(server_ids) if server_ids is not None else None, exact, multi)


Rollup = Literal["avg", "min", "max", "sum", "count", "last", "p50", "p95", "p99", "rate", "increase", "stddev"]
GroupAgg = Literal["sum", "avg", "max", "min", "count"]


@dataclass
class Series:
    labels: dict[str, str]
    points: list[tuple[int, float]] = field(default_factory=list)  # (ts_ms, value)

    @property
    def name(self) -> str:
        return self.labels.get("__name__", "")


@dataclass
class SeriesValue:
    labels: dict[str, str]
    ts_ms: int
    value: float

    @property
    def name(self) -> str:
        return self.labels.get("__name__", "")


@dataclass
class StorageStatus:
    engine: str
    healthy: bool
    version: str | None = None
    retention: str | None = None
    series: int | None = None
    samples: int | None = None
    newest_ms: int | None = None
    details: dict[str, object] = field(default_factory=dict)
    error: str | None = None
