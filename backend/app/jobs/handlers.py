"""Job kinds, schedules and handlers.

Collection interval classes (``DBM_INTERVAL_*_SECONDS``):

* FAST (30 s)      – nothing scheduled separately today; servers can opt in by setting their
                     collection interval to 15–30 s (connections, sessions, locks, waits)
* NORMAL (60 s)    – ``collect_metrics`` default (follows the server's collection interval)
* SLOW (300 s)     – ``collect_query_stats`` (query telemetry), storage/capacity gauges are
                     throttled to this class by the normalizer
* VERY_SLOW (1 h)  – ``discover_inventory``

Each (server, job kind) gets a stable phase offset of up to ``DBM_COLLECTION_JITTER_RATIO`` of
its interval so a large fleet (or a restart) never fires every collection at the same second.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Server
from app.services import advisor, collection
from app.timeseries.catalog import IntervalClass
from app.timeseries.normalizer import interval_seconds

Handler = Callable[[AsyncSession, Server], Awaitable[dict[str, Any]]]

#: kind -> (handler, interval: seconds, an interval class, or None = server.collection_interval_s)
JOB_KINDS: dict[str, tuple[Handler, int | IntervalClass | None]] = {
    "collect_metrics": (collection.collect_metrics, None),
    "collect_query_stats": (collection.collect_query_stats, IntervalClass.SLOW),
    "discover_inventory": (collection.discover, IntervalClass.VERY_SLOW),
    "generate_recommendations": (advisor.run_performance, 900),
    "security_scan": (advisor.run_security, 6 * 3600),
}
#: global jobs: retention (PostgreSQL housekeeping) and the follow-up/retry of time-series
#: deletions (telemetry of removed servers, see app.services.monitoring_storage)
GLOBAL_JOBS: dict[str, int] = {
    "retention": 6 * 3600,
    "timeseries_deletes": 60,
    # alert rules against the collectors' latest values (app.services.alerting)
    "evaluate_alerts": max(get_settings().alert_evaluation_seconds, 15),
    # failed notification deliveries whose backoff has elapsed (app.services.alerting.retry_due)
    "notification_retries": 60,
    # audit events to the configured SIEM destinations (app.services.siem)
    "siem_export": 30,
}


def interval_for(kind: str, server: Server) -> int:
    _, iv = JOB_KINDS[kind]
    if iv is None:
        return max(int(server.collection_interval_s), 15)
    if isinstance(iv, IntervalClass):
        return interval_seconds(iv)
    return iv


def jitter_for(kind: str, server_id: object, interval: int) -> int:
    """Stable per-(server, kind) delay in ``[0, interval × jitter ratio)`` seconds."""
    ratio = max(min(get_settings().collection_jitter_ratio, 0.9), 0.0)
    span = int(interval * ratio)
    if span <= 0:
        return 0
    h = int.from_bytes(hashlib.blake2b(f"{server_id}:{kind}".encode(), digest_size=4).digest(), "big")
    return h % span
