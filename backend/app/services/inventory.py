"""Server inventory service: CRUD, serialization, connection tests."""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import AdapterError, CapabilityLevel
from app.adapters.registry import adapter_class_for, create_adapter, normalize_engine, supported_engines
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.models import (
    Application,
    Cluster,
    DatabaseEntry,
    DataSource,
    Environment,
    Organization,
    Recommendation,
    SecurityFinding,
    Server,
    ServerTag,
    Site,
)
from app.schemas.inventory import (
    EnvRef,
    Ref,
    ServerCreate,
    ServerOut,
    ServerUpdate,
    Tag,
    TestConnectionRequest,
)
from app.services import secrets
from app.services.connections import build_spec
from app.services.metric_catalog import DASHBOARD_METRICS

COMMON_OPTIONS = {"connect_timeout", "statement_timeout"}


def implemented_engines() -> set[str]:
    return {name for name, cls in supported_engines().items() if cls.implemented}


def management_mode(server: Server) -> str:
    """'monitoring' servers never get change actions; servers registered before this setting
    existed keep the previous behaviour ('administration')."""
    return str((server.options or {}).get("management_mode") or "administration")


def connection_options(server: Server) -> dict[str, Any]:
    try:
        keys = {o["key"] for o in adapter_class_for(server.engine).connection_options} | COMMON_OPTIONS
    except ValueError:
        keys = COMMON_OPTIONS
    return {k: v for k, v in (server.options or {}).items() if k in keys}


def _validated_options(engine: str, opts: dict[str, Any] | None) -> dict[str, Any]:
    opts = dict(opts or {})
    cls = adapter_class_for(engine)
    allowed = {o["key"]: o for o in cls.connection_options}
    out: dict[str, Any] = {}
    for k, v in opts.items():
        if v in (None, ""):
            continue
        if k in COMMON_OPTIONS:
            try:
                out[k] = max(1.0, min(float(v), 600.0))
            except (TypeError, ValueError) as exc:
                raise ValidationFailed(f"{k} must be a number of seconds", details={"field": k}) from exc
            continue
        spec = allowed.get(k)
        if spec is None:
            raise ValidationFailed(
                f"Unknown connection option {k!r} for {cls.display_name}",
                details={"field": k, "allowed": sorted(allowed) + sorted(COMMON_OPTIONS)},
            )
        if spec.get("choices") and v not in spec["choices"]:
            raise ValidationFailed(f"{k} must be one of {', '.join(map(str, spec['choices']))}", details={"field": k})
        if spec.get("type") == "number":
            try:
                v = float(v)
            except (TypeError, ValueError) as exc:
                raise ValidationFailed(f"{k} must be a number", details={"field": k}) from exc
        if spec.get("type") == "boolean":
            v = bool(v) if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
        out[k] = v
    return out


def engine_label(engine: str) -> str:
    try:
        return adapter_class_for(engine).display_name
    except ValueError:
        return engine


def implementation(server: Server) -> str:
    if server.is_simulated:
        return "simulated"
    try:
        return "implemented" if adapter_class_for(server.engine).implemented else "planned"
    except ValueError:
        return "planned"


async def counts(db: AsyncSession, ids: list[uuid.UUID]) -> dict[str, dict[uuid.UUID, int]]:
    if not ids:
        return {"dbs": {}, "recs": {}, "sec": {}}
    dbs = dict(
        (
            await db.execute(
                select(DatabaseEntry.server_id, func.count())
                .where(DatabaseEntry.server_id.in_(ids))
                .group_by(DatabaseEntry.server_id)
            )
        ).all()
    )
    recs = dict(
        (
            await db.execute(
                select(Recommendation.server_id, func.count())
                .where(Recommendation.server_id.in_(ids), Recommendation.status == "open")
                .group_by(Recommendation.server_id)
            )
        ).all()
    )
    sec = dict(
        (
            await db.execute(
                select(SecurityFinding.server_id, func.count())
                .where(SecurityFinding.server_id.in_(ids), SecurityFinding.status == "open")
                .group_by(SecurityFinding.server_id)
            )
        ).all()
    )
    return {"dbs": dbs, "recs": recs, "sec": sec}


def to_out(s: Server, c: dict[str, dict[uuid.UUID, int]] | None = None) -> ServerOut:
    lm = s.latest_metrics or {}
    return ServerOut(
        id=s.id,
        name=s.name,
        description=s.description,
        engine=s.engine,
        engine_label=engine_label(s.engine),
        version=s.version,
        host=s.host,
        port=s.port,
        default_database=s.default_database,
        deployment=s.deployment,
        cloud_provider=s.cloud_provider,
        environment=EnvRef(id=s.environment.id, name=s.environment.name, tier=s.environment.tier)
        if s.environment
        else None,
        site=Ref(id=s.site.id, name=s.site.name) if s.site else None,
        cluster=Ref(id=s.cluster.id, name=s.cluster.name) if s.cluster else None,
        cluster_role=s.cluster_role,
        application=Ref(id=s.application.id, name=s.application.name) if s.application else None,
        owner=s.owner,
        business_service=s.business_service,
        tags=[Tag(key=t.key, value=t.value) for t in s.tags],
        tls_mode=s.tls_mode,
        monitoring_enabled=s.monitoring_enabled,
        collection_interval_s=s.collection_interval_s,
        licensed_features=list(s.licensed_features or []),
        is_demo=s.is_demo,
        data_source=s.data_source,
        health_status=s.health_status,
        health_reasons=list(s.health_reasons or []),
        connection_status=s.connection_status,
        last_error=s.last_error,
        last_connected_at=s.last_connected_at,
        last_collected_at=s.last_collected_at,
        circuit_open_until=s.circuit_open_until,
        key_metrics={m: lm.get(m) for m in DASHBOARD_METRICS},
        database_count=c["dbs"].get(s.id, 0) if c else None,
        open_recommendations=c["recs"].get(s.id, 0) if c else None,
        open_findings=c["sec"].get(s.id, 0) if c else None,
        implementation=implementation(s),
        management_mode=management_mode(s),
        connection_options=connection_options(s),
        created_at=s.created_at,
    )


async def _check_refs(db: AsyncSession, data: dict[str, Any]) -> None:
    for key, model in (
        ("environment_id", Environment),
        ("site_id", Site),
        ("cluster_id", Cluster),
        ("application_id", Application),
    ):
        if data.get(key) and await db.get(model, data[key]) is None:
            raise ValidationFailed(f"{key} does not exist", details={"field": key})


async def create_server(db: AsyncSession, body: ServerCreate, actor: str) -> Server:
    engine = normalize_engine(body.engine)
    if engine not in supported_engines():
        raise ValidationFailed(
            f"Unsupported engine {body.engine!r}", details={"supported": sorted(supported_engines())}
        )
    if (await db.execute(select(Server).where(Server.name == body.name))).scalar_one_or_none():
        raise Conflict("A server with this name already exists")
    data = body.model_dump(
        exclude={"monitoring_credential", "admin_credential", "tags", "engine", "management_mode", "connection_options"}
    )
    await _check_refs(db, data)
    org = (await db.execute(select(Organization).order_by(Organization.created_at).limit(1))).scalar_one()
    s = Server(organization_id=org.id, engine=engine, **data)
    s.options = {**_validated_options(engine, body.connection_options), "management_mode": body.management_mode}
    s.tags = [ServerTag(key=t.key, value=t.value) for t in body.tags]
    s.capabilities = {
        k.value: v.model_dump(mode="json") for k, v in adapter_class_for(engine).capability_profile().items()
    }
    db.add(s)
    await db.flush()
    for purpose, cred in (("monitoring", body.monitoring_credential), ("admin", body.admin_credential)):
        if cred:
            await secrets.set_credential(
                db,
                s,
                purpose,
                username=cred.username,
                password=cred.password,
                provider=cred.provider,
                secret_ref=cred.secret_ref,
                actor=actor,
            )
    await db.refresh(s)
    return s


async def update_server(db: AsyncSession, s: Server, body: ServerUpdate) -> dict[str, Any]:
    changes = body.model_dump(exclude_unset=True)
    tags = changes.pop("tags", None)
    mode = changes.pop("management_mode", None)
    opts = changes.pop("connection_options", None)
    await _check_refs(db, changes)
    options = dict(s.options or {})
    if opts is not None:
        keep = {k: v for k, v in options.items() if k not in connection_options(s)}
        options = {**keep, **_validated_options(s.engine, opts)}
        changes["connection_options"] = connection_options(Server(engine=s.engine, options=options))
    if mode is not None:
        options["management_mode"] = mode
        changes["management_mode"] = mode
    if opts is not None or mode is not None:
        s.options = options
    if (
        "name" in changes
        and changes["name"] != s.name
        and (await db.execute(select(Server).where(Server.name == changes["name"]))).scalar_one_or_none()
    ):
        raise Conflict("A server with this name already exists")
    for k, v in changes.items():
        if k not in ("connection_options", "management_mode"):
            setattr(s, k, v)
    if tags is not None:
        s.tags = [ServerTag(key=t["key"], value=t.get("value", "")) for t in tags]
        changes["tags"] = tags
    if any(k in changes for k in ("host", "port", "tls_mode", "tls_ca_pem", "default_database", "connection_options")):
        s.consecutive_failures, s.circuit_open_until = 0, None
    await db.flush()
    await db.refresh(s)
    return {k: v for k, v in changes.items() if k != "tls_ca_pem"}


async def test_connection_params(body: TestConnectionRequest) -> dict[str, Any]:
    engine = normalize_engine(body.engine)
    cls = adapter_class_for(engine)
    if not cls.implemented:
        return {
            "ok": False,
            "engine": engine,
            "error_code": "FEATURE_NOT_AVAILABLE",
            "message": f"{cls.display_name} adapter is scheduled for implementation phase "
            f"{cls.implementation_phase}; the server can be registered for inventory "
            "but live features are disabled.",
        }
    from app.adapters import drivers

    try:
        drivers.require_engine_driver(engine)
    except AdapterError as exc:
        return {"ok": False, "engine": engine, "error_code": exc.code.value, "message": str(exc)}
    tmp = Server(
        name="test",
        engine=engine,
        host=body.host,
        port=body.port,
        default_database=body.default_database,
        tls_mode=body.tls_mode,
        tls_ca_pem=body.tls_ca_pem,
        options=_validated_options(engine, body.connection_options),
        licensed_features=list(body.licensed_features or []),
        data_source=DataSource.REAL,
    )
    adapter = create_adapter(engine, build_spec(tmp, body.username, body.password))
    return await _probe(adapter)


async def test_connection_server(db: AsyncSession, s: Server) -> dict[str, Any]:
    if implementation(s) == "planned":
        cls = adapter_class_for(s.engine)
        return {
            "ok": False,
            "engine": s.engine,
            "error_code": "FEATURE_NOT_AVAILABLE",
            "message": f"{cls.display_name} adapter is scheduled for implementation phase "
            f"{cls.implementation_phase}; live connections are not available yet.",
        }
    from app.services.connections import adapter_for

    try:
        adapter = await adapter_for(db, s)
    except ValidationFailed as exc:
        return {"ok": False, "engine": s.engine, "error_code": "CONFIGURATION_REQUIRED", "message": exc.message}
    return await _probe(adapter)


async def _probe(adapter: Any) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        info = await adapter.test_connection()
        latency = round((time.perf_counter() - start) * 1000, 1)
        caps = await adapter.discover_capabilities()
        return {
            "ok": True,
            "engine": adapter.engine,
            "message": "Connected successfully",
            "version": info.version,
            "latency_ms": latency,
            "server_info": info.model_dump(mode="json"),
            "capabilities": {k.value: v.model_dump(mode="json") for k, v in caps.items()},
        }
    except AdapterError as exc:
        return {
            "ok": False,
            "engine": adapter.engine,
            "error_code": exc.code.value,
            "message": str(exc),
            "latency_ms": round((time.perf_counter() - start) * 1000, 1),
        }
    finally:
        await adapter.close()


def engines_catalog() -> list[dict[str, Any]]:
    from app.adapters import drivers

    status: dict[str, str] = {}
    detail: dict[str, dict[str, Any]] = {}
    for d in drivers.health():
        for e in d["engines"]:
            status[e] = d["status"]
            detail[e] = {"driver": d["driver"], "error": d["error"], "remediation": d["remediation"]}
    out = []
    for name, cls in supported_engines().items():
        prof = cls.engine_profile()
        impl = cls.capability_profile()
        out.append(
            {
                "engine": name,
                "display_name": cls.display_name,
                "default_port": cls.default_port,
                "driver": cls.driver,
                "implementation_phase": cls.implementation_phase,
                "implemented": cls.implemented,
                "support_level": cls.support_level if cls.implemented else "planned",
                "support_note": cls.support_note,
                "driver_status": status.get(name),
                "driver_detail": detail.get(name),
                "connection_options": cls.connection_options,
                "licensable_features": cls.licensable_features,
                "capabilities": {
                    c.value: {"engine": prof[c].model_dump(mode="json"), "adapter": impl[c].model_dump(mode="json")}
                    for c in prof
                },
            }
        )
    return out


async def get_server(db: AsyncSession, server_id: uuid.UUID) -> Server:
    s = await db.get(Server, server_id)
    if s is None:
        raise NotFound("Server not found")
    return s


def capability_usable(server: Server, cap: str) -> bool:
    entry = (server.capabilities or {}).get(cap)
    return bool(entry) and CapabilityLevel(entry.get("level")).usable
