/**
 * Graphical execution plan (React Flow) plus a viewer with Graph / Tree / Raw vendor plan tabs.
 * Data flows from the leaves (scans) up to the root, so edges point child → parent and their
 * width grows with the rows they carry.
 */
import {
  Background, BackgroundVariant, Controls, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider,
  type Edge, type Node, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { AlertTriangle, Copy } from "lucide-react";
import { memo, useMemo, useState } from "react";
import { toast } from "sonner";
import type { PlanIssue, PlanNode } from "@/api/types";
import { keepPointerEvents, FlowStyles } from "@/components/flow/flow-utils";
import { CodeBlock, KeyValue } from "@/components/page";
import { PlanTree } from "@/components/plan-tree";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { compactNum, ms, num } from "@/lib/format";
import { cn } from "@/lib/utils";

const NODE_W = 236;
const NODE_H = 92;
const GAP_X = 28;
const GAP_Y = 64;

/** Estimated vs actual rows: ratio ≥ 10 either way is a misestimate worth flagging. */
export function misestimate(n: PlanNode): number | null {
  if (n.actual_rows == null || n.estimated_rows == null) return null;
  const a = Math.max(n.actual_rows, 1);
  const e = Math.max(n.estimated_rows, 1);
  const r = a / e;
  return r >= 10 || r <= 0.1 ? r : null;
}

export interface PlanLayout { node: PlanNode; x: number; y: number; depth: number }

/** Top-down tidy layout: leaves take consecutive slots, parents centre over their children. */
export function layoutPlan(root: PlanNode): { items: PlanLayout[]; links: { parent: number; child: number; rows: number | null }[] } {
  const items: PlanLayout[] = [];
  const links: { parent: number; child: number; rows: number | null }[] = [];
  let slot = 0;
  const place = (n: PlanNode, depth: number): number => {
    const xs = n.children.map((c) => {
      links.push({ parent: n.id, child: c.id, rows: c.actual_rows ?? c.estimated_rows });
      return place(c, depth + 1);
    });
    const x = xs.length ? (xs[0] + xs[xs.length - 1]) / 2 : slot++ * (NODE_W + GAP_X);
    items.push({ node: n, x, y: depth * (NODE_H + GAP_Y), depth });
    return x;
  };
  place(root, 0);
  return { items, links };
}

interface PlanNodeData extends Record<string, unknown> {
  node: PlanNode;
  issues: PlanIssue[];
  analyzed: boolean;
  selected: boolean;
  onSelect: (id: number) => void;
}

const PlanNodeView = memo(function PlanNodeView({ data }: NodeProps<Node<PlanNodeData>>) {
  const { node: n, issues, analyzed, selected, onSelect } = data;
  const heat = Math.min(Math.max(n.self_cost_pct ?? 0, 0), 100);
  const mis = analyzed ? misestimate(n) : null;
  const warn = issues.length > 0 || n.warnings.length > 0 || n.sort_space_type === "Disk";
  return (
    <div className={cn("dbm-flow-node relative rounded-lg border bg-surface shadow-sm", selected ? "border-accent ring-2 ring-[var(--ring)]" : warn ? "border-warn/60" : "border-border")}
      style={{ width: NODE_W, background: `color-mix(in srgb, var(--crit-soft) ${Math.round(heat * 0.9)}%, var(--surface))` }}>
      <Handle type="target" position={Position.Bottom} isConnectable={false} className="!size-1.5 !border-0 !bg-border-strong" />
      <button type="button" onClick={() => onSelect(n.id)} aria-pressed={selected}
        aria-label={`${n.operator}${n.relation ? ` on ${n.relation}` : ""}, ${num(n.self_cost_pct ?? 0, 0)} percent of cost${warn ? ", has warnings" : ""}`}
        className="nodrag nopan block w-full rounded-lg px-3 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-[12.5px] font-medium text-text">{n.operator}</span>
          {warn && <AlertTriangle className="size-3.5 shrink-0 text-warn" aria-hidden />}
          <span className="ml-auto shrink-0 text-2xs font-medium tabular" style={{ color: heat >= 30 ? "var(--crit)" : "var(--muted)" }}>
            {n.self_cost_pct != null ? `${num(n.self_cost_pct, 0)}%` : ""}
          </span>
        </div>
        <div className="truncate font-mono text-[11px] text-text-2">{n.relation ?? n.index ?? n.detail ?? " "}</div>
        <div className="mt-1 flex items-center gap-2 text-2xs tabular text-muted">
          <span>cost {compactNum(n.total_cost)}</span>
          <span>rows {compactNum(n.estimated_rows)}{analyzed && n.actual_rows != null ? ` → ${compactNum(n.actual_rows)}` : ""}</span>
          {analyzed && n.actual_total_ms != null && <span className="ml-auto">{ms(n.actual_total_ms)}</span>}
        </div>
        {mis !== null && (
          <div className="mt-1"><Badge tone="warn">{mis >= 1 ? `${compactNum(mis)}× more rows than estimated` : `${compactNum(1 / mis)}× fewer rows than estimated`}</Badge></div>
        )}
      </button>
      <Handle type="source" position={Position.Top} isConnectable={false} className="!size-1.5 !border-0 !bg-border-strong" />
    </div>
  );
});

const nodeTypes = { plan: PlanNodeView };

function find(root: PlanNode, id: number): PlanNode | null {
  if (root.id === id) return root;
  for (const c of root.children) {
    const f = find(c, id);
    if (f) return f;
  }
  return null;
}

function NodeDetails({ node, issues, analyzed }: { node: PlanNode; issues: PlanIssue[]; analyzed: boolean }) {
  const rows: [string, React.ReactNode][] = [
    ["Operator", node.operator],
    ...(node.relation ? [["Relation", <span className="font-mono">{node.relation}</span>] as [string, React.ReactNode]] : []),
    ...(node.index ? [["Index", <span className="font-mono">{node.index}</span>] as [string, React.ReactNode]] : []),
    ["Cost (startup … total)", `${compactNum(node.startup_cost)} … ${compactNum(node.total_cost)}`],
    ["Share of plan cost", node.self_cost_pct != null ? `${num(node.self_cost_pct, 1)}%` : "—"],
    ["Rows estimated", compactNum(node.estimated_rows)],
  ];
  if (analyzed) {
    rows.push(["Rows actual", compactNum(node.actual_rows)]);
    rows.push(["Time", node.actual_total_ms != null ? ms(node.actual_total_ms) : "—"]);
    if (node.loops != null) rows.push(["Loops", compactNum(node.loops)]);
    if (node.shared_hit != null || node.shared_read != null) rows.push(["Buffers hit / read", `${compactNum(node.shared_hit)} / ${compactNum(node.shared_read)}`]);
    if (node.rows_removed_by_filter != null) rows.push(["Rows removed by filter", compactNum(node.rows_removed_by_filter)]);
    if (node.temp_written != null) rows.push(["Temp written", compactNum(node.temp_written)]);
  }
  if (node.sort_space_type) rows.push(["Sort space", node.sort_space_type]);
  if (node.filter) rows.push(["Filter", <span className="font-mono text-[12px]">{node.filter}</span>]);
  if (node.detail) rows.push(["Detail", <span className="font-mono text-[12px]">{node.detail}</span>]);
  return (
    <div className="space-y-3" aria-live="polite">
      <KeyValue items={rows} />
      {[...issues.map((i) => ({ key: i.code, title: i.title, detail: i.detail })), ...node.warnings.map((w, k) => ({ key: `w${k}`, title: w, detail: "" }))].map((i) => (
        <div key={i.key} className="flex items-start gap-1.5 rounded-md border border-warn/40 bg-warn-soft/50 px-2.5 py-1.5 text-[12px] text-warn">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden /><span><span className="font-medium">{i.title}</span>{i.detail ? `. ${i.detail}` : ""}</span>
        </div>
      ))}
    </div>
  );
}

export interface PlanGraphProps {
  root: PlanNode;
  issues?: PlanIssue[];
  /** EXPLAIN ANALYZE / actual plan: show actual rows and times */
  analyzed?: boolean;
  height?: number | string;
}

/** Node graph of a plan with cost heat, estimated vs actual rows, warnings and a details panel. */
export function PlanGraph({ root, issues = [], analyzed = false, height = 460 }: PlanGraphProps) {
  const byNode = useMemo(() => {
    const m = new Map<number, PlanIssue[]>();
    for (const i of issues) m.set(i.node_id, [...(m.get(i.node_id) ?? []), i]);
    return m;
  }, [issues]);
  const hottest = useMemo(() => {
    let best = root;
    const visit = (n: PlanNode) => {
      if ((n.self_cost_pct ?? 0) > (best.self_cost_pct ?? 0)) best = n;
      n.children.forEach(visit);
    };
    visit(root);
    return best.id;
  }, [root]);
  const [selected, setSelected] = useState<number>(hottest);
  const { items, links } = useMemo(() => layoutPlan(root), [root]);
  const maxRows = Math.max(1, ...links.map((l) => l.rows ?? 1));
  const nodes = useMemo<Node<PlanNodeData>[]>(() => items.map((it) => ({
    id: String(it.node.id), type: "plan", position: { x: it.x, y: it.y }, draggable: false, selectable: false, focusable: false,
    data: { node: it.node, issues: byNode.get(it.node.id) ?? [], analyzed, selected: it.node.id === selected, onSelect: setSelected },
  })), [items, byNode, analyzed, selected]);
  const edges = useMemo<Edge[]>(() => links.map((l) => ({
    id: `${l.child}->${l.parent}`, source: String(l.child), target: String(l.parent), type: "smoothstep", focusable: false,
    label: l.rows != null ? compactNum(l.rows) : undefined,
    labelStyle: { fontSize: 10, fill: "var(--muted)" },
    labelBgStyle: { fill: "var(--surface)" },
    markerEnd: { type: MarkerType.ArrowClosed, color: "var(--border-strong)", width: 14, height: 14 },
    style: { stroke: "var(--border-strong)", strokeWidth: 1 + 5 * (Math.log10((l.rows ?? 1) + 1) / Math.log10(maxRows + 1)) },
  })), [links, maxRows]);
  const node = find(root, selected) ?? root;
  return (
    <div className="grid gap-3 lg:grid-cols-[1fr_300px]">
      <FlowStyles />
      <div style={{ height, minHeight: 320 }} className="overflow-hidden rounded-lg border border-border bg-bg">
        <ReactFlowProvider>
          <ReactFlow onNodeClick={keepPointerEvents} nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: 0.15, maxZoom: 1.2 }}
            nodesConnectable={false} nodesDraggable={false} minZoom={0.1} proOptions={{ hideAttribution: true }} aria-label="Execution plan graph">
            <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="var(--chart-grid)" />
            <Controls showInteractive={false} position="bottom-left" />
          </ReactFlow>
        </ReactFlowProvider>
      </div>
      <aside className="rounded-lg border border-border bg-surface p-3" aria-label="Plan node details">
        <div className="mb-2 text-[12px] font-medium uppercase tracking-wide text-muted">Selected step</div>
        <NodeDetails node={node} issues={byNode.get(node.id) ?? []} analyzed={analyzed} />
        <p className="mt-3 text-2xs text-muted">Shading shows each step's share of the total cost; edge width shows the rows passed upward.</p>
      </aside>
    </div>
  );
}

function prettyXml(xml: string): string {
  const lines = xml.replace(/>\s*</g, ">\n<").split("\n");
  let depth = 0;
  return lines.map((raw) => {
    const t = raw.trim();
    if (t.startsWith("</")) depth = Math.max(depth - 1, 0);
    const line = "  ".repeat(depth) + t;
    const opens = t.startsWith("<") && !t.startsWith("</") && !t.startsWith("<?") && !t.startsWith("<!") && !t.endsWith("/>") && !/<\/[^>]+>$/.test(t);
    if (opens) depth++;
    return line;
  }).join("\n");
}

/** Human-readable text of the engine's own plan (JSON, showplan XML or plain text). */
export function formatRawPlan(raw: unknown): { text: string; format: "json" | "xml" | "text" } {
  if (raw == null) return { text: "", format: "text" };
  if (typeof raw === "string") {
    const s = raw.trim();
    if (s.startsWith("<")) return { text: prettyXml(s), format: "xml" };
    if (s.startsWith("{") || s.startsWith("[")) {
      try {
        return { text: JSON.stringify(JSON.parse(s), null, 2), format: "json" };
      } catch {
        /* not JSON after all */
      }
    }
    return { text: raw, format: "text" };
  }
  return { text: JSON.stringify(raw, null, 2), format: "json" };
}

export interface PlanViewerProps {
  root: PlanNode;
  issues?: PlanIssue[];
  analyzed?: boolean;
  /** vendor plan as returned by the engine (ExplainResult.raw) */
  raw?: unknown;
  engine?: string;
  height?: number | string;
}

/** Graph / Tree / Raw tabs for one EXPLAIN result. */
export function PlanViewer({ root, issues = [], analyzed = false, raw, engine, height }: PlanViewerProps) {
  const rawText = useMemo(() => formatRawPlan(raw), [raw]);
  const copy = () => {
    navigator.clipboard?.writeText(rawText.text).then(() => toast.success("Plan copied"), () => toast.error("Copy failed"));
  };
  return (
    <Tabs defaultValue="graph">
      <TabsList aria-label="Plan views">
        <TabsTrigger value="graph">Graph</TabsTrigger>
        <TabsTrigger value="tree">Tree</TabsTrigger>
        <TabsTrigger value="raw" disabled={!rawText.text}>Raw {engine ? `(${engine})` : "plan"}</TabsTrigger>
      </TabsList>
      <TabsContent value="graph" className="pt-3"><PlanGraph root={root} issues={issues} analyzed={analyzed} height={height} /></TabsContent>
      <TabsContent value="tree" className="pt-3"><PlanTree root={root} issues={issues} analyzed={analyzed} /></TabsContent>
      <TabsContent value="raw" className="pt-3">
        <div className="mb-2 flex items-center gap-2 text-[12px] text-muted">
          <Badge tone="outline">{rawText.format.toUpperCase()}</Badge>
          <span>Exactly what the engine returned, formatted for reading.</span>
          <Button size="sm" variant="ghost" className="ml-auto" onClick={copy}><Copy />Copy</Button>
        </div>
        <CodeBlock code={rawText.text} maxHeight="32rem" />
      </TabsContent>
    </Tabs>
  );
}
