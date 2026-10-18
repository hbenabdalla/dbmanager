"""Report generation.

Reports are defined as data producers returning (columns, rows). Renderers turn them into a
file format; CSV ships now, and PDF/XLSX renderers can be added by implementing
:class:`Renderer` without touching report definitions.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent, DbAccount, Recommendation, SecurityFinding, Server
from app.services.authz import Authorizer
from app.services.inventory import engine_label

Rows = tuple[list[str], list[list[Any]]]


class Renderer(Protocol):
    media_type: str
    extension: str

    def render(self, title: str, columns: list[str], rows: list[list[Any]]) -> bytes: ...


class CsvRenderer:
    media_type = "text/csv"
    extension = "csv"

    def render(self, title: str, columns: list[str], rows: list[list[Any]]) -> bytes:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(columns)
        for r in rows:
            w.writerow(["" if v is None else (v.isoformat() if isinstance(v, datetime) else v) for v in r])
        return buf.getvalue().encode()


RENDERERS: dict[str, Renderer] = {"csv": CsvRenderer()}


@dataclass
class ReportDef:
    key: str
    title: str
    description: str
    permission: str
    producer: Callable[[AsyncSession, Authorizer], Awaitable[Rows]]


async def _servers(db: AsyncSession, az: Authorizer, perm: str) -> list[Server]:
    stmt = select(Server).order_by(Server.name)
    flt = az.server_filter(perm)
    if flt is not None:
        stmt = stmt.where(flt)
    return list((await db.execute(stmt)).scalars().all())


def _env(s: Server) -> str | None:
    return s.environment.name if s.environment else None


async def fleet_health(db: AsyncSession, az: Authorizer) -> Rows:
    cols = [
        "server",
        "engine",
        "version",
        "environment",
        "health",
        "reasons",
        "connection",
        "last_collected_at",
        "simulated",
    ]
    return cols, [
        [
            s.name,
            engine_label(s.engine),
            s.version,
            _env(s),
            s.health_status,
            "; ".join(s.health_reasons),
            s.connection_status,
            s.last_collected_at,
            s.is_demo,
        ]
        for s in await _servers(db, az, "server.view")
    ]


async def inventory(db: AsyncSession, az: Authorizer) -> Rows:
    cols = [
        "server",
        "engine",
        "version",
        "host",
        "port",
        "environment",
        "site",
        "cluster",
        "cluster_role",
        "application",
        "business_service",
        "owner",
        "deployment",
        "tls_mode",
        "tags",
        "simulated",
    ]
    return cols, [
        [
            s.name,
            engine_label(s.engine),
            s.version,
            s.host,
            s.port,
            _env(s),
            s.site.name if s.site else None,
            s.cluster.name if s.cluster else None,
            s.cluster_role,
            s.application.name if s.application else None,
            s.business_service,
            s.owner,
            s.deployment,
            s.tls_mode,
            ", ".join(f"{t.key}={t.value}" for t in s.tags),
            s.is_demo,
        ]
        for s in await _servers(db, az, "server.view")
    ]


async def capacity(db: AsyncSession, az: Authorizer) -> Rows:
    """Storage size, 30-day growth trend and days until full (only where the stored history
    supports a forecast; see app.services.metrics.capacity)."""
    from app.services import metrics as metrics_svc

    servers = await _servers(db, az, "performance.view")
    cols = [
        "server",
        "engine",
        "environment",
        "size_bytes",
        "capacity_bytes",
        "growth_bytes_per_day",
        "days_until_full",
        "note",
    ]
    trend = {r["server_id"]: r for r in await metrics_svc.capacity([s.id for s in servers], 30)} if servers else {}
    rows = []
    for s in servers:
        t = trend.get(str(s.id), {})
        now_b = (s.latest_metrics or {}).get("storage.database_bytes")
        rows.append(
            [
                s.name,
                engine_label(s.engine),
                _env(s),
                now_b,
                t.get("capacity_bytes") or (s.latest_metrics or {}).get("storage.capacity_bytes"),
                t.get("growth_bytes_per_day"),
                t.get("days_until_full"),
                t.get("reason") or ("" if t else "no storage history"),
            ]
        )
    return cols, rows


async def performance(db: AsyncSession, az: Authorizer) -> Rows:
    keys = [
        "connections.total",
        "connections.utilization",
        "transactions.per_sec",
        "cache.hit_ratio",
        "query.mean_latency_ms",
        "sessions.blocked",
        "replication.lag_seconds",
        "cpu.percent",
    ]
    cols = ["server", "engine", "environment", "health", *keys]
    return cols, [
        [s.name, engine_label(s.engine), _env(s), s.health_status, *[(s.latest_metrics or {}).get(k) for k in keys]]
        for s in await _servers(db, az, "performance.view")
    ]


async def slow_queries(db: AsyncSession, az: Authorizer) -> Rows:
    """Statements with a mean latency of 100 ms or more during the last 24 hours."""
    from app.domain.masking import mask_sql
    from app.models import QueryFingerprint
    from app.services import metrics as metrics_svc

    servers = {str(s.id): s for s in await _servers(db, az, "query.view")}
    cols = [
        "server",
        "database",
        "fingerprint",
        "calls_24h",
        "mean_ms",
        "total_ms_24h",
        "physical_reads_24h",
        "query (masked)",
    ]
    if not servers:
        return cols, []
    items = await metrics_svc.top_queries(list(servers), window_s=86400, sort="mean_ms", limit=500)
    items = [i for i in items if (i["mean_ms"] or 0) >= 100]
    texts = (
        {
            (str(r.server_id), r.fingerprint, r.database): r.normalized_query
            for r in (
                await db.execute(
                    select(QueryFingerprint).where(QueryFingerprint.fingerprint.in_({i["fingerprint"] for i in items}))
                )
            ).scalars()
        }
        if items
        else {}
    )
    return cols, [
        [
            servers[i["server_id"]].name,
            i["database"],
            i["fingerprint"],
            round(i["calls"]),
            i["mean_ms"],
            round(i["total_ms"]),
            i["physical_reads"],
            mask_sql(texts.get((i["server_id"], i["fingerprint"], i["database"] or ""), ""))[:500],
        ]
        for i in items
        if i["server_id"] in servers
    ]


async def _findings(db: AsyncSession, az: Authorizer, model: Any, perm: str) -> list[tuple[Any, Server]]:
    servers = {s.id: s for s in await _servers(db, az, perm)}
    if not servers:
        return []
    rows = (
        (
            await db.execute(
                select(model).where(model.server_id.in_(list(servers)), model.status.in_(["open", "acknowledged"]))
            )
        )
        .scalars()
        .all()
    )
    return [(r, servers[r.server_id]) for r in rows]


async def security_posture(db: AsyncSession, az: Authorizer) -> Rows:
    cols = [
        "server",
        "environment",
        "rule_id",
        "severity",
        "category",
        "title",
        "affected_object",
        "status",
        "recommendation",
        "first_seen_at",
    ]
    return cols, [
        [
            s.name,
            _env(s),
            r.rule_id,
            r.severity,
            r.category,
            r.title,
            r.affected_object,
            r.status,
            r.recommendation,
            r.first_seen_at,
        ]
        for r, s in await _findings(db, az, SecurityFinding, "security.view")
    ]


async def recommendations(db: AsyncSession, az: Authorizer) -> Rows:
    cols = [
        "server",
        "environment",
        "rule_id",
        "severity",
        "category",
        "title",
        "finding",
        "recommendation",
        "expected_impact",
        "risk",
        "confidence",
        "status",
    ]
    return cols, [
        [
            s.name,
            _env(s),
            r.rule_id,
            r.severity,
            r.category,
            r.title,
            r.finding,
            r.recommendation,
            r.expected_impact,
            r.risk,
            r.confidence,
            r.status,
        ]
        for r, s in await _findings(db, az, Recommendation, "performance.view")
    ]


async def privileged_users(db: AsyncSession, az: Authorizer) -> Rows:
    servers = {s.id: s for s in await _servers(db, az, "user.view")}
    cols = ["server", "environment", "account", "kind", "can_login", "admin_reasons", "member_of", "last_login_at"]
    if not servers:
        return cols, []
    rows = (
        (
            await db.execute(
                select(DbAccount).where(DbAccount.server_id.in_(list(servers)), DbAccount.is_admin.is_(True))
            )
        )
        .scalars()
        .all()
    )
    return cols, [
        [
            servers[a.server_id].name,
            _env(servers[a.server_id]),
            a.name,
            a.kind,
            a.can_login,
            "; ".join(a.admin_reasons),
            "; ".join(a.member_of),
            a.last_login_at,
        ]
        for a in rows
    ]


async def audit_activity(db: AsyncSession, az: Authorizer) -> Rows:
    cols = ["day", "action", "result", "events", "users"]
    since = datetime.now(UTC) - timedelta(days=30)
    day = func.date_trunc("day", AuditEvent.ts)
    rows = (
        await db.execute(
            select(
                day, AuditEvent.action, AuditEvent.result, func.count(), func.count(func.distinct(AuditEvent.username))
            )
            .where(AuditEvent.ts >= since)
            .group_by(day, AuditEvent.action, AuditEvent.result)
            .order_by(day.desc())
        )
    ).all()
    return cols, [[d.date().isoformat(), a, r, n, u] for d, a, r, n, u in rows]


REPORTS: dict[str, ReportDef] = {
    r.key: r
    for r in [
        ReportDef(
            "fleet-health", "Fleet health", "Health status and reasons for every server", "server.view", fleet_health
        ),
        ReportDef(
            "inventory",
            "Database inventory",
            "Registered servers with ownership and placement",
            "server.view",
            inventory,
        ),
        ReportDef(
            "capacity", "Capacity", "Database size, 30-day growth and days until full", "performance.view", capacity
        ),
        ReportDef(
            "performance",
            "Performance snapshot",
            "Latest key performance metrics per server",
            "performance.view",
            performance,
        ),
        ReportDef(
            "slow-queries", "Slow queries", "Statements averaging over 100 ms (masked text)", "query.view", slow_queries
        ),
        ReportDef("security-posture", "Security posture", "Open security findings", "security.view", security_posture),
        ReportDef(
            "privileged-users", "Privileged users", "Administrative database accounts", "user.view", privileged_users
        ),
        ReportDef(
            "recommendations",
            "Recommendations",
            "Open performance recommendations",
            "performance.view",
            recommendations,
        ),
        ReportDef(
            "audit-activity", "Audit activity", "Daily audit event counts (30 days)", "audit.view", audit_activity
        ),
    ]
}

PLANNED_FORMATS = ["pdf", "xlsx"]
