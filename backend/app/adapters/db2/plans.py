"""Db2 explain tables (EXPLAIN_OPERATOR + EXPLAIN_STREAM + EXPLAIN_PREDICATE) → PlanNode tree.

A stream from operator A to operator B means A feeds B (A is B's input). Streams whose
source is a data object (SOURCE_TYPE = 'D') name the table or index an operator reads.
"""

from __future__ import annotations

from typing import Any

from app.adapters.dbapi import num, text
from app.domain.plan_analysis import PlanNode


def build_tree(
    operators: list[dict[str, Any]], streams: list[dict[str, Any]], predicates: list[dict[str, Any]]
) -> PlanNode | None:
    nodes: dict[int, PlanNode] = {}
    for o in operators:
        oid = int(o["operator_id"])
        nodes[oid] = PlanNode(
            id=oid,
            operator=text(o["operator_type"]) or "?",
            total_cost=num(o.get("total_cost")),
            raw={
                "io_cost": num(o.get("io_cost")),
                "cpu_cost": num(o.get("cpu_cost")),
                "first_row_cost": num(o.get("first_row_cost")),
                "engine_warnings": [],
            },
        )
    has_parent: set[int] = set()
    for s in streams:
        src_t, tgt_t = text(s.get("source_type")), text(s.get("target_type"))
        tgt = int(s["target_id"]) if s.get("target_id") is not None else None
        src = int(s["source_id"]) if s.get("source_id") is not None else None
        if src_t == "O" and tgt_t == "O" and src in nodes and tgt in nodes:
            nodes[tgt].children.append(nodes[src])
            nodes[src].estimated_rows = num(s.get("stream_count"))
            has_parent.add(src)
        elif src_t == "D" and tgt in nodes:
            obj = f"{text(s.get('object_schema'))}.{text(s.get('object_name'))}"
            node = nodes[tgt]
            if node.operator in ("IXSCAN", "EISCAN", "RIDSCN"):
                node.index = obj
            else:
                node.relation = node.relation or obj
            if node.operator == "TBSCAN":
                node.raw["table_rows"] = num(s.get("stream_count"))
    preds: dict[int, list[str]] = {}
    for p in predicates:
        preds.setdefault(int(p["operator_id"]), []).append(
            f"{text(p.get('how_applied'))}: {text(p.get('predicate_text'))}"
        )
    for oid, items in preds.items():
        if oid in nodes:
            nodes[oid].filter = " AND ".join(items)
    for n in nodes.values():
        if n.operator == "TBSCAN" and n.estimated_rows is None:
            n.estimated_rows = n.raw.get("table_rows")
    roots = [n for oid, n in nodes.items() if oid not in has_parent]
    roots.sort(key=lambda n: (n.operator != "RETURN", n.id))
    return roots[0] if roots else None
