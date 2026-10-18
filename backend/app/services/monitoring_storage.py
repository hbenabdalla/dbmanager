"""Monitoring storage administration: status, retention policy and reliable deletions.

* Retention: the *policy* (desired days) is stored in PostgreSQL (``app_settings``) so it is
  audited and visible; it is *enforced* natively by the time-series database
  (VictoriaMetrics ``-retentionPeriod``). The status page shows both and flags a mismatch.
* Deletions (server removed, simulation removed) go through :func:`delete_series`. When the
  time-series database is down the request is queued in ``app_settings`` and retried by the
  retention job, so telemetry of removed servers never stays behind.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import AppSetting
from app.timeseries.model import from_ms
from app.timeseries.repository import TimeSeriesError, TimeSeriesUnavailable, get_repository
from app.timeseries.writer import get_writer

log = logging.getLogger(__name__)

RETENTION_KEY = "monitoring.retention_days"
PENDING_KEY = "timeseries.pending_deletes"
_oldest_cache: dict[str, Any] = {}


def parse_retention_days(value: str | None) -> int | None:
    """``90d`` / ``12w`` / ``1y`` / ``3`` (months, VictoriaMetrics default unit) → days."""
    if not value:
        return None
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([hdwy]?)\s*", value)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2)
    days = {"h": n / 24, "d": n, "w": n * 7, "y": n * 365, "": n * 31}[unit]
    return max(int(round(days)), 0)


def configured_retention_days() -> int:
    return parse_retention_days(get_settings().timeseries_retention) or 90


async def retention_days(db: AsyncSession) -> int:
    row = await db.get(AppSetting, RETENTION_KEY)
    return int(row.value) if row else configured_retention_days()


async def set_retention_days(db: AsyncSession, days: int, actor: str) -> int:
    if days < 1 or days > 3650:
        from app.core.errors import ValidationFailed

        raise ValidationFailed("retention must be between 1 and 3650 days")
    row = await db.get(AppSetting, RETENTION_KEY)
    if row is None:
        db.add(AppSetting(key=RETENTION_KEY, value=days, updated_by=actor))
    else:
        row.value, row.updated_by = days, actor
    await db.flush()
    return days


# ------------------------------------------------------------------------------------------
# reliable deletion
# ------------------------------------------------------------------------------------------
#: a deleted server's telemetry is deleted again after this delay, catching samples that a
#: collection already in flight (possibly in another worker process) writes after the delete
FOLLOW_UP_SECONDS = 90


def _entry(item: Any) -> dict[str, Any]:
    # entries written by older versions were plain label matches
    return item if isinstance(item, dict) and "match" in item else {"match": item, "not_before": None}


async def _pending(db: AsyncSession) -> list[dict[str, Any]]:
    row = await db.get(AppSetting, PENDING_KEY)
    return [_entry(x) for x in row.value] if row and isinstance(row.value, list) else []


async def _store_pending(db: AsyncSession, items: list[dict[str, Any]]) -> None:
    row = await db.get(AppSetting, PENDING_KEY)
    if row is None:
        db.add(AppSetting(key=PENDING_KEY, value=items, updated_by="system"))
    else:
        row.value = items
    await db.flush()


async def _enqueue(db: AsyncSession, match: dict[str, Any], not_before: datetime | None) -> None:
    items = await _pending(db)
    entry = {"match": match, "not_before": not_before.isoformat() if not_before else None}
    if not any(i["match"] == match for i in items):
        items.append(entry)
        await _store_pending(db, items)


async def delete_series(
    db: AsyncSession,
    match: dict[str, Any],
    *,
    follow_up: dict[str, Any] | None = None,
    deferred: dict[str, Any] | None = None,
) -> bool:
    """Delete telemetry by stable labels (``server_id`` / ``data_source``), never by name.

    Buffered samples of the deleted series are discarded, and ``follow_up`` (default: the same
    match) is deleted once more after :data:`FOLLOW_UP_SECONDS`. Returns False when the
    time-series database is unreachable — the deletion is then queued and retried every
    minute by the ``timeseries_deletes`` job (as ``deferred`` when given — e.g. the removed
    server ids instead of a classification label that newer data may carry by then)."""
    if not match:
        raise ValueError("refusing to delete without a label filter")
    get_writer().discard(match)
    again = follow_up if follow_up is not None else match
    try:
        await get_repository().delete_series(match)
    except TimeSeriesUnavailable as exc:
        log.warning("time-series delete deferred", extra={"match": match, "error": str(exc)})
        queued = deferred if deferred is not None else match
        if queued:
            await _enqueue(db, queued, None)
        return False
    if again:
        await _enqueue(db, again, datetime.now(UTC) + timedelta(seconds=FOLLOW_UP_SECONDS))
    return True


async def retry_pending_deletes(db: AsyncSession, *, now: datetime | None = None) -> int:
    """Run queued deletions that are due. Returns how many were executed."""
    items = await _pending(db)
    if not items:
        return 0
    now = now or datetime.now(UTC)
    left, done = [], 0
    for item in items:
        nb = item.get("not_before")
        if nb and datetime.fromisoformat(nb) > now:
            left.append(item)
            continue
        try:
            get_writer().discard(item["match"])
            await get_repository().delete_series(item["match"])
            done += 1
        except TimeSeriesError:
            left.append(item)
    await _store_pending(db, left)
    return done


# ------------------------------------------------------------------------------------------
# status
# ------------------------------------------------------------------------------------------
async def _oldest_ms(retention: str | None) -> int | None:
    """Oldest stored sample, estimated from the availability series (one per server) and
    cached for 10 minutes so the status page stays cheap."""
    now = time.monotonic()
    if _oldest_cache.get("until", 0) > now:
        return _oldest_cache.get("value")
    repo = get_repository()
    days = parse_retention_days(retention) or configured_retention_days()
    value: int | None = None
    try:
        value = await repo.oldest_sample_ms(days)
    except TimeSeriesError:
        value = None
    _oldest_cache.update({"value": value, "until": now + 600})
    return value


async def status(db: AsyncSession) -> dict[str, Any]:
    s = get_settings()
    repo = get_repository()
    st = await repo.status()
    desired = await retention_days(db)
    effective = parse_retention_days(st.retention)
    pending = await _pending(db)
    oldest = await _oldest_ms(st.retention) if st.healthy else None
    w = get_writer().status()
    return {
        "enabled": s.timeseries_enabled,
        "status": "available" if st.healthy else "unavailable",
        "engine": st.engine,
        "version": st.version,
        "endpoint": s.timeseries_url if s.timeseries_backend == "victoriametrics" else None,
        "error": st.error,
        "retention": {
            "desired_days": desired,
            "effective": st.retention,
            "effective_days": effective,
            "in_sync": effective is None or effective == desired,
            "how_to_apply": "Set DBM_TIMESERIES_RETENTION (e.g. '90d') and restart the timeseries container."
            if effective is not None and effective != desired
            else None,
        },
        "series": st.series,
        "samples": st.samples,
        "oldest_metric": from_ms(oldest).isoformat() if oldest else None,
        "newest_metric": from_ms(st.newest_ms).isoformat() if st.newest_ms else None,
        "details": st.details,
        "ingestion": {
            **{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in w.items()},
            "state": _ingestion_state(w),
        },
        "pending_deletes": sum(1 for p in pending if not p.get("not_before")),
        "scheduled_follow_up_deletes": sum(1 for p in pending if p.get("not_before")),
        "checked_at": datetime.now(UTC).isoformat(),
    }


def _ingestion_state(w: dict[str, Any]) -> str:
    if not w.get("running"):
        return "idle"
    ls, lf = w.get("last_success"), w.get("last_failure")
    if lf and (not ls or lf > ls):
        return "retrying"
    if w.get("pending", 0) > w.get("buffer_max", 1) * 0.8:
        return "backlogged"
    return "ok"
