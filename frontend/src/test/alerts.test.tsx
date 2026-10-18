import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AppRoutes } from "@/App";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthProvider } from "@/lib/auth";
import { ThemeProvider } from "@/lib/theme";
import { mockFetch } from "./utils";

const ME = (perms: string[], platform: string[] = []) => ({
  id: "u1", username: "dba", display_name: "Dana", email: null, auth_source: "local", must_change_password: false,
  permissions: perms, platform_permissions: platform, grants: [{ role: "DBA Administrator", scope_type: "global", scope_id: null, scope_label: "Global" }],
});

const ALERT = {
  id: "a1", rule_id: "r1", rule_name: "Connection pressure", rule_kind: "metric", operator: ">=", server_id: "s1", server_name: "PG-PROD-01",
  engine: "postgresql", environment: "Production", status: "firing", severity: "high", title: "Connection pressure on PG-PROD-01",
  message: "connections.utilization = 96 (>= 90)", metric: "connections.utilization", value: 96, threshold: 90, is_demo: true, suppressed: false,
  pending_since: new Date().toISOString(), fired_at: new Date().toISOString(), acknowledged_at: null, acknowledged_by: null, ack_comment: null,
  resolved_at: null, resolved_by: null,
};
const ABOUT = {
  product: "DB Manager", description: "x", version: { application: "0.11.0-dev", backend: "0.11.0-dev", build: null, git_commit: null, build_date: null, schema: "0004" },
  milestone: { version: "v0.11", name: "First public release" }, license: null,
  links: { documentation: null, github: "https://github.com/hbenabdalla/dbmanager", roadmap: "https://github.com/hbenabdalla/dbmanager/blob/main/ROADMAP.md" },
  engines: [{ engine: "postgresql", display_name: "PostgreSQL", support_level: "supported", validation: "validated", validated_version: "16.4" }],
  status: { api: "ok", database: "ok", timeseries: "ok", background_jobs: "running" },
};

function renderApp(route: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider><QueryClientProvider client={qc}><MemoryRouter initialEntries={[route]}><AuthProvider><TooltipProvider><AppRoutes /></TooltipProvider></AuthProvider></MemoryRouter></QueryClientProvider></ThemeProvider>,
  );
}

describe("alerts", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lists active alerts, shows the top-bar count and acknowledges with a comment", async () => {
    let acked: { path: string; body: unknown } | null = null;
    let status = "firing";
    mockFetch((url, init) => {
      if (url.pathname === "/api/v1/auth/me") return { body: ME(["server.view", "alerts.view", "alerts.acknowledge"]) };
      if (url.pathname === "/api/v1/about") return { body: ABOUT };
      if (url.pathname === "/api/v1/alerts/summary") return { body: { active: 1, unacknowledged: status === "firing" ? 1 : 0, by_severity: { high: 1 }, visible: true } };
      if (url.pathname === "/api/v1/alerts") {
        expect(url.searchParams.getAll("status")).toEqual(["firing", "acknowledged"]);
        return { body: { items: [{ ...ALERT, status, acknowledged_by: status === "acknowledged" ? "dba" : null }], total: 1, page: 1, page_size: 50 } };
      }
      if (url.pathname === "/api/v1/alerts/a1/acknowledge" && init.method === "POST") {
        acked = { path: url.pathname, body: JSON.parse(String(init.body)) };
        status = "acknowledged";
        return { body: { ...ALERT, status } };
      }
      return undefined;
    });
    renderApp("/alerts");
    expect(await screen.findByText("Connection pressure on PG-PROD-01")).toBeInTheDocument();
    expect(screen.getByText("Simulated")).toBeInTheDocument();
    expect(await screen.findByTestId("alert-count")).toHaveTextContent("1");
    await userEvent.click(screen.getByRole("button", { name: /Acknowledge/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Comment (optional)"), "on it");
    await userEvent.click(within(dialog).getByRole("button", { name: "Acknowledge" }));
    await waitFor(() => expect(acked).toEqual({ path: "/api/v1/alerts/a1/acknowledge", body: { comment: "on it" } }));
    expect(await screen.findByText("by dba")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Acknowledge$/ })).not.toBeInTheDocument();
  });

  it("hides acknowledge actions, the bell and management without permissions", async () => {
    mockFetch((url) => {
      if (url.pathname === "/api/v1/auth/me") return { body: ME(["server.view", "alerts.view"]) };
      if (url.pathname === "/api/v1/alerts/summary") return { body: { active: 0, by_severity: {}, visible: true } };
      if (url.pathname === "/api/v1/alerts") return { body: { items: [ALERT], total: 1, page: 1, page_size: 50 } };
      if (url.pathname === "/api/v1/alert-rules") return { body: { items: [], total: 0, page: 1, page_size: 500 } };
      return undefined;
    });
    renderApp("/alerts");
    expect(await screen.findByText("Connection pressure on PG-PROD-01")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Acknowledge/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Resolve/ })).not.toBeInTheDocument();
    expect(screen.queryByTestId("alert-count")).not.toBeInTheDocument();
  });

  it("creates a metric rule with the metric picker", async () => {
    let created: Record<string, unknown> | null = null;
    mockFetch((url, init) => {
      if (url.pathname === "/api/v1/auth/me") return { body: ME(["server.view", "alerts.view"], ["alerts.manage", "channels.manage"]) };
      if (url.pathname === "/api/v1/alert-rules" && init.method === "POST") {
        created = JSON.parse(String(init.body));
        return { status: 201, body: { id: "r9", ...created } };
      }
      if (url.pathname === "/api/v1/alert-rules") return { body: { items: [], total: 0, page: 1, page_size: 500 } };
      if (url.pathname === "/api/v1/metrics/catalog") return { body: [
        { key: "connections.utilization", label: "Connection utilization", unit: "%", description: "Used / max connections" },
        { key: "replication.lag_seconds", label: "Replication lag", unit: "s", description: "Replica delay" },
      ] };
      if (url.pathname === "/api/v1/notification-channels") return { body: [{ id: "c1", name: "on-call", kind: "webhook", enabled: true }] };
      if (url.pathname === "/api/v1/environments") return { body: [] };
      if (url.pathname === "/api/v1/servers") return { body: { items: [], total: 0, page: 1, page_size: 500 } };
      return undefined;
    });
    renderApp("/alerts/rules");
    await userEvent.click(await screen.findByRole("button", { name: /New rule/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Name"), "Replica behind");
    await within(dialog).findByRole("option", { name: /Replication lag/ });
    await userEvent.selectOptions(within(dialog).getByLabelText("Metric"), "replication.lag_seconds");
    const threshold = within(dialog).getByLabelText("Threshold (s)");
    await userEvent.clear(threshold);
    await userEvent.type(threshold, "120");
    await userEvent.click(within(dialog).getByRole("button", { name: "Continue" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Continue" }));
    await userEvent.click(await within(dialog).findByRole("checkbox", { name: /on-call/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Create rule" }));
    await waitFor(() => expect(created).toMatchObject({ name: "Replica behind", kind: "metric", metric: "replication.lag_seconds", threshold: 120, channel_ids: ["c1"], scope_type: "all", scope_id: null }));
  });

  it("never shows a stored channel secret and sends it only when changed", async () => {
    let saved: Record<string, unknown> | null = null;
    mockFetch((url, init) => {
      if (url.pathname === "/api/v1/auth/me") return { body: ME(["server.view"], ["channels.manage"]) };
      if (url.pathname === "/api/v1/notification-channels/c1" && init.method === "PUT") {
        saved = JSON.parse(String(init.body));
        return { body: {} };
      }
      if (url.pathname === "/api/v1/notification-channels") return { body: [{
        id: "c1", name: "on-call", kind: "webhook", enabled: true, min_severity: "high", send_for_simulated: false,
        config: { url: "https://hooks.example.com/x", headers: {} }, has_signing_secret: true, has_smtp_password: false, created_by: "admin", created_at: new Date().toISOString(),
      }] };
      return undefined;
    });
    renderApp("/platform/notifications");
    expect(await screen.findByText("stored (encrypted)")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Edit on-call" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText(/Signing secret/)).toHaveValue("");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved).not.toHaveProperty("signing_secret");
    expect(saved).toMatchObject({ name: "on-call", kind: "webhook", config: { url: "https://hooks.example.com/x" } });
  });

  it("shows the About page with versions, links and engine validation", async () => {
    mockFetch((url) => {
      if (url.pathname === "/api/v1/auth/me") return { body: ME(["server.view"]) };
      if (url.pathname === "/api/v1/about") return { body: ABOUT };
      return undefined;
    });
    renderApp("/platform/about");
    expect(await screen.findByRole("heading", { name: "About DB Manager" })).toBeInTheDocument();
    expect(screen.getByText("Milestone v0.11 — First public release")).toBeInTheDocument();
    expect(screen.getByText("not configured (DBM_DOCUMENTATION_URL)")).toBeInTheDocument();
    expect(screen.getByText("not yet selected")).toBeInTheDocument();
    expect(screen.getByText("Validated")).toBeInTheDocument();
    // documentation is not configured, so the help menu offers GitHub and About only
    await userEvent.click(screen.getByRole("button", { name: "Help" }));
    expect(await screen.findByRole("menuitem", { name: /Source code on GitHub/ })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /Documentation/ })).not.toBeInTheDocument();
  });
});
