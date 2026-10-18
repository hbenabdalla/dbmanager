import { AlertTriangle } from "lucide-react";
import type { PlanIssue, PlanNode } from "@/api/types";
import { compactNum, ms } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Badge } from "./ui/badge";

function Node({ node, depth, issues, analyzed }: { node: PlanNode; depth: number; issues: Map<number, PlanIssue[]>; analyzed: boolean }) {
  const mine = issues.get(node.id) ?? [];
  const heat = node.self_cost_pct ?? 0;
  return (
    <li>
      <div className={cn("group relative grid grid-cols-[1fr_auto] gap-3 rounded-md border px-3 py-2", mine.length ? "border-warn/40 bg-warn-soft/40" : "border-border bg-surface")}
        style={{ marginLeft: depth * 20 }}>
        <span className="absolute inset-y-0 left-0 w-1 rounded-l-md" style={{ background: `color-mix(in srgb, var(--crit) ${Math.min(heat, 100)}%, var(--border))` }} aria-hidden />
        <div className="min-w-0 pl-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{node.operator}</span>
            {node.relation && <span className="font-mono text-[12px] text-text-2">{node.relation}</span>}
            {node.index && <Badge tone="accent">index {node.index}</Badge>}
            {node.sort_space_type === "Disk" && <Badge tone="crit">spilled to disk</Badge>}
          </div>
          {(node.detail || node.filter) && <div className="mt-0.5 truncate font-mono text-[11.5px] text-muted" title={[node.detail, node.filter && `Filter: ${node.filter}`].filter(Boolean).join("; ")}>
            {[node.detail, node.filter && `Filter: ${node.filter}`].filter(Boolean).join("; ")}
          </div>}
          {mine.map((i) => (
            <div key={i.code} className="mt-1 flex items-start gap-1.5 text-[12px] text-warn"><AlertTriangle className="mt-0.5 size-3.5 shrink-0" /><span><span className="font-medium">{i.title}.</span> {i.detail}</span></div>
          ))}
        </div>
        <div className="text-right text-[12px] tabular text-text-2">
          <div>cost {compactNum(node.total_cost)}{node.self_cost_pct !== null && node.self_cost_pct >= 1 ? <span className="text-muted"> ({node.self_cost_pct}%)</span> : null}</div>
          <div className="text-muted">rows {compactNum(node.estimated_rows)}{analyzed && node.actual_rows !== null ? <> est, <span className="text-text-2">{compactNum(node.actual_rows)}</span> actual</> : " est"}</div>
          {analyzed && node.actual_total_ms !== null && <div className="text-muted">{ms(node.actual_total_ms)}{node.loops && node.loops > 1 ? ` × ${compactNum(node.loops)} loops` : ""}</div>}
        </div>
      </div>
      {node.children.length > 0 && (
        <ul className="mt-1.5 space-y-1.5">{node.children.map((c) => <Node key={c.id} node={c} depth={depth + 1} issues={issues} analyzed={analyzed} />)}</ul>
      )}
    </li>
  );
}

export function PlanTree({ root, issues, analyzed }: { root: PlanNode; issues: PlanIssue[]; analyzed: boolean }) {
  const byNode = new Map<number, PlanIssue[]>();
  for (const i of issues) byNode.set(i.node_id, [...(byNode.get(i.node_id) ?? []), i]);
  return <ul className="space-y-1.5 text-[13px]" aria-label="Execution plan"><Node node={root} depth={0} issues={byNode} analyzed={analyzed} /></ul>;
}
