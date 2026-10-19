"""Scheduled reports: generation with the owner's access, e-mail delivery, run history.

The global job ``report_scheduler`` (every minute, on the scheduler leader) runs the schedules
that are due. Each run re-loads the owner's role assignments, so a schedule never reports more
than its owner may see *now*; a disabled or deleted owner, or an owner without access to the
report, fails the run with the reason instead of sending anything.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backup.service import next_run
from app.core.config import get_settings
from app.models import NotificationChannel, ReportRun, ReportSchedule, User
from app.services import audit, notify, reports
from app.services.authz import Authorizer

log = logging.getLogger(__name__)

FREQUENCIES = ("daily", "weekly", "monthly")


class ScheduleError(Exception):
    pass


def schedule_next(sched: ReportSchedule, after: datetime) -> datetime | None:
    return next_run(sched.schedule or {}, after) if sched.enabled else None


async def _owner_authz(db: AsyncSession, sched: ReportSchedule) -> tuple[User, Authorizer]:
    user = await db.get(User, sched.owner_id) if sched.owner_id else None
    if user is None:
        raise ScheduleError("the schedule's owner no longer exists: assign a new owner")
    if not user.is_active:
        raise ScheduleError(f"the owner {user.username} is disabled")
    az = await Authorizer.load(db, user)
    report = reports.REPORTS.get(sched.report_key)
    if report is None:
        raise ScheduleError(f"unknown report {sched.report_key!r}")
    if not reports.allowed(az, report):
        raise ScheduleError(f"the owner {user.username} may no longer run the {report.title} report")
    return user, az


def _body(sched: ReportSchedule, rendered: reports.RenderedReport, owner: str) -> str:
    scope = "; ".join(rendered.scope) or "all servers visible to the owner"
    return (
        f"{rendered.title} — {rendered.description}\n\n"
        f"Schedule: {sched.name}\n"
        f"Generated: {rendered.generated_at:%Y-%m-%d %H:%M} UTC with the access of {owner}\n"
        f"Scope: {scope}\n"
        f"Rows: {len(rendered.rows):,}\n\n"
        "The attached file contains data visible to the schedule's owner. Handle it according to your "
        "data classification.\n\n"
        f"— DBManager {rendered.version}\n"
    )


async def run_schedule(
    db: AsyncSession, sched: ReportSchedule, *, trigger: str, actor: str | None = None, now: datetime | None = None
) -> ReportRun:
    """Generate the report and e-mail it to every channel of the schedule. Always records a
    run (success / partial / failed); never raises for delivery problems."""
    now = now or datetime.now(UTC)
    run = ReportRun(
        id=uuid.uuid4(),
        schedule_id=sched.id,
        report_key=sched.report_key,
        format=sched.format,
        trigger=trigger,
        triggered_by=actor,
        status="failed",
        started_at=now,
        deliveries=[],
    )
    db.add(run)
    try:
        owner, az = await _owner_authz(db, sched)
        params = reports.ReportParams.model_validate(sched.params or {})
        rendered = await reports.produce(
            db, az, reports.REPORTS[sched.report_key], params, generated_for=owner.username
        )
        data, media_type, filename = reports.render(rendered, sched.format)
        run.rows, run.size_bytes = len(rendered.rows), len(data)
        limit = int(get_settings().report_max_attachment_mb * 1024 * 1024)
        if len(data) > limit:
            raise ScheduleError(
                f"the {sched.format.upper()} file is {len(data) / 1048576:.1f} MB, above the "
                f"{get_settings().report_max_attachment_mb:g} MB limit (DBM_REPORT_MAX_ATTACHMENT_MB): narrow the scope"
            )
        channels = (
            list(
                (
                    await db.execute(
                        select(NotificationChannel).where(
                            NotificationChannel.id.in_([uuid.UUID(c) for c in sched.channel_ids or []])
                        )
                    )
                ).scalars()
            )
            if sched.channel_ids
            else []
        )
        if not channels:
            raise ScheduleError("the schedule has no e-mail channel (they may have been deleted)")
        subject = f"[DBManager] {rendered.title} — {sched.name}"
        body = _body(sched, rendered, owner.username)
        ok = 0
        for ch in channels:
            entry: dict[str, Any] = {"channel_id": str(ch.id), "name": ch.name}
            if not ch.enabled:
                entry.update(status="failed", error="channel disabled")
            else:
                try:
                    entry.update(
                        status="sent",
                        recipients=await notify.send_file_email(ch, subject, body, (filename, data, media_type)),
                    )
                    ok += 1
                except notify.DeliveryError as exc:
                    entry.update(status="failed", error=str(exc)[:300])
            run.deliveries = [*run.deliveries, entry]
        run.status = "success" if ok == len(channels) else "partial" if ok else "failed"
        if not ok:
            run.error = "no channel accepted the report: " + "; ".join(
                f"{d['name']}: {d.get('error')}" for d in run.deliveries
            )
    except ScheduleError as exc:
        run.status, run.error = "failed", str(exc)
    except Exception as exc:  # noqa: BLE001 - a broken report must not stop the scheduler
        log.exception("scheduled report %s failed", sched.name)
        run.status, run.error = "failed", f"{type(exc).__name__}: {exc}"[:500]
    run.finished_at = datetime.now(UTC)
    sched.last_run_at, sched.last_status, sched.last_error = now, run.status, run.error
    await audit.record(
        "report.delivered",
        username=actor or "scheduler",
        result="success" if run.status == "success" else "failure",
        category="report",
        target_type="report_schedule",
        target_id=str(sched.id),
        object_name=sched.report_key,
        failure_reason=run.error,
        details={
            "schedule": sched.name,
            "trigger": trigger,
            "format": sched.format,
            "rows": run.rows,
            "status": run.status,
            "channels": [{k: d.get(k) for k in ("name", "status", "recipients")} for d in run.deliveries],
        },
    )
    await db.flush()
    return run


async def due(db: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """Global job ``report_scheduler``."""
    now = now or datetime.now(UTC)
    stats = {"ran": 0, "failed": 0}
    rows = list((await db.execute(select(ReportSchedule).where(ReportSchedule.enabled.is_(True)))).scalars())
    for sched in rows:
        if sched.next_run_at is None:
            sched.next_run_at = schedule_next(sched, now)
            continue
        if sched.next_run_at > now:
            continue
        sched.next_run_at = schedule_next(sched, now)  # first: a slow run is not started twice
        await db.flush()
        run = await run_schedule(db, sched, trigger="schedule", now=now)
        stats["ran"] += 1
        stats["failed"] += run.status == "failed"
    await db.flush()
    return stats
