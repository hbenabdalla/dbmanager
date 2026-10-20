"""Security rules — SQL Server."""

from __future__ import annotations

from app.advisors.engine import EvidenceContext, RuleFinding, rule


@rule(
    "MSSQL-SEC-001", engines=("mssql",), kind="security", category="accounts", severity="high", title="sa login enabled"
)
def mssql_sa(ctx: EvidenceContext) -> list[RuleFinding]:
    if not ctx.security.get("sa_enabled"):
        return []
    return [
        RuleFinding(
            title="The built-in sa login is enabled",
            finding="sa is enabled and not renamed.",
            evidence={"sa_enabled": True, "sa_renamed": ctx.security.get("sa_renamed")},
            recommendation="ALTER LOGIN sa DISABLE; use named administrator logins (ideally Windows/Entra ID).",
            risk="sa is the primary target of brute-force attacks.",
            confidence=1.0,
        )
    ]


@rule(
    "MSSQL-SEC-002",
    engines=("mssql",),
    kind="security",
    category="surface",
    severity="critical",
    title="xp_cmdshell enabled",
)
def mssql_xp_cmdshell(ctx: EvidenceContext) -> list[RuleFinding]:
    if not ctx.security.get("xp_cmdshell"):
        return []
    return [
        RuleFinding(
            title="xp_cmdshell is enabled",
            finding="sys.configurations reports xp_cmdshell = 1.",
            evidence={"xp_cmdshell": 1},
            recommendation="EXEC sp_configure 'xp_cmdshell', 0; RECONFIGURE;",
            risk="Lets sysadmin (or anyone with a proxy account) run OS commands as the service account.",
            confidence=1.0,
        )
    ]


@rule(
    "MSSQL-SEC-003",
    engines=("mssql",),
    kind="security",
    category="privileges",
    severity="high",
    title="sysadmin membership",
)
def mssql_sysadmins(ctx: EvidenceContext) -> list[RuleFinding]:
    members = ctx.security.get("sysadmin_members") or []
    if len(members) <= 2:
        return []
    return [
        RuleFinding(
            title=f"{len(members)} principals are sysadmin",
            finding="sysadmin bypasses all permission checks.",
            evidence={"members": members},
            recommendation="Remove application/service logins from sysadmin; grant specific permissions.",
            risk="Over-privileged service accounts.",
            confidence=0.85,
            severity="high",
        )
    ]


@rule(
    "MSSQL-SEC-004",
    engines=("mssql",),
    kind="security",
    category="privileges",
    severity="medium",
    title="TRUSTWORTHY / guest enabled databases",
)
def mssql_trustworthy(ctx: EvidenceContext) -> list[RuleFinding]:
    out = []
    for db in ctx.security.get("trustworthy_dbs") or []:
        out.append(
            RuleFinding(
                title=f"Database {db} is TRUSTWORTHY",
                finding="TRUSTWORTHY ON allows impersonation to escalate to server level.",
                evidence={"database": db},
                recommendation=f"ALTER DATABASE [{db}] SET TRUSTWORTHY OFF;",
                risk="db_owner of the database can become sysadmin.",
                confidence=0.9,
                severity="high",
                database=db,
            )
        )
    for db in ctx.security.get("guest_enabled_dbs") or []:
        out.append(
            RuleFinding(
                title=f"guest user enabled in {db}",
                finding="guest has CONNECT permission.",
                evidence={"database": db},
                recommendation=f"USE [{db}]; REVOKE CONNECT FROM guest;",
                risk="Any login can access the database.",
                confidence=0.9,
                database=db,
            )
        )
    return out


@rule(
    "MSSQL-SEC-005",
    engines=("mssql",),
    kind="security",
    category="auditing",
    severity="medium",
    title="No server audit",
)
def mssql_audit(ctx: EvidenceContext) -> list[RuleFinding]:
    audits = ctx.security.get("server_audits")
    if audits is None or audits > 0:  # None = not visible to the monitoring login
        return []
    return [
        RuleFinding(
            title="No SQL Server Audit is configured",
            finding="sys.server_audits is empty.",
            evidence={"server_audits": 0},
            recommendation="Create a server audit with FAILED_LOGIN_GROUP, SERVER_ROLE_MEMBER_CHANGE_GROUP and DDL groups.",
            risk="No forensic trail for privileged actions.",
            confidence=0.95,
        )
    ]


@rule(
    "MSSQL-SEC-006",
    engines=("mssql",),
    kind="security",
    category="surface",
    severity="medium",
    title="Risky server features enabled",
)
def mssql_surface(ctx: EvidenceContext) -> list[RuleFinding]:
    s = ctx.security
    enabled = {
        "Ole Automation Procedures": s.get("ole_automation"),
        "Ad Hoc Distributed Queries": s.get("ad_hoc_distributed_queries"),
        "cross db ownership chaining": s.get("cross_db_ownership_chaining"),
        "scan for startup procs": s.get("scan_for_startup_procs"),
    }
    if s.get("clr_enabled") and s.get("clr_strict_security") == 0:
        enabled["clr enabled (with clr strict security off)"] = True
    on = [k for k, v in enabled.items() if v]
    if not on:
        return []
    return [
        RuleFinding(
            title="Surface-area options are enabled",
            finding=f"sys.configurations reports enabled: {', '.join(on)}.",
            evidence={"enabled": on},
            recommendation="Disable options that are not required: EXEC sp_configure '<option>', 0; RECONFIGURE;",
            risk="Each option widens what a compromised login can do (COM objects, OPENROWSET, ownership chains).",
            confidence=0.9,
        )
    ]


@rule(
    "MSSQL-SEC-007",
    engines=("mssql",),
    kind="security",
    category="patching",
    severity="high",
    title="SQL Server version out of support",
)
def mssql_eol(ctx: EvidenceContext) -> list[RuleFinding]:
    from datetime import date  # noqa: PLC0415

    eos = ctx.security.get("end_of_support")
    if not eos or ctx.security.get("cloud_provider"):
        return []
    end = date.fromisoformat(str(eos))
    today = date.today()
    if (end - today).days > 365:
        return []
    past = end <= today
    return [
        RuleFinding(
            title=f"{ctx.security.get('product_name')} {'is out of' if past else 'leaves'} extended support",
            finding=f"Microsoft extended support {'ended' if past else 'ends'} on {end.isoformat()} (version "
            f"{ctx.security.get('version')}).",
            evidence={"version": ctx.security.get("version"), "end_of_support": end.isoformat()},
            recommendation="Plan an upgrade to a supported SQL Server release (or purchase Extended Security Updates).",
            risk="No security fixes after the end-of-support date.",
            confidence=1.0,
            severity="high" if past else "medium",
        )
    ]


@rule(
    "MSSQL-SEC-008",
    engines=("mssql",),
    kind="security",
    category="accounts",
    severity="low",
    title="Orphaned database users",
)
def mssql_orphans(ctx: EvidenceContext) -> list[RuleFinding]:
    return [
        RuleFinding(
            title=f"{len(users)} orphaned user(s) in {db}",
            finding="Database users whose SID matches no server login.",
            evidence={"database": db, "users": users},
            recommendation="Remap with ALTER USER [name] WITH LOGIN = [login], or drop users that are no longer needed.",
            risk="Orphans hide stale access and can be re-attached by creating a login with the same SID.",
            confidence=0.9,
            database=db,
        )
        for db, users in (ctx.security.get("orphaned_users") or {}).items()
        if users
    ]


@rule(
    "MSSQL-SEC-009",
    engines=("mssql",),
    kind="security",
    category="authentication",
    severity="medium",
    title="SQL logins without password policy",
)
def mssql_no_policy(ctx: EvidenceContext) -> list[RuleFinding]:
    logins = [x for x in ctx.security.get("logins_without_policy") or [] if not x.startswith("##")]
    if not logins:
        return []
    return [
        RuleFinding(
            title=f"{len(logins)} SQL login(s) bypass the password policy",
            finding="CHECK_POLICY = OFF: Windows password complexity and lockout do not apply.",
            evidence={"logins": logins},
            recommendation="ALTER LOGIN [name] WITH CHECK_POLICY = ON; (reset the password if it does not comply).",
            risk="Weak passwords and unlimited brute-force attempts.",
            confidence=0.95,
        )
    ]


@rule(
    "MSSQL-SEC-010",
    engines=("mssql",),
    kind="security",
    category="encryption",
    severity="medium",
    title="Encryption not forced",
)
def mssql_force_encryption(ctx: EvidenceContext) -> list[RuleFinding]:
    if ctx.security.get("force_encryption") is not False or ctx.security.get("cloud_provider"):
        return []
    return [
        RuleFinding(
            title="The instance does not force encrypted connections",
            finding="Force Encryption is off: clients may connect without TLS.",
            evidence={"force_encryption": False},
            recommendation="Enable Force Encryption (SQL Server Configuration Manager) with a CA-signed certificate, or "
            "require Encrypt=yes/strict in every client.",
            risk="Credentials of SQL logins and data can travel in clear text on the network.",
            confidence=0.8,
        )
    ]
