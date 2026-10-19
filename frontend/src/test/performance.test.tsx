import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Route, Routes } from "react-router-dom";
import { ruleCondition } from "@/features/alerts/alerts";
import {
  CapacityForecastPage, PlanComparePage, QueryComparePage, QueryRegressionsPage, RecommendationValidationPage, ValidationDetail,
} from "@/features/performance/intelligence";
import { mockFetch, renderWithProviders } from "./utils";

const SERVER = {
  id: "s1", name: "ORA-PROD-01", engine: "oracle", engine_label: "Oracle", implementation: "native", health_status: "healthy", is_demo: false,
  environment: { id: "e1", name: "Production" }, capabilities: {}, permissions: ["server.view", "query.view", "query.explain", "query.view_text"],
};
const servers = (url: URL) => {
  if (url.pathname === "/api/v1/servers") return { body: { items: [SERVER], total: 1, page: 1, page_size: 500 } };
  if (url.pathname === "/api/v1/servers/s1") return { body: SERVER };
  return undefined;
};

describe("z-score alert rules", () => {
  it("describes the baseline condition", () => {
    const base = { kind: "metric" as const, metric: "cpu.percent", operator: ">", threshold: 3, health_states: [], for_seconds: 0 };
    expect(ruleCondition({ ...base, aggregation: "zscore", window_seconds: 0, baseline_days: 14 })).toBe("zscore(cpu.percent, 14d baseline) > 3");
    expect(ruleCondition({ ...base, aggregation: "zscore", window_seconds: 600, baseline_days: 7 })).toBe("zscore(avg(cpu.percent, 10m), 7d baseline) > 3");
    expect(ruleCondition({ ...base, aggregation: "avg", window_seconds: 600 })).toBe("avg(cpu.percent, 10m) > 3");
  });
});

describe("query regressions", () => {
  it("lists slower statements and plan changes with links to compare and explain", async () => {
    mockFetch((url) => servers(url) ?? (url.pathname === "/api/v1/servers/s1/query-regressions" ? {
      body: {
        plan_identity: true, notes: [], text_masked: false,
        settings: { regression_threshold_pct: 50, regression_min_calls: 20, regression_min_delta_ms: 5, regression_recent_hours: 1, baseline_days: 7 },
        items: [{
          database: "SHOP", fingerprint: "ab12", kind: "latency+plan", regressed: true, plan_changed: true, baseline_mean_ms: 10, recent_mean_ms: 40,
          change_pct: 300, delta_ms: 30, baseline_calls: 4000, recent_calls: 120, baseline_plan_id: "111", recent_plan_id: "222",
          baseline_plans: 1, recent_plans: 2, excess_ms: 3600, query_text: "SELECT * FROM orders WHERE id = ?",
        }],
      },
    } : undefined));
    renderWithProviders(<Routes><Route path="/queries/regressions" element={<QueryRegressionsPage />} /></Routes>, { route: "/queries/regressions?server=s1" });
    expect(await screen.findByText("SELECT * FROM orders WHERE id = ?")).toBeInTheDocument();
    expect(screen.getByText("slower")).toBeInTheDocument();
    expect(screen.getByText("plan changed")).toBeInTheDocument();
    expect(screen.getByText("111 → 222")).toBeInTheDocument();
    expect(screen.getByText("+300%")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Compare" })).toHaveAttribute("href", "/queries/compare?server=s1&fp=ab12");
    expect(screen.getByRole("link", { name: "Explain" }).getAttribute("href")).toContain("fp=ab12");
    expect(screen.getByText(/at least 50% and 5 ms/)).toBeInTheDocument();
  });
});

describe("period comparison", () => {
  it("summarizes both periods and filters by status", async () => {
    const row = (fp: string, status: string, a: number, b: number) => ({
      database: "SHOP", fingerprint: fp, status, query_text: `SELECT ${fp}`, mean_change_pct: a ? ((b - a) / a) * 100 : null, calls_change_pct: 0,
      total_ms_delta: (b - a) * 100, a: { calls: a ? 100 : 0, total_ms: a * 100, mean_ms: a || null, rows: 1, cpu_ms: null, logical_reads: null, physical_reads: 5 },
      b: { calls: 100, total_ms: b * 100, mean_ms: b, rows: 1, cpu_ms: null, logical_reads: null, physical_reads: 9 },
    });
    const fetch = mockFetch((url) => servers(url) ?? (url.pathname === "/api/v1/servers/s1/query-compare" ? {
      body: {
        a: { start: "2026-10-17T12:00:00Z", end: "2026-10-18T12:00:00Z" }, b: { start: "2026-10-18T12:00:00Z", end: "2026-10-19T12:00:00Z" },
        summary: { a: { calls: 200, total_ms: 2000, mean_ms: 10 }, b: { calls: 300, total_ms: 6000, mean_ms: 20 } },
        status_counts: { slower: 1, new: 1 }, total_statements: 2, text_masked: false, notes: [],
        items: [row("q1", "slower", 10, 50), row("q2", "new", 0, 5)],
      },
    } : undefined));
    renderWithProviders(<Routes><Route path="/queries/compare" element={<QueryComparePage />} /></Routes>, { route: "/queries/compare?server=s1" });
    expect(await screen.findByText("SELECT q1")).toBeInTheDocument();
    expect(screen.getByText("Total execution time A → B")).toBeInTheDocument();
    expect(fetch.mock.calls.some(([u]) => String(u).includes("preset=day_vs_previous"))).toBe(true);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Show" }), "new");
    expect(screen.queryByText("SELECT q1")).not.toBeInTheDocument();
    expect(screen.getByText("SELECT q2")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Compare" }), "custom");
    await waitFor(() => expect(fetch.mock.calls.some(([u]) => String(u).includes("a_start="))).toBe(true));
  });
});

describe("plan comparison", () => {
  it("compares two saved plans", async () => {
    const plan = (id: string, label: string, cost: number) => ({
      id, server_id: "s1", server_name: "ORA-PROD-01", engine: "oracle", database: null, fingerprint: null, label, statement: "SELECT 1",
      mode: "estimate", total_cost: cost, created_by: "dba", created_at: "2026-10-19T10:00:00Z",
    });
    const node = { id: 0, operator: "TABLE ACCESS FULL", detail: null, relation: "ORDERS", index: null, startup_cost: null, total_cost: 10, estimated_rows: 1,
      actual_rows: null, actual_total_ms: null, loops: null, shared_hit: null, shared_read: null, temp_written: null, sort_space_type: null, filter: null,
      rows_removed_by_filter: null, self_cost_pct: 100, warnings: [], children: [] };
    const fetch = mockFetch((url) => servers(url) ?? (url.pathname === "/api/v1/servers/s1/plans" ? { body: [plan("p1", "before index", 900), plan("p2", "after index", 90)] }
      : url.pathname === "/api/v1/plans/compare" ? {
        body: {
          a: { ...plan("p1", "before index", 900), plan: { engine: "oracle", mode: "estimate", root: node, issues: [], notes: [] } },
          b: { ...plan("p2", "after index", 90), plan: { engine: "oracle", mode: "estimate", root: { ...node, operator: "INDEX RANGE SCAN", index: "ORD_IX" }, issues: [], notes: [] } },
          same_statement: true,
          diff: {
            same_engine: true, identical_shape: false, cost_change_pct: -90, execution_change_pct: null, a: { nodes: 1, total_cost: 900 }, b: { nodes: 1, total_cost: 90 },
            operations_added: ["INDEX RANGE SCAN on ORDERS using ORD_IX"], operations_removed: ["TABLE ACCESS FULL on ORDERS"],
            access_path_changes: [{ relation: "ORDERS", a: ["TABLE ACCESS FULL"], b: ["INDEX RANGE SCAN (ORD_IX)"] }], issues_added: [], issues_resolved: [], notes: [],
          },
        },
      } : undefined));
    renderWithProviders(<Routes><Route path="/queries/plans/compare" element={<PlanComparePage />} /></Routes>, { route: "/queries/plans/compare?server=s1" });
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select before index" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Select after index" }));
    expect(await screen.findByText("INDEX RANGE SCAN on ORDERS using ORD_IX")).toBeInTheDocument();
    expect(screen.getByText("-90%")).toBeInTheDocument();
    expect(screen.getByText("INDEX RANGE SCAN (ORD_IX)")).toBeInTheDocument();
    const cmp = fetch.mock.calls.find(([u]) => String(u).includes("/plans/compare"));
    expect(String(cmp?.[0])).toContain("a=p1");
    expect(String(cmp?.[0])).toContain("b=p2");
  });
});

describe("capacity forecast", () => {
  it("shows days to full with the band and opens the chart", async () => {
    const f = {
      server_id: "s1", server_name: "ORA-PROD-01", engine: "oracle", scope: "tablespace", object: "USERS", points: 21, history_days: 20,
      current_bytes: 2e9, capacity_bytes: 3e9, capacity_basis: "tablespace maximum size", growth_bytes_per_day: 5e7, growth_low: 4e7, growth_high: 6e7,
      days_to_full: { likely: 20, earliest: 15, latest: 26 }, reason: null,
    };
    const fetch = mockFetch((url) => url.pathname === "/api/v1/capacity/forecast" ? { body: { items: [f], total: 1 } }
      : url.pathname === "/api/v1/servers/s1/capacity-forecast" ? { body: { items: [{ ...f, band: [{ t: "2026-10-20T00:00:00Z", likely: 2.05e9, low: 2e9, high: 2.1e9 }], history: [{ t: "2026-10-19T00:00:00Z", v: 2e9 }] }] } }
        : undefined);
    renderWithProviders(<CapacityForecastPage />);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Scope" }), "tablespace");
    expect(await screen.findByText("USERS")).toBeInTheDocument();
    expect(screen.getByText("15 days")).toBeInTheDocument();
    expect(screen.getByText(/20 days · 26 days/)).toBeInTheDocument();
    await userEvent.click(screen.getByText("USERS"));
    expect(await screen.findByRole("img", { name: /forecast with a 90 percent band/ })).toBeInTheDocument();
    expect(fetch.mock.calls.some(([u]) => String(u).includes("/servers/s1/capacity-forecast?scope=tablespace"))).toBe(true);
  });
});

describe("recommendation validation", () => {
  it("renders before/after with the outcome", () => {
    renderWithProviders(<ValidationDetail v={{
      status: "improved", method: { kind: "query", metric: "mean_ms", better: "lower", fingerprint: "ab" }, before: { value: 50, samples: 4000 },
      after: { value: 10, samples: 3900 }, change_pct: -80, window_hours: 24,
    }} />);
    const box = screen.getByTestId("validation");
    expect(within(box).getByText("improved")).toBeInTheDocument();
    expect(box).toHaveTextContent("Change: -80%");
    expect(box).toHaveTextContent("mean statement latency (lower is better)");
  });
  it("summarizes outcomes", async () => {
    mockFetch((url) => url.pathname === "/api/v1/recommendations/validation-summary" ? {
      body: {
        counts: { improved: 3, regressed: 1, measuring: 2 },
        recent: [{
          id: "r1", rule_id: "GEN-PERF-011", server_id: "s1", server_name: "ORA-PROD-01", engine: "oracle", environment: null, is_demo: false, database: null,
          affected_object: "query:ab", category: "queries", severity: "medium", title: "Query is slower than its baseline", finding: "", evidence: {},
          recommendation: "", risk: "", confidence: 0.8, guidance: null, status: "resolved", status_reason: null, status_changed_by: null,
          first_seen_at: "2026-10-18T00:00:00Z", last_seen_at: "2026-10-18T00:00:00Z", resolved_at: "2026-10-18T06:00:00Z",
          validation: { status: "regressed", before: { value: 10, samples: 100 }, after: { value: 15, samples: 100 }, change_pct: 50, window_hours: 24 },
        }],
      },
    } : undefined);
    renderWithProviders(<RecommendationValidationPage />);
    expect(await screen.findByText("Query is slower than its baseline")).toBeInTheDocument();
    expect(screen.getByText("+50%")).toBeInTheDocument();
    expect(screen.getByText("Improved").parentElement).toHaveTextContent("3");
  });
});
