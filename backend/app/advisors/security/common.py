"""Security rules — Engine-neutral (platform view)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.advisors.engine import EvidenceContext, RuleFinding, rule


def _roles(ctx: EvidenceContext) -> list[dict]:
    return ctx.security.get("roles") or []


@rule(
    "GEN-SEC-001",
    engines=("*",),
    kind="security",
    category="encryption",
    severity="medium",
    title="Platform connects without TLS",
)
def platform_tls(ctx: EvidenceContext) -> list[RuleFinding]:
    # Simulated servers are evaluated on their simulated TLS configuration like any other.
    if ctx.tls_mode not in ("disable", "allow", "prefer"):
        return []
    return [
        RuleFinding(
            title="KBM DBManager's connection to this server is not guaranteed to be encrypted",
            finding=f"The server is registered with TLS mode '{ctx.tls_mode}', which permits unencrypted connections.",
            evidence={"tls_mode": ctx.tls_mode},
            recommendation="Set TLS mode to verify-full (or at least require) and provide the CA certificate.",
            risk="Credentials and query results may traverse the network in clear text.",
            confidence=1.0,
            severity="high" if ctx.environment_tier == "production" else "medium",
            remediation="Edit the server → Connection → TLS mode = verify-full; paste the CA PEM.",
        )
    ]


@rule(
    "GEN-SEC-002",
    engines=("*",),
    kind="security",
    category="accounts",
    severity="low",
    title="Dormant privileged accounts",
)
def dormant_accounts(ctx: EvidenceContext) -> list[RuleFinding]:
    now = datetime.now(UTC)
    out = []
    for r in _roles(ctx):
        last = r.get("last_login_at")
        if not last or not r.get("can_login"):
            continue
        days = (now - datetime.fromisoformat(last)).days
        if days < 90:
            continue
        admin = bool(r.get("is_superuser") or r.get("admin_reasons"))
        out.append(
            RuleFinding(
                title=f"Account {r['name']} has not logged in for {days} days",
                finding=f"{r['name']} last authenticated {days} days ago{' and holds administrative privileges' if admin else ''}.",
                evidence={
                    "account": r["name"],
                    "last_login_at": last,
                    "days_inactive": days,
                    "admin_reasons": r.get("admin_reasons"),
                },
                recommendation="Confirm with the owner and lock or drop the account if it is no longer required.",
                risk="Unused accounts are attractive targets for credential stuffing.",
                confidence=0.8,
                affected_object=r["name"],
                severity="high" if admin else "low",
                remediation="Lock the account (e.g. ALTER USER ... ACCOUNT LOCK / NOLOGIN). Not applied automatically.",
            )
        )
    return out
