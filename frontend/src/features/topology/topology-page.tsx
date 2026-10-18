import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Background, BackgroundVariant, Controls, Handle, MiniMap, Position, ReactFlow, ReactFlowProvider, useReactFlow,
  type Edge, type Node, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Boxes, ChevronDown, ChevronRight, Database, Expand, Layers, MapPin, Maximize2, Minimize2, Network, Search, Server as ServerIcon } from "lucide-react";
import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { get } from "@/api/client";
import { useClusters, useEnvironments, useSites } from "@/api/hooks";
import { PageHeader } from "@/components/page";
import { EngineMark, HealthDot } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { fetchAllServers, FlowStyles, keepPointerEvents, STATUS_VAR, useReducedMotion } from "@/components/flow/flow-utils";
import { HEALTH_LABEL, HEALTH_ORDER } from "@/lib/engines";
import { bytes, compactNum } from "@/lib/format";
import { useSourceParam } from "@/lib/fleet-source";
import { cn } from "@/lib/utils";
import { autoExpand, buildTopology, layoutTopology, searchTopology, walk, type DatabaseLite, type LayoutNode, type TopoKind, type TopoNode } from "./model";

const KIND_ICON: Record<TopoKind, typeof Layers> = {
  environment: Layers, site: MapPin, cluster: Boxes, server: ServerIcon, database: Database, more: Expand,
};
const KIND_LABEL: Record<TopoKind, string> = {
  environment: "Environment", site: "Site", cluster: "Cluster", server: "Server", database: "Database", more: "More",
};

interface TopoData extends Record<string, unknown> {
  layout: LayoutNode;
  match: boolean;
  dim: boolean;
  loading: boolean;
  onActivate: (n: LayoutNode) => void;
  onToggle: (n: LayoutNode) => void;
}

function HealthBar({ byHealth, total }: { byHealth: Record<string, number>; total: number }) {
  if (!total) return null;
  return (
    <div className="mt-1.5 flex h-1.5 overflow-hidden rounded-full bg-surface-3" aria-hidden>
      {HEALTH_ORDER.filter((h) => byHealth[h]).map((h) => (
        <span key={h} style={{ width: `${(100 * byHealth[h]) / total}%`, background: STATUS_VAR[h] }} />
      ))}
    </div>
  );
}

const TopoNodeView = memo(function TopoNodeView({ data }: NodeProps<Node<TopoData>>) {
  const { layout, match, dim, loading, onActivate, onToggle } = data;
  const n = layout.node;
  const Icon = KIND_ICON[n.kind];
  const isGroup = n.kind === "environment" || n.kind === "site" || n.kind === "cluster";
  const canExpand = isGroup || (n.kind === "server" && (n.server?.database_count ?? 1) !== 0 && n.server?.implementation !== "planned");
  const healthText = HEALTH_LABEL[n.health] ?? n.health;
  const summary = isGroup
    ? `${KIND_LABEL[n.kind]} ${n.label}, ${n.serverCount} servers, worst health ${healthText}, ${layout.expanded ? "expanded" : "collapsed"}`
    : n.kind === "server" ? `Server ${n.label}, ${n.server?.engine_label ?? ""}, ${healthText}. Open server page`
    : n.kind === "more" ? `Show ${layout.hiddenCount} more` : `Database ${n.label}`;
  return (
    <div
      className={cn(
        "dbm-flow-node relative w-[220px] rounded-lg border bg-surface text-left shadow-sm",
        match ? "border-accent ring-2 ring-[var(--ring)]" : "border-border",
        n.kind === "more" && "border-dashed bg-surface-2",
        n.kind === "database" && "bg-surface-2",
        dim && "opacity-35",
      )}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} className="!size-1.5 !border-0 !bg-border-strong" />
      <span className="absolute inset-y-0 left-0 w-1 rounded-l-lg" style={{ background: n.kind === "more" ? "transparent" : STATUS_VAR[n.health] ?? STATUS_VAR.unknown }} aria-hidden />
      <div className="flex items-stretch">
        <button
          type="button"
          aria-label={summary}
          onClick={() => onActivate(layout)}
          className="nodrag nopan min-w-0 flex-1 rounded-l-lg px-3 py-2 pl-3.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
        >
          <div className="flex items-center gap-1.5 text-[12.5px] font-medium text-text">
            {n.kind === "server" && n.server ? <EngineMark engine={n.server.engine} /> : <Icon className="size-3.5 shrink-0 text-muted" aria-hidden />}
            <span className="truncate">{n.label}</span>
            {n.kind === "server" && <HealthDot health={n.health} className="ml-auto" />}
          </div>
          <div className="mt-0.5 flex items-center gap-1.5 truncate text-2xs text-muted">
            {isGroup && <span className="tabular">{compactNum(n.serverCount)} server{n.serverCount === 1 ? "" : "s"}</span>}
            {n.sub && <span className="truncate">{isGroup ? `· ${n.sub}` : n.sub}</span>}
            {n.kind === "server" && n.server?.cluster_role && <Badge tone="outline" className="ml-auto">{n.server.cluster_role}</Badge>}
            {n.kind === "database" && n.databaseSize != null && <span className="ml-auto tabular">{bytes(n.databaseSize)}</span>}
          </div>
          {isGroup && <HealthBar byHealth={n.byHealth} total={n.serverCount} />}
        </button>
        {canExpand && (
          <button
            type="button"
            onClick={() => onToggle(layout)}
            aria-label={`${layout.expanded ? "Collapse" : "Expand"} ${n.label}${n.kind === "server" ? " databases" : ""}`}
            aria-expanded={layout.expanded}
            className="nodrag nopan flex w-7 shrink-0 items-center justify-center rounded-r-lg border-l border-border text-muted hover:bg-surface-2 hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
          >
            {loading ? <span className="size-3 animate-spin rounded-full border-2 border-border-strong border-t-transparent" /> : layout.expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          </button>
        )}
      </div>
      <Handle type="source" position={Position.Right} isConnectable={false} className="!size-1.5 !border-0 !bg-border-strong" />
    </div>
  );
});

const nodeTypes = { topo: TopoNodeView };

export interface TopologyCanvasProps {
  roots: TopoNode[];
  /** canvas height (CSS length) */
  height?: string;
}

function TopologyCanvas({ roots, height = "calc(100vh - 250px)" }: TopologyCanvasProps) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const reduced = useReducedMotion();
  const flow = useReactFlow();
  const [expanded, setExpanded] = useState<Set<string>>(() => autoExpand(roots));
  const [pages, setPages] = useState<Map<string, number>>(new Map());
  const [databases, setDatabases] = useState<Map<string, DatabaseLite[]>>(new Map());
  const [loadingDb, setLoadingDb] = useState<Set<string>>(new Set());
  const [q, setQ] = useState("");
  const [onlyMatches, setOnlyMatches] = useState(false);

  const search = useMemo(() => searchTopology(roots, q), [roots, q]);
  const searching = q.trim().length > 0;
  const effectiveExpanded = useMemo(() => {
    if (!searching) return expanded;
    return new Set([...expanded, ...search.ancestors]);
  }, [expanded, search, searching]);
  const filter = useMemo(() => {
    if (!searching || !onlyMatches) return null;
    return new Set([...search.matches, ...search.ancestors]);
  }, [search, searching, onlyMatches]);

  const layout = useMemo(
    () => layoutTopology(roots, effectiveExpanded, { pages, databases, filter, matches: search.matches }),
    [roots, effectiveExpanded, pages, databases, filter, search.matches],
  );

  const toggle = useCallback((ln: LayoutNode) => {
    const n = ln.node;
    if (n.kind === "server" && n.server && !databases.has(n.id) && !ln.expanded) {
      setLoadingDb((s) => new Set(s).add(n.id));
      qc.fetchQuery({ queryKey: ["server", n.server.id, "databases"], queryFn: () => get<DatabaseLite[]>(`/servers/${n.server!.id}/databases`), staleTime: 60_000 })
        .then((dbs) => setDatabases((m) => new Map(m).set(n.id, dbs)))
        .catch(() => setDatabases((m) => new Map(m).set(n.id, [])))
        .finally(() => setLoadingDb((s) => { const x = new Set(s); x.delete(n.id); return x; }));
    }
    setExpanded((s) => {
      const x = new Set(s);
      if (x.has(n.id)) x.delete(n.id);
      else x.add(n.id);
      return x;
    });
  }, [databases, qc]);

  const activate = useCallback((ln: LayoutNode) => {
    const n = ln.node;
    if (n.kind === "server" && n.server) navigate(`/servers/${n.server.id}`);
    else if (n.kind === "database" && n.parentId) navigate(`/servers/${n.parentId.slice("server:".length)}?tab=schemas&db=${encodeURIComponent(n.label)}`);
    else if (n.kind === "more" && n.parentId) setPages((m) => new Map(m).set(n.parentId!, (m.get(n.parentId!) ?? 1) + 1));
    else toggle(ln);
  }, [navigate, toggle]);

  const nodes = useMemo<Node<TopoData>[]>(() => layout.nodes.map((ln) => ({
    id: ln.node.id,
    type: "topo",
    position: { x: ln.x, y: ln.y },
    draggable: false,
    focusable: false,
    selectable: false,
    data: {
      layout: ln,
      match: search.matches.has(ln.node.id),
      dim: searching && !onlyMatches && !search.matches.has(ln.node.id) && !search.ancestors.has(ln.node.id),
      loading: loadingDb.has(ln.node.id),
      onActivate: activate,
      onToggle: toggle,
    },
  })), [layout, search, searching, onlyMatches, loadingDb, activate, toggle]);

  const edges = useMemo<Edge[]>(() => layout.edges.map((e) => ({
    id: e.id, source: e.source, target: e.target, type: "smoothstep", focusable: false,
    style: { stroke: "var(--border-strong)", strokeWidth: 1.25, opacity: searching && !(search.matches.has(e.target) || search.ancestors.has(e.target)) ? 0.25 : 1 },
  })), [layout, search, searching]);

  useEffect(() => {
    const t = setTimeout(() => flow.fitView({ padding: 0.15, duration: reduced ? 0 : 250, maxZoom: 1.1, minZoom: 0.6 }), 30);
    return () => clearTimeout(t);
  }, [q, onlyMatches, flow, reduced]);

  const expandAll = () => {
    const all = new Set<string>();
    walk(roots, (n) => { if (n.kind !== "server" && n.kind !== "database") all.add(n.id); });
    setExpanded(all);
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-full max-w-[320px]">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted" aria-hidden />
          <Input aria-label="Search topology" placeholder="Search servers, hosts, clusters, tags" value={q} onChange={(e) => setQ(e.target.value)} className="pl-8" />
        </div>
        {searching && (
          <>
            <span className="text-[12px] text-muted" aria-live="polite">{search.matches.size} match{search.matches.size === 1 ? "" : "es"}</span>
            <label className="flex items-center gap-1.5 text-[12.5px] text-text-2">
              <input type="checkbox" checked={onlyMatches} onChange={(e) => setOnlyMatches(e.target.checked)} className="accent-[var(--accent)]" />
              Matches only
            </label>
          </>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          <Button size="sm" variant="ghost" onClick={expandAll}><Maximize2 />Expand groups</Button>
          <Button size="sm" variant="ghost" onClick={() => { setExpanded(new Set()); setPages(new Map()); }}><Minimize2 />Collapse all</Button>
          <Button size="sm" variant="ghost" onClick={() => setExpanded(autoExpand(roots))}>Reset</Button>
          <Button size="sm" onClick={() => flow.fitView({ padding: 0.15, duration: reduced ? 0 : 250, minZoom: 0.6 })}>Fit view</Button>
        </div>
      </div>
      <Card className="overflow-hidden">
        <div style={{ height, minHeight: 480 }} className="bg-bg">
          <ReactFlow
            onNodeClick={keepPointerEvents}
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{ padding: 0.15, maxZoom: 1.1, minZoom: 0.6 }}
            minZoom={0.05}
            maxZoom={1.6}
            nodesConnectable={false}
            nodesDraggable={false}
            proOptions={{ hideAttribution: true }}
            aria-label="Fleet topology: environments, sites, clusters, servers and databases"
          >
            <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="var(--chart-grid)" />
            <Controls showInteractive={false} position="bottom-left" />
            <MiniMap pannable zoomable position="bottom-right" nodeColor={(n) => STATUS_VAR[(n.data as TopoData).layout.node.health] ?? STATUS_VAR.unknown}
              maskColor="color-mix(in srgb, var(--bg) 70%, transparent)" ariaLabel="Topology overview" />
          </ReactFlow>
        </div>
      </Card>
      <div className="flex flex-wrap items-center gap-3 text-[12px] text-muted">
        {HEALTH_ORDER.map((h) => <span key={h} className="flex items-center gap-1.5"><HealthDot health={h} />{HEALTH_LABEL[h]}</span>)}
        <span className="ml-auto">Click a group to expand or collapse it, a server to open it; the arrow on a server lists its databases.</span>
      </div>
    </div>
  );
}

/** Interactive fleet topology (Environment → Site → Cluster → Server → Database). */
export function TopologyPage() {
  const source = useSourceParam();
  const servers = useQuery({ queryKey: ["servers", "topology", source], queryFn: ({ signal }) => fetchAllServers({ source }, signal), staleTime: 30_000 });
  const envs = useEnvironments();
  const sites = useSites();
  const clusters = useClusters();
  const roots = useMemo(
    () => buildTopology(servers.data ?? [], envs.data ?? [], sites.data ?? [], clusters.data ?? []),
    [servers.data, envs.data, sites.data, clusters.data],
  );
  const total = servers.data?.length ?? 0;
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const s of servers.data ?? []) c[s.health_status] = (c[s.health_status] ?? 0) + 1;
    return c;
  }, [servers.data]);

  return (
    <div>
      <FlowStyles />
      <PageHeader
        title="Topology"
        crumbs={[{ label: "Fleet" }, { label: "Topology" }]}
        description="How the estate is organised: environments, sites, clusters, servers and their databases. Large groups open collapsed and page their members."
        meta={total ? (
          <div className="flex flex-wrap items-center gap-2 text-[12px]">
            <Badge tone="neutral"><Network className="size-3" aria-hidden />{compactNum(total)} servers</Badge>
            {HEALTH_ORDER.filter((h) => counts[h]).map((h) => <Badge key={h} tone="outline"><HealthDot health={h} className="size-1.5" />{counts[h]} {HEALTH_LABEL[h].toLowerCase()}</Badge>)}
          </div>
        ) : undefined}
      />
      {servers.isLoading ? (
        <div className="space-y-3"><Skeleton className="h-8 w-80" /><Skeleton className="h-[480px] w-full" /></div>
      ) : servers.error ? (
        <ErrorState error={servers.error} onRetry={() => servers.refetch()} />
      ) : !total ? (
        <EmptyState icon={<Network className="size-5" />} title="No servers yet">Register a server and it appears here under its environment, site and cluster.</EmptyState>
      ) : (
        <ReactFlowProvider>
          <TopologyCanvas key={`${source}:${roots.length}`} roots={roots} />
        </ReactFlowProvider>
      )}
    </div>
  );
}
