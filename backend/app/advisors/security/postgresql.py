"""Security rules — PostgreSQL."""

from __future__ import annotations

from app.advisors.engine import EvidenceContext, RuleFinding, rule
from app.advisors.security.common import _roles

PG_MIN_SUPPORTED_MAJOR = 14  # PostgreSQL 13 reached end of life in November 2025
SYSTEM_PG_ROLES = {"postgres", "rdsadmin", "azuresu", "cloudsqladmin", "rds_superuser", "azure_superuser"}


def _setting(ctx: EvidenceContext, name: str) -> str | None:
    return (ctx.security.get("settings") or {}).get(name)


@rule(
    "PG-SEC-001",
    engines=("postgresql",),
    kind="security",
    category="privileges",
    severity="high",
    title="Superuser accounts",
)
def pg_superusers(ctx: EvidenceContext) -> list[RuleFinding]:
    supers = [r["name"] for r in _roles(ctx) if r.get("is_superuser") and r.get("can_login")]
    extra = [s for s in supers if s not in SYSTEM_PG_ROLES]
    if not extra:
        return []
    return [
        RuleFinding(
            title=f"{len(extra)} additional login role(s) have SUPERUSER",
            finding="Superuser bypasses every permission check, row-level security and can run OS commands via COPY PROGRAM.",
            evidence={"superusers": supers, "non_default_superusers": extra},
            recommendation="Replace personal/application superusers with least-privilege roles "
            "(pg_monitor, pg_read_all_data, explicit grants).",
            risk="Compromise of any superuser grants full control of the server and host OS user.",
            confidence=0.95,
            severity="critical" if len(extra) > 2 else "high",
            remediation="ALTER ROLE <name> NOSUPERUSER; after granting the narrower roles it needs.",
        )
    ]


@rule(
    "PG-SEC-002",
    engines=("postgresql",),
    kind="security",
    category="encryption",
    severity="high",
    title="SSL disabled on server",
)
def pg_ssl_off(ctx: EvidenceContext) -> list[RuleFinding]:
    if _setting(ctx, "ssl") != "off":
        return []
    return [
        RuleFinding(
            title="Server does not accept TLS connections",
            finding="ssl = off.",
            evidence={"ssl": "off"},
            recommendation="Configure ssl_cert_file/ssl_key_file, set ssl = on and require hostssl in pg_hba.conf.",
            risk="All client traffic, including passwords exchanged during authentication, is unencrypted.",
            confidence=1.0,
            remediation="ALTER SYSTEM SET ssl = on; SELECT pg_reload_conf();",
        )
    ]


@rule(
    "PG-SEC-003",
    engines=("postgresql",),
    kind="security",
    category="encryption",
    severity="medium",
    title="Unencrypted client connections",
)
def pg_non_ssl_clients(ctx: EvidenceContext) -> list[RuleFinding]:
    n = ctx.security.get("non_ssl_connections")
    if not n:
        return []
    return [
        RuleFinding(
            title=f"{n} remote sessions are not using TLS",
            finding=f"{n} of {ctx.security.get('remote_connections')} remote client sessions are connected without TLS.",
            evidence={"non_ssl_connections": n, "remote_connections": ctx.security.get("remote_connections")},
            recommendation="Change 'host' entries in pg_hba.conf to 'hostssl' and configure clients with sslmode=verify-full.",
            risk="Data in transit can be intercepted.",
            confidence=0.95,
        )
    ]


@rule(
    "PG-SEC-004",
    engines=("postgresql",),
    kind="security",
    category="authentication",
    severity="medium",
    title="MD5 password hashing",
)
def pg_md5(ctx: EvidenceContext) -> list[RuleFinding]:
    out = []
    if _setting(ctx, "password_encryption") == "md5":
        out.append(
            RuleFinding(
                title="New passwords are hashed with MD5",
                finding="password_encryption = md5.",
                evidence={"password_encryption": "md5"},
                recommendation="Set password_encryption = 'scram-sha-256'.",
                risk="MD5 hashes are vulnerable to offline brute force and pass-the-hash.",
                confidence=1.0,
            )
        )
    roles = ctx.security.get("md5_password_roles")
    if roles:
        out.append(
            RuleFinding(
                title=f"{len(roles)} roles still store MD5 password hashes",
                finding="These roles must reset their password after switching to SCRAM.",
                evidence={"roles": roles},
                recommendation="Reset these passwords once password_encryption is scram-sha-256 and switch pg_hba to scram-sha-256.",
                risk="MD5 hashes can be replayed or cracked.",
                confidence=1.0,
                severity="high",
            )
        )
    return out


@rule(
    "PG-SEC-005",
    engines=("postgresql",),
    kind="security",
    category="authentication",
    severity="critical",
    title="trust authentication in pg_hba.conf",
)
def pg_trust(ctx: EvidenceContext) -> list[RuleFinding]:
    rules = ctx.security.get("hba_rules")
    if rules is None:
        return []
    trust = [r for r in rules if r.get("auth_method") == "trust" and r.get("type") != "local"]
    if not trust:
        return []
    return [
        RuleFinding(
            title="Network connections are accepted without a password",
            finding=f"{len(trust)} pg_hba.conf rule(s) use 'trust' for TCP connections.",
            evidence={"rules": trust},
            recommendation="Replace trust with scram-sha-256 (or cert/gss) and reload.",
            risk="Anyone able to reach the port can connect as any user, including superusers.",
            confidence=1.0,
        )
    ]


@rule(
    "PG-SEC-006",
    engines=("postgresql",),
    kind="security",
    category="privileges",
    severity="medium",
    title="PUBLIC can create objects in public schema",
)
def pg_public_create(ctx: EvidenceContext) -> list[RuleFinding]:
    if not ctx.security.get("public_schema_create"):
        return []
    return [
        RuleFinding(
            title="Any user can create objects in the public schema",
            finding=f"PUBLIC holds CREATE on schema public in database {ctx.security.get('database')}.",
            evidence={"database": ctx.security.get("database"), "privilege": "CREATE", "grantee": "PUBLIC"},
            recommendation="REVOKE CREATE ON SCHEMA public FROM PUBLIC; (default since PostgreSQL 15).",
            risk="Enables search_path based privilege escalation (CVE-2018-1058 class).",
            confidence=1.0,
            database=ctx.security.get("database"),
        )
    ]


@rule(
    "PG-SEC-007",
    engines=("postgresql",),
    kind="security",
    category="privileges",
    severity="medium",
    title="Roles with powerful attributes",
)
def pg_powerful_attrs(ctx: EvidenceContext) -> list[RuleFinding]:
    out = []
    for r in _roles(ctx):
        if r.get("is_superuser") or r.get("is_system") or r["name"] in SYSTEM_PG_ROLES:
            continue
        attrs = r.get("attributes") or {}
        flags = [k.upper() for k in ("createrole", "bypassrls", "replication") if attrs.get(k)]
        if flags and r.get("can_login"):
            out.append(
                RuleFinding(
                    title=f"{r['name']} has {', '.join(flags)}",
                    finding=f"Login role {r['name']} holds {', '.join(flags)}.",
                    evidence={"role": r["name"], "attributes": flags},
                    recommendation="Remove attributes not required for the role's purpose.",
                    risk="CREATEROLE can grant itself membership in other roles; BYPASSRLS ignores row security.",
                    confidence=0.85,
                    affected_object=r["name"],
                )
            )
    return out


@rule(
    "PG-SEC-008",
    engines=("postgresql",),
    kind="security",
    category="lifecycle",
    severity="high",
    title="Unsupported PostgreSQL version",
)
def pg_version(ctx: EvidenceContext) -> list[RuleFinding]:
    v = ctx.security.get("version_num") or 0
    major = v // 10000
    if not major or major >= PG_MIN_SUPPORTED_MAJOR:
        return []
    return [
        RuleFinding(
            title=f"PostgreSQL {major} no longer receives security fixes",
            finding=f"Server version {ctx.security.get('version')} is past community end of life.",
            evidence={"version": ctx.security.get("version"), "minimum_supported_major": PG_MIN_SUPPORTED_MAJOR},
            recommendation="Plan an upgrade (pg_upgrade or logical replication) to a supported major version.",
            risk="Known vulnerabilities remain unpatched.",
            confidence=1.0,
        )
    ]


@rule(
    "PG-SEC-009",
    engines=("postgresql",),
    kind="security",
    category="auditing",
    severity="low",
    title="Connection logging disabled",
)
def pg_logging(ctx: EvidenceContext) -> list[RuleFinding]:
    out = []
    if _setting(ctx, "log_connections") == "off":
        out.append(
            RuleFinding(
                title="Connections are not logged",
                finding="log_connections = off.",
                evidence={"log_connections": "off"},
                recommendation="Enable log_connections and log_disconnections for authentication forensics.",
                risk="Unauthorized access attempts leave no trace.",
                confidence=1.0,
            )
        )
    if "pgaudit" not in (ctx.security.get("extensions") or {}) and ctx.environment_tier == "production":
        out.append(
            RuleFinding(
                title="No statement-level audit extension",
                finding="pgaudit is not installed on a production server.",
                evidence={"extensions": list((ctx.security.get("extensions") or {}).keys())},
                recommendation="Evaluate pgaudit for DDL/role/privileged statement auditing.",
                risk="Privileged changes cannot be attributed.",
                confidence=0.7,
                severity="info",
            )
        )
    return out


@rule(
    "PG-SEC-010",
    engines=("postgresql",),
    kind="security",
    category="privileges",
    severity="low",
    title="Direct table grants to login users",
)
def pg_direct_grants(ctx: EvidenceContext) -> list[RuleFinding]:
    rows = [r for r in ctx.security.get("direct_table_grants") or [] if r["grants"] >= 10]
    if not rows:
        return []
    return [
        RuleFinding(
            title="Privileges granted directly to users instead of roles",
            finding=f"{len(rows)} login role(s) hold 10+ direct object grants.",
            evidence={"users": rows[:20]},
            recommendation="Grant privileges to group roles and add users as members, so access is reviewable.",
            risk="Direct grants accumulate and are hard to review or revoke consistently.",
            confidence=0.8,
        )
    ]


@rule(
    "PG-SEC-011",
    engines=("postgresql",),
    kind="security",
    category="privileges",
    severity="medium",
    title="SECURITY DEFINER functions without search_path",
)
def pg_secdef(ctx: EvidenceContext) -> list[RuleFinding]:
    funcs = ctx.security.get("security_definer_without_search_path") or []
    if not funcs:
        return []
    return [
        RuleFinding(
            title=f"{len(funcs)} SECURITY DEFINER function(s) lack a fixed search_path",
            finding="These functions run with their owner's privileges but resolve objects using the caller's search_path.",
            evidence={"functions": funcs[:20]},
            recommendation="ALTER FUNCTION ... SET search_path = pg_catalog, <schema>;",
            risk="Callers can hijack object resolution to execute code as the owner.",
            confidence=0.9,
        )
    ]


@rule(
    "PG-SEC-012",
    engines=("postgresql",),
    kind="security",
    category="platform",
    severity="medium",
    title="Monitoring account is a superuser",
)
def pg_monitor_superuser(ctx: EvidenceContext) -> list[RuleFinding]:
    if not ctx.security.get("current_user_superuser"):
        return []
    return [
        RuleFinding(
            title="DBManager connects with a superuser account",
            finding=f"The monitoring credential ({ctx.security.get('current_user')}) is a superuser.",
            evidence={"monitoring_user": ctx.security.get("current_user")},
            recommendation="Create a dedicated role: CREATE ROLE dbm_monitor LOGIN; GRANT pg_monitor TO dbm_monitor; "
            "and register it as the monitoring credential. Keep a separate admin credential for changes.",
            risk="A leaked monitoring credential would grant full control of the server.",
            confidence=1.0,
        )
    ]


@rule(
    "PG-SEC-013",
    engines=("postgresql",),
    kind="security",
    category="authentication",
    severity="high",
    title="Login roles without password",
)
def pg_empty_passwords(ctx: EvidenceContext) -> list[RuleFinding]:
    roles = ctx.security.get("empty_password_logins") or []
    hba = ctx.security.get("hba_rules")
    if not roles or hba is None:
        return []
    if not any(r.get("auth_method") in ("trust", "peer", "ident") for r in hba):
        return []
    return [
        RuleFinding(
            title="Login roles without a password and password-less auth methods present",
            finding=f"{len(roles)} login role(s) have no password while pg_hba.conf uses trust/peer/ident.",
            evidence={"roles": roles},
            recommendation="Set passwords or restrict these roles in pg_hba.conf.",
            risk="Possible password-less access.",
            confidence=0.6,
        )
    ]
