"""Query telemetry history for simulated servers (harmless sample SQL only).

Fingerprint metadata goes to PostgreSQL (``query_fingerprints``, ``data_source=simulated``);
the hourly cumulative counters go to the time-series database, bulk-loaded.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.demo.adapter import DemoAdapter
from app.core.config import get_settings
from app.models import Server
from app.services.collection import upsert_fingerprints
from app.simulation.generators.metrics import bulk_write
from app.timeseries.normalizer import normalize_query_stats


async def backfill_query_stats(
    db: AsyncSession, server: Server, adapter: DemoAdapter, hours: int, now: datetime
) -> int:
    s = get_settings()
    samples = []
    rows_by_key = {}
    known: set[str] = set()
    for h in range(hours, -1, -1):
        ts = now - timedelta(hours=h)
        smp, rows = normalize_query_stats(
            server,
            adapter.query_stats_at(ts),
            ts,
            top_n=s.query_stats_top_n,
            known=known,
            max_fingerprints=s.query_fingerprints_max_per_server,
        )
        known.update(r.fingerprint for r in rows)
        samples.extend(smp)
        for r in rows:
            rows_by_key[(r.fingerprint, r.database)] = r
    await upsert_fingerprints(db, server, list(rows_by_key.values()), now)
    return await bulk_write(samples)
