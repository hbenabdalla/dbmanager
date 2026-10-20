"""Security rules — MySQL / MariaDB."""

from __future__ import annotations

from app.advisors.engine import EvidenceContext, RuleFinding, rule


@rule(
    "MYSQL-SEC-001",
    engines=("mysql", "mariadb"),
    kind="security",
    category="accounts",
    severity="high",
    title="Administrative accounts reachable from any host",
)
def mysql_wildcard_admins(ctx: EvidenceContext) -> list[RuleFinding]:
    a = ctx.security.get("wildcard_host_admins") or []
    if not a:
        return []
    return [
        RuleFinding(
            title="Superuser accounts allow connections from any host",
            finding=f"{', '.join(a)} have global privileges and host '%'.",
            evidence={"accounts": a},
            recommendation="Restrict host part to admin networks (e.g. 'admin'@'10.0.0.%').",
            risk="Credential compromise is exploitable from anywhere the port is reachable.",
            confidence=0.9,
        )
    ]


@rule(
    "MYSQL-SEC-002",
    engines=("mysql", "mariadb"),
    kind="security",
    category="accounts",
    severity="critical",
    title="Anonymous accounts",
)
def mysql_anonymous(ctx: EvidenceContext) -> list[RuleFinding]:
    a = ctx.security.get("anonymous_accounts") or []
    if not a:
        return []
    return [
        RuleFinding(
            title="Anonymous accounts exist",
            finding=f"Accounts with empty user name: {', '.join(a)}.",
            evidence={"accounts": a},
            recommendation="DROP USER ''@'localhost';",
            risk="Anyone can connect without a user name.",
            confidence=1.0,
        )
    ]


@rule(
    "MYSQL-SEC-003",
    engines=("mysql", "mariadb"),
    kind="security",
    category="encryption",
    severity="medium",
    title="Secure transport not required",
)
def mysql_tls(ctx: EvidenceContext) -> list[RuleFinding]:
    if ctx.security.get("require_secure_transport") != "OFF":
        return []
    return [
        RuleFinding(
            title="Clients may connect without TLS",
            finding="require_secure_transport = OFF.",
            evidence={"require_secure_transport": "OFF"},
            recommendation="SET PERSIST require_secure_transport = ON;",
            risk="Unencrypted client sessions.",
            confidence=1.0,
        )
    ]


@rule(
    "MYSQL-SEC-004",
    engines=("mysql", "mariadb"),
    kind="security",
    category="surface",
    severity="medium",
    title="LOAD DATA LOCAL enabled",
)
def mysql_local_infile(ctx: EvidenceContext) -> list[RuleFinding]:
    if ctx.security.get("local_infile") != "ON":
        return []
    return [
        RuleFinding(
            title="local_infile is enabled",
            finding="local_infile = ON.",
            evidence={"local_infile": "ON"},
            recommendation="SET PERSIST local_infile = OFF; unless explicitly required.",
            risk="A malicious server/proxy can read client files; data-loading abuse.",
            confidence=0.9,
        )
    ]


#: vendor lifecycle (end of community/premier support) used by MYSQL-SEC-008
MYSQL_EOL = {(5, 7): "2023-10-31", (8, 0): "2026-04-30"}
MARIADB_EOL = {
    (10, 2): "2022-05-23",
    (10, 3): "2023-05-25",
    (10, 4): "2024-06-18",
    (10, 5): "2025-06-24",
    (10, 6): "2026-07-06",
    # short-term releases: one year of support each
    (10, 7): "2023-02-09",
    (10, 8): "2023-05-20",
    (10, 9): "2023-08-22",
    (10, 10): "2023-11-17",
    (10, 11): "2028-02-16",
    (11, 0): "2024-06-06",
    (11, 1): "2024-08-21",
    (11, 2): "2024-11-21",
    (11, 3): "2024-05-29",
    (11, 4): "2029-05-29",
}


@rule(
    "MYSQL-SEC-005",
    engines=("mysql",),
    kind="security",
    category="authentication",
    severity="medium",
    title="Accounts use the deprecated mysql_native_password plugin",
)
def mysql_native_password(ctx: EvidenceContext) -> list[RuleFinding]:
    accts = ctx.security.get("native_password_accounts") or []
    if not accts:
        return []
    return [
        RuleFinding(
            title=f"{len(accts)} account(s) authenticate with mysql_native_password",
            finding=f"{', '.join(accts[:8])} use SHA-1 based mysql_native_password, deprecated in 8.0.34 and removed in MySQL 9.0.",
            evidence={"accounts": accts},
            recommendation="ALTER USER ... IDENTIFIED WITH caching_sha2_password BY '<new password>'; after checking client support.",
            risk="Weaker password hashing; blocks upgrading to MySQL 9.",
            confidence=0.95,
            remediation="Not applied automatically: rotating the hash requires the account's password.",
        )
    ]


@rule(
    "MYSQL-SEC-006",
    engines=("mysql", "mariadb"),
    kind="security",
    category="privileges",
    severity="high",
    title="FILE privilege with unrestricted secure_file_priv",
)
def mysql_file_priv(ctx: EvidenceContext) -> list[RuleFinding]:
    holders = ctx.security.get("file_privilege_holders") or []
    sfp = ctx.security.get("secure_file_priv")
    if not holders:
        return []
    unrestricted = sfp in ("", None)
    return [
        RuleFinding(
            title=f"{len(holders)} account(s) hold FILE",
            finding=f"FILE lets {', '.join(holders[:6])} read and write server files"
            + (
                " anywhere the mysqld user can access (secure_file_priv is empty)."
                if unrestricted
                else f" under {sfp}."
            ),
            evidence={"accounts": holders, "secure_file_priv": sfp},
            recommendation="REVOKE FILE ON *.* FROM <account>; and set secure_file_priv to a dedicated directory.",
            risk="Reading configuration/key files or writing files used by other services.",
            confidence=0.9,
            severity="high" if unrestricted else "medium",
        )
    ]


@rule(
    "MYSQL-SEC-007",
    engines=("mysql", "mariadb"),
    kind="security",
    category="auditing",
    severity="medium",
    title="No audit plugin active",
)
def mysql_audit(ctx: EvidenceContext) -> list[RuleFinding]:
    if ctx.security.get("audit_plugin_active") or ctx.environment_tier not in ("production", "dr"):
        return []
    plugin = (
        "server_audit (MariaDB Audit Plugin)"
        if ctx.engine == "mariadb"
        else "audit_log (Enterprise) or a community audit plugin"
    )
    return [
        RuleFinding(
            title="Database activity is not audited",
            finding="No audit plugin or component is active on this production server.",
            evidence={"active_plugins": sorted((ctx.security.get("plugins") or {}).keys())[:40]},
            recommendation=f"Enable {plugin} for connections, DDL and privilege changes.",
            risk="Privileged activity cannot be reconstructed after an incident.",
            confidence=0.8,
        )
    ]


def _parse_major(version: str | None) -> tuple[int, int] | None:
    import re

    m = re.match(r"(\d+)\.(\d+)", version or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


@rule(
    "MYSQL-SEC-008",
    engines=("mysql", "mariadb"),
    kind="security",
    category="lifecycle",
    severity="high",
    title="Server version past vendor end of life",
)
def mysql_eol(ctx: EvidenceContext) -> list[RuleFinding]:
    from datetime import UTC, date, datetime

    major = _parse_major(ctx.security.get("version"))
    if major is None:
        return []
    table = MARIADB_EOL if ctx.engine == "mariadb" else MYSQL_EOL
    eol = table.get(major)
    if eol is None and ctx.engine == "mysql" and major < (5, 7):
        eol = "2020-02-01"
    if eol is None and ctx.engine == "mariadb" and major < (10, 2):
        eol = "2020-10-17"  # 10.1 and older
    if eol is None or date.fromisoformat(eol) > datetime.now(UTC).date():
        return []
    return [
        RuleFinding(
            title=f"{ctx.engine} {major[0]}.{major[1]} is past end of life",
            finding=f"Version {ctx.security.get('version')} stopped receiving security fixes on {eol} (vendor lifecycle).",
            evidence={"version": ctx.security.get("version"), "end_of_life": eol},
            recommendation="Plan an upgrade to a supported LTS release (MySQL 8.4 LTS / MariaDB 10.11 or 11.4 LTS).",
            risk="Unpatched vulnerabilities.",
            confidence=0.9,
        )
    ]


@rule(
    "MYSQL-SEC-009",
    engines=("mysql", "mariadb"),
    kind="security",
    category="authentication",
    severity="critical",
    title="Accounts without a password",
)
def mysql_no_password(ctx: EvidenceContext) -> list[RuleFinding]:
    accts = ctx.security.get("accounts_without_password") or []
    if not accts:
        return []
    return [
        RuleFinding(
            title=f"{len(accts)} login account(s) have an empty password",
            finding=f"{', '.join(accts[:8])} can log in with password-based plugins and no password.",
            evidence={"accounts": accts},
            recommendation="Set a strong password or lock the account (ALTER USER ... ACCOUNT LOCK).",
            risk="Unauthenticated access for anyone who can reach the port.",
            confidence=0.95,
        )
    ]


@rule(
    "MYSQL-SEC-010",
    engines=("mysql", "mariadb"),
    kind="security",
    category="authentication",
    severity="low",
    title="No password validation",
)
def mysql_password_validation(ctx: EvidenceContext) -> list[RuleFinding]:
    if ctx.security.get("validate_password_component") is not False:
        return []
    fix = (
        "INSTALL SONAME 'simple_password_check'; (or cracklib_password_check)"
        if ctx.engine == "mariadb"
        else "INSTALL COMPONENT 'file://component_validate_password';"
    )
    return [
        RuleFinding(
            title="Password strength is not enforced",
            finding="No password validation component/plugin is active.",
            evidence={"validate_password": False},
            recommendation=f"{fix} and configure the policy.",
            risk="Weak passwords for new or changed accounts.",
            confidence=0.85,
        )
    ]
