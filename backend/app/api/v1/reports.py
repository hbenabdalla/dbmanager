"""Reports: catalog, preview, download (CSV, PDF) and scheduled e-mail delivery."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select

from app.api.deps import DB, CurrentPrincipal, rate_limit
from app.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.models import Environment, NotificationChannel, ReportRun, ReportSchedule, Server, User
from app.services import audit, report_schedules, reports
from app.services.reports import FINDING_STATUSES, FORMATS, REPORTS, SEVERITIES, ReportParams

router = APIRouter(tags=["reports"])

PREVIEW_ROWS = 50


def _params(
    environment_id: uuid.UUID | None = None,
    server_id: uuid.UUID | None = None,
    engine: str | None = Query(None, max_length=32),
    window_hours: int = Query(24, ge=1, le=24 * 31),
    statuses: list[str] | None = Query(None),
    min_severity: str | None = None,
    scope: Literal["server", "database", "tablespace"] = "server",
) -> ReportParams:
    if statuses and any(s not in FINDING_STATUSES for s in statuses):
        raise ValidationFailed("invalid status", details={"allowed": list(FINDING_STATUSES)})
    if min_severity and min_severity not in SEVERITIES:
        raise ValidationFailed("invalid severity", details={"allowed": list(SEVERITIES)})
    return ReportParams(
        environment_id=environment_id,
        server_id=server_id,
        engine=engine,
        window_hours=window_hours,
        statuses=statuses,
        min_severity=min_severity,
        scope=scope,
    )


Params = Annotated[ReportParams, Depends(_params)]


def _report(p: CurrentPrincipal, key: str) -> reports.ReportDef:
    p.authz.require_anywhere("reports.view")
    r = REPORTS.get(key)
    if r is None:
        raise NotFound("Unknown report")
    if r.platform:
        p.authz.require(r.permission)
    else:
        p.authz.require_anywhere(r.permission)
    return r


@router.get("/reports")
async def list_reports(p: CurrentPrincipal) -> list[dict[str, Any]]:
    return [
        {
            "key": r.key,
            "title": r.title,
            "description": r.description,
            "permission": r.permission,
            "formats": list(FORMATS),
            "params": list(r.params),
            "available": reports.allowed(p.authz, r),
        }
        for r in REPORTS.values()
    ]


@router.get("/reports/{key}/preview", dependencies=[rate_limit("expensive")])
async def preview(key: str, db: DB, p: CurrentPrincipal, params: Params) -> dict[str, Any]:
    r = _report(p, key)
    out = await reports.produce(db, p.authz, r, params, generated_for=p.username)
    return {
        "key": r.key,
        "title": r.title,
        "columns": out.columns,
        "rows": [[reports.cell(v) for v in row] for row in out.rows[:PREVIEW_ROWS]],
        "total_rows": len(out.rows),
        "scope": out.scope,
    }


@router.get("/reports/{key}.{fmt}", dependencies=[rate_limit("expensive")])
async def download(key: str, fmt: str, db: DB, p: CurrentPrincipal, params: Params) -> Response:
    r = _report(p, key)
    if fmt not in FORMATS:
        raise ValidationFailed(f"Format {fmt!r} is not available", details={"available": list(FORMATS)})
    out = await reports.produce(db, p.authz, r, params, generated_for=p.username)
    data, media_type, filename = reports.render(out, fmt)
    await audit.record(
        "report.download",
        actor=p,
        category="report",
        object_name=key,
        details={"rows": len(out.rows), "format": fmt, "scope": out.scope},
    )
    return Response(data, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ------------------------------------------------------------------------------------------
# schedules
# ------------------------------------------------------------------------------------------
class ScheduleSpec(BaseModel):
    frequency: Literal["daily", "weekly", "monthly"]
    time: str = Field("07:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    time_zone: str = Field("UTC", max_length=64)
    weekday: int = Field(1, ge=1, le=7)
    day_of_month: int = Field(1, ge=1, le=28)

    @field_validator("time_zone")
    @classmethod
    def _tz(cls, v: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {v!r}") from exc
        return v


class ScheduleIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    report_key: str
    format: Literal["csv", "pdf"] = "pdf"
    params: ReportParams = Field(default_factory=ReportParams)
    schedule: ScheduleSpec
    channel_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=10)
    #: who the report runs as (default: the creator); must be an active user
    owner_id: uuid.UUID | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def _report(self) -> ScheduleIn:
        if self.report_key not in REPORTS:
            raise ValueError(f"unknown report {self.report_key!r}")
        if self.params.statuses and any(s not in FINDING_STATUSES for s in self.params.statuses):
            raise ValueError("invalid status")
        if self.params.min_severity and self.params.min_severity not in SEVERITIES:
            raise ValueError("invalid severity")
        return self


async def _check(db: DB, p: CurrentPrincipal, body: ScheduleIn) -> uuid.UUID:
    channels = list(
        (await db.execute(select(NotificationChannel).where(NotificationChannel.id.in_(body.channel_ids)))).scalars()
    )
    if len(channels) != len(set(body.channel_ids)):
        raise ValidationFailed("unknown notification channel")
    if any(c.kind != "email" for c in channels):
        raise ValidationFailed("reports are delivered by e-mail: pick e-mail channels")
    if body.params.environment_id and await db.get(Environment, body.params.environment_id) is None:
        raise ValidationFailed("unknown environment")
    if body.params.server_id and await db.get(Server, body.params.server_id) is None:
        raise ValidationFailed("unknown server")
    owner_id = body.owner_id or p.user.id
    owner = await db.get(User, owner_id)
    if owner is None or not owner.is_active:
        raise ValidationFailed("the owner must be an active user")
    if owner_id != p.user.id:
        # running a report as someone else is an administrative act
        p.authz.require("users.manage")
    else:
        if not reports.allowed(p.authz, REPORTS[body.report_key]):
            raise PermissionDenied("You cannot run this report yourself")
    return owner_id


async def _out(db: DB, s: ReportSchedule) -> dict[str, Any]:
    owner = await db.get(User, s.owner_id) if s.owner_id else None
    r = REPORTS.get(s.report_key)
    return {
        "id": str(s.id),
        "name": s.name,
        "report_key": s.report_key,
        "report_title": r.title if r else s.report_key,
        "format": s.format,
        "params": s.params,
        "schedule": s.schedule,
        "channel_ids": s.channel_ids,
        "owner_id": str(s.owner_id) if s.owner_id else None,
        "owner": owner.username if owner else None,
        "enabled": s.enabled,
        "next_run_at": s.next_run_at,
        "last_run_at": s.last_run_at,
        "last_status": s.last_status,
        "last_error": s.last_error,
        "created_by": s.created_by,
        "created_at": s.created_at,
    }


async def _load(db: DB, schedule_id: uuid.UUID) -> ReportSchedule:
    s = await db.get(ReportSchedule, schedule_id)
    if s is None:
        raise NotFound("Report schedule not found")
    return s


@router.get("/report-schedules")
async def list_schedules(db: DB, p: CurrentPrincipal) -> list[dict[str, Any]]:
    p.authz.require("reports.schedule")
    rows = (await db.execute(select(ReportSchedule).order_by(ReportSchedule.name))).scalars()
    return [await _out(db, s) for s in rows]


@router.post("/report-schedules", status_code=201)
async def create_schedule(body: ScheduleIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("reports.schedule")
    if (await db.execute(select(func.count()).where(ReportSchedule.name == body.name))).scalar_one():
        raise Conflict("A schedule with this name exists")
    owner_id = await _check(db, p, body)
    s = ReportSchedule(
        id=uuid.uuid4(),
        name=body.name,
        report_key=body.report_key,
        format=body.format,
        params=body.params.model_dump(mode="json", exclude_defaults=True),
        schedule=body.schedule.model_dump(),
        channel_ids=[str(c) for c in body.channel_ids],
        owner_id=owner_id,
        enabled=body.enabled,
        created_by=p.username,
    )
    s.next_run_at = report_schedules.schedule_next(s, datetime.now(UTC))
    db.add(s)
    await db.flush()
    await audit.record(
        "report_schedule.create",
        actor=p,
        category="report",
        target_type="report_schedule",
        target_id=str(s.id),
        object_name=s.name,
        details={"report": s.report_key, "format": s.format, "schedule": s.schedule, "channels": len(s.channel_ids)},
    )
    return await _out(db, s)


@router.put("/report-schedules/{schedule_id}")
async def update_schedule(schedule_id: uuid.UUID, body: ScheduleIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("reports.schedule")
    s = await _load(db, schedule_id)
    if (
        body.name != s.name
        and (await db.execute(select(func.count()).where(ReportSchedule.name == body.name))).scalar_one()
    ):
        raise Conflict("A schedule with this name exists")
    owner_id = await _check(db, p, body)
    s.name, s.report_key, s.format = body.name, body.report_key, body.format
    s.params = body.params.model_dump(mode="json", exclude_defaults=True)
    s.schedule = body.schedule.model_dump()
    s.channel_ids = [str(c) for c in body.channel_ids]
    s.owner_id, s.enabled = owner_id, body.enabled
    s.next_run_at = report_schedules.schedule_next(s, datetime.now(UTC))
    await audit.record(
        "report_schedule.update",
        actor=p,
        category="report",
        target_type="report_schedule",
        target_id=str(s.id),
        object_name=s.name,
        details={"report": s.report_key, "format": s.format, "schedule": s.schedule, "enabled": s.enabled},
    )
    return await _out(db, s)


@router.delete("/report-schedules/{schedule_id}")
async def delete_schedule(schedule_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("reports.schedule")
    s = await _load(db, schedule_id)
    await db.delete(s)
    await audit.record(
        "report_schedule.delete",
        actor=p,
        category="report",
        target_type="report_schedule",
        target_id=str(schedule_id),
        object_name=s.name,
    )
    return {"deleted": str(schedule_id)}


@router.post("/report-schedules/{schedule_id}/run", dependencies=[rate_limit("expensive")])
async def run_now(schedule_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    """Generate and send the report now (the regular schedule is unchanged)."""
    p.authz.require("reports.schedule")
    s = await _load(db, schedule_id)
    run = await report_schedules.run_schedule(db, s, trigger="manual", actor=p.username)
    return _run_out(run)


def _run_out(r: ReportRun) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "schedule_id": str(r.schedule_id) if r.schedule_id else None,
        "report_key": r.report_key,
        "format": r.format,
        "status": r.status,
        "trigger": r.trigger,
        "triggered_by": r.triggered_by,
        "rows": r.rows,
        "size_bytes": r.size_bytes,
        "deliveries": r.deliveries,
        "error": r.error,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
    }


@router.get("/report-schedules/{schedule_id}/runs")
async def schedule_runs(
    schedule_id: uuid.UUID, db: DB, p: CurrentPrincipal, limit: int = Query(50, ge=1, le=500)
) -> list[dict[str, Any]]:
    p.authz.require("reports.schedule")
    await _load(db, schedule_id)
    rows = (
        await db.execute(
            select(ReportRun)
            .where(ReportRun.schedule_id == schedule_id)
            .order_by(ReportRun.started_at.desc())
            .limit(limit)
        )
    ).scalars()
    return [_run_out(r) for r in rows]
