"""SQL workspace: classification, guarded execution, history."""

from __future__ import annotations

import csv
import hashlib
import io
import uuid
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.adapters.base import Capability
from app.api.deps import DB, CurrentPrincipal, load_server, rate_limit
from app.core.config import get_settings
from app.core.csvsafe import csv_safe
from app.core.errors import ConfirmationRequired, PermissionDenied, ValidationFailed
from app.domain.masking import redact_secret_clauses
from app.domain.risk import RiskClass
from app.domain.sql_classifier import classify_sql
from app.models import QueryHistory, Server
from app.schemas.common import Page, PageParams, paginate
from app.services import app_settings, audit
from app.services.authz import server_ref
from app.services.connections import adapter_for, adapter_preferring, require_capability, target

router = APIRouter(prefix="/workspace", tags=["workspace"])


class ClassifyRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=200_000)
    server_id: uuid.UUID | None = None
    database: str | None = None


class ExecuteRequest(BaseModel):
    server_id: uuid.UUID
    database: str | None = None
    sql: str = Field(min_length=1, max_length=200_000)
    max_rows: int | None = Field(None, ge=1)
    timeout_seconds: float | None = Field(None, gt=0)
    confirm: str | None = None
    reason: str | None = Field(None, max_length=1000)


def _confirmation(s: Server, risk: RiskClass) -> str | None:
    """Expected confirmation token, or None when no confirmation is needed."""
    is_prod = bool(s.environment and s.environment.tier == "production")
    if risk is RiskClass.DESTRUCTIVE or (is_prod and risk is not RiskClass.READ):
        return s.name
    if risk is RiskClass.PRIVILEGED_CHANGE:
        return "CONFIRM"
    return None


@router.post("/classify")
async def classify(body: ClassifyRequest, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    engine = "postgresql"
    out: dict[str, Any] = {}
    if body.server_id:
        s = await load_server(db, p, body.server_id)
        engine = s.engine
        c = classify_sql(body.sql, engine)
        ref = server_ref(s, body.database)
        out["missing_permissions"] = [perm for perm in c.required_permissions if not p.authz.has(perm, ref)]
        exp = _confirmation(s, c.risk)
        out["confirmation"] = None if exp is None else ("server name" if exp == s.name else exp)
    else:
        c = classify_sql(body.sql, engine)
    return {**c.to_dict(), **out}


@router.post("/execute", dependencies=[rate_limit("expensive")])
async def execute(body: ExecuteRequest, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    settings = get_settings()
    s = await load_server(db, p, body.server_id)
    ref = server_ref(s, body.database)
    c = classify_sql(body.sql, s.engine)
    if c.statement_count == 0:
        raise ValidationFailed("Nothing to execute")
    if c.statement_count > 1:
        raise ValidationFailed(
            "Run one statement at a time; multiple statements are not allowed",
            details={"statement_count": c.statement_count},
        )
    if c.statement_type == "TRANSACTION":
        raise ValidationFailed("Transaction control statements are managed by the workspace")
    missing = [perm for perm in c.required_permissions if not p.authz.has(perm, ref)]
    digest = hashlib.sha256(body.sql.encode()).hexdigest()[:16]
    if missing:
        await audit.record(
            "workspace.execute",
            actor=p,
            server=s,
            database=body.database,
            result="denied",
            category="query",
            risk_class=c.risk.value,
            failure_reason=f"missing {', '.join(missing)}",
            details={"sha256": digest, "statement_type": c.statement_type},
        )
        raise PermissionDenied(
            f"Your role does not allow {c.statement_type} statements on this database ({c.risk.value})",
            details={"missing_permissions": missing, "classification": c.to_dict()},
        )
    require_capability(s, Capability.SQL_WORKSPACE)
    expected = _confirmation(s, c.risk)
    if expected is not None and body.confirm != expected:
        raise ConfirmationRequired(
            "This statement changes the database. Review it and confirm to continue.",
            details={
                "classification": c.to_dict(),
                "expected": "server name" if expected == s.name else expected,
                "production": bool(s.environment and s.environment.tier == "production"),
            },
        )
    is_prod = bool(s.environment and s.environment.tier == "production")
    if is_prod and not c.read_only and not await app_settings.get(db, "workspace.allow_writes_in_production"):
        raise PermissionDenied("Data-modifying statements on production servers are disabled by platform settings")
    if c.risk is RiskClass.DESTRUCTIVE and is_prod and not body.reason:
        raise ValidationFailed("A change reason is required for destructive statements in production")

    max_rows = min(body.max_rows or settings.workspace_default_rows, settings.workspace_max_rows)
    timeout = min(body.timeout_seconds or settings.workspace_timeout_seconds, settings.workspace_timeout_seconds * 4)
    read_only = c.read_only
    adapter = (
        (await adapter_preferring(db, s, "admin", "monitoring")) if read_only else (await adapter_for(db, s, "admin"))
    )
    history = QueryHistory(
        user_id=p.user.id,
        server_id=s.id,
        database=body.database,
        statement=redact_secret_clauses(body.sql)[:50_000],
        statement_type=c.statement_type,
        risk_class=c.risk.value,
        status="running",
    )
    action = "workspace.execute"
    try:
        async with target(adapter) as a:
            res = await a.execute_sql(
                body.sql, body.database, read_only=read_only, max_rows=max_rows, timeout_s=timeout
            )
    except Exception as exc:
        history.status, history.error = "error", str(exc)[:2000]
        db.add(history)
        await db.commit()
        await audit.record(
            action,
            actor=p,
            server=s,
            database=body.database,
            result="failure",
            category="query",
            risk_class=c.risk.value,
            failure_reason=str(exc)[:500],
            change_reason=body.reason,
            details={"sha256": digest, "statement_type": c.statement_type, "objects": c.objects},
        )
        raise
    history.status, history.duration_ms, history.row_count = "ok", res.duration_ms, res.row_count
    db.add(history)
    await audit.record(
        action,
        actor=p,
        server=s,
        database=body.database,
        category="query",
        risk_class=c.risk.value,
        change_reason=body.reason,
        details={
            "sha256": digest,
            "statement_type": c.statement_type,
            "objects": c.objects,
            "rows": res.row_count,
            "committed": res.committed,
            "duration_ms": res.duration_ms,
        },
    )
    return {**res.model_dump(mode="json"), "classification": c.to_dict(), "max_rows": max_rows, "simulated": s.is_demo}


@router.post(
    "/export.csv",
    summary="Execute a read-only statement and download the result as CSV",
    dependencies=[rate_limit("expensive")],
)
async def export_csv(body: ExecuteRequest, db: DB, p: CurrentPrincipal) -> StreamingResponse:
    s = await load_server(db, p, body.server_id)
    c = classify_sql(body.sql, s.engine)
    if not c.read_only or c.statement_count != 1:
        raise ValidationFailed("Only single read-only statements can be exported")
    body.confirm = None
    result = await execute(body, db, p)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([col["name"] for col in result["columns"]])
    for row in result["rows"]:
        w.writerow([csv_safe(v) for v in row])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="query-result.csv"'},
    )


@router.get("/history")
async def history(
    db: DB, p: CurrentPrincipal, params: PageParams = Depends(), server_id: uuid.UUID | None = None
) -> Page[dict[str, Any]]:
    stmt = (
        select(QueryHistory, Server.name)
        .join(Server, Server.id == QueryHistory.server_id)
        .where(QueryHistory.user_id == p.user.id)
        .order_by(QueryHistory.created_at.desc())
    )
    if server_id:
        stmt = stmt.where(QueryHistory.server_id == server_id)
    total_stmt = select(QueryHistory).where(QueryHistory.user_id == p.user.id)
    if server_id:
        total_stmt = total_stmt.where(QueryHistory.server_id == server_id)
    _, total = await paginate(db, total_stmt, 1, 1)
    rows = (await db.execute(stmt.limit(params.page_size).offset((params.page - 1) * params.page_size))).all()
    return Page(
        items=[
            {
                "id": str(h.id),
                "server_id": str(h.server_id),
                "server_name": name,
                "database": h.database,
                "statement": h.statement,
                "statement_type": h.statement_type,
                "risk_class": h.risk_class,
                "status": h.status,
                "duration_ms": h.duration_ms,
                "row_count": h.row_count,
                "error": h.error,
                "created_at": h.created_at,
            }
            for h, name in rows
        ],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )
