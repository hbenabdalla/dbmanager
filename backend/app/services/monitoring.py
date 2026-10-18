"""Compatibility facade: the monitoring read side now lives in :mod:`app.services.metrics`
and reads the time-series database. Kept so existing imports keep working."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import metrics as _metrics
from app.services.metrics import RANGES, TARGET_POINTS, resolve_range, step_for

bucket_seconds = step_for

__all__ = ["RANGES", "TARGET_POINTS", "bucket_seconds", "resolve_range", "series"]


async def series(
    db: AsyncSession | None,
    server_ids: list[uuid.UUID],
    metrics: list[str],
    start: datetime,
    end: datetime,
    *,
    aggregate_servers: str | None = None,
    labeled: bool = False,
    agg: str | None = None,
) -> dict[str, Any]:
    return await _metrics.series(
        server_ids,
        metrics,
        start,
        end,
        aggregate_servers=aggregate_servers,
        labeled=labeled,
        agg=agg,  # type: ignore[arg-type]
    )
