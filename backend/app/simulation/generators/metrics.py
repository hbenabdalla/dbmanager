"""Metric history for simulated servers, bulk-loaded into the time-series database.

Every sample carries ``data_source="simulated"`` and the server's id, so the history can be
removed reliably by label (never by name) and never mixes with real telemetry.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.demo.adapter import DemoAdapter
from app.models import Server
from app.services import metric_catalog
from app.simulation.profiles import SimulationProfile
from app.timeseries.catalog import name_for
from app.timeseries.model import Sample
from app.timeseries.normalizer import LABEL_MAP, base_labels
from app.timeseries.repository import get_repository

BULK_CHUNK = 25_000

#: metrics kept in history (the live collector stores the full snapshot every interval)
HISTORY_METRICS = [
    "connections.total",
    "connections.active",
    "connections.idle",
    "connections.utilization",
    "transactions.per_sec",
    "cache.hit_ratio",
    "query.mean_latency_ms",
    "query.calls_per_sec",
    "sessions.blocked",
    "io.blocks_read_per_sec",
    "storage.database_bytes",
    "storage.utilization",
    "cpu.percent",
    "memory.used_bytes",
    "replication.lag_seconds",
    "temp.bytes_per_sec",
    "availability.up",
    "locks.total",
    "deadlocks.count",
]


def sample_times(profile: SimulationProfile, now: datetime) -> list[datetime]:
    """Timestamps from oldest to newest; finer resolution for recent history."""
    bands = sorted(profile.resolution)  # (age limit hours, step minutes), youngest first
    out: list[datetime] = []
    t = now - timedelta(days=profile.history_days)
    while t <= now:
        out.append(t)
        age_h = (now - t).total_seconds() / 3600
        step = next((m for limit, m in bands if age_h <= limit), bands[-1][1])
        t += timedelta(minutes=step)
    return out


async def bulk_write(samples: list[Sample]) -> int:
    """Bulk ingestion straight to the repository (large compressed requests), bypassing the
    live writer's buffer. Raises TimeSeriesUnavailable when the storage is down."""
    repo = get_repository()
    n = 0
    for i in range(0, len(samples), BULK_CHUNK):
        n += await repo.write_metrics_batch(samples[i : i + BULK_CHUNK])
    return n


def outage_samples(server: Server, start: datetime, end: datetime, step_minutes: int = 5) -> list[Sample]:
    labels = base_labels(server)
    out = []
    t = start + timedelta(minutes=step_minutes)
    while t < end:
        out.append(Sample.make(name_for("availability.up"), labels, 0.0, t))
        t += timedelta(minutes=step_minutes)
    return out


async def backfill_metrics(
    db: AsyncSession, server: Server, adapter: DemoAdapter, profile: SimulationProfile, now: datetime
) -> int:
    """Write metric history for one server and set its latest metrics and health. Returns samples."""
    labels = base_labels(server)
    names = {m: name_for(m) for m in HISTORY_METRICS}
    samples: list[Sample] = []
    for ts in sample_times(profile, now):
        snap = adapter.metrics_at(ts)
        g = snap.gauges
        for m in HISTORY_METRICS:
            if m in g and g[m] is not None:
                samples.append(Sample.make(names[m], labels, float(g[m]), ts))
        for key, lbl, value in snap.labeled:
            extra = {LABEL_MAP[k]: str(v) for k, v in lbl.items() if k in LABEL_MAP}
            samples.append(Sample.make(name_for(key), {**labels, **extra}, float(value), ts))
    written = await bulk_write(samples)
    latest = adapter.metrics_at(now)
    server.latest_metrics = {**latest.gauges, "_collected_at": now.isoformat()}
    server.health_status, server.health_reasons = metric_catalog.compute_health(latest.gauges)
    server.last_collected_at = now
    return written
