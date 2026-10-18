"""Platform setup wizard (resumable) and the post-setup checklist."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import DB, CurrentPrincipal
from app.core.errors import ValidationFailed
from app.models import (
    AppSetting,
    CollectorJob,
    Credential,
    DataSource,
    Environment,
    IdentityProvider,
    Organization,
    Server,
    Site,
)
from app.services import app_settings, audit

router = APIRouter(tags=["setup"])

STEPS = [
    "welcome",
    "admin",
    "organization",
    "sites",
    "environments",
    "authentication",
    "monitoring",
    "retention",
    "database",
    "review",
    "complete",
]
SETUP_KEY = "platform.setup"
TZ_KEY = "organization.timezone"
#: default collection interval for newly registered servers per monitoring profile
PROFILES: dict[str, dict[str, Any]] = {
    "light": {"collection_interval_s": 300, "description": "every 5 minutes: large estates, minimal overhead"},
    "standard": {"collection_interval_s": 60, "description": "every minute: recommended default"},
    "intensive": {"collection_interval_s": 15, "description": "every 15 seconds: short investigations, small fleets"},
}


class SetupUpdate(BaseModel):
    current_step: str | None = None
    completed_steps: list[str] | None = None
    data: dict[str, dict[str, Any]] | None = None


class OrganizationIn(BaseModel):
    name: str = Field(min_length=2, max_length=128)
    timezone: str = Field(min_length=1, max_length=64)


class MonitoringProfileIn(BaseModel):
    profile: Literal["light", "standard", "intensive"]


async def _row(db: DB, key: str) -> AppSetting | None:
    return await db.get(AppSetting, key)


async def _state(db: DB) -> dict[str, Any]:
    row = await _row(db, SETUP_KEY)
    st = dict(row.value) if row and isinstance(row.value, dict) else {}
    return {
        "completed": bool(st.get("completed")),
        "current_step": st.get("current_step"),
        "completed_steps": list(st.get("completed_steps") or []),
        "data": dict(st.get("data") or {}),
        "completed_at": st.get("completed_at"),
    }


async def _save(db: DB, st: dict[str, Any], actor: str) -> None:
    row = await _row(db, SETUP_KEY)
    if row is None:
        db.add(AppSetting(key=SETUP_KEY, value=st, updated_by=actor))
    else:
        row.value, row.updated_by = st, actor
    await db.flush()


async def _org(db: DB) -> Organization:
    return (await db.execute(select(Organization).order_by(Organization.created_at).limit(1))).scalar_one()


async def _payload(db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    st = await _state(db)
    manage = p.authz.has("settings.manage")
    base = {"completed": st["completed"], "needs_setup": not st["completed"] and manage, "steps": STEPS}
    if not manage:
        return base
    org = await _org(db)
    tz = await _row(db, TZ_KEY)
    return {
        **base,
        "current_step": st["current_step"],
        "completed_steps": st["completed_steps"],
        "data": st["data"],
        "completed_at": st["completed_at"],
        "organization": {"id": str(org.id), "name": org.name, "timezone": tz.value if tz else "UTC"},
    }


@router.get("/platform/setup", summary="Setup wizard state (resumable)")
async def get_setup(db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    return await _payload(db, p)


@router.put("/platform/setup", summary="Save setup wizard progress")
async def put_setup(body: SetupUpdate, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("settings.manage")
    st = await _state(db)
    if body.current_step is not None:
        if body.current_step not in STEPS:
            raise ValidationFailed(f"unknown step {body.current_step!r}", details={"steps": STEPS})
        st["current_step"] = body.current_step
    if body.completed_steps is not None:
        bad = [s for s in body.completed_steps if s not in STEPS]
        if bad:
            raise ValidationFailed(f"unknown steps {bad}", details={"steps": STEPS})
        st["completed_steps"] = [s for s in STEPS if s in set(body.completed_steps)]
    if body.data:
        bad = [k for k in body.data if k not in STEPS]
        if bad:
            raise ValidationFailed(f"unknown steps {bad}", details={"steps": STEPS})
        for k, v in body.data.items():
            st["data"][k] = {**(st["data"].get(k) or {}), **audit.sanitize(v)}  # never persist secrets
    await _save(db, st, p.username)
    await audit.record(
        "setup.progress",
        actor=p,
        category="platform",
        details={
            "current_step": st["current_step"],
            "completed_steps": st["completed_steps"],
            "data_steps": sorted((body.data or {}).keys()),
        },
    )
    return await _payload(db, p)


@router.post("/platform/setup/organization", summary="Organization name and time zone")
async def set_organization(body: OrganizationIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("settings.manage")
    try:
        ZoneInfo(body.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationFailed(f"unknown time zone {body.timezone!r} (use an IANA name such as Asia/Riyadh)") from exc
    if body.timezone not in available_timezones() and body.timezone != "UTC":
        raise ValidationFailed(f"unknown time zone {body.timezone!r}")
    org = await _org(db)
    old = {"name": org.name}
    clash = (
        await db.execute(select(Organization).where(Organization.name == body.name.strip(), Organization.id != org.id))
    ).scalar_one_or_none()
    if clash:
        raise ValidationFailed("another organization already uses this name")
    org.name = body.name.strip()
    tz = await _row(db, TZ_KEY)
    if tz is None:
        db.add(AppSetting(key=TZ_KEY, value=body.timezone, updated_by=p.username))
    else:
        old["timezone"] = tz.value
        tz.value, tz.updated_by = body.timezone, p.username
    st = await _state(db)
    st["data"]["organization"] = {"name": org.name, "timezone": body.timezone}
    await _save(db, st, p.username)
    await audit.record(
        "setup.organization",
        actor=p,
        category="platform",
        object_name=org.name,
        details={"before": old, "after": {"name": org.name, "timezone": body.timezone}},
    )
    return await _payload(db, p)


@router.post("/platform/setup/monitoring", summary="Default monitoring profile for new servers")
async def set_monitoring_profile(body: MonitoringProfileIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("settings.manage")
    prof = PROFILES[body.profile]
    await app_settings.put(db, {"collection.default_interval_s": prof["collection_interval_s"]}, p.username)
    st = await _state(db)
    st["data"]["monitoring"] = {"profile": body.profile}
    await _save(db, st, p.username)
    await audit.record(
        "setup.monitoring_profile", actor=p, category="platform", details={"profile": body.profile, **prof}
    )
    return {
        "profile": body.profile,
        "intervals": {"collection_interval_s": prof["collection_interval_s"]},
        "description": prof["description"],
        "applies_to": "servers registered from now on; existing servers keep their interval",
        "profiles": PROFILES,
    }


@router.post("/platform/setup/complete", summary="Mark the setup wizard as completed")
async def complete_setup(db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("settings.manage")
    st = await _state(db)
    st["completed"] = True
    st["current_step"] = "complete"
    st["completed_steps"] = [s for s in STEPS if s in set(st["completed_steps"]) | {"complete"}]
    st["completed_at"] = datetime.now(UTC).isoformat()
    await _save(db, st, p.username)
    await audit.record(
        "setup.complete", actor=p, category="platform", details={"completed_steps": st["completed_steps"]}
    )
    return await _payload(db, p)


async def _count(db: DB, stmt: Any) -> int:
    return int((await db.execute(stmt)).scalar_one() or 0)


@router.get("/platform/setup/checklist", summary="Post-setup checklist computed from the platform state")
async def checklist(db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    from app.adapters import drivers
    from app.jobs import runner as job_runner

    st = await _state(db)
    real = Server.data_source == DataSource.REAL
    sites = await _count(db, select(func.count()).select_from(Site).where(Site.data_source != DataSource.SIMULATED))
    envs = await _count(
        db, select(func.count()).select_from(Environment).where(Environment.data_source != DataSource.SIMULATED)
    )
    idps = await _count(
        db, select(func.count()).select_from(IdentityProvider).where(IdentityProvider.enabled.is_(True))
    )
    servers = await _count(db, select(func.count()).select_from(Server).where(real))
    creds = await _count(
        db,
        select(func.count())
        .select_from(Credential)
        .join(Server, Server.id == Credential.server_id)
        .where(real, Credential.purpose == "monitoring"),
    )
    recent_ok = await _count(
        db,
        select(func.count())
        .select_from(CollectorJob)
        .where(CollectorJob.status == "success", CollectorJob.created_at >= datetime.now(UTC) - timedelta(hours=1)),
    )
    simulated = await _count(
        db, select(func.count()).select_from(Server).where(Server.data_source == DataSource.SIMULATED)
    )
    retention_set = await _row(db, "monitoring.retention_days") is not None or "retention" in st["completed_steps"]
    org_set = await _row(db, TZ_KEY) is not None or "organization" in st["data"]
    missing = [d["driver"] for d in drivers.health() if d["status"] == "missing"]
    items = [
        (
            "admin_password_changed",
            "Change the initial administrator password",
            "Signed-in administrator no longer uses a generated password.",
            not p.user.must_change_password,
            "#change-password",
            False,
        ),
        (
            "organization",
            "Name your organization and time zone",
            "Used in reports and schedules.",
            org_set,
            "/setup?step=organization",
            False,
        ),
        (
            "sites",
            "Add a site",
            "Data centers or cloud regions group servers in the topology.",
            sites > 0,
            "/fleet/environments",
            False,
        ),
        (
            "environments",
            "Define environments",
            "Production, DR, UAT … drive risk rules and RBAC scopes.",
            envs > 0,
            "/fleet/environments",
            False,
        ),
        (
            "authentication",
            "Connect LDAP or OIDC",
            "Optional: sign in with corporate accounts.",
            idps > 0,
            "/platform/identity",
            True,
        ),
        (
            "first_server",
            "Register your first database server",
            "Monitoring starts after registration.",
            servers > 0,
            "/fleet/servers?add=1",
            False,
        ),
        (
            "monitoring_credential",
            "Give a server a monitoring credential",
            "A least-privilege account lets collectors read metrics.",
            creds > 0,
            "/platform/credentials",
            False,
        ),
        (
            "collectors",
            "Collectors are running",
            "A worker process is collecting metrics.",
            job_runner.runner is not None or recent_ok > 0,
            "/platform/collectors",
            False,
        ),
        (
            "retention",
            "Review telemetry retention",
            "How long metrics are kept.",
            retention_set,
            "/platform/monitoring-storage",
            False,
        ),
        (
            "drivers_healthy",
            "All database drivers installed",
            "Missing: " + ", ".join(missing) if missing else "Every engine driver is available.",
            not missing,
            "/platform/drivers",
            False,
        ),
        (
            "simulation_off",
            "Remove the simulated fleet",
            "Optional: demo servers are clearly labelled.",
            simulated == 0,
            "/platform/simulation",
            True,
        ),
    ]
    out = [
        {"id": i, "title": t, "description": d, "done": bool(done), "href": h, "optional": opt}
        for i, t, d, done, h, opt in items
    ]
    return {"items": out, "done": sum(1 for i in out if i["done"]), "total": len(out)}
