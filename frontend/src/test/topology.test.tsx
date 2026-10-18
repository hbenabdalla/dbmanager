import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { beforeAll, describe, expect, it } from "vitest";
import type { PlanNode, Server } from "@/api/types";
import { formatRawPlan, layoutPlan, misestimate, PlanGraph, PlanViewer } from "@/components/plan-graph";
import { autoExpand, buildTopology, layoutTopology, PAGE, searchTopology } from "@/features/topology/model";
import { ReplicationTopology } from "@/features/topology/replication";
import { buildReplicationGraph, lagLabel, linkHealth, type ReplicationInfo } from "@/features/topology/replication-model";
import { TopologyPage } from "@/features/topology/topology-page";
import { mockFetch, renderWithProviders } from "./utils";

// React Flow measures nodes with these browser APIs; jsdom lacks them.
beforeAll(() => {
  // a ResizeObserver that reports each observed element once, so React Flow marks nodes measured
  class MeasuringRO {
    cb: ResizeObserverCallback;
    constructor(cb: ResizeObserverCallback) { this.cb = cb; }
    observe(el: Element) {
      queueMicrotask(() => this.cb([{ target: el, contentRect: { width: 220, height: 80 } } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver));
    }
    unobserve() {}
    disconnect() {}
  }
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = MeasuringRO;
  class DOMMatrixReadOnlyStub {
    m22: number;
    constructor(t?: string) {
      const scale = t?.match(/scale\(([1-9.])/)?.[1];
      this.m22 = scale !== undefined ? +scale : 1;
    }
  }
  (globalThis as unknown as { DOMMatrixReadOnly: unknown }).DOMMatrixReadOnly = DOMMatrixReadOnlyStub;
  Object.defineProperties(HTMLElement.prototype, {
    offsetHeight: { configurable: true, get() { return 80; } },
    offsetWidth: { configurable: true, get() { return 220; } },
  });
  (SVGElement.prototype as unknown as { getBBox: () => DOMRect }).getBBox = () => ({ x: 0, y: 0, width: 40, height: 12 }) as DOMRect;
});

let seq = 0;
function server(p: Partial<Server> & { name: string }): Server {
  seq += 1;
  return {
    id: `s${seq}`, description: null, engine: "postgresql", engine_label: "PostgreSQL", version: "16", host: `${p.name}.db.internal`, port: 5432,
    default_database: null, deployment: "self-managed", cloud_provider: null, environment: { id: "e1", name: "Production", tier: "production" },
    site: { id: "site1", name: "Riyadh DC" }, cluster: null, cluster_role: null, application: null, owner: null, business_service: null,
    tags: [], tls_mode: "require", monitoring_enabled: true, collection_interval_s: 60, licensed_features: [], is_demo: false,
    health_status: "healthy", health_reasons: [], connection_status: "ok", last_error: null, last_connected_at: null,
    last_collected_at: null, circuit_open_until: null, key_metrics: {}, database_count: 3, open_recommendations: 0,
    open_findings: 0, implementation: "implemented", created_at: "2026-01-01T00:00:00Z", ...p,
  };
}

describe("topology model", () => {
  it("groups Environment → Site → Cluster → Server and aggregates health", () => {
    const c = { id: "c1", name: "orders-pg" };
    const roots = buildTopology([
      server({ name: "pg-a", cluster: c, cluster_role: "primary" }),
      server({ name: "pg-b", cluster: c, cluster_role: "replica", health_status: "critical" }),
      server({ name: "lone", site: null }),
      server({ name: "dev1", environment: { id: "e2", name: "Development", tier: "development" } }),
    ], [{ id: "e1", name: "Production", tier: "production", description: null, sort_order: 1 }, { id: "e2", name: "Development", tier: "development", description: null, sort_order: 5 }]);
    expect(roots.map((r) => r.label)).toEqual(["Production", "Development"]);
    const prod = roots[0];
    expect(prod.serverCount).toBe(3);
    expect(prod.health).toBe("critical");
    expect(prod.byHealth).toEqual({ healthy: 2, critical: 1 });
    const site = prod.children.find((s) => s.label === "Riyadh DC")!;
    expect(site.children[0].kind).toBe("cluster");
    expect(site.children[0].children.map((s) => s.label)).toEqual(["pg-a", "pg-b"]);
    expect(prod.children.some((s) => s.label === "No site")).toBe(true);
  });

  it("opens a small fleet fully and a 1,200-server fleet at group level, paging large groups", () => {
    const small = buildTopology([server({ name: "a" }), server({ name: "b" })]);
    const smallOpen = autoExpand(small);
    expect(smallOpen.size).toBe(2); // environment + site

    const big = buildTopology(Array.from({ length: 1200 }, (_, i) => server({
      name: `srv-${String(i).padStart(4, "0")}`, site: { id: `site${i % 6}`, name: `Site ${i % 6}` },
      environment: i % 2 ? { id: "e1", name: "Production", tier: "production" } : { id: "e2", name: "UAT", tier: "uat" },
    })));
    const open = autoExpand(big);
    const { nodes } = layoutTopology(big, open);
    expect(nodes.length).toBeLessThan(150);
    expect(nodes.some((n) => n.node.kind === "server")).toBe(false);

    const site = big[0].children[0];
    const opened = layoutTopology(big, new Set([big[0].id, site.id]));
    const more = opened.nodes.find((n) => n.node.kind === "more")!;
    expect(more.hiddenCount).toBe(site.children.length - PAGE);
    const paged = layoutTopology(big, new Set([big[0].id, site.id]), { pages: new Map([[site.id, 99]]) });
    expect(paged.nodes.some((n) => n.node.kind === "more")).toBe(false);
  });

  it("search finds servers by host/tag and returns their ancestors", () => {
    const roots = buildTopology([server({ name: "alpha", tags: [{ key: "app", value: "billing" }] }), server({ name: "beta" })]);
    const r = searchTopology(roots, "app=billing");
    expect(r.matches.size).toBe(1);
    expect(r.ancestors.size).toBe(2);
    const filtered = layoutTopology(roots, r.ancestors, { filter: new Set([...r.matches, ...r.ancestors]), matches: r.matches });
    expect(filtered.nodes.filter((n) => n.node.kind === "server").map((n) => n.node.label)).toEqual(["alpha"]);
  });
});

describe("TopologyPage", () => {
  it("renders the fleet and opens a server on click", async () => {
    const s = server({ name: "pay-db-1" });
    mockFetch((url) => {
      if (url.pathname === "/api/v1/servers") return { body: { items: [s, server({ name: "pay-db-2", health_status: "warning" })], total: 2, page: 1, page_size: 500 } };
      if (url.pathname === "/api/v1/environments") return { body: [{ id: "e1", name: "Production", tier: "production", description: null, sort_order: 1 }] };
      if (["/api/v1/sites", "/api/v1/clusters"].includes(url.pathname)) return { body: [] };
      return undefined;
    });
    renderWithProviders(<Routes><Route path="/" element={<TopologyPage />} /><Route path="/servers/:id" element={<p>server page {s.id}</p>} /></Routes>);
    const node = await screen.findByRole("button", { name: /Server pay-db-1/ });
    expect(screen.getByRole("button", { name: /Environment Production, 2 servers, worst health Warning/ })).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Search topology"), "pay-db-2");
    expect(await screen.findByText("1 match")).toBeInTheDocument();
    await userEvent.click(node);
    expect(await screen.findByText(`server page ${s.id}`)).toBeInTheDocument();
  });

  it("explains an empty fleet", async () => {
    mockFetch((url) => url.pathname === "/api/v1/servers" ? { body: { items: [], total: 0, page: 1, page_size: 500 } } : { body: [] });
    renderWithProviders(<TopologyPage />);
    expect(await screen.findByText("No servers yet")).toBeInTheDocument();
  });
});

describe("replication model", () => {
  it("links a primary to registered and external replicas with lag health", () => {
    const c = { id: "c1", name: "orders" };
    const p = server({ name: "pg-p", cluster: c, cluster_role: "primary" });
    const r = server({ name: "pg-r", host: "10.0.0.7", cluster: c, cluster_role: "replica" });
    const r2 = server({ name: "pg-r2", cluster: c, cluster_role: "replica" });
    const info = new Map<string, ReplicationInfo | null>([
      [p.id, { role: "primary", lag_seconds: 3, upstream: null, notes: [], replicas: [
        { application_name: "walreceiver", client_addr: "10.0.0.7", state: "streaming", sync_state: "async", replay_lag_s: 3 },
        { application_name: "dr-standby", client_addr: "10.40.0.12", state: "streaming", replay_lag_s: 95 },
      ] }],
      [r.id, { role: "replica", lag_seconds: 3, upstream: { sender_host: "pg-p.db.internal", status: "streaming" }, replicas: [], notes: [] }],
      [r2.id, null],
    ]);
    const g = buildReplicationGraph([p, r, r2], info);
    const toR = g.edges.find((e) => e.target === r.id)!;
    expect(toR.source).toBe(p.id);
    expect(toR.health).toBe("ok");
    expect(toR.inferred).toBe(false);
    const ext = g.edges.find((e) => e.target.startsWith("ext:"))!;
    expect(ext.health).toBe("crit");
    expect(g.nodes.find((n) => n.id === ext.target)!.label).toBe("dr-standby");
    const inferred = g.edges.find((e) => e.target === r2.id)!;
    expect(inferred.inferred).toBe(true);
    expect(g.edges.filter((e) => e.target === r.id)).toHaveLength(1);
  });

  it("classifies link states and formats lag", () => {
    expect(linkHealth(30, "streaming")).toBe("warn");
    expect(linkHealth(null, "NOT_HEALTHY")).toBe("crit");
    expect(linkHealth(null, "NOT_SYNCHRONIZING")).toBe("crit");
    expect(linkHealth(null, "HEALTHY")).toBe("ok");
    expect(linkHealth(null, null)).toBe("unknown");
    expect(lagLabel(0.2)).toBe("< 1 s");
    expect(lagLabel(300)).toBe("5 min");
  });

  it("renders the diagram with lag labels", async () => {
    const c = { id: "c9", name: "erp" };
    const p = server({ name: "ora-p", engine: "oracle", cluster: c, cluster_role: "primary" });
    const r = server({ name: "ora-s", engine: "oracle", cluster: c, cluster_role: "replica" });
    mockFetch((url) => {
      if (url.pathname === `/api/v1/servers/${p.id}/replication`) return { body: { role: "primary", replicas: [], upstream: null, lag_seconds: null, notes: [] } };
      if (url.pathname === `/api/v1/servers/${r.id}/replication`) return { body: { role: "replica", replicas: [], upstream: null, lag_seconds: 12, notes: [] } };
      return undefined;
    });
    renderWithProviders(<ReplicationTopology servers={[p, r]} />);
    expect(await screen.findByText(/12 s/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /primary ora-p/ })).toBeInTheDocument();
    expect(screen.getByText(/1 replication link: ora-p to ora-s, 12 s, Lagging/)).toBeInTheDocument();
  });
});

const plan: PlanNode = {
  id: 0, operator: "Hash Join", detail: "Hash Cond: (o.customer_id = c.id)", relation: null, index: null, startup_cost: 10, total_cost: 900,
  estimated_rows: 100, actual_rows: 5000, actual_total_ms: 42, loops: 1, shared_hit: 10, shared_read: 2, temp_written: null,
  sort_space_type: null, filter: null, rows_removed_by_filter: null, self_cost_pct: 20, warnings: [], children: [
    { id: 1, operator: "Seq Scan", detail: null, relation: "orders", index: null, startup_cost: 0, total_cost: 700, estimated_rows: 20000,
      actual_rows: 20000, actual_total_ms: 30, loops: 1, shared_hit: null, shared_read: null, temp_written: null, sort_space_type: null,
      filter: "(total > 50)", rows_removed_by_filter: 100, self_cost_pct: 70, warnings: ["large sequential scan"], children: [] },
    { id: 2, operator: "Index Scan", detail: null, relation: "customers", index: "customers_pkey", startup_cost: 0, total_cost: 100,
      estimated_rows: 5000, actual_rows: 5000, actual_total_ms: 2, loops: 1, shared_hit: null, shared_read: null, temp_written: null,
      sort_space_type: null, filter: null, rows_removed_by_filter: null, self_cost_pct: 10, warnings: [], children: [] },
  ],
};

describe("plan graph", () => {
  it("lays out parents above their children and flags misestimates", () => {
    const { items, links } = layoutPlan(plan);
    const root = items.find((i) => i.node.id === 0)!;
    const kids = items.filter((i) => i.node.id !== 0);
    expect(kids.every((k) => k.y > root.y)).toBe(true);
    expect(root.x).toBe((kids[0].x + kids[1].x) / 2);
    expect(links).toHaveLength(2);
    expect(misestimate(plan)).toBe(50);
    expect(misestimate(plan.children[0])).toBeNull();
  });

  it("selects the most expensive step and shows its details", async () => {
    renderWithProviders(<PlanGraph root={plan} analyzed issues={[{ node_id: 1, severity: "medium", code: "SEQ", title: "Sequential scan on a large table", detail: "Consider an index on total" }]} />);
    const details = screen.getByRole("complementary", { name: "Plan node details" });
    expect(within(details).getByText("Seq Scan")).toBeInTheDocument();
    expect(within(details).getByText(/Consider an index on total/)).toBeInTheDocument();
    expect(screen.getByText("50× more rows than estimated")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /Index Scan on customers/ }));
    await waitFor(() => expect(within(details).getByText("customers_pkey")).toBeInTheDocument());
  });

  it("formats raw vendor plans", () => {
    expect(formatRawPlan('[{"Plan":{"Node Type":"Seq Scan"}}]').format).toBe("json");
    const xml = formatRawPlan("<ShowPlanXML><BatchSequence><Batch/></BatchSequence></ShowPlanXML>");
    expect(xml.format).toBe("xml");
    expect(xml.text.split("\n")).toEqual(["<ShowPlanXML>", "  <BatchSequence>", "    <Batch/>", "  </BatchSequence>", "</ShowPlanXML>"]);
    expect(formatRawPlan("Plan hash value: 1").format).toBe("text");
    expect(formatRawPlan({ a: 1 }).text).toContain('"a": 1');
  });

  it("switches between graph, tree and raw views", async () => {
    renderWithProviders(<PlanViewer root={plan} analyzed raw={{ Plan: { "Node Type": "Hash Join" } }} engine="postgresql" />);
    await userEvent.click(screen.getByRole("tab", { name: "Tree" }));
    expect(screen.getByRole("list", { name: "Execution plan" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /Raw/ }));
    expect(screen.getByText(/"Node Type": "Hash Join"/)).toBeInTheDocument();
  });
});
