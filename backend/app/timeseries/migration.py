"""Copy legacy PostgreSQL metric history into the time-series database.

``python -m app.cli metrics migrate-to-timeseries [--dry-run] [--batch-size N]
[--from-date YYYY-MM-DD] [--to-date YYYY-MM-DD] [--server-id UUID]``

* **Idempotent** – samples keep their original timestamps and the time-series database
  deduplicates identical (series, timestamp) pairs, so re-running never creates duplicates.
* **Resumable** – progress (the last migrated row id per table) is checkpointed in
  ``app_settings`` after every batch, per filter combination; an interrupted run continues
  where it stopped. ``--restart`` ignores the checkpoint.
* **Non-destructive** – the legacy tables are only read. They are dropped by a later
  migration once the result has been verified (see docs/monitoring/timeseries-storage.md, phase F).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import QueryStat
from app.core.config import get_settings
from app.models import AppSetting, MetricSample, QueryFingerprint, QueryStatSnapshot, Server
from app.timeseries.catalog import name_for
from app.timeseries.model import LabelPolicyError, Sample
from app.timeseries.normalizer import LABEL_MAP, base_labels, normalize_query_stats
from app.timeseries.repository import TimeSeriesRepository, TimeSeriesUnavailable, get_repository

log = logging.getLogger(__name__)
CHECKPOINT_PREFIX = "timeseries.migration:"
WRITE_CHUNK = 25_000


@dataclass
class MigrationOptions:
    dry_run: bool = False
    batch_size: int = 20_000
    from_date: datetime | None = None
    to_date: datetime | None = None
    server_id: uuid.UUID | None = None
    restart: bool = False

    def signature(self) -> str:
        raw = json.dumps(
            {
                "from": self.from_date.isoformat() if self.from_date else None,
                "to": self.to_date.isoformat() if self.to_date else None,
                "server": str(self.server_id) if self.server_id else None,
            },
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:12]


@dataclass
class MigrationReport:
    metric_rows_total: int = 0
    query_rows_total: int = 0
    metric_rows_done: int = 0
    query_rows_done: int = 0
    samples_written: int = 0
    rows_skipped: int = 0
    fingerprints: int = 0
    resumed_from: dict[str, int] = field(default_factory=dict)
    completed: bool = False
    dry_run: bool = False
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _filters(model: Any, o: MigrationOptions) -> list[Any]:
    conds = []
    if o.from_date:
        conds.append(model.ts >= o.from_date)
    if o.to_date:
        conds.append(model.ts < o.to_date)
    if o.server_id:
        conds.append(model.server_id == o.server_id)
    return conds


async def _load_checkpoint(db: AsyncSession, key: str) -> dict[str, Any]:
    row = await db.get(AppSetting, key)
    return dict(row.value) if row and isinstance(row.value, dict) else {}


async def _save_checkpoint(db: AsyncSession, key: str, value: dict[str, Any]) -> None:
    row = await db.get(AppSetting, key)
    value = {**value, "updated_at": datetime.now(UTC).isoformat()}
    if row is None:
        db.add(AppSetting(key=key, value=value, updated_by="metrics-migration"))
    else:
        row.value = value
    await db.commit()


async def _write(repo: TimeSeriesRepository, samples: list[Sample], attempts: int = 5) -> int:
    n = 0
    for i in range(0, len(samples), WRITE_CHUNK):
        chunk = samples[i : i + WRITE_CHUNK]
        for attempt in range(attempts):
            try:
                n += await repo.write_metrics_batch(chunk)
                break
            except TimeSeriesUnavailable:
                if attempt == attempts - 1:
                    raise
                await asyncio.sleep(min(2**attempt, 30))
    return n


def metric_rows_to_samples(rows: list[MetricSample], servers: dict[uuid.UUID, Any]) -> tuple[list[Sample], int]:
    samples: list[Sample] = []
    skipped = 0
    for r in rows:
        srv = servers.get(r.server_id)
        if srv is None:
            skipped += 1
            continue
        extra: dict[str, str] = {}
        if r.labels:
            if any(k not in LABEL_MAP for k in r.labels):
                skipped += 1  # unbounded / unknown label keys are not migrated
                continue
            extra = {LABEL_MAP[k]: str(v)[:128] for k, v in r.labels.items()}
        try:
            samples.append(Sample.make(name_for(r.metric), {**base_labels(srv), **extra}, float(r.value), r.ts))
        except (LabelPolicyError, ValueError, TypeError):
            skipped += 1
    return samples, skipped


async def migrate(
    session_factory: Callable[[], Any],
    opts: MigrationOptions,
    *,
    progress: Callable[[str], None] = print,
    repo: TimeSeriesRepository | None = None,
) -> MigrationReport:
    """Run (or dry-run) the migration. ``session_factory`` returns an async session context."""
    repo = repo or get_repository()
    s = get_settings()
    rep = MigrationReport(dry_run=opts.dry_run)
    started = time.monotonic()
    key = CHECKPOINT_PREFIX + opts.signature()
    async with session_factory() as db:
        servers = {sv.id: sv for sv in (await db.execute(select(Server))).scalars()}
        for sv in servers.values():
            db.expunge(sv)
        cp = {} if opts.restart else await _load_checkpoint(db, key)
        last_metric = int(cp.get("metric_samples", 0))
        last_query = int(cp.get("query_stat_snapshots", 0))
        rep.resumed_from = {"metric_samples": last_metric, "query_stat_snapshots": last_query}
        rep.metric_rows_total = (
            await db.execute(select(func.count()).select_from(MetricSample).where(*_filters(MetricSample, opts)))
        ).scalar_one()
        rep.query_rows_total = (
            await db.execute(
                select(func.count()).select_from(QueryStatSnapshot).where(*_filters(QueryStatSnapshot, opts))
            )
        ).scalar_one()
        remaining_m = (
            await db.execute(
                select(func.count())
                .select_from(MetricSample)
                .where(MetricSample.id > last_metric, *_filters(MetricSample, opts))
            )
        ).scalar_one()
        remaining_q = (
            await db.execute(
                select(func.count())
                .select_from(QueryStatSnapshot)
                .where(QueryStatSnapshot.id > last_query, *_filters(QueryStatSnapshot, opts))
            )
        ).scalar_one()
    progress(
        f"legacy metric rows: {rep.metric_rows_total:,} (to migrate: {remaining_m:,}); "
        f"legacy query snapshot rows: {rep.query_rows_total:,} (to migrate: {remaining_q:,})"
    )
    if last_metric or last_query:
        progress(f"resuming from checkpoint {key} (metric id > {last_metric}, query id > {last_query})")
    if opts.dry_run:
        progress("dry run - nothing written")
        rep.seconds = time.monotonic() - started
        return rep
    if not await repo.health_check():
        raise TimeSeriesUnavailable("the time-series database is not reachable - nothing migrated")

    # ---- metric samples -------------------------------------------------------------------
    done_m = 0
    while True:
        async with session_factory() as db:
            rows = list(
                (
                    await db.execute(
                        select(MetricSample)
                        .where(MetricSample.id > last_metric, *_filters(MetricSample, opts))
                        .order_by(MetricSample.id)
                        .limit(opts.batch_size)
                    )
                ).scalars()
            )
            if not rows:
                break
            samples, skipped = metric_rows_to_samples(rows, servers)
            rep.samples_written += await _write(repo, samples)
            rep.rows_skipped += skipped
            last_metric = rows[-1].id
            done_m += len(rows)
            await _save_checkpoint(
                db, key, {"metric_samples": last_metric, "query_stat_snapshots": last_query, "completed": False}
            )
        rep.metric_rows_done = done_m
        progress(f"metrics: {done_m:,}/{remaining_m:,} rows ({100 * done_m / max(remaining_m, 1):.1f}%)")

    # ---- query statistic snapshots --------------------------------------------------------
    done_q = 0
    known: dict[uuid.UUID, set[str]] = {}
    while True:
        async with session_factory() as db:
            rows = list(
                (
                    await db.execute(
                        select(QueryStatSnapshot)
                        .where(QueryStatSnapshot.id > last_query, *_filters(QueryStatSnapshot, opts))
                        .order_by(QueryStatSnapshot.id)
                        .limit(opts.batch_size)
                    )
                ).scalars()
            )
            if not rows:
                break
            # never split one snapshot (server, ts) across batches: its statements are merged
            # per fingerprint and must be written as one sample per series
            tail = rows[-1]
            rows += list(
                (
                    await db.execute(
                        select(QueryStatSnapshot)
                        .where(
                            QueryStatSnapshot.id > tail.id,
                            QueryStatSnapshot.server_id == tail.server_id,
                            QueryStatSnapshot.ts == tail.ts,
                            *_filters(QueryStatSnapshot, opts),
                        )
                        .order_by(QueryStatSnapshot.id)
                    )
                ).scalars()
            )
            groups: dict[tuple[uuid.UUID, datetime], list[QueryStatSnapshot]] = {}
            for r in rows:
                groups.setdefault((r.server_id, r.ts), []).append(r)
            samples = []
            meta: dict[uuid.UUID, dict[tuple[str, str], tuple[Any, datetime, datetime]]] = {}
            for (sid, ts), snap in groups.items():
                srv = servers.get(sid)
                if srv is None:
                    rep.rows_skipped += len(snap)
                    continue
                if sid not in known:
                    known[sid] = set(
                        (
                            await db.execute(
                                select(QueryFingerprint.fingerprint).where(QueryFingerprint.server_id == sid)
                            )
                        ).scalars()
                    )
                stats = [
                    QueryStat(
                        query_id=r.query_id,
                        query_text=r.query_text,
                        database=r.database,
                        username=r.username,
                        calls=r.calls,
                        total_ms=r.total_ms,
                        mean_ms=r.mean_ms,
                        max_ms=r.max_ms,
                        rows=r.rows,
                        cpu_ms=r.cpu_ms,
                        logical_reads=r.logical_reads,
                        physical_reads=r.physical_reads,
                        temp_blocks=r.temp_blocks,
                        lock_wait_ms=r.lock_wait_ms,
                    )
                    for r in snap
                ]
                smp, fps = normalize_query_stats(
                    srv,
                    stats,
                    ts,
                    top_n=10**6,
                    known=known[sid],
                    max_fingerprints=s.query_fingerprints_max_per_server,
                )
                known[sid].update(f.fingerprint for f in fps)
                samples += smp
                m = meta.setdefault(sid, {})
                for f in fps:
                    k = (f.fingerprint, (f.database or "")[:128])
                    prev = m.get(k)
                    m[k] = (f, min(prev[1], ts) if prev else ts, max(prev[2], ts) if prev else ts)
            rep.samples_written += await _write(repo, samples)
            for sid, m in meta.items():
                await _upsert_with_dates(db, servers[sid], list(m.values()))
                rep.fingerprints += len(m)
            last_query = rows[-1].id
            done_q += len(rows)
            await _save_checkpoint(
                db, key, {"metric_samples": last_metric, "query_stat_snapshots": last_query, "completed": False}
            )
        rep.query_rows_done = done_q
        progress(f"query statistics: {done_q:,}/{remaining_q:,} rows ({100 * done_q / max(remaining_q, 1):.1f}%)")

    async with session_factory() as db:
        await _save_checkpoint(
            db,
            key,
            {
                "metric_samples": last_metric,
                "query_stat_snapshots": last_query,
                "completed": True,
                "completed_at": datetime.now(UTC).isoformat(),
                "samples_written": rep.samples_written,
            },
        )
    await repo.flush()
    rep.completed = True
    rep.seconds = round(time.monotonic() - started, 1)
    return rep


async def _upsert_with_dates(db: AsyncSession, server: Any, items: list[tuple[Any, datetime, datetime]]) -> None:
    """Fingerprint metadata with the historical first/last seen (never moves first_seen later)."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    if not items:
        return
    values = [
        {
            "server_id": server.id,
            "fingerprint": f.fingerprint,
            "database": (f.database or "")[:128],
            "engine": server.engine,
            "username": f.username,
            "normalized_query": f.normalized_text,
            "engine_query_id": f.engine_query_id,
            "data_source": server.data_source,
            "first_seen": first,
            "last_seen": last,
        }
        for f, first, last in items
    ]
    stmt = pg_insert(QueryFingerprint).values(values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_query_fingerprints_server_fp_db",
        set_={
            "first_seen": func.least(QueryFingerprint.first_seen, stmt.excluded.first_seen),
            "last_seen": func.greatest(QueryFingerprint.last_seen, stmt.excluded.last_seen),
        },
    )
    await db.execute(stmt)
