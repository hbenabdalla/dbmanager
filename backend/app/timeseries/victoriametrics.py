"""VictoriaMetrics implementation of :class:`TimeSeriesRepository` (single-node HTTP API).

* writes: ``/api/v1/import/prometheus`` (text format with explicit timestamps, gzip)
* reads: ``/api/v1/query`` and ``/api/v1/query_range`` with MetricsQL (POST forms, so large
  server-id lists never hit URL length limits)
* deletes: ``/api/v1/admin/tsdb/delete_series``
* duplicates: the container runs with ``-dedup.minScrapeInterval=1ms`` so a retried or
  re-migrated sample with the same series and timestamp is stored once.
"""

from __future__ import annotations

import gzip
import logging
import re
import time
from datetime import datetime
from typing import Any

import httpx

from app.core.metrics import TIMESERIES_QUERY_LATENCY
from app.timeseries.model import GroupAgg, Rollup, Sample, Selector, Series, SeriesValue, StorageStatus
from app.timeseries.repository import TimeSeriesError, TimeSeriesRepository, TimeSeriesUnavailable

log = logging.getLogger(__name__)

_QUANTILE = {"p50": 0.5, "p95": 0.95, "p99": 0.99}
_ROLLUP_FN = {
    "avg": "avg_over_time",
    "min": "min_over_time",
    "max": "max_over_time",
    "sum": "sum_over_time",
    "count": "count_over_time",
    "last": "last_over_time",
    "stddev": "stddev_over_time",
    "rate": "rate",
    "increase": "increase",
}
_REGEX_META = re.compile(r"([\\.^$|?*+()\[\]{}])")


def _quote(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _regex_alt(values: tuple[str, ...] | list[str]) -> str:
    return "|".join(_REGEX_META.sub(r"\\\1", v) for v in values)


def selector_expr(sel: Selector) -> str | None:
    """MetricsQL series selector, or None when the selector can match nothing."""
    if sel.server_ids is not None and not sel.server_ids:
        return None
    parts = []
    if len(sel.metrics) == 1:
        parts.append(f"__name__={_quote(sel.metrics[0])}")
    else:
        parts.append(f"__name__=~{_quote(_regex_alt(sel.metrics))}")
    if sel.server_ids is not None:
        ids = sel.server_ids
        parts.append(f"server_id={_quote(ids[0])}" if len(ids) == 1 else f"server_id=~{_quote(_regex_alt(ids))}")
    for k, v in sel.match:
        parts.append(f"{k}={_quote(v)}")
    for k, vs in sel.match_in:
        if not vs:
            return None
        parts.append(f"{k}=~{_quote(_regex_alt(vs))}")
    return "{" + ",".join(parts) + "}"


def rollup_expr(sel_expr: str, rollup: Rollup, window: str | None = None) -> str:
    """Rollup that keeps metric names (MetricsQL drops them by default), so one query can
    return several metrics."""
    w = f"[{window}]" if window else ""
    if rollup in _QUANTILE:
        return f"quantile_over_time({_QUANTILE[rollup]}, {sel_expr}{w}) keep_metric_names"
    return f"{_ROLLUP_FN[rollup]}({sel_expr}{w}) keep_metric_names"


def group_expr(expr: str, group_by: tuple[str, ...] | None, agg: GroupAgg) -> str:
    if group_by is None:
        return expr
    keys = ("__name__", *[g for g in group_by if g != "__name__"])
    return f"{agg} by ({','.join(keys)})({expr})"


def sample_line(s: Sample) -> str:
    labels = ",".join(f"{k}={_quote(v)}" for k, v in s.labels)
    return f"{s.name}{{{labels}}} {s.value!r} {s.ts_ms}"


class VictoriaMetricsRepository(TimeSeriesRepository):
    engine = "victoriametrics"

    def __init__(
        self,
        url: str,
        username: str | None = None,
        password: str | None = None,
        timeout: float = 10.0,
        retention: str | None = None,
    ):
        self.url = url.rstrip("/")
        self.retention = retention
        auth = httpx.BasicAuth(username, password or "") if username else None
        self._client = httpx.AsyncClient(
            base_url=self.url,
            auth=auth,
            timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    # --- transport ------------------------------------------------------------------------
    async def _request(self, method: str, path: str, op: str, **kw: Any) -> httpx.Response:
        started = time.perf_counter()
        try:
            r = await self._client.request(method, path, **kw)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TimeSeriesUnavailable(f"time-series storage unreachable: {type(exc).__name__}") from exc
        finally:
            TIMESERIES_QUERY_LATENCY.labels(op).observe(time.perf_counter() - started)
        if r.status_code >= 500 or r.status_code in (401, 403):
            # auth problems are configuration errors that make storage unusable, not bad queries
            raise TimeSeriesUnavailable(f"time-series storage answered HTTP {r.status_code}")
        if r.status_code >= 400:
            raise TimeSeriesError(f"time-series storage rejected the request: {r.text[:300]}")
        return r

    async def _query(self, op: str, path: str, form: dict[str, Any]) -> list[dict[str, Any]]:
        r = await self._request("POST", path, op, data=form)
        body = r.json()
        if body.get("status") != "success":
            raise TimeSeriesError(str(body.get("error"))[:300])
        return body["data"]["result"]

    # --- write ----------------------------------------------------------------------------
    async def write_metrics_batch(self, samples: list[Sample]) -> int:
        lines = [sample_line(s) for s in samples if s.finite]
        if not lines:
            return 0
        payload = ("\n".join(lines) + "\n").encode()
        headers = {"Content-Type": "text/plain"}
        if len(payload) > 4096:
            payload = gzip.compress(payload, compresslevel=5)
            headers["Content-Encoding"] = "gzip"
        await self._request("POST", "/api/v1/import/prometheus", "write", content=payload, headers=headers)
        return len(lines)

    # --- read -----------------------------------------------------------------------------
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
        sel = selector_expr(selector)
        if sel is None:
            return []
        # Without an explicit window MetricsQL uses max(step, sample interval): every step gets a
        # value even when it is shorter than the collection interval, and long ranges are
        # downsampled at query time.
        expr = group_expr(rollup_expr(sel, rollup), group_by, group_agg)
        result = await self._query(
            "query_range",
            "/api/v1/query_range",
            {"query": expr, "start": f"{start.timestamp():.3f}", "end": f"{end.timestamp():.3f}", "step": f"{step_s}s"},
        )
        out = []
        for r in result:
            pts = [(int(float(t) * 1000), float(v)) for t, v in r.get("values", [])]
            out.append(Series(labels=r["metric"], points=[(t, v) for t, v in pts if v == v]))
        return out

    async def get_latest(self, selector: Selector, lookback_s: int = 900) -> list[SeriesValue]:
        sel = selector_expr(selector)
        if sel is None:
            return []
        values = await self._query(
            "latest", "/api/v1/query", {"query": f"last_over_time({sel}[{lookback_s}s]) keep_metric_names"}
        )
        stamps = await self._query(
            "latest", "/api/v1/query", {"query": f"tlast_over_time({sel}[{lookback_s}s]) keep_metric_names"}
        )
        ts_by_key = {_key(r["metric"]): float(r["value"][1]) for r in stamps}
        out = []
        for r in values:
            labels = r["metric"]
            ts = ts_by_key.get(_key(labels), float(r["value"][0]))
            out.append(SeriesValue(labels=labels, ts_ms=int(ts * 1000), value=float(r["value"][1])))
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
        sel = selector_expr(selector)
        if sel is None:
            return []
        window = f"{max(int((end - start).total_seconds()), 1)}s"
        expr = group_expr(rollup_expr(sel, fn, window), group_by, group_agg)
        result = await self._query("aggregate", "/api/v1/query", {"query": expr, "time": f"{end.timestamp():.3f}"})
        return [
            SeriesValue(labels=r["metric"], ts_ms=int(float(r["value"][0]) * 1000), value=float(r["value"][1]))
            for r in result
        ]

    async def top(self, selector: Selector, k: int, lookback_s: int = 900, fn: Rollup = "last") -> list[SeriesValue]:
        sel = selector_expr(selector)
        if sel is None:
            return []
        expr = rollup_expr(sel, fn, f"{int(lookback_s)}s")
        result = await self._query("top", "/api/v1/query", {"query": f"topk({int(k)}, {expr})"})
        out = [
            SeriesValue(labels=r["metric"], ts_ms=int(float(r["value"][0]) * 1000), value=float(r["value"][1]))
            for r in result
        ]
        out.sort(key=lambda v: v.value, reverse=True)
        return out

    # --- maintenance ----------------------------------------------------------------------
    async def delete_series(self, match: dict[str, str | list[str]]) -> None:
        if not match or any(isinstance(v, list) and not v for v in match.values()):
            raise TimeSeriesError("refusing to delete without a label filter")
        expr = (
            "{"
            + ",".join(
                f"{k}=~{_quote(_regex_alt(v))}" if isinstance(v, list) else f"{k}={_quote(v)}"
                for k, v in sorted(match.items())
            )
            + "}"
        )
        await self._request("POST", "/api/v1/admin/tsdb/delete_series", "delete", data={"match[]": expr})

    async def oldest_sample_ms(self, lookback_days: int) -> int | None:
        res = await self._query(
            "status", "/api/v1/query", {"query": f"min(tfirst_over_time(db_up[{max(int(lookback_days), 1)}d]))"}
        )
        return int(float(res[0]["value"][1]) * 1000) if res else None

    async def flush(self) -> None:
        try:
            await self._request("GET", "/internal/force_flush", "flush")
        except TimeSeriesError:
            pass

    async def health_check(self) -> bool:
        try:
            r = await self._request("GET", "/health", "health")
            return r.status_code == 200
        except TimeSeriesError:
            return False

    async def status(self) -> StorageStatus:
        st = StorageStatus(engine="VictoriaMetrics", healthy=False, retention=self.retention)
        try:
            st.healthy = await self.health_check()
            metrics_text = (await self._request("GET", "/metrics", "status")).text
            m = re.search(r'vm_app_version\{[^}]*short_version="([^"]+)"', metrics_text)
            st.version = m.group(1) if m else None
            rows = re.findall(r'^vm_rows\{type="storage/[^"]+"\} (\d+)', metrics_text, re.M)
            st.samples = sum(int(x) for x in rows) if rows else None
            flags = (await self._request("GET", "/flags", "status")).text
            fm = re.search(r'-retentionPeriod="([^"]+)"', flags)
            if fm:
                st.retention = fm.group(1)
            dm = re.search(r'-dedup.minScrapeInterval="([^"]+)"', flags)
            st.details["deduplication"] = dm.group(1) if dm else None
            tsdb = (await self._request("GET", "/api/v1/status/tsdb", "status")).json().get("data", {})
            st.series = tsdb.get("totalSeries")
            newest = await self._query("status", "/api/v1/query", {"query": "max(tlast_over_time(db_up[1h]))"})
            st.newest_ms = int(float(newest[0]["value"][1]) * 1000) if newest else None
        except TimeSeriesUnavailable as exc:
            st.healthy, st.error = False, str(exc)
        except TimeSeriesError as exc:
            st.error = str(exc)
        return st

    async def close(self) -> None:
        await self._client.aclose()


def _key(labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(labels.items()))
