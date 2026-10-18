import { useQueries, useQuery } from "@tanstack/react-query";
import {
  Background, BackgroundVariant, BaseEdge, Controls, EdgeLabelRenderer, getSmoothStepPath, Handle, MarkerType, Position,
  ReactFlow, ReactFlowProvider, type Edge, type EdgeProps, type Node, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { ExternalLink, GitFork } from "lucide-react";
import { memo, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { get } from "@/api/client";
import type { Page, Server } from "@/api/types";
import { PageHeader } from "@/components/page";
import { EngineMark, HealthDot } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { fetchAllServers, FlowStyles, keepPointerEvents, STATUS_VAR, useReducedMotion } from "@/components/flow/flow-utils";
import { useSourceParam } from "@/lib/fleet-source";
import { cn } from "@/lib/utils";
import { buildReplicationGraph, lagLabel, layoutReplication, type LinkHealth, type ReplEdge, type ReplicationInfo, type ReplNode } from "./replication-model";

const LINK_VAR: Record<LinkHealth, string> = { ok: "var(--ok)", warn: "var(--warn)", crit: "var(--crit)", unknown: "var(--border-strong)" };
const LINK_LABEL: Record<LinkHealth, string> = { ok: "Healthy", warn: "Lagging", crit: "Broken or far behind", unknown: "Unknown" };
/** Status requests are live adapter calls: cap how many one view fans out. */
export const MAX_STATUS_REQUESTS = 60;

interface ReplNodeData extends Record<string, unknown> { n: ReplNode; focus: boolean; onOpen: (n: ReplNode) => void }
interface ReplEdgeData extends Record<string, unknown> { e: ReplEdge; animate: boolean }
interface FrameData extends Record<string, unknown> { label: string | null; w: number; h: number }

const ReplNodeView = memo(function ReplNodeView({ data }: NodeProps<Node<ReplNodeData>>) {
  const { n, focus, onOpen } = data;
  const health = n.server?.health_status ?? "unknown";
  const content = (
    <>
      <div className="flex items-center gap-1.5 text-[12.5px] font-medium">
        {n.server ? <EngineMark engine={n.server.engine} /> : <ExternalLink className="size-3.5 text-muted" aria-hidden />}
        <span className="truncate">{n.label}</span>
        {n.server && <HealthDot health={health} className="ml-auto" />}
      </div>
      <div className="mt-0.5 flex items-center gap-1.5 text-2xs text-muted">
        <span className="truncate">{n.sub}</span>
        <Badge tone={n.role === "primary" ? "accent" : "outline"} className="ml-auto">{n.external ? "external" : n.role}</Badge>
      </div>
    </>
  );
  return (
    <div className={cn("dbm-flow-node relative w-[232px] rounded-lg border bg-surface shadow-sm", focus ? "border-accent ring-2 ring-[var(--ring)]" : "border-border", n.external && "border-dashed bg-surface-2")}>
      <Handle type="target" position={Position.Left} isConnectable={false} className="!size-1.5 !border-0 !bg-border-strong" />
      <span className="absolute inset-y-0 left-0 w-1 rounded-l-lg" style={{ background: n.server ? STATUS_VAR[health] : "transparent" }} aria-hidden />
      {n.server ? (
        <button type="button" onClick={() => onOpen(n)} aria-label={`${n.role} ${n.label}. Open server page`}
          className="nodrag nopan block w-full rounded-lg px-3 py-2 pl-3.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]">{content}</button>
      ) : (
        <div className="px-3 py-2 pl-3.5" aria-label={`external replica ${n.label}`}>{content}</div>
      )}
      <Handle type="source" position={Position.Right} isConnectable={false} className="!size-1.5 !border-0 !bg-border-strong" />
    </div>
  );
});

function FrameView({ data }: NodeProps<Node<FrameData>>) {
  return (
    <div className="pointer-events-none rounded-xl border border-dashed border-border-strong/70 bg-surface-2/40" style={{ width: data.w, height: data.h }}>
      <div className="px-3 pt-2 text-[12px] font-medium text-text-2">{data.label ?? "Not in a cluster"}</div>
    </div>
  );
}

function FlowEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd }: EdgeProps<Edge<ReplEdgeData>>) {
  const e = data!.e;
  const [path, lx, ly] = getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, borderRadius: 12 });
  const color = LINK_VAR[e.health];
  const moving = data!.animate && e.health !== "crit";
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={{ stroke: color, strokeWidth: 2, opacity: e.inferred ? 0.65 : 1 }}
        className={cn(moving && "dbm-flow-animated")} />
      <EdgeLabelRenderer>
        <div
          className="nodrag nopan absolute rounded-full border bg-surface px-2 py-px text-2xs font-medium tabular shadow-sm"
          style={{ transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)`, borderColor: color, color }}
          title={[e.state && `state ${e.state}`, e.sync && `mode ${e.sync}`, e.inferred && "inferred from cluster roles"].filter(Boolean).join(" · ") || undefined}
        >
          {lagLabel(e.lag)}{e.sync ? ` · ${e.sync.toLowerCase()}` : ""}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

const nodeTypes = { repl: ReplNodeView, frame: FrameView };
const edgeTypes = { flow: FlowEdge };

export interface ReplicationTopologyProps {
  /** servers to draw (typically one cluster, or a server and its cluster peers) */
  servers: Server[];
  /** highlighted server (server-tab use) */
  focusServerId?: string;
  /** canvas height in px or CSS length */
  height?: number | string;
}

/** Primary → replica graph with directional flow, lag labels and link health colours. */
export function ReplicationTopology({ servers, focusServerId, height = 420 }: ReplicationTopologyProps) {
  const navigate = useNavigate();
  const reduced = useReducedMotion();
  const polled = servers.filter((s) => s.implementation !== "planned").slice(0, MAX_STATUS_REQUESTS);
  const results = useQueries({
    queries: polled.map((s) => ({
      queryKey: ["server", s.id, "replication"],
      queryFn: () => get<ReplicationInfo>(`/servers/${s.id}/replication`),
      staleTime: 30_000,
      refetchInterval: 30_000,
      retry: false,
    })),
  });
  const loading = results.some((r) => r.isLoading);
  const info = useMemo(() => {
    const m = new Map<string, ReplicationInfo | null>();
    polled.forEach((s, i) => m.set(s.id, results[i]?.data ?? null));
    return m;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [results.map((r) => r.dataUpdatedAt).join(","), servers]);
  const graph = useMemo(() => buildReplicationGraph(servers, info), [servers, info]);
  const { pos, frames } = useMemo(() => layoutReplication(graph), [graph]);

  const nodes = useMemo<Node[]>(() => [
    ...frames.map((f, i) => ({
      id: `frame:${i}`, type: "frame", position: { x: f.x, y: f.y }, data: { label: f.cluster, w: f.w, h: f.h } satisfies FrameData,
      draggable: false, selectable: false, focusable: false, zIndex: -1,
    })),
    ...graph.nodes.map((n) => ({
      id: n.id, type: "repl", position: pos.get(n.id) ?? { x: 0, y: 0 }, draggable: false, selectable: false, focusable: false,
      data: { n, focus: n.id === focusServerId, onOpen: (x: ReplNode) => x.server && navigate(`/servers/${x.server.id}`) } satisfies ReplNodeData,
    })),
  ], [frames, graph, pos, focusServerId, navigate]);
  const edges = useMemo<Edge[]>(() => graph.edges.map((e) => ({
    id: e.id, source: e.source, target: e.target, type: "flow", focusable: false,
    markerEnd: { type: MarkerType.ArrowClosed, color: LINK_VAR[e.health], width: 16, height: 16 },
    data: { e, animate: !reduced } satisfies ReplEdgeData,
  })), [graph, reduced]);

  const summary = graph.edges.length
    ? `${graph.edges.length} replication link${graph.edges.length === 1 ? "" : "s"}: ` +
      graph.edges.map((e) => `${graph.nodes.find((n) => n.id === e.source)?.label} to ${graph.nodes.find((n) => n.id === e.target)?.label}, ${lagLabel(e.lag)}, ${LINK_LABEL[e.health]}`).join("; ")
    : "No replication links found";

  return (
    <div className="space-y-2">
      <FlowStyles />
      <div style={{ height, minHeight: 260 }} className="overflow-hidden rounded-lg border border-border bg-bg">
        <ReactFlowProvider>
          <ReactFlow onNodeClick={keepPointerEvents} nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} fitView fitViewOptions={{ padding: 0.2, maxZoom: 1.1 }}
            nodesConnectable={false} nodesDraggable={false} proOptions={{ hideAttribution: true }} minZoom={0.1}
            aria-label="Replication topology">
            <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="var(--chart-grid)" />
            <Controls showInteractive={false} position="bottom-left" />
          </ReactFlow>
        </ReactFlowProvider>
      </div>
      <p className="sr-only" aria-live="polite">{summary}</p>
      <div className="flex flex-wrap items-center gap-3 text-[12px] text-muted">
        {(Object.keys(LINK_LABEL) as LinkHealth[]).map((h) => (
          <span key={h} className="flex items-center gap-1.5"><span className="inline-block h-0.5 w-4 rounded" style={{ background: LINK_VAR[h] }} aria-hidden />{LINK_LABEL[h]}</span>
        ))}
        <span>Arrows point from primary to replica. Faded links are inferred from cluster roles.</span>
        {loading && <span className="ml-auto">Reading replication status…</span>}
        {servers.length > polled.length && <span className="ml-auto">Live status read for the first {polled.length} servers.</span>}
      </div>
    </div>
  );
}

/** Server-tab variant: the server, its cluster peers and anything it reports as a replica. */
export function ServerReplicationTopology({ server, height }: { server: Server; height?: number | string }) {
  const peers = useQuery({
    queryKey: ["servers", "cluster-peers", server.cluster?.id],
    queryFn: () => get<Page<Server>>("/servers", { cluster_id: server.cluster!.id, page_size: 200, source: "all" }),
    enabled: !!server.cluster,
    staleTime: 60_000,
  });
  if (server.cluster && peers.isLoading) return <Skeleton className="h-[320px] w-full" />;
  const list = server.cluster ? peers.data?.items ?? [server] : [server];
  return <ReplicationTopology servers={list.some((s) => s.id === server.id) ? list : [server, ...list]} focusServerId={server.id} height={height} />;
}

/** Fleet-wide page: one replication diagram per cluster, picked from a list. */
export function ReplicationTopologyPage() {
  const source = useSourceParam();
  const q = useQuery({ queryKey: ["servers", "topology", source], queryFn: ({ signal }) => fetchAllServers({ source }, signal), staleTime: 30_000 });
  const clusters = useMemo(() => {
    const m = new Map<string, { id: string; name: string; servers: Server[] }>();
    for (const s of q.data ?? []) {
      if (!s.cluster) continue;
      const c = m.get(s.cluster.id) ?? { id: s.cluster.id, name: s.cluster.name, servers: [] };
      c.servers.push(s);
      m.set(s.cluster.id, c);
    }
    return [...m.values()].sort((a, b) => a.name.localeCompare(b.name));
  }, [q.data]);
  const [picked, setPicked] = useState<string>("");
  const current = clusters.find((c) => c.id === picked) ?? clusters[0];
  const standalone = (q.data ?? []).filter((s) => !s.cluster).length;
  return (
    <div>
      <PageHeader title="Replication topology" crumbs={[{ label: "Monitoring" }, { label: "Replication topology" }]}
        description="Live primary → replica links per cluster, from each server's replication status. Lag is read from the engine (pg_stat_replication, SHOW REPLICA STATUS, availability group DMVs, Data Guard destinations, HADR)." />
      {q.isLoading ? <Skeleton className="h-[460px] w-full" /> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !clusters.length ? (
        <EmptyState icon={<GitFork className="size-5" />} title="No clusters">
          Replication is drawn per cluster. Put primary and replica servers in the same cluster (server settings) to see their links here.
        </EmptyState>
      ) : (
        <Card className="space-y-3 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <label htmlFor="repl-cluster" className="text-[12.5px] text-text-2">Cluster</label>
            <Select id="repl-cluster" className="w-64" value={current?.id} onChange={(e) => setPicked(e.target.value)}>
              {clusters.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.servers.length})</option>)}
            </Select>
            <span className="ml-auto text-[12px] text-muted">{clusters.length} cluster{clusters.length === 1 ? "" : "s"} · {standalone} standalone server{standalone === 1 ? "" : "s"}</span>
          </div>
          {current && <ReplicationTopology key={current.id} servers={current.servers} height={460} />}
        </Card>
      )}
    </div>
  );
}
