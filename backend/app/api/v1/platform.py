"""Platform administration: collectors, settings, identity providers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.deps import DB, CurrentPrincipal
from app.core.config import get_settings
from app.core.crypto import get_keyring
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.jobs import runner as job_runner
from app.models import CollectorJob, GroupMapping, IdentityProvider, Server
from app.schemas.common import Message, Page, PageParams
from app.schemas.identity import (
    GroupMappingIn,
    GroupMappingOut,
    IdentityProviderIn,
    IdentityProviderOut,
    IdentityProviderUpdate,
)
from app.services import app_settings, audit
from app.services.oidc import redirect_uri, validate_config

router = APIRouter(tags=["platform"])
SECRET_CONFIG_KEYS = {"bind_password", "client_secret", "password", "secret"}


# ---- collectors ------------------------------------------------------------------------
@router.get("/collectors/status")
async def collectors_status(db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require_anywhere("collectors.view")
    since = datetime.now(UTC) - timedelta(hours=1)
    by_status = dict(
        (
            await db.execute(
                select(CollectorJob.status, func.count())
                .where(CollectorJob.created_at >= since)
                .group_by(CollectorJob.status)
            )
        ).all()
    )
    by_kind = [
        {"kind": k, "status": st, "count": n, "avg_ms": round(float(avg or 0), 1)}
        for k, st, n, avg in (
            await db.execute(
                select(CollectorJob.kind, CollectorJob.status, func.count(), func.avg(CollectorJob.duration_ms))
                .where(CollectorJob.created_at >= since)
                .group_by(CollectorJob.kind, CollectorJob.status)
            )
        ).all()
    ]
    pending = (
        await db.execute(select(func.count()).select_from(CollectorJob).where(CollectorJob.status == "pending"))
    ).scalar_one()
    circuits = (
        await db.execute(
            select(
                Server.id, Server.name, Server.circuit_open_until, Server.consecutive_failures, Server.last_error
            ).where(Server.circuit_open_until > datetime.now(UTC))
        )
    ).all()
    r = job_runner.runner
    s = get_settings()
    return {
        "process_role": s.process_role,
        "worker_running": r is not None,
        "is_leader": bool(r and r.is_leader),
        "started_at": r.started_at if r else None,
        "last_scheduler_tick": r.last_tick if r else None,
        "concurrency": s.collector_concurrency,
        "pending": pending,
        "last_hour": by_status,
        "by_kind": by_kind,
        "open_circuits": [
            {"server_id": str(i), "server_name": n, "until": u, "failures": f, "error": e} for i, n, u, f, e in circuits
        ],
    }


@router.get("/collectors/jobs")
async def jobs(
    db: DB,
    p: CurrentPrincipal,
    params: PageParams = Depends(),
    status: list[str] | None = Query(None),
    kind: str | None = None,
) -> Page[dict[str, Any]]:
    p.authz.require_anywhere("collectors.view")
    stmt = select(CollectorJob, Server.name).outerjoin(Server, Server.id == CollectorJob.server_id)
    flt = p.authz.server_filter("collectors.view")
    if flt is not None:
        stmt = stmt.where(flt)
    if status:
        stmt = stmt.where(CollectorJob.status.in_(status))
    if kind:
        stmt = stmt.where(CollectorJob.kind == kind)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(CollectorJob.created_at.desc())
            .limit(params.page_size)
            .offset((params.page - 1) * params.page_size)
        )
    ).all()
    return Page(
        items=[
            {
                "id": str(j.id),
                "server_id": str(j.server_id) if j.server_id else None,
                "server_name": n,
                "kind": j.kind,
                "status": j.status,
                "trigger": j.trigger,
                "created_at": j.created_at,
                "finished_at": j.finished_at,
                "duration_ms": j.duration_ms,
                "error": j.last_error,
                "worker": j.locked_by,
            }
            for j, n in rows
        ],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


# ---- settings ----------------------------------------------------------------------------
@router.get("/settings")
async def get_settings_view(db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    s = get_settings()
    editable = await app_settings.get_all(db)
    readonly = {
        "env": s.env,
        "process_role": s.process_role,
        "demo_mode": s.demo_mode,
        "public_base_url": s.public_base_url,
        "session_ttl_minutes": s.session_ttl_minutes,
        "session_idle_timeout_minutes": s.session_idle_timeout_minutes,
        "login_max_failures": s.login_max_failures,
        "login_lockout_minutes": s.login_lockout_minutes,
        "collector_concurrency": s.collector_concurrency,
        "metrics_retention_days (legacy PostgreSQL tables)": s.metrics_retention_days,
        "timeseries_backend": s.timeseries_backend if s.timeseries_enabled else "disabled",
        "timeseries_retention": s.timeseries_retention,
        "metric_batch_size": s.metric_batch_size,
        "metric_flush_interval": s.metric_flush_interval,
        "workspace_max_rows": s.workspace_max_rows,
        "workspace_timeout_seconds": s.workspace_timeout_seconds,
        "master_key_id": s.master_key_id,
        "cookie_secure": s.cookie_secure,
    }
    if not p.authz.has("settings.manage"):
        return {"editable": {"ui.banner_text": editable["ui.banner_text"]}, "readonly": {"demo_mode": s.demo_mode}}
    return {
        "editable": editable,
        "descriptions": {k: v[2] for k, v in app_settings.EDITABLE.items()},
        "readonly": readonly,
    }


@router.put("/settings")
async def put_settings(values: dict[str, Any], db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("settings.manage")
    out = await app_settings.put(db, values, p.username)
    await audit.record("settings.update", actor=p, category="platform", details={"values": values})
    return {"editable": out}


# ---- identity providers ------------------------------------------------------------------
def _idp_out(i: IdentityProvider, mappings: list[GroupMapping]) -> IdentityProviderOut:
    cfg = {k: v for k, v in (i.config or {}).items() if k not in SECRET_CONFIG_KEYS}
    if i.kind == "oidc":
        cfg["redirect_uri_effective"] = redirect_uri(i)
    return IdentityProviderOut(
        id=i.id,
        kind=i.kind,
        name=i.name,
        enabled=i.enabled,
        config=cfg,
        has_secret=bool(i.secret),
        mappings=[GroupMappingOut.model_validate(m) for m in mappings],
    )


async def _mappings(db: DB, pid: uuid.UUID) -> list[GroupMapping]:
    return list((await db.execute(select(GroupMapping).where(GroupMapping.provider_id == pid))).scalars())


@router.get("/identity-providers", response_model=list[IdentityProviderOut])
async def list_idps(db: DB, p: CurrentPrincipal) -> list[IdentityProviderOut]:
    p.authz.require("idp.manage")
    rows = (await db.execute(select(IdentityProvider).order_by(IdentityProvider.name))).scalars().all()
    return [_idp_out(i, await _mappings(db, i.id)) for i in rows]


@router.post("/identity-providers", response_model=IdentityProviderOut, status_code=201)
async def create_idp(body: IdentityProviderIn, db: DB, p: CurrentPrincipal) -> IdentityProviderOut:
    p.authz.require("idp.manage")
    if body.kind == "saml":
        raise ValidationFailed("SAML is architected but not enabled in this release; use OIDC")
    cfg = {k: v for k, v in body.config.items() if k not in SECRET_CONFIG_KEYS}
    validate_config(body.kind, cfg)
    if (await db.execute(select(IdentityProvider).where(IdentityProvider.name == body.name))).scalar_one_or_none():
        raise Conflict("A provider with this name already exists")
    i = IdentityProvider(kind=body.kind, name=body.name, enabled=body.enabled, config=cfg)
    db.add(i)
    await db.flush()
    if body.secret:
        i.secret = get_keyring().encrypt(body.secret, f"idp:{i.id}").to_dict()
    await audit.record(
        "idp.create",
        actor=p,
        category="platform",
        target_type="identity_provider",
        target_id=str(i.id),
        object_name=i.name,
        details={"kind": i.kind, "config": cfg},
    )
    return _idp_out(i, [])


@router.patch("/identity-providers/{idp_id}", response_model=IdentityProviderOut)
async def update_idp(
    idp_id: uuid.UUID, body: IdentityProviderUpdate, db: DB, p: CurrentPrincipal
) -> IdentityProviderOut:
    p.authz.require("idp.manage")
    i = await db.get(IdentityProvider, idp_id)
    if i is None:
        raise NotFound("Provider not found")
    if body.config is not None:
        cfg = {k: v for k, v in body.config.items() if k not in SECRET_CONFIG_KEYS}
        validate_config(i.kind, cfg)
        i.config = cfg
    if body.name:
        i.name = body.name
    if body.enabled is not None:
        i.enabled = body.enabled
    if body.secret:
        i.secret = get_keyring().encrypt(body.secret, f"idp:{i.id}").to_dict()
    await audit.record(
        "idp.update",
        actor=p,
        category="platform",
        target_type="identity_provider",
        target_id=str(i.id),
        object_name=i.name,
        details={"enabled": i.enabled, "secret_changed": bool(body.secret)},
    )
    return _idp_out(i, await _mappings(db, i.id))


@router.delete("/identity-providers/{idp_id}", response_model=Message)
async def delete_idp(idp_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> Message:
    p.authz.require("idp.manage")
    i = await db.get(IdentityProvider, idp_id)
    if i is None:
        raise NotFound("Provider not found")
    await db.delete(i)
    await audit.record(
        "idp.delete",
        actor=p,
        category="platform",
        target_type="identity_provider",
        target_id=str(idp_id),
        object_name=i.name,
        risk_class="PRIVILEGED_CHANGE",
    )
    return Message(message="Provider deleted")


@router.post("/identity-providers/{idp_id}/test")
async def test_idp(idp_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("idp.manage")
    i = await db.get(IdentityProvider, idp_id)
    if i is None:
        raise NotFound("Provider not found")
    try:
        if i.kind == "ldap":
            from app.services.ldap_auth import test_provider

            res = await test_provider(i)
            ok = bool(res.get("bind"))
            msg = "Service account bind succeeded" if ok else f"Bind failed: {res.get('result')}"
        else:
            from app.services.oidc import _metadata

            meta = await _metadata(i.config["issuer"])
            ok, msg = True, f"Discovery OK: {meta.get('issuer')}"
    except Exception as exc:  # noqa: BLE001
        ok, msg = False, f"{type(exc).__name__}: {exc}"
    await audit.record(
        "idp.test",
        actor=p,
        category="platform",
        object_name=i.name,
        result="success" if ok else "failure",
        failure_reason=None if ok else msg,
    )
    return {"ok": ok, "message": msg}


@router.post("/identity-providers/{idp_id}/mappings", response_model=GroupMappingOut, status_code=201)
async def add_mapping(idp_id: uuid.UUID, body: GroupMappingIn, db: DB, p: CurrentPrincipal) -> GroupMappingOut:
    p.authz.require("idp.manage")
    p.authz.require("users.manage")
    if await db.get(IdentityProvider, idp_id) is None:
        raise NotFound("Provider not found")
    m = GroupMapping(provider_id=idp_id, external_group=body.external_group, group_id=body.group_id)
    db.add(m)
    await db.flush()
    await audit.record(
        "idp.mapping.add",
        actor=p,
        category="platform",
        object_name=body.external_group,
        details={"group_id": str(body.group_id)},
        risk_class="PRIVILEGED_CHANGE",
    )
    return GroupMappingOut.model_validate(m)


@router.delete("/identity-providers/{idp_id}/mappings/{mapping_id}", response_model=Message)
async def delete_mapping(idp_id: uuid.UUID, mapping_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> Message:
    p.authz.require("idp.manage")
    m = await db.get(GroupMapping, mapping_id)
    if m is None or m.provider_id != idp_id:
        raise NotFound("Mapping not found")
    await db.delete(m)
    await audit.record("idp.mapping.delete", actor=p, category="platform", object_name=m.external_group)
    return Message(message="Mapping removed")


# ---- database drivers & engine support ----------------------------------------------------------
MATRIX_ROWS: list[tuple[str, str]] = [
    ("Connection", "connection"),
    ("Inventory", "objects"),
    ("Users", "users"),
    ("Privileges", "privileges"),
    ("Sessions", "sessions"),
    ("Locks", "locks"),
    ("Metrics", "performance_metrics"),
    ("Queries", "query_statistics"),
    ("Explain", "explain"),
    ("Replication", "replication"),
    ("Configuration", "configuration"),
    ("Security", "security_scan"),
    ("Recommendations", "recommendations"),
    ("Administration", "administration"),
]
MATRIX_STATUS = {
    "FULL": "FULL",
    "PARTIAL": "PARTIAL",
    "REQUIRES_CONFIGURATION": "REQUIRES CONFIGURATION",
    "REQUIRES_EXTENSION": "REQUIRES CONFIGURATION",
    "REQUIRES_PRIVILEGE": "REQUIRES PRIVILEGE",
    "REQUIRES_LICENSE": "REQUIRES LICENSE",
    "REQUIRES_EXTERNAL_MONITORING": "REQUIRES EXTERNAL MONITORING",
    "NOT_SUPPORTED": "NOT SUPPORTED",
    "NOT_IMPLEMENTED": "NOT IMPLEMENTED",
    "PLANNED": "NOT IMPLEMENTED",
}


#: matrix column → validation-suite capability (tests/integration/matrix.py)
VALIDATION_COLUMNS = {
    "Connection": "connection",
    "Inventory": "inventory",
    "Users": "users",
    "Privileges": "privileges",
    "Sessions": "sessions",
    "Locks": "locks",
    "Metrics": "metrics",
    "Queries": "queries",
    "Explain": "explain",
    "Replication": "storage",
    "Configuration": "storage",
    "Security": "security",
    "Recommendations": "recommendations",
    "Administration": "administration",
}


def real_engine_validation() -> dict[str, Any]:
    """Results of the real-engine suite shipped with this build (app/data/real_engine_validation.json,
    refreshed by scripts/integration-test). Missing file → every engine pending."""
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "data" / "real_engine_validation.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"engines": {}, "pending": {}}


def _validation_for(engine: str, data: dict[str, Any]) -> dict[str, Any]:
    e = (data.get("engines") or {}).get(engine)
    pending = (data.get("pending") or {}).get(engine)
    if e and e.get("result") in ("passed", "failed"):
        return {
            "status": "validated" if e["result"] == "passed" else "failed",
            "version": e.get("version"),
            "validated_at": e.get("validated_at") or data.get("generated_at"),
            "environment": data.get("environment"),
            "capabilities": {k: v.get("status") for k, v in (e.get("capabilities") or {}).items()},
            "note": None,
        }
    status = "external" if pending and "External validation" in pending else "pending"
    return {"status": status, "version": None, "validated_at": None, "capabilities": {}, "note": pending}


@router.get("/platform/drivers", summary="Installed database drivers and client libraries")
async def database_drivers(p: CurrentPrincipal) -> list[dict[str, Any]]:
    p.authz.require_anywhere("collectors.view")
    from app.adapters import drivers

    return drivers.health()


@router.get("/platform/engine-support", summary="Capability matrix generated from the adapter definitions")
async def engine_support(p: CurrentPrincipal) -> dict[str, Any]:
    from app.adapters import drivers
    from app.adapters.base import Capability
    from app.adapters.registry import supported_engines
    from app.advisors.engine import catalog as rule_catalog

    rules: dict[str, int] = {}
    for r in rule_catalog():
        for e in r["engines"]:
            rules[e] = rules.get(e, 0) + 1
    health = drivers.health()
    driver_status = {e: d["status"] for d in health for e in d["engines"]}
    driver_detail = {e: {"error": d["error"], "remediation": d["remediation"]} for d in health for e in d["engines"]}
    validation = real_engine_validation()
    engines = []
    for name, cls in supported_engines().items():
        impl, eng = cls.capability_profile(), cls.engine_profile()
        cells = {}
        val = _validation_for(name, validation) if cls.implemented and name != "demo" else None
        for label, key in MATRIX_ROWS:
            c = impl[Capability(key)]
            cells[label] = {
                "status": MATRIX_STATUS.get(c.level.value, c.level.value),
                "level": c.level.value,
                "note": c.note,
                "engine_level": eng[Capability(key)].level.value,
                # real-engine evidence for this cell: validated | failed | skipped | not_tested | None
                "validated": (val["capabilities"].get(VALIDATION_COLUMNS[label]) if val else None),
            }
        engines.append(
            {
                "engine": name,
                "display_name": cls.display_name,
                "implemented": cls.implemented,
                "support_level": cls.support_level if cls.implemented else "planned",
                "support_note": cls.support_note,
                "implementation_phase": cls.implementation_phase,
                "driver": cls.driver,
                "driver_status": driver_status.get(name),
                "driver_detail": driver_detail.get(name),
                "validation": val,
                "engine_specific_rules": rules.get(name, 0),
                "generic_rules": rules.get("*", 0),
                "capabilities": cells,
                "licensable_features": cls.licensable_features,
            }
        )
    return {"rows": [label for label, _ in MATRIX_ROWS], "engines": engines}
