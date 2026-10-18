"""Oracle plan rows (PLAN_TABLE from EXPLAIN PLAN, or V$SQL_PLAN_STATISTICS_ALL after a run
with the GATHER_PLAN_STATISTICS hint) → engine-neutral :class:`PlanNode` trees."""

from __future__ import annotations

from typing import Any

from app.adapters.dbapi import num, text
from app.domain.plan_analysis import PlanNode


def build_tree(rows: list[dict[str, Any]], *, actual: bool = False) -> PlanNode | None:
    nodes: dict[int, PlanNode] = {}
    order: list[tuple[int, int | None]] = []
    for r in rows:
        nid = int(r["id"])
        op = " ".join(x for x in (text(r.get("operation")), text(r.get("options"))) if x)
        otype = (text(r.get("object_type")) or "").upper()
        name = text(r.get("object_name"))
        owner = text(r.get("object_owner"))
        relation = index = None
        if name:
            if otype.startswith("INDEX") or (text(r.get("operation")) or "").startswith("INDEX"):
                index = name
            else:
                relation = f"{owner}.{name}" if owner else name
        preds = [p for p in (text(r.get("access_predicates")), text(r.get("filter_predicates"))) if p]
        node = PlanNode(
            id=nid + 1,
            operator=op or "?",
            relation=relation,
            index=index,
            total_cost=num(r.get("cost")),
            estimated_rows=num(r.get("cardinality")),
            filter=" AND ".join(preds) if preds else None,
            raw={
                "operation": text(r.get("operation")),
                "options": text(r.get("options")),
                "object_type": otype or None,
                "access_predicates": text(r.get("access_predicates")),
                "filter_predicates": text(r.get("filter_predicates")),
                "bytes": num(r.get("bytes")),
                "partition_start": text(r.get("partition_start")),
                "partition_stop": text(r.get("partition_stop")),
                "engine_warnings": [],
            },
        )
        if actual:
            node.actual_rows = num(r.get("last_output_rows"))
            node.loops = num(r.get("last_starts"))
            elapsed = num(r.get("last_elapsed_time"))
            node.actual_total_ms = elapsed / 1000.0 if elapsed is not None else None
            node.shared_hit = (
                int(num(r.get("last_cr_buffer_gets"), 0) or 0) if r.get("last_cr_buffer_gets") is not None else None
            )
            node.shared_read = (
                int(num(r.get("last_disk_reads"), 0) or 0) if r.get("last_disk_reads") is not None else None
            )
            temp = num(r.get("last_tempseg_size"))
            if temp:
                node.temp_written = int(temp)
                node.raw["engine_warnings"].append(
                    {
                        "code": "TEMP_SPILL",
                        "severity": "medium",
                        "title": "Work area spilled to temporary tablespace",
                        "detail": f"{op} used {int(temp):,} bytes of temp space.",
                    }
                )
        if op == "MERGE JOIN CARTESIAN":
            node.raw["engine_warnings"].append(
                {
                    "code": "CARTESIAN_JOIN",
                    "severity": "high",
                    "title": "Cartesian join",
                    "detail": "MERGE JOIN CARTESIAN in the plan.",
                }
            )
        nodes[nid] = node
        pid = r.get("parent_id")
        order.append((nid, int(pid) if pid is not None else None))
    root = None
    for nid, pid in order:
        if pid is None or pid not in nodes:
            root = root or nodes[nid]
        else:
            nodes[pid].children.append(nodes[nid])
    return root
