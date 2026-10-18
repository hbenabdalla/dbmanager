"""MySQL / MariaDB execution plans → engine-neutral :class:`PlanNode` trees.

* MySQL 8.0.16+: ``EXPLAIN FORMAT=TREE`` (estimates) and ``EXPLAIN ANALYZE`` (8.0.18+,
  executes the statement) share one indented text format.
* MariaDB and MySQL < 8.0.16: ``EXPLAIN FORMAT=JSON`` / MariaDB ``ANALYZE FORMAT=JSON``.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.plan_analysis import PlanNode

_TREE_LINE = re.compile(
    r"^(?P<indent>\s*)-> (?P<desc>.*?)"
    r"(?:\s+\(cost=(?P<c1>[\d.e+]+)(?:\.\.(?P<c2>[\d.e+]+))?\s+rows=(?P<rows>[\d.e+]+)\))?"
    r"(?:\s+\(actual time=(?P<t1>[\d.e+]+)\.\.(?P<t2>[\d.e+]+)\s+rows=(?P<arows>[\d.e+]+)\s+loops=(?P<loops>[\d.e+]+)\))?"
    r"(?P<never>\s+\(never executed\))?\s*$"
)
_ON = re.compile(r"^(?P<op>.+?) on (?P<rel>[`\w.$#<>]+)(?: using (?P<idx>[`\w.$#<>]+))?(?P<rest>.*)$")

ACCESS = {
    "ALL": "Full Table Scan",
    "index": "Full Index Scan",
    "range": "Index Range Scan",
    "ref": "Index Lookup",
    "eq_ref": "Unique Index Lookup",
    "ref_or_null": "Index Lookup (or NULL)",
    "const": "Const Row",
    "system": "System Row",
    "fulltext": "Fulltext Index",
    "index_merge": "Index Merge",
    "unique_subquery": "Unique Subquery",
    "index_subquery": "Index Subquery",
    "hash": "Hash Join Lookup",
}


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def parse_tree(text: str) -> PlanNode | None:
    """Parse MySQL ``FORMAT=TREE`` / ``EXPLAIN ANALYZE`` output."""
    counter = 0
    root: PlanNode | None = None
    stack: list[tuple[int, PlanNode]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        m = _TREE_LINE.match(line)
        if not m:
            if stack:  # continuation of a long description
                stack[-1][1].detail = ((stack[-1][1].detail or "") + " " + line.strip()).strip()
            continue
        counter += 1
        desc = m.group("desc").strip()
        op, relation, index, detail, flt = desc, None, None, None, None
        om = _ON.match(desc)
        if om and not desc.startswith(("Filter:", "Sort:")):
            op, relation, index = om.group("op"), om.group("rel").strip("`"), (om.group("idx") or "").strip("`") or None
            detail = om.group("rest").strip() or None
        elif ": " in desc:
            op, detail = desc.split(": ", 1)
            if op == "Filter":
                flt = detail
        node = PlanNode(
            id=counter,
            operator=op,
            detail=detail,
            relation=relation,
            index=index,
            startup_cost=_f(m.group("c1")) if m.group("c2") else None,
            total_cost=_f(m.group("c2") or m.group("c1")),
            estimated_rows=_f(m.group("rows")),
            actual_rows=_f(m.group("arows")),
            actual_total_ms=_f(m.group("t2")),
            loops=_f(m.group("loops")),
            filter=flt,
            raw={"line": desc, "never_executed": bool(m.group("never"))},
        )
        if node.operator.lower().startswith("table scan"):
            node.operator = "Table scan"
        depth = len(m.group("indent"))
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if stack:
            stack[-1][1].children.append(node)
        else:
            root = root or node
        stack.append((depth, node))
    return root


class _JsonConverter:
    def __init__(self) -> None:
        self.n = 0

    def node(self, op: str, **kw: Any) -> PlanNode:
        self.n += 1
        return PlanNode(id=self.n, operator=op, **kw)

    def table(self, t: dict[str, Any]) -> PlanNode:
        access = t.get("access_type") or "?"
        cost = t.get("cost_info") or {}
        rows = t.get("rows_examined_per_scan", t.get("rows"))
        extra = []
        for k in (
            "using_index",
            "using_temporary_table",
            "using_filesort",
            "using_join_buffer",
            "using_index_condition",
        ):
            if t.get(k):
                extra.append(k.replace("_", " "))
        n = self.node(
            ACCESS.get(access, access),
            relation=t.get("table_name"),
            index=t.get("key"),
            detail=", ".join(
                x
                for x in (
                    f"key columns: {', '.join(t['used_key_parts'])}" if t.get("used_key_parts") else None,
                    f"ref: {', '.join(map(str, t['ref']))}" if t.get("ref") else None,
                    "; ".join(extra) or None,
                )
                if x
            )
            or None,
            total_cost=_f(cost.get("prefix_cost") or cost.get("read_cost") or t.get("cost")),
            estimated_rows=_f(rows),
            actual_rows=_f(t.get("r_rows")),
            loops=_f(t.get("r_loops")),
            actual_total_ms=_f(t.get("r_total_time_ms")),
            filter=t.get("attached_condition"),
            raw={k: v for k, v in t.items() if not isinstance(v, (dict, list))},
        )
        if n.actual_rows is not None and t.get("r_filtered") is not None and n.actual_rows:
            kept = n.actual_rows * float(t["r_filtered"]) / 100.0
            n.rows_removed_by_filter = max(n.actual_rows - kept, 0.0)
        for key in ("materialized_from_subquery", "attached_subqueries"):
            sub = t.get(key)
            if isinstance(sub, dict):
                n.children.extend(self.walk(sub))
            elif isinstance(sub, list):
                for s in sub:
                    n.children.extend(self.walk(s))
        return n

    def walk(self, d: Any) -> list[PlanNode]:
        if isinstance(d, list):
            out: list[PlanNode] = []
            for x in d:
                out.extend(self.walk(x))
            return out
        if not isinstance(d, dict):
            return []
        out = []
        for key, val in d.items():
            if key == "query_block":
                qb = self.node(
                    f"Query block #{val.get('select_id', '?')}",
                    total_cost=_f((val.get("cost_info") or {}).get("query_cost") or val.get("cost")),
                    actual_total_ms=_f(val.get("r_total_time_ms")),
                )
                qb.children = self.walk({k: v for k, v in val.items() if k not in ("select_id", "cost_info", "cost")})
                out.append(qb)
            elif key == "table" and isinstance(val, dict):
                out.append(self.table(val))
            elif key == "nested_loop":
                nl = self.node("Nested Loop")
                nl.children = self.walk(val)
                if len(nl.children) == 1:
                    out.extend(nl.children)
                else:
                    out.append(nl)
            elif key in ("block-nl-join", "bka-join"):
                n = self.node("Block Nested Loop" if key == "block-nl-join" else "Batched Key Access")
                n.children = self.walk(val)
                out.append(n)
            elif key in ("ordering_operation", "filesort", "read_sorted_file"):
                n = self.node(
                    "Sort",
                    detail=(val.get("sort_key") if isinstance(val, dict) else None),
                    actual_rows=_f(val.get("r_output_rows")) if isinstance(val, dict) else None,
                )
                if isinstance(val, dict) and (val.get("using_temporary_table") or "temporary_table" in val):
                    n.detail = ((n.detail or "") + " using temporary table").strip()
                n.children = (
                    self.walk({k: v for k, v in val.items() if isinstance(v, (dict, list))})
                    if isinstance(val, dict)
                    else []
                )
                out.append(n)
            elif key == "grouping_operation":
                n = self.node("Aggregate")
                n.children = self.walk(val)
                out.append(n)
            elif key == "temporary_table":
                n = self.node("Temporary Table")
                n.children = self.walk(val)
                out.append(n)
            elif key == "duplicates_removal":
                n = self.node("Distinct")
                n.children = self.walk(val)
                out.append(n)
            elif key == "union_result":
                n = self.node("Union", relation=val.get("table_name") if isinstance(val, dict) else None)
                specs = (
                    (val.get("query_specifications") or val.get("query_blocks") or []) if isinstance(val, dict) else []
                )
                n.children = self.walk(specs)
                out.append(n)
            elif key in ("subqueries", "optimized_away_subqueries", "select_list_subqueries", "having_subqueries"):
                for s in val if isinstance(val, list) else [val]:
                    sq = self.node("Subquery")
                    sq.children = self.walk(s)
                    out.append(sq)
            elif isinstance(val, (dict, list)) and key not in ("cost_info",):
                out.extend(self.walk(val))
        return out


def parse_json(doc: dict[str, Any]) -> PlanNode | None:
    nodes = _JsonConverter().walk(doc)
    if not nodes:
        return None
    if len(nodes) == 1:
        return nodes[0]
    root = PlanNode(id=0, operator="Statement")
    root.children = nodes
    return root
