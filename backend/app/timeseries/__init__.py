"""Monitoring telemetry storage (time-series database).

PostgreSQL keeps identity, inventory, configuration, recommendations, findings, audit and
jobs; every time-series sample goes through this package to the time-series database.

Pipeline: adapter → collector → :mod:`normalizer` → :mod:`writer` (batching) →
:class:`repository.TimeSeriesRepository`.
"""

from app.timeseries.model import MetricType, Sample, Selector, Series, SeriesValue, StorageStatus
from app.timeseries.repository import (
    TimeSeriesError,
    TimeSeriesRepository,
    TimeSeriesUnavailable,
    get_repository,
    set_repository,
)

__all__ = [
    "MetricType",
    "Sample",
    "Selector",
    "Series",
    "SeriesValue",
    "StorageStatus",
    "TimeSeriesError",
    "TimeSeriesRepository",
    "TimeSeriesUnavailable",
    "get_repository",
    "set_repository",
]
