"""Security rules — Db2."""

from __future__ import annotations

from app.advisors.engine import EvidenceContext, RuleFinding, rule


@rule(
    "DB2-SEC-001",
    engines=("db2",),
    kind="security",
    category="privileges",
    severity="medium",
    title="Authorities granted to PUBLIC",
)
def db2_public(ctx: EvidenceContext) -> list[RuleFinding]:
    grants = [g for g in ctx.security.get("public_grants") or [] if g in ("CREATETAB", "BINDADD", "IMPLICIT_SCHEMA")]
    if not grants:
        return []
    return [
        RuleFinding(
            title="PUBLIC holds database authorities",
            finding=f"PUBLIC has {', '.join(grants)}.",
            evidence={"authorities": grants},
            recommendation="REVOKE CREATETAB, BINDADD, IMPLICIT_SCHEMA ON DATABASE FROM PUBLIC; (or create the DB RESTRICTIVE).",
            risk="Any authenticated user can create objects and packages.",
            confidence=0.9,
        )
    ]


@rule(
    "DB2-SEC-002",
    engines=("db2",),
    kind="security",
    category="privileges",
    severity="high",
    title="DBADM held by application/batch users",
)
def db2_dbadm(ctx: EvidenceContext) -> list[RuleFinding]:
    holders = [h for h in ctx.security.get("dbadm_holders") or [] if h not in ("DB2INST1",)]
    if not holders:
        return []
    return [
        RuleFinding(
            title="Non-administrative users hold DBADM",
            finding=f"DBADM granted to {', '.join(holders)}.",
            evidence={"holders": holders},
            recommendation="Replace DBADM with specific privileges or roles.",
            risk="DBADM grants access to all data in the database.",
            confidence=0.85,
            severity="high",
        )
    ]


@rule(
    "DB2-SEC-003",
    engines=("db2",),
    kind="security",
    category="auditing",
    severity="medium",
    title="db2audit not active",
)
def db2_audit(ctx: EvidenceContext) -> list[RuleFinding]:
    if ctx.security.get("audit_active") is not False:  # None = not visible to this user
        return []
    return [
        RuleFinding(
            title="Db2 audit facility is not active",
            finding="No audit policy is associated with the database.",
            evidence={"audit_active": False},
            recommendation="CREATE AUDIT POLICY ... and AUDIT DATABASE USING POLICY ...",
            risk="Privileged activity is not recorded.",
            confidence=0.9,
        )
    ]


@rule(
    "DB2-SEC-004",
    engines=("db2",),
    kind="security",
    category="encryption",
    severity="medium",
    title="Unencrypted authentication / no TLS port",
)
def db2_auth(ctx: EvidenceContext) -> list[RuleFinding]:
    if ctx.security.get("dbm_cfg_readable") is False or ctx.security.get("authentication") is None:
        return []  # the configuration could not be read: no evidence either way
    if ctx.security.get("ssl_svcename") or ctx.security.get("authentication") in (
        "SERVER_ENCRYPT",
        "SERVER_ENCRYPT_AES",
    ):
        return []
    return [
        RuleFinding(
            title="Credentials and data may be sent unencrypted",
            finding=f"AUTHENTICATION = {ctx.security.get('authentication')} and no SSL_SVCENAME configured.",
            evidence={"authentication": ctx.security.get("authentication"), "ssl_svcename": None},
            recommendation="Configure SSL_SVCENAME with a server certificate and require TLS for clients.",
            risk="Network interception.",
            confidence=0.85,
        )
    ]


@rule(
    "DB2-SEC-005",
    engines=("db2",),
    kind="security",
    category="privileges",
    severity="high",
    title="Data access / access control authorities",
)
def db2_powerful(ctx: EvidenceContext) -> list[RuleFinding]:
    out = []
    for key, name, why in (
        ("dataaccess_holders", "DATAACCESS", "reads every table and executes every package"),
        ("accessctrl_holders", "ACCESSCTRL", "grants and revokes most privileges"),
        ("secadm_holders", "SECADM", "manages roles, audit policies, trusted contexts and LBAC"),
        ("not_fenced_holders", "CREATE_NOT_FENCED_ROUTINE", "runs routine code inside the engine process"),
    ):
        holders = [h for h in ctx.security.get(key) or [] if h not in ("DB2INST1",)]
        if len(holders) > (1 if name == "SECADM" else 0):
            out.append(
                RuleFinding(
                    title=f"{len(holders)} holder(s) of {name}",
                    finding=f"{name} {why}: {', '.join(holders[:15])}.",
                    evidence={"authority": name, "holders": holders},
                    recommendation=f"Keep {name} to named administrators (separation of duties: SECADM separate from DBADM).",
                    risk="Over-privileged accounts widen the impact of a credential compromise.",
                    confidence=0.85,
                    severity="medium" if name == "SECADM" else "high",
                )
            )
    return out


@rule(
    "DB2-SEC-006",
    engines=("db2",),
    kind="security",
    category="patching",
    severity="high",
    title="Db2 release out of support",
)
def db2_eol(ctx: EvidenceContext) -> list[RuleFinding]:
    eos = ctx.security.get("end_of_support")
    if not eos:
        return []
    return [
        RuleFinding(
            title=f"Db2 {ctx.security.get('version')} is out of support",
            finding=f"IBM support for this release ended on {eos}.",
            evidence={"version": ctx.security.get("version"), "end_of_support": eos},
            recommendation="Upgrade to Db2 11.5 or 12.1.",
            risk="No security fix packs.",
            confidence=0.95,
        )
    ]


@rule(
    "DB2-SEC-007",
    engines=("db2",),
    kind="security",
    category="privileges",
    severity="medium",
    title="Tables readable by PUBLIC",
)
def db2_public_tables(ctx: EvidenceContext) -> list[RuleFinding]:
    n = ctx.security.get("public_table_select_grants") or 0
    if n <= 0:
        return []
    return [
        RuleFinding(
            title=f"{n} application table(s) grant SELECT to PUBLIC",
            finding="Every authenticated user can read these tables.",
            evidence={"public_table_select_grants": n},
            recommendation="Revoke SELECT from PUBLIC and grant to roles instead.",
            risk="Data exposure to every database user.",
            confidence=0.85,
        )
    ]
