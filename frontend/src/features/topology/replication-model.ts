/**
 * Replication graph from per-server replication status (GET /servers/{id}/replication) and
 * cluster membership. Engine payloads differ (pg_stat_replication, SHOW REPLICAS, availability
 * group DMVs, V$ARCHIVE_DEST_STATUS, MON_GET_HADR), so fields are read by a small set of known
 * keys, case-insensitively. Edges always point from the source of changes to the receiver.
 */
import type { Server } from "@/api/types";

export interface ReplicationInfo {
  role: string;
  replicas: Record<string, unknown>[];
  upstream: Record<string, unknown> | null;
  lag_seconds: number | null;
  slots?: Record<string, unknown>[];
  notes: string[];
}

export type LinkHealth = "ok" | "warn" | "crit" | "unknown";

export interface ReplNode {
  id: string;
  label: string;
  sub: string | null;
  server?: Server;
  role: string;
  cluster: string | null;
  external: boolean;
}

export interface ReplEdge {
  id: string;
  source: string;
  target: string;
  lag: number | null;
  state: string | null;
  health: LinkHealth;
  inferred: boolean;
  sync: string | null;
}

const ID_KEYS = ["application_name", "replica_server_name", "client_addr", "standby_member_host", "destination", "host", "dest_name", "hostname"];
const LAG_KEYS = ["replay_lag_s", "secondary_lag_seconds", "seconds_behind", "lag_seconds", "seconds_behind_source", "seconds_behind_master"];
const STATE_KEYS = ["state", "synchronization_health_desc", "connected_state_desc", "hadr_connect_status", "hadr_state", "status", "io_running"];
const SYNC_KEYS = ["sync_state", "availability_mode_desc", "hadr_syncmode"];
const UPSTREAM_KEYS = ["sender_host", "source_host", "master_host", "primary_member_host", "replica_server_name", "host"];

export function pick(rec: Record<string, unknown> | null | undefined, keys: string[]): unknown {
  if (!rec) return undefined;
  const lower = new Map(Object.entries(rec).map(([k, v]) => [k.toLowerCase(), v]));
  for (const k of keys) {
    const v = lower.get(k);
    if (v !== undefined && v !== null && v !== "") return v;
  }
  return undefined;
}

const num = (v: unknown): number | null => {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() !== "" ? Number(v) : NaN;
  return Number.isFinite(n) ? n : null;
};

const norm = (v: string) => v.trim().toLowerCase().replace(/^\[|\]$/g, "").replace(/:\d+$/, "");

/** Lag/state → link health. Thresholds: ≤ 10 s ok, ≤ 60 s warning, beyond critical. */
export function linkHealth(lag: number | null, state: string | null): LinkHealth {
  const s = (state ?? "").toLowerCase();
  if (s && /(error|disconnect|not_healthy|not healthy|not_sync|not sync|stopped|fail|offline|^no$|deferred)/.test(s)) return "crit";
  if (lag === null) return s && /(stream|healthy|connected|peer|valid|yes|online|synchroniz)/.test(s) ? "ok" : "unknown";
  if (lag <= 10) return "ok";
  if (lag <= 60) return "warn";
  return "crit";
}

export function lagLabel(lag: number | null): string {
  if (lag === null) return "lag n/a";
  if (lag < 1) return "< 1 s";
  if (lag < 120) return `${Math.round(lag)} s`;
  if (lag < 7200) return `${Math.round(lag / 60)} min`;
  return `${(lag / 3600).toFixed(1)} h`;
}

const isAddress = (v: string) => /^\d{1,3}(\.\d{1,3}){3}$/.test(v) || v.includes(":");
/** Short host name ("db1" for "db1.corp.local"); IP addresses are only matched whole. */
const shortName = (v: string) => (isAddress(v) ? v : v.split(".")[0]);

function matcher(servers: Server[]) {
  const map = new Map<string, Server>();
  for (const s of servers) {
    for (const k of [s.host, s.name, shortName(norm(s.host)), shortName(norm(s.name))]) {
      if (k && !map.has(norm(k))) map.set(norm(k), s);
    }
  }
  return (v: unknown): Server | undefined => {
    if (typeof v !== "string" || !v) return undefined;
    const n = norm(v);
    return map.get(n) ?? map.get(shortName(n));
  };
}

export function buildReplicationGraph(servers: Server[], info: Map<string, ReplicationInfo | null>): { nodes: ReplNode[]; edges: ReplEdge[] } {
  const find = matcher(servers);
  const nodes = new Map<string, ReplNode>();
  const edges = new Map<string, ReplEdge>();
  const roleOf = (s: Server) => info.get(s.id)?.role ?? s.cluster_role ?? "unknown";
  for (const s of servers) {
    nodes.set(s.id, { id: s.id, label: s.name, sub: `${s.host}:${s.port}`, server: s, role: roleOf(s), cluster: s.cluster?.name ?? null, external: false });
  }
  const addEdge = (source: string, target: string, lag: number | null, state: string | null, sync: string | null, inferred: boolean) => {
    if (source === target) return;
    const id = `${source}->${target}`;
    const prev = edges.get(id);
    if (prev && !prev.inferred) {
      if (prev.lag === null && lag !== null) prev.lag = lag;
      if (!prev.state && state) prev.state = state;
      prev.health = linkHealth(prev.lag, prev.state);
      return;
    }
    edges.set(id, { id, source, target, lag, state, sync, inferred, health: linkHealth(lag, state) });
  };

  for (const s of servers) {
    const ri = info.get(s.id);
    if (!ri) continue;
    for (const r of ri.replicas ?? []) {
      const ident = pick(r, ID_KEYS);
      const lag = num(pick(r, LAG_KEYS));
      const state = pick(r, STATE_KEYS);
      const sync = pick(r, SYNC_KEYS);
      const match = find(ident) ?? find(pick(r, ["client_addr"]));
      let target = match?.id;
      if (!target) {
        target = `ext:${s.id}:${String(ident ?? nodes.size)}`;
        nodes.set(target, {
          id: target, label: String(pick(r, ["application_name", "replica_server_name", "dest_name", "standby_member_host"]) ?? ident ?? "replica"),
          sub: (pick(r, ["client_addr", "destination", "standby_member_host"]) as string | undefined) ?? "not registered in KBM DBManager",
          role: "replica", cluster: s.cluster?.name ?? null, external: true,
        });
      }
      addEdge(s.id, target, lag, state == null ? null : String(state), sync == null ? null : String(sync), false);
    }
    if (ri.role === "replica" && ri.upstream) {
      const up = find(pick(ri.upstream, UPSTREAM_KEYS));
      const state = pick(ri.upstream, ["status", "state", "io_running", "hadr_connect_status", "connected_state_desc"]);
      if (up) addEdge(up.id, s.id, ri.lag_seconds, state == null ? null : String(state), null, false);
    }
  }
  // cluster roles fill in links nobody reported (monitoring account without the privilege, or
  // a replica that does not expose its upstream)
  const byCluster = new Map<string, Server[]>();
  for (const s of servers) if (s.cluster) byCluster.set(s.cluster.id, [...(byCluster.get(s.cluster.id) ?? []), s]);
  for (const members of byCluster.values()) {
    const primaries = members.filter((m) => roleOf(m) === "primary");
    const replicas = members.filter((m) => ["replica", "standby"].includes(roleOf(m)));
    if (primaries.length !== 1) continue;
    for (const r of replicas) {
      if ([...edges.values()].some((e) => e.target === r.id)) continue;
      const lag = info.get(r.id)?.lag_seconds ?? r.key_metrics?.["replication.lag_seconds"] ?? null;
      addEdge(primaries[0].id, r.id, lag, null, null, true);
    }
  }
  return { nodes: [...nodes.values()], edges: [...edges.values()] };
}

/** Column per replication depth (primary 0, its replicas 1, cascaded 2 …); clusters stacked. */
export function layoutReplication(g: { nodes: ReplNode[]; edges: ReplEdge[] }, colW = 300, rowH = 96) {
  const incoming = new Map<string, string[]>();
  for (const e of g.edges) incoming.set(e.target, [...(incoming.get(e.target) ?? []), e.source]);
  const depth = new Map<string, number>();
  const depthOf = (id: string, seen = new Set<string>()): number => {
    if (depth.has(id)) return depth.get(id)!;
    if (seen.has(id)) return 0;
    seen.add(id);
    const ins = incoming.get(id) ?? [];
    const d = ins.length ? Math.max(...ins.map((s) => depthOf(s, seen))) + 1 : 0;
    depth.set(id, d);
    return d;
  };
  g.nodes.forEach((n) => depthOf(n.id));
  const groups = new Map<string, ReplNode[]>();
  for (const n of g.nodes) groups.set(n.cluster ?? "￿", [...(groups.get(n.cluster ?? "￿") ?? []), n]);
  const pos = new Map<string, { x: number; y: number }>();
  const frames: { cluster: string | null; x: number; y: number; w: number; h: number }[] = [];
  let top = 0;
  for (const [cluster, members] of [...groups.entries()].sort(([a], [b]) => a.localeCompare(b))) {
    const cols = new Map<number, ReplNode[]>();
    for (const m of members) cols.set(depth.get(m.id) ?? 0, [...(cols.get(depth.get(m.id) ?? 0) ?? []), m]);
    let rows = 0;
    for (const [d, list] of cols) {
      list.sort((a, b) => a.label.localeCompare(b.label)).forEach((m, i) => pos.set(m.id, { x: d * colW + 24, y: top + 40 + i * rowH }));
      rows = Math.max(rows, list.length);
    }
    const w = Math.max(...cols.keys()) * colW + 24 + 232 + 24;
    const h = 40 + rows * rowH;
    frames.push({ cluster: cluster === "￿" ? null : cluster, x: 0, y: top, w, h });
    top += h + 36;
  }
  return { pos, frames };
}
