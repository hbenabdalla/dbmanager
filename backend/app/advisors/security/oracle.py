"""Security rules — Oracle."""

from __future__ import annotations

from app.advisors.engine import EvidenceContext, RuleFinding, rule


@rule(
    "ORA-SEC-001",
    engines=("oracle",),
    kind="security",
    category="accounts",
    severity="high",
    title="Default / sample accounts open",
)
def ora_default_accounts(ctx: EvidenceContext) -> list[RuleFinding]:
    accts = ctx.security.get("default_accounts_open") or []
    if not accts:
        return []
    return [
        RuleFinding(
            title=f"Default accounts are open: {', '.join(accts)}",
            finding="Oracle-supplied or sample accounts are OPEN with possibly well-known passwords.",
            evidence={"accounts": accts},
            recommendation="ALTER USER <name> ACCOUNT LOCK PASSWORD EXPIRE; (or drop sample schemas).",
            risk="Well-known credentials enable trivial unauthorized access.",
            confidence=0.9,
        )
    ]


@rule(
    "ORA-SEC-002",
    engines=("oracle",),
    kind="security",
    category="privileges",
    severity="medium",
    title="PUBLIC EXECUTE on network/file packages",
)
def ora_public_execute(ctx: EvidenceContext) -> list[RuleFinding]:
    pkgs = ctx.security.get("public_execute_packages") or []
    if not pkgs:
        return []
    return [
        RuleFinding(
            title="PUBLIC can execute sensitive PL/SQL packages",
            finding=f"EXECUTE granted to PUBLIC on {', '.join(pkgs)}.",
            evidence={"packages": pkgs},
            recommendation="REVOKE EXECUTE ON <package> FROM PUBLIC; grant to required schemas only.",
            risk="Allows any user to read/write files or open network connections from the database.",
            confidence=0.85,
            severity="high" if len(pkgs) > 1 else "medium",
        )
    ]


@rule(
    "ORA-SEC-003",
    engines=("oracle",),
    kind="security",
    category="auditing",
    severity="medium",
    title="Auditing disabled",
)
def ora_audit(ctx: EvidenceContext) -> list[RuleFinding]:
    if ctx.security.get("audit_trail") != "NONE" or ctx.security.get("unified_auditing"):
        return []
    return [
        RuleFinding(
            title="Database auditing is disabled",
            finding="AUDIT_TRAIL = NONE and Unified Auditing is not enabled.",
            evidence={"audit_trail": "NONE", "unified_auditing": False},
            recommendation="Enable Unified Auditing policies for logons, privileged and DDL activity.",
            risk="Privileged activity cannot be reconstructed.",
            confidence=0.95,
        )
    ]


@rule(
    "ORA-SEC-004",
    engines=("oracle",),
    kind="security",
    category="authentication",
    severity="low",
    title="Password profiles without lifetime",
)
def ora_profiles(ctx: EvidenceContext) -> list[RuleFinding]:
    p = ctx.security.get("unlimited_password_profiles") or []
    if not p:
        return []
    return [
        RuleFinding(
            title="Profiles allow non-expiring passwords",
            finding=f"PASSWORD_LIFE_TIME UNLIMITED in profiles {', '.join(p)}.",
            evidence={"profiles": p},
            recommendation="Define PASSWORD_LIFE_TIME, FAILED_LOGIN_ATTEMPTS and a verify function.",
            risk="Long-lived passwords increase exposure.",
            confidence=0.8,
        )
    ]


@rule(
    "ORA-SEC-005",
    engines=("oracle",),
    kind="security",
    category="encryption",
    severity="medium",
    title="Unencrypted Oracle Net connections accepted",
)
def ora_nne(ctx: EvidenceContext) -> list[RuleFinding]:
    level = ctx.security.get("network_encryption")
    if level in (None, "REQUIRED", "TCPS"):
        return []
    return [
        RuleFinding(
            title="The listener accepts unencrypted connections",
            finding="The monitoring connection was established over plain TCP without encryption. The thin driver "
            "cannot negotiate Native Network Encryption, so SQLNET.ENCRYPTION_SERVER is not REQUIRED for this service.",
            evidence={
                "monitoring_session_protocol": ctx.security.get("monitoring_session_protocol"),
                "inferred": level,
            },
            recommendation="Offer a TCPS (TLS) listener endpoint and register the server with TLS mode require/verify-full; "
            "for thick clients, set SQLNET.ENCRYPTION_SERVER = REQUIRED.",
            risk="Clients may exchange data (and SQL text) in clear text on the network.",
            confidence=0.8,
        )
    ]


def _list_rule(key: str, threshold: int = 0):  # type: ignore[no-untyped-def]
    def get(ctx: EvidenceContext) -> list:
        v = ctx.security.get(key)
        return list(v) if isinstance(v, (list, dict)) and len(v) > threshold else []

    return get


@rule(
    "ORA-SEC-006",
    engines=("oracle",),
    kind="security",
    category="privileges",
    severity="high",
    title="DBA role granted to application accounts",
)
def ora_dba_grantees(ctx: EvidenceContext) -> list[RuleFinding]:
    g = _list_rule("dba_role_grantees")(ctx)
    if not g:
        return []
    return [
        RuleFinding(
            title=f"{len(g)} non-Oracle account(s) hold the DBA role",
            finding=f"DBA granted to: {', '.join(g[:20])}.",
            evidence={"grantees": g},
            recommendation="Replace DBA with the specific system/object privileges each account needs "
            "(or with SYSBACKUP/SYSDG/SYSKM for administrative duties).",
            risk="DBA includes every ANY privilege: data access and privilege escalation across all schemas.",
            confidence=0.9,
        )
    ]


@rule(
    "ORA-SEC-007",
    engines=("oracle",),
    kind="security",
    category="privileges",
    severity="medium",
    title="Powerful ANY / system privileges granted directly",
)
def ora_any_privs(ctx: EvidenceContext) -> list[RuleFinding]:
    grants = ctx.security.get("any_privilege_grantees") or {}
    if not grants:
        return []
    critical = {"GRANT ANY PRIVILEGE", "GRANT ANY ROLE", "BECOME USER", "ALTER SYSTEM", "EXEMPT ACCESS POLICY"}
    worst = any(set(v) & critical for v in grants.values())
    return [
        RuleFinding(
            title=f"{len(grants)} account(s)/role(s) hold ANY or system-level privileges",
            finding="; ".join(f"{k}: {', '.join(sorted(v)[:6])}" for k, v in list(grants.items())[:10]),
            evidence={"grantees": grants},
            recommendation="Revoke ANY privileges and grant object privileges on the required schemas instead.",
            risk="ANY privileges bypass object-level access control.",
            confidence=0.85,
            severity="high" if worst else "medium",
        )
    ]


@rule(
    "ORA-SEC-008",
    engines=("oracle",),
    kind="security",
    category="authentication",
    severity="high",
    title="Accounts with only legacy 10G password hashes",
)
def ora_weak_hashes(ctx: EvidenceContext) -> list[RuleFinding]:
    rows = ctx.security.get("weak_password_versions") or []
    if not rows:
        return []
    return [
        RuleFinding(
            title=f"{len(rows)} open account(s) only have the 10G password verifier",
            finding="The DES-based 10G verifier is case-insensitive and fast to brute-force.",
            evidence={"accounts": rows},
            recommendation="Set SQLNET.ALLOWED_LOGON_VERSION_SERVER to 12 or higher and reset these passwords.",
            risk="Password hashes can be cracked offline if dictionary data leaks.",
            confidence=0.9,
        )
    ]


@rule(
    "ORA-SEC-009",
    engines=("oracle",),
    kind="security",
    category="patching",
    severity="high",
    title="Oracle release out of support",
)
def ora_eol(ctx: EvidenceContext) -> list[RuleFinding]:
    eos = ctx.security.get("end_of_support")
    if not eos:
        return []
    return [
        RuleFinding(
            title=f"Oracle {ctx.security.get('version')} is past its support end date",
            finding=f"Support for this release ended on {eos}; no security patches (RUs) are produced.",
            evidence={"version": ctx.security.get("version"), "end_of_support": eos},
            recommendation="Upgrade to a long-term support release (19c or 23ai).",
            risk="Unpatched vulnerabilities.",
            confidence=0.95,
        )
    ]


@rule(
    "ORA-SEC-010",
    engines=("oracle",),
    kind="security",
    category="privileges",
    severity="high",
    title="System privileges granted to PUBLIC",
)
def ora_public_sys(ctx: EvidenceContext) -> list[RuleFinding]:
    privs = ctx.security.get("public_system_privileges") or []
    if not privs:
        return []
    return [
        RuleFinding(
            title="PUBLIC holds system privileges",
            finding=f"Every database account inherits: {', '.join(privs)}.",
            evidence={"privileges": privs},
            recommendation="REVOKE <privilege> FROM PUBLIC; grant to the accounts that need it.",
            risk="Any account, including future ones, gains these privileges.",
            confidence=0.95,
        )
    ]


@rule(
    "ORA-SEC-011",
    engines=("oracle",),
    kind="security",
    category="authentication",
    severity="medium",
    title="Profiles without failed-login lockout",
)
def ora_failed_login(ctx: EvidenceContext) -> list[RuleFinding]:
    p = ctx.security.get("unlimited_failed_login_profiles") or []
    if not p:
        return []
    return [
        RuleFinding(
            title="Password guessing is not limited",
            finding=f"FAILED_LOGIN_ATTEMPTS is UNLIMITED in profiles used by open accounts: {', '.join(p)}.",
            evidence={"profiles": p},
            recommendation="ALTER PROFILE <name> LIMIT FAILED_LOGIN_ATTEMPTS 10 PASSWORD_LOCK_TIME 1;",
            risk="Unlimited online brute-force attempts.",
            confidence=0.85,
        )
    ]
