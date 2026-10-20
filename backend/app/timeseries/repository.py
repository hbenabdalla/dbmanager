"""TimeSeriesRepository: the only door from the application to monitoring telemetry storage.

Services depend on this interface, never on a storage client. Implementations:

* :class:`app.timeseries.victoriametrics.VictoriaMetricsRepository` — production
* :class:`app.timeseries.memory.InMemoryTimeSeriesRepository` — tests / development

Every method raises :class:`TimeSeriesUnavailable` when the storage cannot be reached, so
callers can degrade gracefully (the rest of the application keeps working).
"""

from __future__ import annotations

import abc
from datetime import datetime
from functools import lru_cache

from app.timeseries.model import GroupAgg, Rollup, Sample, Selector, Series, SeriesValue, StorageStatus


class TimeSeriesError(Exception):
    """Storage rejected a request (bad query, bad data)."""


class TimeSeriesUnavailable(TimeSeriesError):
    """Storage is unreachable, timed out or answered 5xx."""


class TimeSeriesRepository(abc.ABC):
    engine: str = "abstract"

    # --- write ----------------------------------------------------------------------------
    @abc.abstractmethod
    async def write_metrics_batch(self, samples: list[Sample]) -> int:
        """Write samples in one request. Idempotent: re-sending the same (series, timestamp)
        does not create duplicates. Returns the number of samples accepted."""

    async def write_metric(self, sample: Sample) -> int:
        return await self.write_metrics_batch([sample])

    # --- read -----------------------------------------------------------------------------
    @abc.abstractmethod
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
        """Points every ``step_s`` seconds; each point is ``rollup`` of the raw samples in that step.
        With ``group_by`` the series are combined with ``group_agg`` per label set."""

    @abc.abstractmethod
    async def get_latest(self, selector: Selector, lookback_s: int = 900) -> list[SeriesValue]:
        """Most recent sample of each series within ``lookback_s`` (with its real timestamp)."""

    @abc.abstractmethod
    async def aggregate(
        self,
        selector: Selector,
        start: datetime,
        end: datetime,
        fn: Rollup,
        group_by: tuple[str, ...] | None = None,
        group_agg: GroupAgg = "sum",
    ) -> list[SeriesValue]:
        """One value per series (or group) summarizing the whole window."""

    @abc.abstractmethod
    async def top(self, selector: Selector, k: int, lookback_s: int = 900, fn: Rollup = "last") -> list[SeriesValue]:
        """The ``k`` series with the highest ``fn`` over the last ``lookback_s`` seconds
        (``last`` = latest value, ``increase`` = growth of a counter, …)."""

    async def query_metric(self, selector: Selector, start: datetime, end: datetime, step_s: int) -> list[Series]:
        return await self.query_range(selector, start, end, step_s)

    async def query_metrics(
        self, selectors: list[Selector], start: datetime, end: datetime, step_s: int
    ) -> list[list[Series]]:
        return [await self.query_range(s, start, end, step_s) for s in selectors]

    # --- maintenance ----------------------------------------------------------------------
    @abc.abstractmethod
    async def delete_series(self, match: dict[str, str | list[str]]) -> None:
        """Delete every series whose labels match all ``match`` entries (a value or a list of
        accepted values), e.g. one server, a set of server ids, or every simulated series.
        ``match`` must not be empty."""

    async def delete_server_metrics(self, server_id: str) -> None:
        await self.delete_series({"server_id": server_id})

    @abc.abstractmethod
    async def health_check(self) -> bool: ...

    @abc.abstractmethod
    async def status(self) -> StorageStatus: ...

    async def oldest_sample_ms(self, lookback_days: int) -> int | None:
        """Timestamp of the oldest stored availability sample (None when unknown)."""
        return None

    async def flush(self) -> None:  # noqa: B027 - optional hook
        """Make recently written samples visible to queries (no-op where not needed)."""

    async def close(self) -> None:  # noqa: B027 - optional hook
        """Release connections."""


_override: TimeSeriesRepository | None = None


def set_repository(repo: TimeSeriesRepository | None) -> None:
    """Install a specific repository (tests). ``None`` restores the configured one."""
    global _override
    _override = repo
    _configured.cache_clear()


@lru_cache
def _configured() -> TimeSeriesRepository:
    from app.core.config import get_settings

    s = get_settings()
    if s.timeseries_backend == "memory" or not s.timeseries_enabled:
        from app.timeseries.memory import InMemoryTimeSeriesRepository

        return InMemoryTimeSeriesRepository()
    from app.core.tlsclient import timeseries_verify
    from app.timeseries.victoriametrics import VictoriaMetricsRepository

    return VictoriaMetricsRepository(
        url=s.timeseries_url,
        username=s.timeseries_username or None,
        password=s.timeseries_password.get_secret_value() or None,
        timeout=s.timeseries_timeout_seconds,
        retention=s.timeseries_retention,
        verify=timeseries_verify(s),
    )


def get_repository() -> TimeSeriesRepository:
    return _override or _configured()
