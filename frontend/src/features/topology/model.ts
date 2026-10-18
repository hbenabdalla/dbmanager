/**
 * Fleet topology model: Environment → Site → Cluster → Server → Database.
 *
 * Pure functions (no React) so the grouping, aggregation and layout are unit-testable and fast
 * enough for 1,000+ servers: the tree is built once, the visible graph is recomputed from the
 * expansion state, and large groups are paged ("+N more") instead of rendering every node.
 */
import type { Environment, Server, Site, Cluster } from "@/api/types";
import { HEALTH_ORDER } from "@/lib/engines";

export type TopoKind = "environment" | "site" | "cluster" | "server" | "database" | "more";

export interface TopoNode {
  id: string;
  kind: TopoKind;
  label: string;
  sub: string | null;
  health: string;
  /** servers below this node (1 for a server) */
  serverCount: number;
  byHealth: Record<string, number>;
  children: TopoNode[];
  server?: Server;
  databaseSize?: number | null;
  parentId: string | null;
}

export interface DatabaseLite { name: string; size_bytes: number | null; is_system: boolean }

const worst = (a: string, b: string): string => {
  const ia = HEALTH_ORDER.indexOf(a as (typeof HEALTH_ORDER)[number]);
  const ib = HEALTH_ORDER.indexOf(b as (typeof HEALTH_ORDER)[number]);
  if (ia === -1) return b;
  if (ib === -1) return a;
  return ia <= ib ? a : b;
};

function group(id: string, kind: TopoKind, label: string, sub: string | null, parentId: string | null): TopoNode {
  return { id, kind, label, sub, health: "healthy", serverCount: 0, byHealth: {}, children: [], parentId };
}

function finish(n: TopoNode): TopoNode {
  if (n.kind === "server" || n.kind === "database") return n;
  n.children.forEach(finish);
  n.children.sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === "cluster" ? -1 : b.kind === "cluster" ? 1 : 0;
    return a.label.localeCompare(b.label);
  });
  n.serverCount = 0;
  n.byHealth = {};
  n.health = n.children.length ? "healthy" : "unknown";
  for (const c of n.children) {
    n.serverCount += c.serverCount;
    for (const [h, v] of Object.entries(c.byHealth)) n.byHealth[h] = (n.byHealth[h] ?? 0) + v;
    n.health = worst(n.health, c.health);
  }
  return n;
}

/** Build the hierarchy. Servers without a site/cluster attach one level up. */
export function buildTopology(servers: Server[], envs: Environment[] = [], sites: Site[] = [], clusters: Cluster[] = []): TopoNode[] {
  const envOrder = new Map(envs.map((e) => [e.id, e.sort_order]));
  const siteMeta = new Map(sites.map((s) => [s.id, s]));
  const clusterMeta = new Map(clusters.map((c) => [c.id, c]));
  const roots = new Map<string, TopoNode>();
  const index = new Map<string, TopoNode>();
  const child = (parent: TopoNode, id: string, make: () => TopoNode) => {
    let n = index.get(id);
    if (!n) {
      n = make();
      index.set(id, n);
      parent.children.push(n);
    }
    return n;
  };
  for (const s of servers) {
    const envId = `env:${s.environment?.id ?? "none"}`;
    let env = roots.get(envId);
    if (!env) {
      env = group(envId, "environment", s.environment?.name ?? "No environment", s.environment?.tier ?? null, null);
      roots.set(envId, env);
      index.set(envId, env);
    }
    const siteId = `${envId}/site:${s.site?.id ?? "none"}`;
    const meta = s.site ? siteMeta.get(s.site.id) : undefined;
    const site = child(env, siteId, () =>
      group(siteId, "site", s.site?.name ?? "No site", meta ? [meta.provider, meta.region].filter(Boolean).join(" · ") || null : null, envId));
    let parent = site;
    if (s.cluster) {
      const cid = `${siteId}/cluster:${s.cluster.id}`;
      const cm = clusterMeta.get(s.cluster.id);
      parent = child(site, cid, () => group(cid, "cluster", s.cluster!.name, cm?.kind ?? null, siteId));
    }
    const health = s.health_status ?? "unknown";
    parent.children.push({
      id: `server:${s.id}`, kind: "server", label: s.name, sub: `${s.host}:${s.port}`, health, serverCount: 1,
      byHealth: { [health]: 1 }, children: [], server: s, parentId: parent.id,
    });
  }
  const out = [...roots.values()].map(finish);
  out.sort((a, b) => {
    const oa = envOrder.get(a.id.slice(4)) ?? 999;
    const ob = envOrder.get(b.id.slice(4)) ?? 999;
    return oa - ob || a.label.localeCompare(b.label);
  });
  return out;
}

export function walk(nodes: TopoNode[], fn: (n: TopoNode) => void): void {
  for (const n of nodes) {
    fn(n);
    walk(n.children, fn);
  }
}

/**
 * Default expansion that keeps the first view readable for any fleet size: expand level by
 * level (environments, then sites, then clusters) while the visible node count stays under the
 * budget. A 10-server fleet opens fully; a 2,000-server fleet opens at environment/site level.
 */
export function autoExpand(roots: TopoNode[], budget = 140): Set<string> {
  const expanded = new Set<string>();
  let frontier = roots;
  let visible = roots.length;
  while (frontier.length) {
    const groups = frontier.filter((n) => n.kind !== "server" && n.children.length);
    const add = groups.reduce((sum, n) => sum + Math.min(n.children.length, PAGE + 1), 0);
    if (!groups.length || visible + add > budget) break;
    groups.forEach((n) => expanded.add(n.id));
    visible += add;
    frontier = groups.flatMap((n) => n.children);
  }
  return expanded;
}

/** Ids of every ancestor of nodes matching the search, plus the matches. */
export function searchTopology(roots: TopoNode[], q: string): { matches: Set<string>; ancestors: Set<string> } {
  const matches = new Set<string>();
  const ancestors = new Set<string>();
  const needle = q.trim().toLowerCase();
  if (!needle) return { matches, ancestors };
  const byId = new Map<string, TopoNode>();
  walk(roots, (n) => byId.set(n.id, n));
  walk(roots, (n) => {
    const hay = [n.label, n.sub, n.server?.host, n.server?.engine_label, ...(n.server?.tags ?? []).map((t) => `${t.key}=${t.value}`)]
      .filter(Boolean).join(" ").toLowerCase();
    if (hay.includes(needle)) {
      matches.add(n.id);
      let p = n.parentId;
      while (p) {
        ancestors.add(p);
        p = byId.get(p)?.parentId ?? null;
      }
    }
  });
  return { matches, ancestors };
}

/** Children rendered per group before a "+N more" node (expanded further on click). */
export const PAGE = 40;

export interface LayoutNode { node: TopoNode; x: number; y: number; depth: number; expanded: boolean; hiddenCount?: number }
export interface LayoutEdge { id: string; source: string; target: string }

export const COL_W = 260;
export const ROW_H = 66;

/**
 * Left-to-right tidy tree: leaves get consecutive rows, a parent sits at the middle of its
 * visible children. ``pages`` holds how many pages of children a group shows.
 */
export function layoutTopology(
  roots: TopoNode[],
  expanded: Set<string>,
  opts: { pages?: Map<string, number>; databases?: Map<string, DatabaseLite[]>; filter?: Set<string> | null; matches?: Set<string> } = {},
): { nodes: LayoutNode[]; edges: LayoutEdge[] } {
  const nodes: LayoutNode[] = [];
  const edges: LayoutEdge[] = [];
  let row = 0;
  const place = (n: TopoNode, depth: number): number => {
    const isOpen = expanded.has(n.id);
    let kids: TopoNode[] = [];
    if (isOpen) {
      if (n.kind === "server") {
        kids = (opts.databases?.get(n.id) ?? []).map((d) => ({
          id: `${n.id}/db:${d.name}`, kind: "database" as const, label: d.name, sub: d.is_system ? "system" : null,
          health: n.health, serverCount: 0, byHealth: {}, children: [], databaseSize: d.size_bytes, parentId: n.id,
        }));
      } else {
        kids = opts.filter && !opts.matches?.has(n.id) ? n.children.filter((c) => opts.filter!.has(c.id)) : n.children;
      }
    }
    const limit = PAGE * (opts.pages?.get(n.id) ?? 1);
    const shown = kids.slice(0, limit);
    const hidden = kids.length - shown.length;
    const ys: number[] = [];
    for (const c of shown) {
      ys.push(place(c, depth + 1));
      edges.push({ id: `${n.id}->${c.id}`, source: n.id, target: c.id });
    }
    if (hidden > 0) {
      const more: TopoNode = {
        id: `${n.id}/more`, kind: "more", label: `+${hidden} more`, sub: "show next", health: "unknown",
        serverCount: 0, byHealth: {}, children: [], parentId: n.id,
      };
      const y = row++ * ROW_H;
      nodes.push({ node: more, x: (depth + 1) * COL_W, y, depth: depth + 1, expanded: false, hiddenCount: hidden });
      edges.push({ id: `${n.id}->${more.id}`, source: n.id, target: more.id });
      ys.push(y);
    }
    const y = ys.length ? (ys[0] + ys[ys.length - 1]) / 2 : row++ * ROW_H;
    nodes.push({ node: n, x: depth * COL_W, y, depth, expanded: isOpen && (kids.length > 0 || n.kind === "server") });
    return y;
  };
  const visibleRoots = opts.filter ? roots.filter((r) => opts.filter!.has(r.id)) : roots;
  for (const r of visibleRoots) {
    place(r, 0);
    row += 0.5; // breathing room between environments
  }
  return { nodes, edges };
}
