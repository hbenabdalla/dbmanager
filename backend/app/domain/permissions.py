"""Permission catalog and system role definitions.

Permissions are granular capabilities. Roles are just named sets of permissions and are
never checked by name in code — only permissions are checked.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionDef:
    key: str
    group: str
    description: str
    # Lowest scope at which this permission is meaningful. Platform-only permissions
    # cannot be granted at narrower scopes.
    platform_only: bool = False


_P = PermissionDef

CATALOG: tuple[PermissionDef, ...] = (
    # inventory
    _P("server.view", "Fleet", "View registered servers and their metadata"),
    _P("server.register", "Fleet", "Register new servers"),
    _P("server.modify", "Fleet", "Modify server metadata and monitoring settings"),
    _P("server.delete", "Fleet", "Remove servers from the platform"),
    _P("credentials.manage", "Fleet", "Set or rotate credentials used to connect to servers"),
    # databases & schema
    _P("database.view", "Administration", "List databases and their properties"),
    _P("database.create", "Administration", "Create databases"),
    _P("database.modify", "Administration", "Modify database properties"),
    _P("database.delete", "Administration", "Drop databases"),
    _P("schema.view", "Administration", "Browse schemas and objects"),
    _P("schema.modify", "Administration", "Create / alter schema objects (DDL)"),
    # access
    _P("user.view", "Access", "View database users and roles"),
    _P("user.manage", "Access", "Create, alter and drop database users and roles"),
    _P("privilege.view", "Access", "View grants and privileges"),
    _P("privilege.manage", "Access", "Grant and revoke privileges"),
    # sessions & queries
    _P("session.view", "Monitoring", "View sessions and locks"),
    _P("session.manage", "Monitoring", "Cancel queries / terminate sessions"),
    _P("query.view", "Queries", "View query statistics (normalized, masked text)"),
    _P("query.view_text", "Queries", "View unmasked SQL text including literals"),
    _P("query.execute", "Queries", "Run read-only SQL in the workspace"),
    _P("query.modify", "Queries", "Run data-modifying and DDL SQL in the workspace"),
    _P("query.explain", "Queries", "Request estimated execution plans (EXPLAIN)"),
    _P("query.explain_analyze", "Queries", "Run EXPLAIN ANALYZE (executes the statement)"),
    # performance / security / logs
    _P("performance.view", "Monitoring", "View metrics, dashboards and recommendations"),
    _P("performance.tune", "Optimization", "Acknowledge/dismiss recommendations and apply tuning actions"),
    _P("security.view", "Security", "View security posture and findings"),
    _P("security.manage", "Security", "Triage security findings and run scans"),
    _P("logs.view", "Monitoring", "View collection and database logs"),
    # alerting
    _P("alerts.view", "Alerting", "View alerts for visible servers"),
    _P("alerts.acknowledge", "Alerting", "Acknowledge and resolve alerts"),
    _P("alerts.manage", "Alerting", "Create and edit alert rules and maintenance windows", platform_only=True),
    _P("channels.manage", "Alerting", "Configure notification channels (webhook, email)", platform_only=True),
    # platform
    _P("audit.view", "Platform", "View the audit log", platform_only=True),
    _P("audit.export", "Platform", "Export the audit log", platform_only=True),
    _P("audit.forward", "Platform", "Configure forwarding of audit events to a SIEM", platform_only=True),
    _P("reports.view", "Platform", "Generate and download reports"),
    _P(
        "reports.schedule",
        "Platform",
        "Schedule reports e-mailed to notification channels (each schedule runs with its owner's access)",
        platform_only=True,
    ),
    _P("users.manage", "Platform", "Manage platform users and groups", platform_only=True),
    _P("roles.manage", "Platform", "Create and edit roles", platform_only=True),
    _P("rbac.manage", "Platform", "Assign roles to users and groups"),
    _P("idp.manage", "Platform", "Configure LDAP / OIDC identity providers", platform_only=True),
    _P("collectors.view", "Platform", "View collector status"),
    _P("collectors.manage", "Platform", "Trigger and configure collection"),
    _P("settings.manage", "Platform", "Change platform settings", platform_only=True),
    _P("inventory.manage", "Platform", "Manage environments, sites, clusters and applications", platform_only=True),
    # backup & restore
    _P("backup.view", "Backup", "View backups, backup jobs, policies and protection status"),
    _P("backup.create", "Backup", "Run backups (platform backups need this at platform scope)"),
    _P("backup.verify", "Backup", "Verify the integrity of stored backups"),
    _P("backup.delete", "Backup", "Delete stored backups", platform_only=True),
    _P("backup.policy.manage", "Backup", "Create and edit backup policies (schedule, retention)", platform_only=True),
    _P("backup.target.manage", "Backup", "Configure backup storage targets", platform_only=True),
    _P("restore.execute", "Backup", "Start restores (together with restore.platform or restore.database)"),
    _P(
        "restore.platform",
        "Backup",
        "Restore DBManager's own configuration from a platform backup",
        platform_only=True,
    ),
    _P("restore.database", "Backup", "Restore a managed database from its backup into a new database"),
    # simulation (demo fleet) — granted only to Platform Administrator by default
    _P("simulation.view", "Simulation", "View the simulated (demo) fleet status", platform_only=True),
    _P("simulation.generate", "Simulation", "Generate or regenerate a simulated demo fleet", platform_only=True),
    _P("simulation.delete", "Simulation", "Remove all simulated fleet data", platform_only=True),
)

ALL_PERMISSIONS: frozenset[str] = frozenset(p.key for p in CATALOG)
PLATFORM_ONLY: frozenset[str] = frozenset(p.key for p in CATALOG if p.platform_only)

_READ_BASE = {"server.view", "database.view", "schema.view"}

SYSTEM_ROLES: dict[str, tuple[str, set[str]]] = {
    "Platform Administrator": ("Full platform administration", set(ALL_PERMISSIONS)),
    "DBA Administrator": (
        "Administer assigned database systems",
        _READ_BASE
        | {
            "alerts.view",
            "alerts.acknowledge",
            "server.modify",
            "credentials.manage",
            "database.create",
            "database.modify",
            "database.delete",
            "schema.modify",
            "user.view",
            "user.manage",
            "privilege.view",
            "privilege.manage",
            "session.view",
            "session.manage",
            "query.view",
            "query.view_text",
            "query.execute",
            "query.modify",
            "query.explain",
            "query.explain_analyze",
            "performance.view",
            "performance.tune",
            "security.view",
            "logs.view",
            "reports.view",
            "collectors.view",
            "collectors.manage",
            "backup.view",
            "backup.create",
            "backup.verify",
            "restore.execute",
            "restore.database",
        },
    ),
    "Backup Operator": (
        "Run and verify backups and restore managed databases (not the platform itself)",
        {
            "server.view",
            "database.view",
            "backup.view",
            "backup.create",
            "backup.verify",
            "restore.execute",
            "restore.database",
        },
    ),
    "Database Operator": (
        "Operational actions against assigned databases",
        _READ_BASE
        | {
            "alerts.view",
            "alerts.acknowledge",
            "session.view",
            "session.manage",
            "query.view",
            "query.execute",
            "query.explain",
            "performance.view",
            "logs.view",
            "collectors.view",
            "collectors.manage",
        },
    ),
    "Performance Engineer": (
        "Monitoring, query analysis and tuning",
        _READ_BASE
        | {
            "alerts.view",
            "alerts.acknowledge",
            "session.view",
            "query.view",
            "query.view_text",
            "query.execute",
            "query.explain",
            "query.explain_analyze",
            "performance.view",
            "performance.tune",
            "logs.view",
            "reports.view",
            "collectors.view",
        },
    ),
    "Security Auditor": (
        "Security configuration and audit access",
        _READ_BASE
        | {
            "alerts.view",
            "user.view",
            "privilege.view",
            "security.view",
            "security.manage",
            "audit.view",
            "audit.export",
            "reports.view",
            "logs.view",
            "backup.view",
        },
    ),
    "Monitoring User": ("Dashboards, metrics and alerts", {"server.view", "performance.view", "alerts.view"}),
    "Log Viewer": ("Logs only", {"server.view", "logs.view"}),
    "Read Only": (
        "Read-only metadata",
        _READ_BASE | {"user.view", "privilege.view", "performance.view", "security.view", "query.view", "alerts.view"},
    ),
}


def validate_permissions(perms: list[str] | set[str]) -> list[str]:
    unknown = sorted(set(perms) - ALL_PERMISSIONS)
    return unknown
