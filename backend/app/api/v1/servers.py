"""Server inventory endpoints."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select

from app.api import source as fleet_source
from app.api.deps import DB, CurrentPrincipal, load_server, rate_limit
from app.api.source import SourceQuery
from app.core.errors import ConfirmationRequired, Conflict, ValidationFailed
from app.domain.scopes import ResourceRef
from app.jobs.handlers import JOB_KINDS
from app.jobs.runner import enqueue
from app.models import DataSource, Environment, Server, ServerTag
from app.schemas.common import Message, Page, PageParams, apply_sort
from app.schemas.inventory import (
    CredentialIn,
    EngineOut,
    ServerCreate,
    ServerDetailOut,
    ServerOut,
    ServerUpdate,
    TestConnectionRequest,
    TestConnectionResult,
)
from app.services import audit, inventory, secrets
from app.services.authz import server_ref

router = APIRouter(tags=["servers"])


@router.get("/engines", response_model=list[EngineOut], summary="Supported engines and their capability profiles")
async def engines(p: CurrentPrincipal) -> list[EngineOut]:
    return [EngineOut(**e) for e in inventory.engines_catalog()]


@router.get("/servers", response_model=Page[ServerOut])
async def list_servers(
    db: DB,
    p: CurrentPrincipal,
    params: PageParams = Depends(),
    engine: list[str] | None = Query(None),
    environment_id: list[uuid.UUID] | None = Query(None),
    health: list[str] | None = Query(None),
    site_id: uuid.UUID | None = None,
    cluster_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
    tag: str | None = Query(None, description="key or key=value"),
    demo: bool | None = Query(None, description="deprecated: use source"),
    source: SourceQuery = None,
) -> Page[ServerOut]:
    stmt = select(Server).outerjoin(Environment, Server.environment_id == Environment.id)
    flt = p.authz.server_filter("server.view")
    if flt is not None:
        stmt = stmt.where(flt)
    src_cond = fleet_source.condition(await fleet_source.resolve(db, p, source))
    if src_cond is not None:
        stmt = stmt.where(src_cond)
    if params.q:
        like = f"%{params.q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Server.name).like(like),
                func.lower(Server.host).like(like),
                func.lower(Server.description).like(like),
                func.lower(Server.owner).like(like),
            )
        )
    if engine:
        stmt = stmt.where(Server.engine.in_(engine))
    if environment_id:
        stmt = stmt.where(Server.environment_id.in_(environment_id))
    if health:
        stmt = stmt.where(Server.health_status.in_(health))
    if site_id:
        stmt = stmt.where(Server.site_id == site_id)
    if cluster_id:
        stmt = stmt.where(Server.cluster_id == cluster_id)
    if application_id:
        stmt = stmt.where(Server.application_id == application_id)
    if demo is not None:
        stmt = stmt.where(Server.is_demo.is_(demo))
    if tag:
        k, _, v = tag.partition("=")
        sub = select(ServerTag.server_id).where(ServerTag.key == k)
        if v:
            sub = sub.where(ServerTag.value == v)
        stmt = stmt.where(Server.id.in_(sub))
    health_rank = func.array_position(
        ["unavailable", "critical", "warning", "unknown", "healthy"], Server.health_status
    )
    stmt = apply_sort(
        stmt,
        params.sort,
        {
            "name": Server.name,
            "engine": Server.engine,
            "health": health_rank,
            "environment": Environment.sort_order,
            "last_collected_at": Server.last_collected_at,
            "created_at": Server.created_at,
        },
        Server.name,
    )
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (
        (await db.execute(stmt.limit(params.page_size).offset((params.page - 1) * params.page_size)))
        .scalars()
        .unique()
        .all()
    )
    c = await inventory.counts(db, [s.id for s in rows])
    return Page(items=[inventory.to_out(s, c) for s in rows], total=total, page=params.page, page_size=params.page_size)


def _env_ref(body_env: uuid.UUID | None) -> ResourceRef:
    # registering into an environment requires server.register for that environment (or platform)
    return ResourceRef(server_id="*", environment_id=str(body_env) if body_env else None)


@router.post("/servers", response_model=ServerOut, status_code=201)
async def register_server(body: ServerCreate, db: DB, p: CurrentPrincipal) -> ServerOut:
    if not (p.authz.has("server.register") or p.authz.has("server.register", _env_ref(body.environment_id))):
        p.authz.require("server.register")
    if (body.monitoring_credential or body.admin_credential) and not (
        p.authz.has("credentials.manage") or p.authz.has("credentials.manage", _env_ref(body.environment_id))
    ):
        p.authz.require("credentials.manage")
    s = await inventory.create_server(db, body, p.username)
    await audit.record(
        "server.register",
        actor=p,
        server=s,
        target_type="server",
        target_id=str(s.id),
        risk_class="LOW_RISK_CHANGE",
        details={
            "engine": s.engine,
            "host": s.host,
            "port": s.port,
            "credentials": [k for k in ("monitoring", "admin") if getattr(body, f"{k}_credential")],
        },
    )
    if inventory.implementation(s) == "implemented" and body.monitoring_credential:
        for kind in ("discover_inventory", "collect_metrics"):
            await enqueue(s.id, kind, "manual", db=db)
    c = await inventory.counts(db, [s.id])
    return inventory.to_out(s, c)


@router.post(
    "/servers/test-connection",
    response_model=TestConnectionResult,
    summary="Test connection parameters before registering",
)
async def test_new_connection(body: TestConnectionRequest, p: CurrentPrincipal) -> TestConnectionResult:
    p.authz.require_anywhere("server.register")
    try:
        if body.provider != "local":
            body.password = await secrets.resolve_reference(body.provider, body.secret_ref)
        res = await inventory.test_connection_params(body)
    except ValidationFailed as exc:
        res = {"ok": False, "engine": body.engine, "error_code": "CREDENTIAL_UNAVAILABLE", "message": exc.message}
    await audit.record(
        "server.test_connection",
        actor=p,
        result="success" if res["ok"] else "failure",
        failure_reason=None if res["ok"] else res["message"],
        details={"engine": body.engine, "host": body.host, "port": body.port},
    )
    return TestConnectionResult(**res)


@router.get("/servers/{server_id}", response_model=ServerDetailOut)
async def get_server(server_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> ServerDetailOut:
    s = await load_server(db, p, server_id)
    ref = server_ref(s)
    c = await inventory.counts(db, [s.id])
    base = inventory.to_out(s, c).model_dump()
    creds = {}
    can_creds = p.authz.has("credentials.manage", ref)
    for purpose in ("monitoring", "readonly", "admin"):
        d = secrets.describe(await secrets.get_credential(db, s.id, purpose))
        creds[purpose] = d if can_creds else ({"configured": True} if d else None)
    unavailable = (s.latest_metrics or {}).get("_unavailable") or {}
    return ServerDetailOut(
        **base,
        capabilities=s.capabilities or {},
        credentials=creds,
        server_info=(s.collector_state or {}).get("server_info"),
        unavailable_metrics=unavailable,
        permissions=sorted(p.authz.permissions_for(ref)),
        simulation=(s.options or {}).get("simulation") if s.is_simulated else None,
        collection_status=(s.collector_state or {}).get("components") or {},
    )


@router.patch("/servers/{server_id}", response_model=ServerOut)
async def update_server(server_id: uuid.UUID, body: ServerUpdate, db: DB, p: CurrentPrincipal) -> ServerOut:
    s = await load_server(db, p, server_id, "server.modify")
    if body.environment_id and body.environment_id != s.environment_id:
        # moving a server into another environment must be allowed in the destination too
        dest = ResourceRef(
            server_id=str(s.id),
            environment_id=str(body.environment_id),
            site_id=str(s.site_id) if s.site_id else None,
            cluster_id=str(s.cluster_id) if s.cluster_id else None,
        )
        p.authz.require("server.modify", dest)
    changes = await inventory.update_server(db, s, body)
    await audit.record(
        "server.modify",
        actor=p,
        server=s,
        target_type="server",
        target_id=str(s.id),
        risk_class="LOW_RISK_CHANGE",
        details={"changes": changes},
    )
    return inventory.to_out(s, await inventory.counts(db, [s.id]))


@router.delete("/servers/{server_id}", response_model=Message)
async def delete_server(
    server_id: uuid.UUID,
    db: DB,
    p: CurrentPrincipal,
    confirm: str | None = Query(None, description="type the server name to confirm"),
) -> Message:
    s = await load_server(db, p, server_id, "server.delete")
    if confirm != s.name:
        raise ConfirmationRequired(
            "Type the server name to confirm removal from the platform",
            details={"expected": "server name", "risk": "DESTRUCTIVE"},
        )
    name = s.name
    await db.delete(s)
    from app.services import monitoring_storage

    # telemetry is deleted by the server's stable id (queued and retried if the TSDB is down)
    ts_done = await monitoring_storage.delete_series(db, {"server_id": str(server_id)})
    await audit.record(
        "server.delete",
        actor=p,
        target_type="server",
        target_id=str(server_id),
        object_name=name,
        risk_class="DESTRUCTIVE",
        details={
            "note": "inventory record, metrics and findings removed; the database server itself is untouched",
            "timeseries_delete": "done" if ts_done else "queued",
        },
    )
    return Message(message=f"{name} removed from DBManager")


@router.post(
    "/servers/{server_id}/test-connection", response_model=TestConnectionResult, dependencies=[rate_limit("expensive")]
)
async def test_server_connection(server_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> TestConnectionResult:
    s = await load_server(db, p, server_id)
    if not (p.authz.has("server.modify", server_ref(s)) or p.authz.has("collectors.manage", server_ref(s))):
        p.authz.require("server.modify", server_ref(s))
    res = await inventory.test_connection_server(db, s)
    if res["ok"]:
        from app.services.collection import mark_success

        mark_success(s)
        s.version = res.get("version") or s.version
        if res.get("capabilities"):
            s.capabilities = res["capabilities"]
    elif inventory.implementation(s) != "planned":
        from app.services.collection import mark_failure

        mark_failure(s, res["message"])
    await audit.record(
        "server.test_connection",
        actor=p,
        server=s,
        result="success" if res["ok"] else "failure",
        failure_reason=None if res["ok"] else res["message"],
    )
    return TestConnectionResult(**res)


@router.post(
    "/servers/{server_id}/collect",
    response_model=Message,
    summary="Queue an immediate collection",
    dependencies=[rate_limit("expensive")],
)
async def collect_now(
    server_id: uuid.UUID, db: DB, p: CurrentPrincipal, kinds: list[str] | None = Query(None)
) -> Message:
    s = await load_server(db, p, server_id, "collectors.manage")
    wanted = kinds or list(JOB_KINDS)
    queued = [k for k in wanted if k in JOB_KINDS and await enqueue(s.id, k, "manual", db=db)]
    s.circuit_open_until = None
    await audit.record("server.collect", actor=p, server=s, details={"kinds": queued}, category="collector")
    return Message(message=f"Queued {len(queued)} collection job(s)", details={"queued": queued})


@router.put("/servers/{server_id}/credentials/{purpose}", response_model=Message)
async def set_credential(
    server_id: uuid.UUID,
    purpose: Literal["monitoring", "readonly", "admin"],
    body: CredentialIn,
    db: DB,
    p: CurrentPrincipal,
) -> Message:
    s = await load_server(db, p, server_id, "credentials.manage")
    if s.is_simulated:
        raise Conflict(
            "Simulated servers never connect to a database, so they take no credentials.",
            code="simulated_server",
        )
    await secrets.set_credential(
        db,
        s,
        purpose,
        username=body.username,
        password=body.password,
        provider=body.provider,
        secret_ref=body.secret_ref,
        actor=p.username,
    )
    s.consecutive_failures, s.circuit_open_until = 0, None
    await audit.record(
        "credentials.set",
        actor=p,
        server=s,
        target_type="credential",
        object_name=purpose,
        risk_class="PRIVILEGED_CHANGE",
        details={"username": body.username, "provider": body.provider},
    )
    if inventory.implementation(s) == "implemented" and purpose == "monitoring":
        await enqueue(s.id, "discover_inventory", "manual", db=db)
    return Message(message=f"{purpose.capitalize()} credential saved")


@router.delete("/servers/{server_id}/credentials/{purpose}", response_model=Message)
async def delete_credential(
    server_id: uuid.UUID, purpose: Literal["monitoring", "readonly", "admin"], db: DB, p: CurrentPrincipal
) -> Message:
    s = await load_server(db, p, server_id, "credentials.manage")
    cred = await secrets.get_credential(db, s.id, purpose)
    if cred:
        await db.delete(cred)
    await audit.record(
        "credentials.delete",
        actor=p,
        server=s,
        target_type="credential",
        object_name=purpose,
        risk_class="PRIVILEGED_CHANGE",
    )
    return Message(message=f"{purpose.capitalize()} credential removed")


@router.get("/servers/{server_id}/capabilities")
async def capabilities(server_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> dict[str, object]:
    s = await load_server(db, p, server_id)
    return {"engine": s.engine, "implementation": inventory.implementation(s), "capabilities": s.capabilities}


@router.get("/databases", summary="Fleet-wide database inventory (from the last discovery)")
async def fleet_databases(
    db: DB,
    p: CurrentPrincipal,
    params: PageParams = Depends(),
    engine: str | None = None,
    include_system: bool = False,
    source: SourceQuery = None,
) -> Page[dict[str, object]]:
    from app.models import DatabaseEntry

    stmt = select(DatabaseEntry, Server).join(Server, Server.id == DatabaseEntry.server_id)
    flt = p.authz.server_filter("database.view")
    if flt is not None:
        stmt = stmt.where(flt)
    src_cond = fleet_source.condition(await fleet_source.resolve(db, p, source))
    if src_cond is not None:
        stmt = stmt.where(src_cond)
    if not include_system:
        stmt = stmt.where(DatabaseEntry.properties["is_system"].astext != "true")
    if engine:
        stmt = stmt.where(Server.engine == engine)
    if params.q:
        like = f"%{params.q.lower()}%"
        stmt = stmt.where(or_(func.lower(DatabaseEntry.name).like(like), func.lower(Server.name).like(like)))
    stmt = apply_sort(
        stmt,
        params.sort,
        {"name": DatabaseEntry.name, "size": DatabaseEntry.size_bytes, "server": Server.name},
        DatabaseEntry.size_bytes.desc().nulls_last(),
    )
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (await db.execute(stmt.limit(params.page_size).offset((params.page - 1) * params.page_size))).all()
    return Page(
        items=[
            {
                "name": d.name,
                "size_bytes": d.size_bytes,
                "owner": d.owner,
                "properties": d.properties,
                "discovered_at": d.discovered_at,
                "server_id": str(s.id),
                "server_name": s.name,
                "engine": s.engine,
                "environment": s.environment.name if s.environment else None,
                "is_demo": s.is_demo,
            }
            for d, s in rows
        ],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.get("/credentials", summary="Credential coverage across servers (no secret material)")
async def credentials_overview(db: DB, p: CurrentPrincipal) -> list[dict[str, object]]:
    from app.models import Credential

    p.authz.require_anywhere("credentials.manage")
    # simulated servers never have credentials; they are not part of this view
    stmt = select(Server).where(Server.data_source != DataSource.SIMULATED)
    flt = p.authz.server_filter("credentials.manage")
    if flt is not None:
        stmt = stmt.where(flt)
    servers = (await db.execute(stmt.order_by(Server.name))).scalars().all()
    creds: dict[object, dict[str, Credential]] = {}
    for c in (await db.execute(select(Credential).where(Credential.server_id.in_([s.id for s in servers])))).scalars():
        creds.setdefault(c.server_id, {})[c.purpose] = c
    return [
        {
            "server_id": str(s.id),
            "server_name": s.name,
            "engine": s.engine,
            "is_demo": s.is_demo,
            "environment": s.environment.name if s.environment else None,
            "tls_mode": s.tls_mode,
            "connection_status": s.connection_status,
            "monitoring": secrets.describe(creds.get(s.id, {}).get("monitoring")),
            "admin": secrets.describe(creds.get(s.id, {}).get("admin")),
        }
        for s in servers
    ]


@router.get(
    "/servers/{server_id}/diagnostics",
    summary="Download the collector diagnostic bundle of a server (redacted, no credentials)",
)
async def diagnostics(server_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> JSONResponse:
    from datetime import UTC, datetime

    from app.services import diagnostics as diag

    s = await load_server(db, p, server_id)
    p.authz.require("collectors.view", server_ref(s))
    data = await diag.bundle(db, s, p.username)
    await audit.record("server.diagnostics", actor=p, category="operations", server=s, object_name=s.name)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return JSONResponse(
        data, headers={"Content-Disposition": f'attachment; filename="dbm-diagnostics-{s.name}-{stamp}.json"'}
    )
