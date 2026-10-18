"""Database onboarding: staged (streamed) connection tests and bulk registration."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.adapters.base import BaseDatabaseAdapter
from app.adapters.registry import create_adapter, normalize_engine
from app.api.deps import DB, CurrentPrincipal, load_server, rate_limit
from app.core.errors import ValidationFailed
from app.models import Server
from app.schemas.inventory import TestConnectionRequest
from app.services import audit, inventory, onboarding
from app.services.authz import server_ref
from app.services.connections import adapter_for, build_spec

router = APIRouter(tags=["onboarding"])
NDJSON = "application/x-ndjson"


async def _stream(events: AsyncIterator[dict[str, Any]], on_result: Any) -> AsyncIterator[bytes]:
    async for e in events:
        if e["stage"] == "result":
            await on_result(e["result"])
        yield (json.dumps(e, default=str) + "\n").encode()


def _timeout(opts: dict[str, Any]) -> float:
    try:
        return float(opts.get("connect_timeout") or 8)
    except (TypeError, ValueError):
        return 8.0


@router.post(
    "/servers/test-connection/stream",
    summary="Staged connection test (NDJSON stream)",
    dependencies=[rate_limit("expensive")],
)
async def stream_new_connection(body: TestConnectionRequest, p: CurrentPrincipal) -> StreamingResponse:
    p.authz.require_anywhere("server.register")
    validation_error: str | None = None
    adapter: BaseDatabaseAdapter | None = None
    try:
        engine = normalize_engine(body.engine)
    except ValueError:
        engine, validation_error = body.engine, f"unsupported engine {body.engine!r}"
    if validation_error is None:
        try:
            tmp = Server(
                name="test",
                engine=engine,
                host=body.host,
                port=body.port,
                default_database=body.default_database,
                tls_mode=body.tls_mode,
                tls_ca_pem=body.tls_ca_pem,
                options=inventory._validated_options(engine, body.connection_options),
                licensed_features=list(body.licensed_features or []),
            )
            spec = build_spec(tmp, body.username, body.password)
        except (ValidationFailed, ValueError) as exc:
            validation_error = getattr(exc, "message", None) or str(exc)

    async def make() -> BaseDatabaseAdapter:
        nonlocal adapter
        adapter = create_adapter(engine, spec)
        return adapter

    async def on_result(res: dict[str, Any]) -> None:
        await audit.record(
            "server.test_connection",
            actor=p,
            result="success" if res["ok"] else "failure",
            failure_reason=None if res["ok"] else res["message"],
            details={"engine": body.engine, "host": body.host, "port": body.port, "staged": True},
        )

    events = onboarding.staged_test(
        engine=engine,
        host=body.host,
        port=body.port,
        tls_mode=body.tls_mode,
        make_adapter=make,
        validation_error=validation_error,
        connect_timeout=_timeout(body.connection_options),
    )
    return StreamingResponse(_stream(events, on_result), media_type=NDJSON, headers={"Cache-Control": "no-store"})


@router.post(
    "/servers/{server_id}/test-connection/stream",
    summary="Staged connection test of a registered server",
    dependencies=[rate_limit("expensive")],
)
async def stream_server_connection(server_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> StreamingResponse:
    s = await load_server(db, p, server_id)
    if not (p.authz.has("server.modify", server_ref(s)) or p.authz.has("collectors.manage", server_ref(s))):
        p.authz.require("server.modify", server_ref(s))
    validation_error: str | None = None
    adapter: BaseDatabaseAdapter | None = None
    impl = inventory.implementation(s)
    if impl == "planned":
        validation_error = "no adapter is available for this engine yet"
    else:
        try:
            adapter = await adapter_for(db, s)  # resolves the stored credential now (no I/O)
        except ValidationFailed as exc:
            validation_error = exc.message
    sid, name, engine, host, port, tls = s.id, s.name, s.engine, s.host, s.port, s.tls_mode
    simulated = impl == "simulated"
    timeout = _timeout(s.options or {})

    async def make() -> BaseDatabaseAdapter:
        assert adapter is not None
        return adapter

    async def on_result(res: dict[str, Any]) -> None:
        from app.db.session import session_scope
        from app.services.collection import mark_failure, mark_success

        async with session_scope() as db2:
            srv = await db2.get(Server, sid)
            if srv is not None and not simulated:
                if res["ok"]:
                    mark_success(srv)
                    srv.version = res.get("version") or srv.version
                    if res.get("capabilities"):
                        srv.capabilities = res["capabilities"]
                elif impl != "planned":
                    mark_failure(srv, res["message"])
        await audit.record(
            "server.test_connection",
            actor=p,
            target_type="server",
            target_id=str(sid),
            object_name=name,
            result="success" if res["ok"] else "failure",
            failure_reason=None if res["ok"] else res["message"],
            details={"staged": True},
        )

    events = onboarding.staged_test(
        engine=engine,
        host=host,
        port=port,
        tls_mode=tls,
        make_adapter=make,
        validation_error=validation_error,
        simulated=simulated,
        connect_timeout=timeout,
    )
    return StreamingResponse(_stream(events, on_result), media_type=NDJSON, headers={"Cache-Control": "no-store"})


# ---- bulk -------------------------------------------------------------------------------
class BulkRows(BaseModel):
    rows: list[dict[str, Any]] = Field(max_length=2000)


class BulkCreate(BulkRows):
    monitoring_enabled: bool = True


def _require_register(p: CurrentPrincipal) -> None:
    p.authz.require_anywhere("server.register")


@router.get("/servers/bulk/template.csv", summary="CSV template for bulk onboarding (no password column)")
async def bulk_template(p: CurrentPrincipal) -> Response:
    _require_register(p)
    return Response(
        onboarding.template_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="dbmanager-servers-template.csv"'},
    )


@router.post(
    "/servers/bulk/validate", summary="Validate rows for bulk onboarding", dependencies=[rate_limit("expensive")]
)
async def bulk_validate(body: BulkRows, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    _require_register(p)
    return await onboarding.validate_rows(db, body.rows)


@router.post(
    "/servers/bulk/create",
    summary="Register the valid rows (Monitoring Only unless stated)",
    dependencies=[rate_limit("expensive")],
)
async def bulk_create(body: BulkCreate, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    _require_register(p)
    if not p.authz.has("server.register"):
        # environment-scoped registrars: every row must target an environment they may register into
        from app.domain.scopes import ResourceRef

        checked = await onboarding.validate_rows(db, body.rows)
        for r in checked["rows"]:
            env = (r["normalized"].get("environment") or {}).get("id")
            if r["ok"] and not p.authz.has("server.register", ResourceRef(server_id="*", environment_id=env)):
                p.authz.require("server.register", ResourceRef(server_id="*", environment_id=env))
    if any((r or {}).get("credential") for r in body.rows):
        p.authz.require_anywhere("credentials.manage")
    results, created = await onboarding.create_rows(
        db, body.rows, monitoring_enabled=body.monitoring_enabled, actor=p.username
    )
    from app.jobs.runner import enqueue

    for s in created:
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
                "bulk": True,
                "management_mode": (s.options or {}).get("management_mode"),
            },
        )
        if s.monitoring_enabled and inventory.implementation(s) == "implemented":
            for kind in ("discover_inventory", "collect_metrics"):
                await enqueue(s.id, kind, "manual", db=db)
    n_ok = sum(1 for r in results if r["ok"])
    await audit.record(
        "server.bulk_register",
        actor=p,
        category="admin",
        risk_class="LOW_RISK_CHANGE",
        result="success" if n_ok == len(results) else "failure",
        details={"rows": len(results), "created": n_ok, "failed": len(results) - n_ok},
    )
    return {"results": results, "created": n_ok, "failed": len(results) - n_ok}
