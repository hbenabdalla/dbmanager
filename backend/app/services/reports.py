"""Report generation.

Reports are data producers returning (columns, rows) for the servers the caller (or the owner of
a schedule) may see, narrowed by optional :class:`ReportParams`. Renderers turn them into a file
format: CSV and PDF (:mod:`app.services.report_pdf`). Scheduled delivery is in
:mod:`app.services.report_schedules`.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.csvsafe import csv_safe
from app.models import AuditEvent, DbAccount, Recommendation, SecurityFinding, Server
from app.services.authz import Authorizer
from app.services.inventory import engine_label

Rows = tuple[list[str], list[list[Any]]]

SEVERITIES = ("critical", "high", "medium", "low", "info")
FINDING_STATUSES = ("open", "acknowledged", "dismissed", "resolved")


class ReportParams(BaseModel):
    """Optional narrowing of a report. Every report still only covers the servers the caller's
    role allows; parameters can only reduce that set."""

    environment_id: uuid.UUID | None = None
    server_id: uuid.UUID | None = None
    engine: str | None = Field(None, max_length=32)
    #: activity window of query reports (hours)
    window_hours: int = Field(24, ge=1, le=24 * 31)
    #: findings reports: statuses to include (default: open and acknowledged)
    statuses: list[str] | None = None
    #: findings reports: lowest severity to include
    min_severity: str | None = None
    #: capacity forecast: server | database | tablespace
    scope: str = "server"


class Renderer(Protocol):
    media_type: str
    extension: str

    def render(self, report: RenderedReport) -> bytes: ...


@dataclass
class RenderedReport:
    """What a renderer needs: the data plus the context printed on the document."""

    key: str
    title: str
    description: str
    columns: list[str]
    rows: list[list[Any]]
    generated_at: datetime
    generated_for: str
    scope: list[str] = field(default_factory=list)
    version: str = ""


def cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, float):
        return f"{v:.4g}" if abs(v) < 1e6 else f"{v:.0f}"
    return str(v)


class CsvRenderer:
    media_type = "text/csv"
    extension = "csv"

    def render(self, report: RenderedReport) -> bytes:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(report.columns)
        for r in report.rows:
            w.writerow([csv_safe(v) for v in r])
        return buf.getvalue().encode()


def renderers() -> dict[str, Renderer]:
    from app.services.report_pdf import PdfRenderer

    return {"csv": CsvRenderer(), "pdf": PdfRenderer()}


@dataclass
class ReportDef:
    key: str
    title: str
    description: str
    permission: str
    producer: Callable[[AsyncSession, Authorizer, ReportParams], Awaitable[Rows]]
    #: parameters this report understands (besides the server scope)
    params: tuple[str, ...] = ()
    #: platform-wide permission (not narrowed per server)
    platform: bool = False


async def _servers(db: AsyncSession, az: Authorizer, perm: str, params: ReportParams | None = None) -> list[Server]:
    stmt = select(Server).order_by(Server.name)
    flt = az.server_filter(perm)
    if flt is not None:
        stmt = stmt.where(flt)
    if params is not None:
        if params.environment_id:
            stmt = stmt.where(Server.environment_id == params.environment_id)
        if params.server_id:
            stmt = stmt.where(Server.id == params.server_id)
        if params.engine:
            stmt = stmt.where(Server.engine == params.engine)
    return list((await db.execute(stmt)).scalars().all())


def _env(s: Server) -> str | None:
    return s.environment.name if s.environment else None


async def fleet_health(db: AsyncSession, az: Authorizer, params: ReportParams) -> Rows:
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
        for s in await _servers(db, az, "server.view", params)
    ]


async def inventory(db: AsyncSession, az: Authorizer, params: ReportParams) -> Rows:
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
        for s in await _servers(db, az, "server.view", params)
    ]


async def capacity(db: AsyncSession, az: Authorizer, params: ReportParams) -> Rows:
    """Storage size, 30-day growth trend and days until full (only where the stored history
    supports a forecast; see app.services.metrics.capacity)."""
    from app.services import metrics as metrics_svc

    servers = await _servers(db, az, "performance.view", params)
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


async def performance(db: AsyncSession, az: Authorizer, params: ReportParams) -> Rows:
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
        for s in await _servers(db, az, "performance.view", params)
    ]


async def slow_queries(db: AsyncSession, az: Authorizer, params: ReportParams) -> Rows:
    """Statements with a mean latency of 100 ms or more during the last 24 hours."""
    from app.domain.masking import mask_sql
    from app.models import QueryFingerprint
    from app.services import metrics as metrics_svc

    servers = {str(s.id): s for s in await _servers(db, az, "query.view", params)}
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


async def _findings(
    db: AsyncSession, az: Authorizer, model: Any, perm: str, params: ReportParams
) -> list[tuple[Any, Server]]:
    servers = {s.id: s for s in await _servers(db, az, perm, params)}
    if not servers:
        return []
    statuses = [x for x in (params.statuses or ["open", "acknowledged"]) if x in FINDING_STATUSES]
    stmt = select(model).where(model.server_id.in_(list(servers)), model.status.in_(statuses))
    if params.min_severity in SEVERITIES:
        stmt = stmt.where(model.severity.in_(SEVERITIES[: SEVERITIES.index(params.min_severity) + 1]))
    rows = (await db.execute(stmt)).scalars().all()
    order = {sv: i for i, sv in enumerate(SEVERITIES)}
    out = [(r, servers[r.server_id]) for r in rows]
    out.sort(key=lambda x: (order.get(x[0].severity, 9), x[1].name, x[0].rule_id))
    return out


async def security_posture(db: AsyncSession, az: Authorizer, params: ReportParams) -> Rows:
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
        for r, s in await _findings(db, az, SecurityFinding, "security.view", params)
    ]


async def recommendations(db: AsyncSession, az: Authorizer, params: ReportParams) -> Rows:
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
        for r, s in await _findings(db, az, Recommendation, "performance.view", params)
    ]


async def privileged_users(db: AsyncSession, az: Authorizer, params: ReportParams) -> Rows:
    servers = {s.id: s for s in await _servers(db, az, "user.view", params)}
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


async def audit_activity(db: AsyncSession, az: Authorizer, params: ReportParams) -> Rows:
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


async def security_findings(db: AsyncSession, az: Authorizer, params: ReportParams) -> Rows:
    """Security findings in the chosen statuses (default open and acknowledged), most severe first,
    with their lifecycle and remediation."""
    cols = [
        "server",
        "engine",
        "environment",
        "severity",
        "rule_id",
        "category",
        "title",
        "affected_object",
        "database",
        "status",
        "status_reason",
        "first_seen_at",
        "last_seen_at",
        "resolved_at",
        "remediation",
    ]
    return cols, [
        [
            s.name,
            engine_label(s.engine),
            _env(s),
            r.severity,
            r.rule_id,
            r.category,
            r.title,
            r.affected_object,
            r.database,
            r.status,
            r.status_reason,
            r.first_seen_at,
            r.last_seen_at,
            r.resolved_at,
            r.remediation or r.guidance,
        ]
        for r, s in await _findings(db, az, SecurityFinding, "security.view", params)
    ]


async def _query_texts(db: AsyncSession, items: list[dict[str, Any]]) -> dict[tuple[str, str, str], str]:
    from app.models import QueryFingerprint

    if not items:
        return {}
    rows = (
        await db.execute(
            select(QueryFingerprint).where(
                QueryFingerprint.server_id.in_({uuid.UUID(i["server_id"]) for i in items}),
                QueryFingerprint.fingerprint.in_({i["fingerprint"] for i in items}),
            )
        )
    ).scalars()
    return {(str(r.server_id), r.fingerprint, r.database): r.normalized_query for r in rows}


async def query_statistics(db: AsyncSession, az: Authorizer, params: ReportParams) -> Rows:
    """Top statements by total execution time in the window, with their share of the measured
    time per server. Statement text is always masked (literals removed) in files."""
    from app.domain.masking import mask_sql
    from app.services import metrics as metrics_svc

    servers = {str(s.id): s for s in await _servers(db, az, "query.view", params)}
    cols = [
        "server",
        "engine",
        "database",
        "fingerprint",
        "calls",
        "total_ms",
        "mean_ms",
        "max_ms",
        "share_pct",
        "rows",
        "cpu_ms",
        "logical_reads",
        "physical_reads",
        "query (masked)",
    ]
    if not servers:
        return cols, []
    window = params.window_hours * 3600
    items = await metrics_svc.top_queries(list(servers), window_s=window, sort="total_ms", limit=500)
    per_server: dict[str, float] = {}
    for i in items:
        per_server[i["server_id"]] = per_server.get(i["server_id"], 0.0) + (i["total_ms"] or 0.0)
    texts = await _query_texts(db, items)
    return cols, [
        [
            servers[i["server_id"]].name,
            engine_label(servers[i["server_id"]].engine),
            i["database"],
            i["fingerprint"],
            round(i["calls"]),
            round(i["total_ms"] or 0),
            i["mean_ms"],
            i["max_ms"],
            round(100 * (i["total_ms"] or 0) / per_server[i["server_id"]], 1)
            if per_server.get(i["server_id"])
            else None,
            i["rows"],
            i["cpu_ms"],
            i["logical_reads"],
            i["physical_reads"],
            mask_sql(texts.get((i["server_id"], i["fingerprint"], i["database"] or ""), ""))[:500],
        ]
        for i in items
        if i["server_id"] in servers
    ]


async def query_regressions(db: AsyncSession, az: Authorizer, params: ReportParams) -> Rows:
    """Statements slower than their own baseline (thresholds from the platform settings), with
    plan changes where the engine exposes plan identity."""
    from app.domain.masking import mask_sql
    from app.services import advisor

    servers = await _servers(db, az, "query.view", params)
    cols = [
        "server",
        "engine",
        "database",
        "fingerprint",
        "kind",
        "baseline_mean_ms",
        "recent_mean_ms",
        "change_pct",
        "recent_calls",
        "baseline_calls",
        "plan_changed",
        "baseline_plan",
        "recent_plan",
        "query (masked)",
    ]
    settings = await advisor.perf_settings(db)
    found: list[tuple[Server, dict[str, Any]]] = []
    for s in servers:
        for r in await advisor.regressions_for(s.id, settings):
            r["server_id"] = str(s.id)
            found.append((s, r))
    texts = await _query_texts(db, [r for _s, r in found])
    return cols, [
        [
            s.name,
            engine_label(s.engine),
            r["database"],
            r["fingerprint"],
            r["kind"],
            r["baseline_mean_ms"],
            r["recent_mean_ms"],
            r["change_pct"],
            r["recent_calls"],
            r["baseline_calls"],
            r["plan_changed"],
            r["baseline_plan_id"] or r["baseline_plans"],
            r["recent_plan_id"] or r["recent_plans"],
            mask_sql(texts.get((str(s.id), r["fingerprint"], r["database"] or ""), ""))[:500],
        ]
        for s, r in found
    ]


async def capacity_forecast(db: AsyncSession, az: Authorizer, params: ReportParams) -> Rows:
    """Robust storage forecasts (Theil–Sen, 90 % band) per server, database or tablespace."""
    from app.services import perf_intel

    servers = {str(s.id): s for s in await _servers(db, az, "performance.view", params)}
    scope = params.scope if params.scope in ("server", "database", "tablespace") else "server"
    cols = [
        "server",
        "engine",
        "environment",
        "scope",
        "object",
        "current_bytes",
        "capacity_bytes",
        "growth_bytes_per_day",
        "growth_low",
        "growth_high",
        "days_to_full_earliest",
        "days_to_full_likely",
        "days_to_full_latest",
        "history_days",
        "note",
    ]
    if not servers:
        return cols, []
    items = await perf_intel.capacity_forecast(list(servers), scope)  # type: ignore[arg-type]
    out = []
    for f in items:
        s = servers.get(f["server_id"])
        if s is None:
            continue
        d = f.get("days_to_full") or {}
        out.append(
            [
                s.name,
                engine_label(s.engine),
                _env(s),
                f["scope"],
                f.get("object"),
                f.get("current_bytes"),
                f.get("capacity_bytes"),
                f.get("growth_bytes_per_day"),
                f.get("growth_low"),
                f.get("growth_high"),
                d.get("earliest"),
                d.get("likely"),
                d.get("latest"),
                f.get("history_days"),
                f.get("reason") or "",
            ]
        )
    return cols, out


SCOPE_PARAMS = ("environment_id", "server_id", "engine")
FINDING_PARAMS = (*SCOPE_PARAMS, "statuses", "min_severity")

REPORTS: dict[str, ReportDef] = {
    r.key: r
    for r in [
        ReportDef(
            "fleet-health",
            "Fleet health",
            "Health status and reasons for every server",
            "server.view",
            fleet_health,
            SCOPE_PARAMS,
        ),
        ReportDef(
            "inventory",
            "Database inventory",
            "Registered servers with ownership and placement",
            "server.view",
            inventory,
            SCOPE_PARAMS,
        ),
        ReportDef(
            "capacity",
            "Capacity",
            "Database size, 30-day growth and days until full",
            "performance.view",
            capacity,
            SCOPE_PARAMS,
        ),
        ReportDef(
            "capacity-forecast",
            "Capacity forecast",
            "Robust growth trend with a 90 % band and days to full per server, database or tablespace",
            "performance.view",
            capacity_forecast,
            (*SCOPE_PARAMS, "scope"),
        ),
        ReportDef(
            "performance",
            "Performance snapshot",
            "Latest key performance metrics per server",
            "performance.view",
            performance,
            SCOPE_PARAMS,
        ),
        ReportDef(
            "query-statistics",
            "Query statistics",
            "Top statements by total execution time in the window, with their share per server (masked text)",
            "query.view",
            query_statistics,
            (*SCOPE_PARAMS, "window_hours"),
        ),
        ReportDef(
            "slow-queries",
            "Slow queries",
            "Statements averaging over 100 ms in the last 24 hours (masked text)",
            "query.view",
            slow_queries,
            SCOPE_PARAMS,
        ),
        ReportDef(
            "query-regressions",
            "Query regressions",
            "Statements slower than their own baseline, with plan changes where detectable (masked text)",
            "query.view",
            query_regressions,
            SCOPE_PARAMS,
        ),
        ReportDef(
            "security-findings",
            "Security findings",
            "Security findings by status and severity, with lifecycle dates and remediation",
            "security.view",
            security_findings,
            FINDING_PARAMS,
        ),
        ReportDef(
            "security-posture",
            "Security posture",
            "Open security findings",
            "security.view",
            security_posture,
            SCOPE_PARAMS,
        ),
        ReportDef(
            "privileged-users",
            "Privileged users",
            "Administrative database accounts",
            "user.view",
            privileged_users,
            SCOPE_PARAMS,
        ),
        ReportDef(
            "recommendations",
            "Recommendations",
            "Performance recommendations by status and severity",
            "performance.view",
            recommendations,
            FINDING_PARAMS,
        ),
        ReportDef(
            "audit-activity",
            "Audit activity",
            "Daily audit event counts (30 days)",
            "audit.view",
            audit_activity,
            (),
            platform=True,
        ),
    ]
}


def allowed(az: Authorizer, report: ReportDef) -> bool:
    if not az.has_anywhere("reports.view"):
        return False
    return az.has(report.permission) if report.platform else az.has_anywhere(report.permission)


async def _describe_scope(db: AsyncSession, params: ReportParams) -> list[str]:
    """The scope with names rather than ids (printed on documents and e-mails)."""
    from app.models import Environment, Server

    out = []
    if params.environment_id:
        env = await db.get(Environment, params.environment_id)
        out.append(f"environment {env.name if env else params.environment_id}")
    if params.server_id:
        srv = await db.get(Server, params.server_id)
        out.append(f"server {srv.name if srv else params.server_id}")
    if params.engine:
        out.append(f"engine {params.engine}")
    return out


async def produce(
    db: AsyncSession, az: Authorizer, report: ReportDef, params: ReportParams, *, generated_for: str
) -> RenderedReport:
    """Run a report for a principal (the caller, or a schedule's owner)."""
    from app import __version__

    cols, rows = await report.producer(db, az, params)
    scope = await _describe_scope(db, params)
    if "window_hours" in report.params:
        scope.append(f"last {params.window_hours} h")
    if "statuses" in report.params:
        scope.append("status " + ", ".join(params.statuses or ["open", "acknowledged"]))
    if "min_severity" in report.params and params.min_severity:
        scope.append(f"severity {params.min_severity} and above")
    if "scope" in report.params:
        scope.append(f"per {params.scope}")
    return RenderedReport(
        key=report.key,
        title=report.title,
        description=report.description,
        columns=cols,
        rows=rows,
        generated_at=datetime.now(UTC),
        generated_for=generated_for,
        scope=scope,
        version=__version__,
    )


def render(report: RenderedReport, fmt: str) -> tuple[bytes, str, str]:
    """(content, media type, file name)."""
    r = renderers()[fmt]
    stamp = report.generated_at.strftime("%Y%m%d-%H%M")
    return r.render(report), r.media_type, f"{report.key}-{stamp}.{r.extension}"


#: formats offered by the API (``renderers()``); XLSX is not planned in a milestone yet
FORMATS = ("csv", "pdf")
