"""Buffered, batching metric writer.

Collectors hand normalized samples to :func:`get_writer().submit` and return immediately. A
background task flushes the buffer every ``DBM_METRIC_FLUSH_INTERVAL`` seconds or as soon as
``DBM_METRIC_BATCH_SIZE`` samples are waiting.

Failure handling: a failed batch stays at the head of the buffer and is retried with
exponential backoff (1 s doubling up to ``DBM_METRIC_WRITE_MAX_BACKOFF_SECONDS``); no tight
retry loop. While the storage is down the buffer keeps up to ``DBM_METRIC_BUFFER_MAX`` samples;
beyond that the oldest samples are dropped and counted. A storage outage never raises into the
collectors, the API or the scheduler.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

from app.core.config import get_settings
from app.core.metrics import (
    METRICS_BATCH_SIZE,
    METRICS_BUFFERED,
    METRICS_COLLECTED,
    METRICS_DROPPED,
    METRICS_WRITE_FAILURES,
    METRICS_WRITE_LATENCY,
    METRICS_WRITTEN,
)
from app.timeseries.model import Sample
from app.timeseries.repository import TimeSeriesError, TimeSeriesRepository, get_repository

log = logging.getLogger("app.timeseries.writer")


class MetricWriter:
    def __init__(
        self,
        repo: TimeSeriesRepository | None = None,
        *,
        batch_size: int | None = None,
        flush_interval: float | None = None,
        buffer_max: int | None = None,
        max_backoff: float | None = None,
    ):
        s = get_settings()
        self._repo = repo
        self.batch_size = batch_size or s.metric_batch_size
        self.flush_interval = flush_interval or s.metric_flush_interval
        self.buffer_max = buffer_max or s.metric_buffer_max
        self.max_backoff = max_backoff or s.metric_write_max_backoff_seconds
        self._buf: deque[Sample] = deque()
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stop = False
        self._backoff = 0.0
        self.written_total = 0
        self.failed_batches = 0
        self.dropped_total = 0
        self.last_success: datetime | None = None
        self.last_failure: datetime | None = None
        self.last_error: str | None = None

    @property
    def repo(self) -> TimeSeriesRepository:
        return self._repo or get_repository()

    # --- producer side ----------------------------------------------------------------------
    def submit(self, samples: list[Sample]) -> None:
        """Queue samples (never blocks, never raises)."""
        if not samples:
            return
        METRICS_COLLECTED.inc(len(samples))
        self._buf.extend(samples)
        self._trim()
        if len(self._buf) >= self.batch_size:
            self._wake.set()

    def discard(self, match: dict[str, str | list[str]]) -> int:
        """Drop buffered samples of series that are being deleted (so a flush right after a
        deletion cannot resurrect them)."""

        def hit(smp: Sample) -> bool:
            ld = smp.label_dict
            return all((ld.get(k) in v) if isinstance(v, list) else ld.get(k) == v for k, v in match.items())

        before = len(self._buf)
        self._buf = deque(x for x in self._buf if not hit(x))
        dropped = before - len(self._buf)
        if dropped:
            METRICS_DROPPED.labels("deleted").inc(dropped)
            METRICS_BUFFERED.set(len(self._buf))
        return dropped

    @property
    def pending(self) -> int:
        return len(self._buf)

    # --- flushing ---------------------------------------------------------------------------
    async def _write_one_batch(self) -> bool:
        """Write the head of the buffer. Returns False when the storage failed (the batch is put
        back at the head, so ordering is kept and nothing is lost)."""
        n = min(self.batch_size, len(self._buf))
        if not n:
            return True
        batch = [self._buf.popleft() for _ in range(n)]
        started = time.perf_counter()
        try:
            await self.repo.write_metrics_batch(batch)
        except Exception as exc:  # noqa: BLE001 - never let the writer die
            self._buf.extendleft(reversed(batch))
            self.failed_batches += 1
            self.last_failure = datetime.now(UTC)
            self.last_error = (str(exc) if isinstance(exc, TimeSeriesError) else f"{type(exc).__name__}: {exc}")[:500]
            METRICS_WRITE_FAILURES.inc()
            if isinstance(exc, TimeSeriesError):
                log.warning("metric batch write failed; will retry", extra={"samples": n, "error": str(exc)})
            else:
                log.exception("unexpected metric write error")
            self._trim()
            return False
        self.written_total += n
        self.last_success = datetime.now(UTC)
        METRICS_WRITTEN.inc(n)
        METRICS_BATCH_SIZE.observe(n)
        METRICS_WRITE_LATENCY.observe(time.perf_counter() - started)
        METRICS_BUFFERED.set(len(self._buf))
        return True

    def _trim(self) -> None:
        overflow = len(self._buf) - self.buffer_max
        if overflow > 0:
            for _ in range(overflow):
                self._buf.popleft()
            self.dropped_total += overflow
            METRICS_DROPPED.labels("buffer_overflow").inc(overflow)
            log.warning("metric buffer full: dropped oldest samples", extra={"dropped": overflow})
        METRICS_BUFFERED.set(len(self._buf))

    async def flush_all(self, *, raise_on_failure: bool = False) -> bool:
        """Write everything buffered now (used by one-off jobs, the CLI and tests)."""
        async with self._lock:
            while self._buf:
                if not await self._write_one_batch():
                    if raise_on_failure:
                        raise TimeSeriesError(self.last_error or "metric write failed")
                    return False
            self._backoff = 0.0
            return True

    async def _loop(self) -> None:
        while not self._stop:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.flush_interval + self._backoff)
            except TimeoutError:
                pass
            self._wake.clear()
            if self._stop:
                break
            ok = await self.flush_all()
            if ok:
                self._backoff = 0.0
            else:
                self._backoff = min(max(self._backoff * 2, 1.0), self.max_backoff)

    def start(self) -> None:
        if self._task is None:
            self._stop = False
            self._task = asyncio.create_task(self._loop(), name="dbm-metric-writer")

    async def stop(self) -> None:
        self._stop = True
        self._wake.set()
        if self._task:
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        await self.flush_all()  # best effort on shutdown

    def status(self) -> dict[str, Any]:
        return {
            "running": self._task is not None,
            "pending": len(self._buf),
            "buffer_max": self.buffer_max,
            "batch_size": self.batch_size,
            "flush_interval_seconds": self.flush_interval,
            "written_total": self.written_total,
            "failed_batches": self.failed_batches,
            "dropped_total": self.dropped_total,
            "retry_backoff_seconds": self._backoff,
            "last_success": self.last_success,
            "last_failure": self.last_failure,
            "last_error": self.last_error,
        }


_writer: MetricWriter | None = None


def get_writer() -> MetricWriter:
    global _writer
    if _writer is None:
        _writer = MetricWriter()
    return _writer


def reset_writer(writer: MetricWriter | None = None) -> None:
    global _writer
    _writer = writer
