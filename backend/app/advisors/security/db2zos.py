"""Security rules — Db2 for z/OS (catalog authorities only; RACF/ACF2/Top Secret are not visible)."""

from __future__ import annotations

from app.advisors.engine import EvidenceContext, RuleFinding, rule


@rule(
    "ZOS-SEC-001",
    engines=("db2zos",),
    kind="security",
    category="privileges",
    severity="medium",
    title="Many SYSADM / SYSCTRL holders",
)
def zos_sysadm(ctx: EvidenceContext) -> list[RuleFinding]:
    holders = sorted(set(ctx.security.get("sysadm_holders") or []) | set(ctx.security.get("sysctrl_holders") or []))
    if len(holders) <= 3:
        return []
    return [
        RuleFinding(
            title=f"{len(holders)} authorization IDs hold SYSADM or SYSCTRL in the catalog",
            finding=f"Holders: {', '.join(holders[:20])}. Install SYSADM IDs from DSNZPARM are not listed.",
            evidence={"holders": holders, "note": ctx.security.get("external_security_note")},
            recommendation="Use SECADM/SQLADM/DBADM with separation of duties; review with your RACF administrator.",
            risk="Unrestricted access to every object in the subsystem.",
            confidence=0.75,
        )
    ]


@rule(
    "ZOS-SEC-002",
    engines=("db2zos",),
    kind="security",
    category="privileges",
    severity="medium",
    title="PUBLIC table privileges",
)
def zos_public(ctx: EvidenceContext) -> list[RuleFinding]:
    n = ctx.security.get("public_table_grants") or 0
    auths = ctx.security.get("public_authorities") or []
    if n <= 0 and not auths:
        return []
    return [
        RuleFinding(
            title="PUBLIC holds privileges",
            finding=f"{n} application table grant(s) and authorities {', '.join(auths) or 'none'} are granted to PUBLIC.",
            evidence={"public_table_grants": n, "public_authorities": auths},
            recommendation="Revoke from PUBLIC and grant to secondary authorization IDs (RACF groups) or roles.",
            risk="Every authorization ID that can connect inherits these privileges.",
            confidence=0.8,
        )
    ]
