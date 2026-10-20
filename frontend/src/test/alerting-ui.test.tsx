import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { Toaster } from "sonner";
import { AppRoutes } from "@/App";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ServerAlertsPanel } from "@/features/alerts/server-alerts";
import { AuthProvider } from "@/lib/auth";
import { ThemeProvider } from "@/lib/theme";
import { mockFetch } from "./utils";

const ME = (perms: string[], platform: string[] = []) => ({
  id: "u1", username: "dba", display_name: "Dana", email: null, auth_source: "local", must_change_password: false,
  permissions: perms, platform_permissions: platform, grants: [{ role: "DBA Administrator", scope_type: "global", scope_id: null, scope_label: "Global" }],
});
const ENVS = [{ id: "e1", name: "Production", tier: "production" }, { id: "e2", name: "UAT", tier: "uat" }];
const ENGINES = [{ engine: "postgresql", display_name: "PostgreSQL" }, { engine: "oracle", display_name: "Oracle Database" }];
const ALERT = {
  id: "a1", rule_id: "r1", rule_name: "Connection pressure", rule_kind: "metric", operator: ">=", server_id: "s1", server_name: "PG-PROD-01",
  engine: "postgresql", environment: "Production", status: "firing", severity: "high", title: "Connection pressure on PG-PROD-01",
  message: "avg(connections.utilization, 15m) = 96 (>= 90)", metric: "connections.utilization", value: 96, threshold: 90, is_demo: false, suppressed: false,
  pending_since: "2026-10-18T10:00:00Z", fired_at: "2026-10-18T10:05:00Z", acknowledged_at: null, acknowledged_by: null, ack_comment: null,
  resolved_at: null, resolved_by: null, notified_at: "2026-10-18T10:05:00Z", notify_count: 2,
};
const CHANNEL = {
  id: "c1", name: "on-call", kind: "webhook", enabled: true, min_severity: "high", send_for_simulated: false, preset: "slack",
  routes: { subscribe_all: true, environment_ids: ["e1"], tags: [], engines: [] },
  config: { url_display: "https://hooks.slack.com/…", header_names: [] }, has_url: true, has_signing_secret: false, has_smtp_password: false, created_by: "admin", created_at: "2026-10-18T09:00:00Z",
};
const WINDOW = {
  id: "w1", name: "Nightly patching", reason: null, scope_type: "server", scope_id: "s1", starts_at: "2026-10-19T22:00:00Z", ends_at: "2026-10-20T00:00:00Z",
  recurrence: "weekly", weekdays: [1, 3], time_zone: "Europe/Berlin", repeat_until: null, state: "scheduled",
  current_or_next_start: "2026-10-19T22:00:00Z", current_or_next_end: "2026-10-20T00:00:00Z", created_by: "admin",
};
const CATALOG = [{ key: "connections.utilization", label: "Connection utilization", unit: "%", description: "Share of max connections in use" }];

function providers(children: React.ReactNode, route = "/") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider><QueryClientProvider client={qc}><MemoryRouter initialEntries={[route]}><AuthProvider><TooltipProvider>{children}</TooltipProvider></AuthProvider></MemoryRouter></QueryClientProvider><Toaster /></ThemeProvider>,
  );
}
const renderApp = (route: string) => providers(<AppRoutes />, route);
const json = (init: RequestInit) => JSON.parse(String(init.body));
const page = <T,>(items: T[]) => ({ items, total: items.length, page: 1, page_size: 500 });

describe("alerting UI", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends a test notification and shows the delivery log with a retrying row", async () => {
    let tested = false;
    mockFetch((url, init) => {
      if (url.pathname === "/api/v1/auth/me") return { body: ME(["server.view"], ["channels.manage"]) };
      if (url.pathname === "/api/v1/notification-channels") return { body: [CHANNEL] };
      if (url.pathname === "/api/v1/notification-channels/c1/test" && init.method === "POST") {
        tested = true;
        return { body: { status: "sent", response_code: 200, error: null, attempts: 1 } };
      }
      if (url.pathname === "/api/v1/notification-channels/c1/deliveries") return { body: [
        { id: 2, alert_id: "a1", event: "firing", status: "retrying", attempts: 1, response_code: 503, error: "receiver answered HTTP 503", created_at: "2026-10-18T10:05:00Z", next_attempt_at: "2026-10-18T10:06:00Z" },
        { id: 1, alert_id: null, event: "test", status: "sent", attempts: 1, response_code: 200, error: null, created_at: "2026-10-18T10:00:00Z", next_attempt_at: null },
      ] };
      return undefined;
    });
    renderApp("/platform/notifications");
    expect(await screen.findByText("Slack")).toBeInTheDocument();
    expect(screen.getByText("all alerts")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Test/ }));
    expect(await screen.findByText(/Test notification sent through on-call \(HTTP 200\)/)).toBeInTheDocument();
    expect(tested).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Delivery log of on-call" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("retrying")).toBeInTheDocument();
    expect(within(dialog).getByText(/receiver answered HTTP 503 — next attempt/)).toBeInTheDocument();
  });

  it("creates a Teams channel with routing filters", async () => {
    let saved: Record<string, unknown> | null = null;
    mockFetch((url, init) => {
      if (url.pathname === "/api/v1/auth/me") return { body: ME(["server.view"], ["channels.manage"]) };
      if (url.pathname === "/api/v1/notification-channels" && init.method === "POST") { saved = json(init); return { status: 201, body: { ...CHANNEL, id: "c2" } }; }
      if (url.pathname === "/api/v1/notification-channels") return { body: [] };
      if (url.pathname === "/api/v1/environments") return { body: ENVS };
      if (url.pathname === "/api/v1/engines") return { body: ENGINES };
      return undefined;
    });
    renderApp("/platform/notifications");
    await userEvent.click(await screen.findByRole("button", { name: /New channel/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Name"), "teams-dba");
    await userEvent.selectOptions(within(dialog).getByLabelText("Payload format"), "teams");
    await userEvent.type(within(dialog).getByLabelText("URL"), "https://example.webhook.office.com/workflows/1");
    await userEvent.click(within(dialog).getByRole("switch", { name: "Receive all alerts" }));
    await userEvent.click(await within(dialog).findByRole("checkbox", { name: "Production" }));
    await userEvent.click(await within(dialog).findByRole("checkbox", { name: "Oracle Database" }));
    await userEvent.type(within(dialog).getByLabelText(/Only servers with tags/), "team=payments, tier");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create channel" }));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved).toMatchObject({
      name: "teams-dba", kind: "webhook", preset: "teams",
      routes: { subscribe_all: true, environment_ids: ["e1"], engines: ["oracle"], tags: ["team=payments", "tier"] },
    });
  });

  it("creates a rule with tag scope, a history window and re-notification", async () => {
    let saved: Record<string, unknown> | null = null;
    mockFetch((url, init) => {
      if (url.pathname === "/api/v1/auth/me") return { body: ME(["server.view", "alerts.view"], ["alerts.manage"]) };
      if (url.pathname === "/api/v1/alert-rules" && init.method === "POST") { saved = json(init); return { status: 201, body: {} }; }
      if (url.pathname === "/api/v1/alert-rules") return { body: page([]) };
      if (url.pathname === "/api/v1/metrics/catalog") return { body: CATALOG };
      if (url.pathname === "/api/v1/notification-channels") return { body: [] };
      if (url.pathname === "/api/v1/environments") return { body: ENVS };
      if (url.pathname === "/api/v1/servers") return { body: page([]) };
      return undefined;
    });
    renderApp("/alerts/rules");
    await userEvent.click(await screen.findByRole("button", { name: /New rule/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Name"), "Sustained pressure");
    await screen.findByRole("option", { name: /Connection utilization/ });
    await userEvent.selectOptions(within(dialog).getByLabelText("Evaluate"), "avg");
    const win = within(dialog).getByLabelText("Window (minutes)");
    await userEvent.clear(win);
    await userEvent.type(win, "15");
    await userEvent.click(within(dialog).getByRole("button", { name: "Continue" }));
    await userEvent.selectOptions(within(dialog).getByLabelText("Applies to"), "tag");
    const cont = within(dialog).getByRole("button", { name: "Continue" });
    expect(cont).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Tag"), "team=payments");
    await userEvent.click(cont);
    const re = within(dialog).getByLabelText(/Re-notify every/);
    await userEvent.clear(re);
    await userEvent.type(re, "30");
    expect(within(dialog).getByText(/avg\(connections\.utilization, 15m\) >= 90/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Create rule" }));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved).toMatchObject({ aggregation: "avg", window_seconds: 900, scope_type: "tag", scope_id: "team=payments", renotify_minutes: 30 });
  });

  it("schedules a weekly window in a time zone and edits it with PUT", async () => {
    const calls: { method: string; path: string; body: Record<string, unknown> }[] = [];
    mockFetch((url, init) => {
      if (url.pathname === "/api/v1/auth/me") return { body: ME(["server.view", "alerts.view"], ["alerts.manage"]) };
      if (url.pathname.startsWith("/api/v1/maintenance-windows") && (init.method === "POST" || init.method === "PUT")) {
        calls.push({ method: init.method, path: url.pathname, body: json(init) });
        return { body: WINDOW };
      }
      if (url.pathname === "/api/v1/maintenance-windows") return { body: page([WINDOW]) };
      if (url.pathname === "/api/v1/environments") return { body: ENVS };
      if (url.pathname === "/api/v1/servers") return { body: page([{ id: "s1", name: "PG-PROD-01" }]) };
      return undefined;
    });
    renderApp("/alerts/maintenance");
    expect(await screen.findByText("Weekly on Mon, Wed (Europe/Berlin)")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Schedule window/ }));
    let dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Name"), "Weekend backups");
    await userEvent.selectOptions(within(dialog).getByLabelText("Repeats"), "weekly");
    for (const d of within(dialog).getAllByRole("checkbox")) if ((d as HTMLInputElement).checked) await userEvent.click(d);
    await userEvent.click(within(dialog).getByRole("checkbox", { name: "Sat" }));
    await userEvent.click(within(dialog).getByRole("checkbox", { name: "Sun" }));
    await userEvent.selectOptions(within(dialog).getByLabelText("Time zone"), "UTC");
    await userEvent.click(within(dialog).getByRole("button", { name: "Schedule" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toMatchObject({ method: "POST", body: { name: "Weekend backups", recurrence: "weekly", weekdays: [6, 7], time_zone: "UTC" } });

    await userEvent.click(await screen.findByRole("button", { name: "Edit Nightly patching" }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Time zone")).toHaveValue("Europe/Berlin");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[1]).toMatchObject({ method: "PUT", path: "/api/v1/maintenance-windows/w1", body: { recurrence: "weekly", weekdays: [1, 3], scope_type: "server", scope_id: "s1" } });
  });

  it("shows the alert detail with comments and notifications and adds a comment", async () => {
    let posted: Record<string, unknown> | null = null;
    const comments = [{ id: "k1", author: "dba", body: "looking into it", created_at: "2026-10-18T10:10:00Z" }];
    mockFetch((url, init) => {
      if (url.pathname === "/api/v1/auth/me") return { body: ME(["server.view", "alerts.view", "alerts.acknowledge"]) };
      if (url.pathname === "/api/v1/alerts/summary") return { body: { active: 1, unacknowledged: 1, by_severity: { high: 1 }, visible: true } };
      if (url.pathname === "/api/v1/alerts") return { body: { items: [ALERT], total: 1, page: 1, page_size: 50 } };
      if (url.pathname === "/api/v1/alerts/a1/comments" && init.method === "POST") {
        posted = json(init);
        comments.push({ id: "k2", author: "dba", body: String(posted?.body), created_at: "2026-10-18T10:20:00Z" });
        return { status: 201, body: comments[1] };
      }
      if (url.pathname === "/api/v1/alerts/a1") return { body: { ...ALERT, comments: [...comments], notifications: [
        { channel: "on-call", event: "firing", status: "sent", attempts: 1, error: null, created_at: "2026-10-18T10:05:00Z", next_attempt_at: null },
        { channel: "mail", event: "firing", status: "retrying", attempts: 2, error: "SMTPServerDisconnected", created_at: "2026-10-18T10:05:00Z", next_attempt_at: "2026-10-18T10:10:00Z" },
      ] } };
      return undefined;
    });
    renderApp("/alerts");
    await userEvent.click(await screen.findByRole("button", { name: "Connection pressure on PG-PROD-01" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("looking into it")).toBeInTheDocument();
    expect(within(dialog).getByText(/2 notifications/)).toBeInTheDocument();
    expect(within(dialog).getByText("retrying")).toBeInTheDocument();
    expect(within(dialog).getByText(/SMTPServerDisconnected — next attempt/)).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Add a comment"), "failover at 11:00");
    await userEvent.click(within(dialog).getByRole("button", { name: "Comment" }));
    await waitFor(() => expect(posted).toEqual({ body: "failover at 11:00" }));
    expect(await within(dialog).findByText("failover at 11:00")).toBeInTheDocument();
  });

  it("shows active alerts and maintenance windows on the server page panel", async () => {
    mockFetch((url) => {
      if (url.pathname === "/api/v1/auth/me") return { body: ME(["server.view", "alerts.view"]) };
      if (url.pathname === "/api/v1/alerts") {
        expect(url.searchParams.get("server_id")).toBe("s1");
        return { body: { items: [ALERT], total: 1, page: 1, page_size: 50 } };
      }
      if (url.pathname === "/api/v1/maintenance-windows") {
        expect(url.searchParams.get("server_id")).toBe("s1");
        return { body: page([WINDOW]) };
      }
      return undefined;
    });
    providers(<ServerAlertsPanel serverId="s1" />);
    const panel = await screen.findByRole("region", { name: "Alerts and maintenance" });
    expect(await within(panel).findByRole("button", { name: "Connection pressure on PG-PROD-01" })).toBeInTheDocument();
    expect(within(panel).getByText("Nightly patching")).toBeInTheDocument();
    expect(within(panel).getByText(/Weekly on Mon, Wed/)).toBeInTheDocument();
  });

  it("marks servers with active alerts in the fleet list", async () => {
    const srv = {
      id: "s1", name: "PG-PROD-01", description: null, engine: "postgresql", engine_label: "PostgreSQL", version: "16", host: "pg1", port: 5432,
      default_database: null, deployment: "self-managed", cloud_provider: null, environment: { id: "e1", name: "Production", tier: "production" },
      site: null, cluster: null, cluster_role: null, application: null, owner: null, business_service: null, tags: [], tls_mode: "require",
      monitoring_enabled: true, collection_interval_s: 60, licensed_features: [], is_demo: false, health_status: "healthy", health_reasons: [],
      connection_status: "ok", last_error: null, last_connected_at: null, last_collected_at: null, circuit_open_until: null, key_metrics: {},
      database_count: 1, open_recommendations: 0, open_findings: 0, implementation: "implemented", created_at: "2026-01-01T00:00:00Z",
    };
    mockFetch((url) => {
      if (url.pathname === "/api/v1/auth/me") return { body: ME(["server.view", "alerts.view"]) };
      if (url.pathname === "/api/v1/servers") return { body: { items: [srv, { ...srv, id: "s2", name: "PG-PROD-02" }], total: 2, page: 1, page_size: 50 } };
      if (url.pathname === "/api/v1/alerts/summary") {
        if (url.searchParams.get("by_server") === "true") return { body: { active: 2, by_severity: { critical: 2 }, visible: true, by_server: { s1: { active: 2, unacknowledged: 1, top_severity: "critical" } } } };
        return { body: { active: 2, by_severity: { critical: 2 }, visible: true } };
      }
      if (url.pathname === "/api/v1/environments" || url.pathname === "/api/v1/engines") return { body: [] };
      return undefined;
    });
    renderApp("/fleet/servers");
    expect(await screen.findByText("PG-PROD-02")).toBeInTheDocument();
    expect(await screen.findByText(/2 active alerts/)).toBeInTheDocument();
    expect(screen.getAllByText(/active alert/)).toHaveLength(1);
  });
});
