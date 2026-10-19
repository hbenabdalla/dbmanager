"""Performance rules — Db2 for z/OS (real-time statistics; recommendations only)."""

from __future__ import annotations

from app.advisors.engine import EvidenceContext, RuleFinding, rule


def _recommend(ctx: EvidenceContext, key: str, utility: str, why: str, rule_risk: str) -> list[RuleFinding]:
    items = ctx.tuning.get(key) or []
    if not items:
        return []
    return [
        RuleFinding(
            title=f"{utility} recommended for {len(items)} object(s)",
            finding=why,
            evidence={"objects": items[:30], "thresholds": ctx.tuning.get("thresholds")},
            recommendation=f"Schedule {utility} for these objects through your z/OS utility jobs "
            "(DBManager only recommends; it never runs utilities).",
            expected_impact="Keeps access paths and recovery times healthy.",
            risk=rule_risk,
            confidence=0.7,
        )
    ]


@rule(
    "ZOS-PERF-001",
    engines=("db2zos",),
    kind="performance",
    category="maintenance",
    severity="medium",
    title="REORG indicated by real-time statistics",
)
def zos_reorg(ctx: EvidenceContext) -> list[RuleFinding]:
    return _recommend(
        ctx,
        "reorg_recommended",
        "REORG TABLESPACE",
        "Inserts/deletes or unclustered inserts since the last REORG exceed DSNACCOX-style thresholds.",
        "REORG SHRLEVEL CHANGE still needs a drain at the switch phase.",
    )


@rule(
    "ZOS-PERF-002",
    engines=("db2zos",),
    kind="performance",
    category="statistics",
    severity="low",
    title="RUNSTATS indicated by real-time statistics",
)
def zos_runstats(ctx: EvidenceContext) -> list[RuleFinding]:
    return _recommend(
        ctx,
        "runstats_recommended",
        "RUNSTATS",
        "More than 20% of rows changed since the last RUNSTATS (or statistics were never collected).",
        "New statistics can change access paths at the next BIND/REBIND or dynamic prepare.",
    )


@rule(
    "ZOS-PERF-003",
    engines=("db2zos",),
    kind="performance",
    category="recoverability",
    severity="medium",
    title="Image COPY overdue",
)
def zos_copy(ctx: EvidenceContext) -> list[RuleFinding]:
    return _recommend(
        ctx,
        "copy_recommended",
        "COPY",
        "Pages were updated and the last image copy is older than 7 days (or missing).",
        "Longer recovery from the log if the object must be recovered.",
    )
