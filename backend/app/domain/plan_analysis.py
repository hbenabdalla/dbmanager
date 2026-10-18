"""Engine-neutral execution plan model and issue detection.

Adapters convert their native plan format into :class:`PlanNode` trees. Detection rules
here operate only on the normalized tree, so they apply to every engine whose adapter
fills the relevant fields.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PlanNode(BaseModel):
    id: int
    operator: str
    detail: str | None = None
    relation: str | None = None
    index: str | None = None
    startup_cost: float | None = None
    total_cost: float | None = None
    estimated_rows: float | None = None
    actual_rows: float | None = None
    actual_total_ms: float | None = None
    loops: float | None = None
    shared_hit: int | None = None
    shared_read: int | None = None
    temp_written: int | None = None
    sort_space_type: str | None = None
    filter: str | None = None
    rows_removed_by_filter: float | None = None
    self_cost_pct: float | None = None
    warnings: list[str] = Field(default_factory=list)
    children: list[PlanNode] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)


class PlanIssue(BaseModel):
    node_id: int
    severity: str  # high|medium|low
    code: str
    title: str
    detail: str


class ExplainResult(BaseModel):
    engine: str
    mode: str  # estimate|analyze
    executed: bool
    root: PlanNode | None
    issues: list[PlanIssue] = Field(default_factory=list)
    total_cost: float | None = None
    planning_ms: float | None = None
    execution_ms: float | None = None
    raw: Any = None
    notes: list[str] = Field(default_factory=list)


def _walk(node: PlanNode) -> list[PlanNode]:
    out = [node]
    for c in node.children:
        out.extend(_walk(c))
    return out


def analyze_plan(root: PlanNode, analyzed: bool) -> list[PlanIssue]:
    issues: list[PlanIssue] = []
    nodes = _walk(root)
    total = root.total_cost or 0.0

    for n in nodes:
        child_cost = sum((c.total_cost or 0.0) for c in n.children)
        if total > 0 and n.total_cost is not None:
            n.self_cost_pct = round(max(0.0, n.total_cost - child_cost) / total * 100, 1)

        op = n.operator.lower()
        rows = n.actual_rows if analyzed and n.actual_rows is not None else n.estimated_rows

        for w in n.raw.get("engine_warnings") or []:
            issues.append(
                PlanIssue(
                    node_id=n.id,
                    severity=w.get("severity", "medium"),
                    code=w["code"],
                    title=w["title"],
                    detail=w["detail"],
                )
            )
            n.warnings.append(w["title"].lower())

        if (rows or 0) > 50_000 and ("key lookup" in op or "rid lookup" in op or "table access by index rowid" in op):
            issues.append(
                PlanIssue(
                    node_id=n.id,
                    severity="medium",
                    code="KEY_LOOKUP",
                    title="Many row lookups after an index access",
                    detail=f"~{int(rows or 0):,} rows are fetched from the base table one by one; a covering index "
                    "(INCLUDE columns) can remove the lookups.",
                )
            )
            n.warnings.append("lookups")

        full = (
            "seq scan" in op
            or "full scan" in op
            or "table scan" in op
            or "access full" in op
            or "clustered index scan" in op
            or op.startswith("tbscan")
            or (op == "index scan" and n.raw.get("physical_op") == "Index Scan")
        )
        if full and (rows or 0) > 10_000:
            msg = f"Full scan of {n.relation or 'relation'} reading ~{int(rows or 0):,} rows"
            if n.filter:
                msg += f" with filter {n.filter}; an index on the filtered columns may help"
            issues.append(
                PlanIssue(
                    node_id=n.id,
                    severity="medium" if (rows or 0) < 1_000_000 else "high",
                    code="FULL_SCAN",
                    title="Sequential / full table scan",
                    detail=msg,
                )
            )
            n.warnings.append("full scan")

        if n.rows_removed_by_filter and rows is not None and n.rows_removed_by_filter > 10 * max(rows, 1):
            issues.append(
                PlanIssue(
                    node_id=n.id,
                    severity="medium",
                    code="INEFFICIENT_FILTER",
                    title="Filter discards most rows",
                    detail=f"{int(n.rows_removed_by_filter):,} rows removed by filter vs {int(rows):,} kept — "
                    "an index or a more selective access path could avoid reading them.",
                )
            )
            n.warnings.append("inefficient filter")

        if "sort" in op and (n.sort_space_type or "").lower() == "disk":
            issues.append(
                PlanIssue(
                    node_id=n.id,
                    severity="high",
                    code="SORT_SPILL",
                    title="Sort spilled to disk",
                    detail="The sort exceeded work memory and used temporary files.",
                )
            )
            n.warnings.append("disk sort")
        elif "sort" in op and (rows or 0) > 1_000_000:
            issues.append(
                PlanIssue(
                    node_id=n.id,
                    severity="low",
                    code="LARGE_SORT",
                    title="Large sort",
                    detail=f"Sorting ~{int(rows or 0):,} rows.",
                )
            )

        own_temp = (n.temp_written or 0) - sum((c.temp_written or 0) for c in n.children)  # PG reports inclusive counts
        if own_temp > 0:
            issues.append(
                PlanIssue(
                    node_id=n.id,
                    severity="medium",
                    code="TEMP_SPILL",
                    title="Temporary spill",
                    detail=f"{own_temp:,} temp blocks written.",
                )
            )
            n.warnings.append("temp spill")

        if "nested loop" in op and len(n.children) == 2:
            outer = n.children[0]
            outer_rows = (outer.actual_rows if analyzed else outer.estimated_rows) or 0
            loops_rows = outer_rows * (outer.loops or 1)
            if loops_rows > 50_000:
                issues.append(
                    PlanIssue(
                        node_id=n.id,
                        severity="medium",
                        code="EXPENSIVE_NESTED_LOOP",
                        title="Expensive nested loop",
                        detail=f"Inner side executed for ~{int(loops_rows):,} outer rows; a hash or merge join "
                        "or a better index on the join key may be cheaper.",
                    )
                )
                n.warnings.append("expensive join")

        if analyzed and n.actual_rows is not None and n.estimated_rows:
            actual = max(n.actual_rows, 1)
            est = max(n.estimated_rows, 1)
            ratio = max(actual / est, est / actual)
            if ratio >= 100 and max(actual, est) > 1000:
                issues.append(
                    PlanIssue(
                        node_id=n.id,
                        severity="medium",
                        code="CARDINALITY_MISESTIMATE",
                        title="Poor cardinality estimate",
                        detail=f"Estimated {int(est):,} rows, actual {int(actual):,} ({ratio:,.0f}x off). "
                        "Stale or insufficient statistics can cause bad join orders.",
                    )
                )
                n.warnings.append("misestimate")

        if (n.shared_read or 0) > 100_000:
            issues.append(
                PlanIssue(
                    node_id=n.id,
                    severity="medium",
                    code="EXCESSIVE_READS",
                    title="Excessive reads",
                    detail=f"{n.shared_read:,} blocks read from disk/OS cache.",
                )
            )

        if n.self_cost_pct is not None and n.self_cost_pct >= 60 and total > 1000:
            issues.append(
                PlanIssue(
                    node_id=n.id,
                    severity="low",
                    code="DOMINANT_OPERATOR",
                    title="Dominant operator",
                    detail=f"{n.operator} accounts for {n.self_cost_pct}% of the estimated cost.",
                )
            )
    return issues
