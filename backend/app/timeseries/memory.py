"""In-memory :class:`TimeSeriesRepository` for tests and container-less development.

It follows the same semantics as the VictoriaMetrics implementation (deduplication by
(series, timestamp), rollups over steps, grouping) closely enough for unit and API tests.
Not for production: data lives in process memory only.
"""

from __future__ import annotations

import bisect
import statistics
from datetime import UTC, datetime, timedelta
from typing import Any

from app.timeseries.model import GroupAgg, Rollup, Sample, Selector, Series, SeriesValue, StorageStatus
from app.timeseries.repository import TimeSeriesError, TimeSeriesRepository, TimeSeriesUnavailable

SeriesKey = tuple[str, tuple[tuple[str, str], ...]]


def _quantile(vals: list[float], q: float) -> float:
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _increase(points: list[tuple[int, float]]) -> float:
    total, prev = 0.0, None
    for _, v in points:
        if prev is not None:
            total += v - prev if v >= prev else v  # counter reset
        prev = v
    return total


def rollup_values(points: list[tuple[int, float]], fn: Rollup) -> float | None:
    if not points:
        return None
    vals = [v for _, v in points]
    if fn == "avg":
        return sum(vals) / len(vals)
    if fn == "min":
        return min(vals)
    if fn == "max":
        return max(vals)
    if fn == "sum":
        return sum(vals)
    if fn == "count":
        return float(len(vals))
    if fn == "last":
        return vals[-1]
    if fn == "stddev":
        return statistics.pstdev(vals) if len(vals) > 1 else 0.0
    if fn in ("p50", "p95", "p99"):
        return _quantile(vals, {"p50": 0.5, "p95": 0.95, "p99": 0.99}[fn])
    if fn in ("rate", "increase"):
        if len(points) < 2:
            return None
        inc = _increase(points)
        if fn == "increase":
            return inc
        dt = (points[-1][0] - points[0][0]) / 1000
        return inc / dt if dt > 0 else None
    raise TimeSeriesError(f"unknown rollup {fn}")


def _group(
    items: list[tuple[dict[str, str], Any]], group_by: tuple[str, ...], agg: GroupAgg
) -> dict[tuple[tuple[str, str], ...], list[Any]]:
    out: dict[tuple[tuple[str, str], ...], list[Any]] = {}
    keys = ("__name__", *group_by)
    for labels, val in items:
        k = tuple((g, labels[g]) for g in keys if g in labels)
        out.setdefault(k, []).append(val)
    return out


def _agg(vals: list[float], agg: GroupAgg) -> float:
    if agg == "sum":
        return sum(vals)
    if agg == "avg":
        return sum(vals) / len(vals)
    if agg == "max":
        return max(vals)
    if agg == "min":
        return min(vals)
    return float(len(vals))


class InMemoryTimeSeriesRepository(TimeSeriesRepository):
    engine = "memory"

    def __init__(self) -> None:
        self._data: dict[SeriesKey, dict[int, float]] = {}
        self.available = True  # tests flip this to simulate an outage
        self.writes = 0

    def _check(self) -> None:
        if not self.available:
            raise TimeSeriesUnavailable("time-series storage unreachable (simulated outage)")

    async def write_metrics_batch(self, samples: list[Sample]) -> int:
        self._check()
        n = 0
        for s in samples:
            if not s.finite:
                continue
            self._data.setdefault((s.name, s.labels), {})[s.ts_ms] = s.value
            n += 1
        self.writes += 1
        return n

    def _match(self, sel: Selector) -> list[tuple[dict[str, str], list[tuple[int, float]]]]:
        if sel.server_ids is not None and not sel.server_ids:
            return []
        ids = set(sel.server_ids) if sel.server_ids is not None else None
        out = []
        for (name, labels), pts in self._data.items():
            if name not in sel.metrics:
                continue
            ld = dict(labels)
            if ids is not None and ld.get("server_id") not in ids:
                continue
            if any(ld.get(k, "") != v for k, v in sel.match):  # "" = label absent (as in PromQL)
                continue
            if any(ld.get(k) not in vs for k, vs in sel.match_in):
                continue
            out.append(({"__name__": name, **ld}, sorted(pts.items())))
        return out

    @staticmethod
    def _window(
        points: list[tuple[int, float]], start_ms: int, end_ms: int, fn: Rollup | None = None
    ) -> list[tuple[int, float]]:
        """Samples in ``(start, end]``; counter functions also see the sample just before the
        window (as VictoriaMetrics does), so an increase is never lost between steps."""
        ts = [p[0] for p in points]
        lo = bisect.bisect_right(ts, start_ms)
        if fn in ("rate", "increase") and lo > 0:
            lo -= 1
        return points[lo : bisect.bisect_right(ts, end_ms)]

    async def query_range(
        self,
        selector: Selector,
        start: datetime,
        end: datetime,
        step_s: int,
        rollup: Rollup = "avg",
        group_by: tuple[str, ...] | None = None,
        group_agg: GroupAgg = "sum",
    ) -> list[Series]:
        self._check()
        s_ms, e_ms, step_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000), step_s * 1000
        per_series: list[tuple[dict[str, str], list[tuple[int, float]]]] = []
        for labels, pts in self._match(selector):
            gaps = [b[0] - a[0] for a, b in zip(pts, pts[1:], strict=False)]
            interval = statistics.median(gaps) if gaps else step_ms
            window = max(step_ms, int(interval))
            out_pts = []
            t = s_ms
            while t <= e_ms:
                v = rollup_values(self._window(pts, t - window, t, rollup), rollup)
                if v is not None:
                    out_pts.append((t, v))
                t += step_ms
            if out_pts:
                per_series.append((labels, out_pts))
        if group_by is None:
            return [Series(labels=lbl, points=pts) for lbl, pts in per_series]
        grouped: dict[tuple[tuple[str, str], ...], dict[int, list[float]]] = {}
        for k, lists in _group([(lbl, pts) for lbl, pts in per_series], group_by, group_agg).items():
            acc = grouped.setdefault(k, {})
            for pts in lists:
                for t, v in pts:
                    acc.setdefault(t, []).append(v)
        return [
            Series(labels=dict(k), points=[(t, _agg(vs, group_agg)) for t, vs in sorted(acc.items())])
            for k, acc in grouped.items()
        ]

    async def get_latest(self, selector: Selector, lookback_s: int = 900) -> list[SeriesValue]:
        self._check()
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        out = []
        for labels, pts in self._match(selector):
            recent = [p for p in pts if p[0] >= now_ms - lookback_s * 1000 and p[0] <= now_ms]
            if recent:
                out.append(SeriesValue(labels=labels, ts_ms=recent[-1][0], value=recent[-1][1]))
        return out

    async def aggregate(
        self,
        selector: Selector,
        start: datetime,
        end: datetime,
        fn: Rollup,
        group_by: tuple[str, ...] | None = None,
        group_agg: GroupAgg = "sum",
    ) -> list[SeriesValue]:
        self._check()
        s_ms, e_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        items = []
        for labels, pts in self._match(selector):
            v = rollup_values(self._window(pts, s_ms, e_ms, fn), fn)
            if v is not None:
                items.append((labels, v))
        if group_by is None:
            return [SeriesValue(labels=lbl, ts_ms=e_ms, value=v) for lbl, v in items]
        return [
            SeriesValue(labels=dict(k), ts_ms=e_ms, value=_agg(vs, group_agg))
            for k, vs in _group(items, group_by, group_agg).items()
        ]

    async def top(self, selector: Selector, k: int, lookback_s: int = 900, fn: Rollup = "last") -> list[SeriesValue]:
        if fn == "last":
            values = await self.get_latest(selector, lookback_s)
        else:
            end = datetime.now(UTC)
            values = await self.aggregate(selector, end - timedelta(seconds=lookback_s), end, fn)
        return sorted(values, key=lambda v: v.value, reverse=True)[:k]

    async def delete_series(self, match: dict[str, str | list[str]]) -> None:
        self._check()
        if not match or any(isinstance(v, list) and not v for v in match.values()):
            raise TimeSeriesError("refusing to delete without a label filter")

        def hit(name: str, labels: dict[str, str], k: str, v: str | list[str]) -> bool:
            actual = name if k == "__name__" else labels.get(k)
            return actual in v if isinstance(v, list) else actual == v

        for key in [k for k in self._data if all(hit(k[0], dict(k[1]), m, v) for m, v in match.items())]:
            del self._data[key]

    async def oldest_sample_ms(self, lookback_days: int) -> int | None:
        self._check()
        return min((min(p) for (n, _l), p in self._data.items() if n == "db_up" and p), default=None)

    async def health_check(self) -> bool:
        return self.available

    async def status(self) -> StorageStatus:
        if not self.available:
            return StorageStatus(engine="in-memory (tests)", healthy=False, error="simulated outage")
        newest = max((max(p) for p in self._data.values() if p), default=None)
        return StorageStatus(
            engine="in-memory (tests)",
            healthy=True,
            version="n/a",
            retention="process lifetime",
            series=len(self._data),
            samples=sum(len(p) for p in self._data.values()),
            newest_ms=newest,
        )

    def clear(self) -> None:
        self._data.clear()
