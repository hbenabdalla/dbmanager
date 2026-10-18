"""SQL Server showplan XML → engine-neutral :class:`PlanNode` trees.

Estimated plans come from ``SET SHOWPLAN_XML ON`` (the statement is compiled, not run);
actual plans from ``SET STATISTICS XML ON`` (the statement runs). Missing-index hints in the
plan are surfaced as *evidence* only: the optimizer's suggestions ignore existing similar
indexes and write costs.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET  # noqa: S405 - plan XML produced by the target server, size-limited
from typing import Any

from app.domain.plan_analysis import PlanNode

NS = "{http://schemas.microsoft.com/sqlserver/2004/07/showplan}"
MAX_PLAN_BYTES = 20 * 1024 * 1024


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


class _Converter:
    def __init__(self) -> None:
        self.n = 0

    def relop(self, el: ET.Element) -> PlanNode:
        self.n += 1
        physical, logical = el.get("PhysicalOp", "?"), el.get("LogicalOp", "")
        obj = el.find(f".//{NS}Object")
        relation = index = None
        # only the Object that belongs to this operator (not to a nested RelOp)
        for child in el:
            o = child.find(f"{NS}Object") if _local(child.tag) != "RelOp" else None
            if o is not None:
                obj = o
                break
        else:
            obj = None
        if obj is not None:
            relation = ".".join(x.strip("[]") for x in (obj.get("Schema"), obj.get("Table")) if x) or None
            index = (obj.get("Index") or "").strip("[]") or None
        est_rows = _f(el.get("EstimateRows"))
        rebinds = (_f(el.get("EstimateRebinds")) or 0) + (_f(el.get("EstimateRewinds")) or 0)
        actual_rows = actual_ms = loops = None
        rti = el.find(f"{NS}RunTimeInformation")
        if rti is not None:
            threads = rti.findall(f"{NS}RunTimeCountersPerThread")
            actual_rows = sum(_f(t.get("ActualRows")) or 0 for t in threads)
            loops = sum(_f(t.get("ActualExecutions")) or 0 for t in threads)
            ms = [_f(t.get("ActualElapsedms")) for t in threads if t.get("ActualElapsedms") is not None]
            actual_ms = max(ms) if ms else None
        warnings: list[dict[str, Any]] = []
        w = el.find(f"{NS}Warnings")
        if w is not None:
            for spill in w.findall(f"{NS}SpillToTempDb"):
                warnings.append(
                    {
                        "code": "TEMP_SPILL",
                        "severity": "medium",
                        "title": "Spill to tempdb",
                        "detail": f"{physical} spilled to tempdb (spill level {spill.get('SpillLevel', '?')}).",
                    }
                )
            if w.get("NoJoinPredicate") == "true":
                warnings.append(
                    {
                        "code": "NO_JOIN_PREDICATE",
                        "severity": "high",
                        "title": "Join without predicate",
                        "detail": "Cartesian product.",
                    }
                )
            for conv in w.findall(f"{NS}PlanAffectingConvert"):
                warnings.append(
                    {
                        "code": "IMPLICIT_CONVERSION",
                        "severity": "medium",
                        "title": "Implicit conversion affects the plan",
                        "detail": f"{conv.get('ConvertIssue')}: {conv.get('Expression')}",
                    }
                )
        predicate = None
        for child in el:
            p = child.find(f"{NS}Predicate")
            if p is not None and _local(child.tag) != "RelOp":
                so = p.find(f".//{NS}ScalarOperator")
                predicate = so.get("ScalarString") if so is not None else None
                break
        node = PlanNode(
            id=self.n,
            operator=physical,
            detail=logical if logical and logical != physical else None,
            relation=relation,
            index=index,
            total_cost=_f(el.get("EstimatedTotalSubtreeCost")),
            estimated_rows=est_rows * (1 + rebinds) if est_rows is not None else None,
            actual_rows=actual_rows,
            actual_total_ms=actual_ms,
            loops=loops,
            filter=predicate,
            raw={
                "physical_op": physical,
                "logical_op": logical,
                "estimate_io": _f(el.get("EstimateIO")),
                "estimate_cpu": _f(el.get("EstimateCPU")),
                "parallel": el.get("Parallel") == "true",
                "engine_warnings": warnings,
            },
        )
        # child operators are RelOp elements nested one level below an operator-specific element
        for child in el:
            for sub in child.findall(f"{NS}RelOp"):
                node.children.append(self.relop(sub))
        if _local(el.tag) == "RelOp" and not node.children:
            for sub in el.findall(f"./*/*/{NS}RelOp"):
                node.children.append(self.relop(sub))
        return node


def parse_showplan(xml_text: str) -> tuple[PlanNode | None, list[dict[str, Any]], dict[str, Any]]:
    """Returns (root, missing-index hints, statement attributes)."""
    if len(xml_text.encode("utf-8", "ignore")) > MAX_PLAN_BYTES:
        raise ValueError("execution plan too large to display")
    doc = ET.fromstring(xml_text)  # noqa: S314
    stmt = doc.find(f".//{NS}StmtSimple")
    qp = doc.find(f".//{NS}QueryPlan")
    attrs = {
        k: v
        for k, v in (stmt.attrib if stmt is not None else {}).items()
        if k.startswith(("Statement", "Query", "Cardinality"))
    }
    missing = []
    for g in doc.findall(f".//{NS}MissingIndexGroup"):
        for mi in g.findall(f"{NS}MissingIndex"):
            cols: dict[str, list[str]] = {}
            for cg in mi.findall(f"{NS}ColumnGroup"):
                cols[cg.get("Usage", "?").lower()] = [c.get("Name", "").strip("[]") for c in cg.findall(f"{NS}Column")]
            missing.append(
                {
                    "impact": _f(g.get("Impact")),
                    "table": ".".join(
                        x.strip("[]") for x in (mi.get("Database"), mi.get("Schema"), mi.get("Table")) if x
                    ),
                    **cols,
                }
            )
    if qp is None:
        return None, missing, attrs
    first = qp.find(f"{NS}RelOp")
    if first is None:
        return None, missing, attrs
    root = _Converter().relop(first)
    if stmt is not None and stmt.get("StatementSubTreeCost") and root.total_cost is None:
        root.total_cost = _f(stmt.get("StatementSubTreeCost"))
    return root, missing, attrs
