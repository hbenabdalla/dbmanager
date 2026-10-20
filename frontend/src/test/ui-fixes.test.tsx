import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { Toaster } from "sonner";
import { AppRoutes } from "@/App";
import { fetchAllServers } from "@/api/hooks";
import type { ServerDetail } from "@/api/types";
import { CommandPalette } from "@/components/command-palette";
import { TooltipProvider } from "@/components/ui/tooltip";
import { evaluateSummary } from "@/features/alerts/alerts";
import { ServerProtectionPanel } from "@/features/backup/protection";
import { comparableMetrics, type CatalogMetric } from "@/features/monitoring/monitoring";
import { SETTINGS_PERMS } from "@/features/server/server-page";
import { UsersTab } from "@/features/server/tabs/access";
import { AdministrationTab } from "@/features/server/tabs/administration";
import { SetupChecklist } from "@/features/setup/checklist";
import { AuthProvider } from "@/lib/auth";
import { NAV_GROUPS } from "@/lib/nav";
import { ThemeProvider } from "@/lib/theme";

const ME = (perms: string[], platform: string[] = []) => ({
  id: "u1", username: "dba", display_name: "Dana", email: null, auth_source: "local", must_change_password: false,
  permissions: perms, platform_permissions: platform, grants: [], mfa_enabled: true, mfa_enrollment_required: false,
});
type Reply = { status?: number; body: unknown } | undefined;
type Call = { path: string; method: string; body: unknown; query: URLSearchParams };

function stub(handler: (path: string, method: string, url: URL) => Reply) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init.method ?? "GET").toUpperCase();
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    const path = url.pathname.replace(/^\/api\/v1/, "");
    calls.push({ path, method, body, query: url.searchParams });
    const out = handler(path, method, url) ?? { status: 404, body: { error: { code: "not_found", message: "not mocked" } } };
    return new Response(JSON.stringify(out.body), { status: out.status ?? 200, headers: { "Content-Type": "application/json" } });
  }));
  return calls;
}
function providers(children: React.ReactNode, route = "/") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider><QueryClientProvider client={qc}><MemoryRouter initialEntries={[route]}><AuthProvider><TooltipProvider>{children}</TooltipProvider></AuthProvider></MemoryRouter></QueryClientProvider><Toaster /></ThemeProvider>,
  );
}
const last = (calls: Call[], method: string, path: string) => [...calls].reverse().find((c) => c.method === method && c.path === path);
const fail = (message = "boom"): Reply => ({ status: 500, body: { error: { code: "internal", message } } });

const SERVER = (permissions: string[], over: Record<string, unknown> = {}) => ({
  id: "s1", name: "PG-PROD-01", engine: "postgresql", engine_label: "PostgreSQL", implementation: "implemented", health_status: "healthy",
  health_reasons: [], is_demo: false, host: "10.0.0.5", port: 5432, default_database: null, description: null, version: "16.2",
  environment: { id: "e1", name: "Production", tier: "production" }, site: null, cluster: null, cluster_role: null, application: null,
  owner: null, tags: [], tls_mode: "verify-full", monitoring_enabled: true, collection_interval_s: 60, management_mode: "administration",
  connection_options: {}, credentials: { monitoring: null, admin: null, readonly: null }, capabilities: {}, server_info: null,
  unavailable_metrics: {}, key_metrics: {}, last_collected_at: null, circuit_open_until: null, last_error: null, permissions, ...over,
}) as unknown as ServerDetail;
const LIST = (items: unknown[]) => ({ body: { items, total: items.length, page: 1, page_size: 500 } });

afterEach(() => vi.unstubAllGlobals());

// ---- 1. administration instructions -------------------------------------------------------------
describe("guarded administration links", () => {
  it("points the Databases page at the selected server's Administration tab instead of the SQL workspace", async () => {
    stub((path) => {
      if (path === "/auth/me") return { body: ME(["server.view", "database.view"]) };
      if (path === "/servers") return LIST([SERVER(["database.view"])]);
      if (path === "/servers/s1") return { body: SERVER(["database.view"]) };
      if (path === "/servers/s1/databases") return { body: [] };
      return undefined;
    });
    providers(<AppRoutes />, "/admin/databases?server=s1");
    const link = await screen.findByRole("link", { name: "Administration tab" });
    expect(link).toHaveAttribute("href", "/servers/s1?tab=administration");
    expect(screen.queryByText(/SQL workspace/)).not.toBeInTheDocument();
  });

  it("explains account changes on the Users tab with a link that preselects create_user", async () => {
    stub((path) => {
      if (path === "/auth/me") return { body: ME(["user.view"]) };
      if (path === "/servers/s1/database-users" || path === "/servers/s1/roles") return { body: [] };
      return undefined;
    });
    providers(<UsersTab server={SERVER(["user.view"])} />);
    const link = await screen.findByRole("link", { name: "Administration tab" });
    expect(link).toHaveAttribute("href", "/servers/s1?tab=administration&action=create_user");
    expect(screen.queryByText(/SQL workspace/)).not.toBeInTheDocument();
  });

  it("preselects the action named in ?action= on the Administration tab", async () => {
    stub((path) => {
      if (path === "/auth/me") return { body: ME(["user.manage"]) };
      if (path === "/servers/s1/admin/actions") return { body: { management_mode: "administration", available: true, blockers: [], actions: [
        { action: "create_schema", label: "Create schema", risk: "LOW", permission: "schema.manage", params: ["name"], allowed: true },
        { action: "create_user", label: "Create user", risk: "PRIVILEGED_CHANGE", permission: "user.manage", params: ["name", "password"], allowed: true },
      ] } };
      return undefined;
    });
    providers(<Routes><Route path="/servers/:id" element={<AdministrationTab server={SERVER(["user.manage"])} />} /></Routes>, "/servers/s1?tab=administration&action=create_user");
    await waitFor(() => expect(screen.getByLabelText("Action")).toHaveValue("create_user"));
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
  });

  it("'+ Add → Database user' leads to a server choice that continues on the Administration tab", async () => {
    stub((path) => {
      if (path === "/auth/me") return { body: ME(["server.view", "user.view", "user.manage"]) };
      if (path === "/servers") return LIST([SERVER(["user.manage"], { id: "s0", name: "MON-ONLY", management_mode: "monitoring" }), SERVER(["user.manage"])]);
      return undefined;
    });
    providers(<AppRoutes />, "/");
    await userEvent.click(await screen.findByRole("button", { name: "Add" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /Database user/ }));
    expect(await screen.findByRole("heading", { name: "Create a database user" })).toBeInTheDocument();
    // administration-enabled servers come first
    await waitFor(() => expect(screen.getByRole("link", { name: "Continue to Administration" })).toHaveAttribute("href", "/servers/s1?tab=administration&action=create_user"));
  });
});

// ---- 2. settings permission ----------------------------------------------------------------------
describe("platform settings page", () => {
  it("shows access denied without settings.manage and does not read /settings", async () => {
    const calls = stub((path) => (path === "/auth/me" ? { body: ME(["server.view"]) } : path === "/settings" ? { body: { editable: { "ui.banner_text": "" }, readonly: { demo_mode: false } } } : undefined));
    providers(<AppRoutes />, "/platform/settings");
    expect(await screen.findByText("You don't have access to platform settings")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save settings" })).not.toBeInTheDocument();
    // the shell reads /settings for the banner, but the page itself does not render its payload
    expect(screen.queryByText("Platform behaviour")).not.toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/settings").length).toBeLessThanOrEqual(1);
  });

  it("shows the MFA policy for settings.manage holders when the payload has it", async () => {
    stub((path) => {
      if (path === "/auth/me") return { body: ME([], ["settings.manage"]) };
      if (path === "/settings") return { body: { editable: { "ui.banner_text": "Hello" }, descriptions: {}, readonly: { env: "production", mfa_required_for: "admins", mfa_issuer: "ACME DBManager" } } };
      return undefined;
    });
    providers(<AppRoutes />, "/platform/settings");
    expect(await screen.findByText("Multi-factor authentication policy")).toBeInTheDocument();
    expect(screen.getByText("MFA required for")).toBeInTheDocument();
    expect(screen.getByText("admins")).toBeInTheDocument();
    expect(screen.getByText("ACME DBManager")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save settings" })).toBeInTheDocument();
  });

  it("shows an error state when /settings fails", async () => {
    stub((path) => (path === "/auth/me" ? { body: ME([], ["settings.manage"]) } : path === "/settings" ? fail("settings down") : undefined));
    providers(<AppRoutes />, "/platform/settings");
    expect(await screen.findByText("settings down")).toBeInTheDocument();
  });
});

// ---- 3. server Settings tab ----------------------------------------------------------------------
describe("server page Settings tab", () => {
  it("includes diagnostics and security scan permissions", () => {
    expect(SETTINGS_PERMS).toEqual(expect.arrayContaining(["collectors.view", "security.manage", "server.modify"]));
  });

  it.each([
    [["server.view", "collectors.view"], true],
    [["server.view", "security.manage"], true],
    [["server.view"], false],
  ])("permissions %j enable the tab: %s", async (perms, enabled) => {
    stub((path) => {
      if (path === "/auth/me") return { body: ME(["server.view"]) };
      if (path === "/servers/s1") return { body: SERVER(perms) };
      return undefined;
    });
    providers(<AppRoutes />, "/servers/s1");
    const tab = await screen.findByRole("tab", { name: "Settings" });
    if (enabled) expect(tab).toBeEnabled();
    else expect(tab).toBeDisabled();
  });
});

// ---- 4. error states -----------------------------------------------------------------------------
describe("error states instead of endless skeletons", () => {
  it("collectors page shows status and job errors", async () => {
    stub((path) => {
      if (path === "/auth/me") return { body: ME([], ["collectors.view"]) };
      if (path === "/collectors/status") return fail("status unavailable");
      if (path === "/collectors/jobs") return fail("jobs unavailable");
      return undefined;
    });
    providers(<AppRoutes />, "/platform/collectors");
    expect(await screen.findByText("status unavailable")).toBeInTheDocument();
    expect(await screen.findByText("jobs unavailable")).toBeInTheDocument();
  });

  it("credentials page shows a secret sources error", async () => {
    stub((path) => {
      if (path === "/auth/me") return { body: ME(["credentials.manage"], ["credentials.manage"]) };
      if (path === "/credentials") return { body: [] };
      if (path === "/platform/secret-providers") return fail("providers unavailable");
      return undefined;
    });
    providers(<AppRoutes />, "/platform/credentials");
    expect(await screen.findByText("providers unavailable")).toBeInTheDocument();
  });

  it("setup checklist card shows an error with retry", async () => {
    stub((path) => (path === "/auth/me" ? { body: ME([], ["settings.manage"]) } : path === "/platform/setup/checklist" ? fail("checklist failed") : undefined));
    providers(<SetupChecklist />);
    expect(await screen.findByText("checklist failed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("backup protection panel shows an error", async () => {
    stub((path) => (path === "/auth/me" ? { body: ME(["backup.view"]) } : path === "/servers/s1/backup-protection" ? fail("protection failed") : undefined));
    providers(<ServerProtectionPanel serverId="s1" />);
    expect(await screen.findByText("protection failed")).toBeInTheDocument();
  });

  it("replication table shows the server list error", async () => {
    stub((path) => (path === "/auth/me" ? { body: ME(["performance.view"]) } : path === "/servers" ? fail("fleet unavailable") : undefined));
    providers(<AppRoutes />, "/monitoring/replication");
    expect((await screen.findAllByText("fleet unavailable")).length).toBeGreaterThan(0);
  });
});

// ---- 5. ?create=1 and placement links ------------------------------------------------------------
describe("create deep links", () => {
  it("opens the new environment dialog for ?create=1 and links to Placement", async () => {
    stub((path) => {
      if (path === "/auth/me") return { body: ME(["server.view"], ["inventory.manage"]) };
      if (path === "/environments") return { body: [{ id: "e1", name: "Production", tier: "production" }] };
      if (path === "/servers") return LIST([]);
      return undefined;
    });
    providers(<AppRoutes />, "/fleet/environments?create=1");
    expect(await screen.findByRole("dialog", { name: "New environment" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("link", { name: "Edit or delete in Placement" })).toHaveAttribute("href", "/fleet/placement?tab=environments");
  });

  it("does not open the dialog without inventory.manage", async () => {
    stub((path) => {
      if (path === "/auth/me") return { body: ME(["server.view"]) };
      if (path === "/environments") return { body: [] };
      if (path === "/servers") return LIST([]);
      return undefined;
    });
    providers(<AppRoutes />, "/fleet/environments?create=1");
    expect(await screen.findByRole("heading", { name: "Environments" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("links clusters to the Placement clusters tab, which the tab parameter selects", async () => {
    stub((path) => {
      if (path === "/auth/me") return { body: ME(["server.view"], ["inventory.manage"]) };
      if (path === "/clusters") return { body: [] };
      if (["/environments", "/sites", "/applications"].includes(path)) return { body: [] };
      if (path === "/servers") return LIST([]);
      return undefined;
    });
    providers(<AppRoutes />, "/fleet/clusters");
    const link = await screen.findByRole("link", { name: "Edit or delete in Placement" });
    expect(link).toHaveAttribute("href", "/fleet/placement?tab=clusters");
    await userEvent.click(link);
    expect(await screen.findByRole("tab", { name: "Clusters" })).toHaveAttribute("data-state", "active");
  });

  it("opens the create user dialog for ?create=1 on Platform → Users", async () => {
    stub((path) => {
      if (path === "/auth/me") return { body: ME([], ["users.manage"]) };
      if (path === "/users") return LIST([]);
      if (path === "/groups") return { body: [] };
      return undefined;
    });
    providers(<AppRoutes />, "/platform/users?create=1");
    expect(await screen.findByRole("dialog", { name: "Create local user" })).toBeInTheDocument();
  });
});

// ---- 6. backup pin, jobs, engine support ---------------------------------------------------------
const BACKUP = (pinned: boolean) => ({
  id: "b1b2c3d4-0000-4000-8000-000000000001", kind: "platform", status: "SUCCESS", method: "platform", tool: null, server_id: null, server_name: null,
  engine: null, database: null, policy_id: null, policy_name: null, target_id: "t1", target_name: "Local", job_id: null, location: "x", location_kind: "target",
  size_bytes: 10, sha256: null, encrypted: false, encryption: { mode: "none" }, app_version: "1", schema_version: null, format_version: "1",
  verification: null, verified_at: null, error: null, pinned, created_by: "dba", created_at: "2026-10-18T09:00:00Z", finished_at: null, deleted_at: null,
});
const JOB = (id: string, status: string, extra: object = {}) => ({
  id, kind: "database_backup", status, trigger: "manual", policy_id: null, target_id: "t1", params: {},
  progress: { step: "running", total: 3, done: 1 }, result: null, error: null, cancel_requested: false, requested_by: "dana",
  correlation_id: null, created_at: "2026-10-18T09:00:00Z", started_at: "2026-10-18T09:00:05Z", finished_at: null, ...extra,
});

describe("backup pin, jobs and engine support", () => {
  it("pins and unpins from the history row actions", async () => {
    let pinned = false;
    const calls = stub((path, method, url) => {
      if (path === "/auth/me") return { body: ME(["backup.view"], ["backup.view", "backup.policy.manage"]) };
      if (path === "/backups" && method === "GET") return LIST([BACKUP(pinned)]);
      if (path === `/backups/${BACKUP(false).id}/pin` && method === "POST") { pinned = url.searchParams.get("pinned") === "true"; return { body: BACKUP(pinned) }; }
      return undefined;
    });
    providers(<AppRoutes />, "/platform/backups?tab=history");
    await userEvent.click(await screen.findByRole("button", { name: "Pin b1b2c3d4" }));
    await waitFor(() => expect(last(calls, "POST", `/backups/${BACKUP(false).id}/pin`)?.query.get("pinned")).toBe("true"));
    expect(await screen.findByText(/Backup pinned/)).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Unpin b1b2c3d4" }));
    await waitFor(() => expect(last(calls, "POST", `/backups/${BACKUP(false).id}/pin`)?.query.get("pinned")).toBe("false"));
  });

  it("hides Pin without backup.policy.manage", async () => {
    stub((path) => (path === "/auth/me" ? { body: ME(["backup.view"], ["backup.view"]) } : path === "/backups" ? LIST([BACKUP(false)]) : undefined));
    providers(<AppRoutes />, "/platform/backups?tab=history");
    expect(await screen.findByText("DBManager platform")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pin b1b2c3d4" })).not.toBeInTheDocument();
  });

  it("lists jobs and cancels an active one", async () => {
    const calls = stub((path, method) => {
      if (path === "/auth/me") return { body: ME(["backup.view", "backup.create"]) };
      if (path === "/backup/jobs" && method === "GET") return { body: [JOB("aaaaaaaa-1", "RUNNING"), JOB("bbbbbbbb-2", "FAILED", { error: "pg_dump: connection refused", finished_at: "2026-10-18T09:01:00Z" })] };
      if (path === "/backup/jobs/aaaaaaaa-1/cancel" && method === "POST") return { body: JOB("aaaaaaaa-1", "RUNNING", { cancel_requested: true }) };
      return undefined;
    });
    providers(<AppRoutes />, "/platform/backups?tab=jobs");
    expect(await screen.findByText("pg_dump: connection refused")).toBeInTheDocument();
    expect(screen.getAllByText("dana").length).toBe(2);
    expect(screen.getAllByText("running · 1 of 3").length).toBe(2);
    expect(screen.queryByRole("button", { name: "Cancel job bbbbbbbb" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel job aaaaaaaa" }));
    await waitFor(() => expect(last(calls, "POST", "/backup/jobs/aaaaaaaa-1/cancel")).toBeTruthy());
    expect(await screen.findByText("Cancellation requested")).toBeInTheDocument();
  });

  it("has nav entries for jobs and engine support", () => {
    const items = NAV_GROUPS.flatMap((g) => g.sections).find((s) => s.label === "Backup & Restore")!.items!.map((i) => i.to);
    expect(items).toEqual(expect.arrayContaining(["/platform/backups?tab=jobs", "/platform/backups?tab=engines"]));
  });
});

// ---- 7. alert rules ------------------------------------------------------------------------------
describe("alert rules evaluate now", () => {
  it("summarises evaluation counters", () => {
    expect(evaluateSummary({ rules: 3, fired: 1, resolved: 2, pending: 0, notified: 1, suppressed: 0 })).toBe("1 fired, 2 resolved, 1 notified");
    expect(evaluateSummary({ rules: 0, fired: 0, resolved: 0, pending: 0, notified: 0, suppressed: 0 })).toBe("No changes");
  });

  it("evaluates all rules for alerts.manage and shows the result", async () => {
    const calls = stub((path, method) => {
      if (path === "/auth/me") return { body: ME(["alerts.view"], ["alerts.view", "alerts.manage"]) };
      if (path === "/alert-rules") return LIST([]);
      if (path === "/alerts/evaluate" && method === "POST") return { body: { rules: 3, fired: 1, resolved: 0, pending: 2, notified: 1, suppressed: 0, renotified: 0, released: 0 } };
      return undefined;
    });
    providers(<AppRoutes />, "/alerts/rules");
    expect(await screen.findByText(/z-score \(deviation from the server's own baseline\)/)).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: /Evaluate now/ }));
    await waitFor(() => expect(last(calls, "POST", "/alerts/evaluate")).toBeTruthy());
    expect(await screen.findByText("Evaluated 3 rules")).toBeInTheDocument();
    expect(screen.getByText("1 fired, 2 pending, 1 notified")).toBeInTheDocument();
  });

  it("hides Evaluate now without alerts.manage", async () => {
    stub((path) => (path === "/auth/me" ? { body: ME(["alerts.view"], ["alerts.view"]) } : path === "/alert-rules" ? LIST([]) : undefined));
    providers(<AppRoutes />, "/alerts/rules");
    expect(await screen.findByRole("heading", { name: "Alert rules" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Evaluate now/ })).not.toBeInTheDocument();
  });
});

// ---- 8. identity ---------------------------------------------------------------------------------
describe("OIDC page and identity nav", () => {
  it("renders only OIDC providers at /platform/oidc", async () => {
    stub((path) => {
      if (path === "/auth/me") return { body: ME([], ["idp.manage"]) };
      if (path === "/identity-providers") return { body: [
        { id: "p1", kind: "oidc", name: "Keycloak", enabled: true, config: {}, has_secret: true, mappings: [] },
        { id: "p2", kind: "ldap", name: "Corp AD", enabled: true, config: {}, has_secret: true, mappings: [] },
      ] };
      return undefined;
    });
    providers(<AppRoutes />, "/platform/oidc");
    expect(await screen.findByRole("heading", { name: "OpenID Connect" })).toBeInTheDocument();
    expect(await screen.findByText("Keycloak")).toBeInTheDocument();
    expect(screen.queryByText("Corp AD")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /LDAP directory/ })).not.toBeInTheDocument();
  });

  it("lists OIDC and the identity wizard under Platform → Configuration", () => {
    const config = NAV_GROUPS.find((g) => g.id === "platform")!.sections.find((s) => s.label === "Configuration")!.items!;
    expect(config.find((i) => i.to === "/platform/oidc")).toMatchObject({ perm: "idp.manage", platform: true });
    expect(config.find((i) => i.to === "/platform/identity/wizard")).toMatchObject({ perm: "idp.manage", platform: true });
  });
});

// ---- 9. command palette --------------------------------------------------------------------------
describe("command palette actions", () => {
  it("offers create backup, generate report and account security when permitted", async () => {
    stub((path) => (path === "/auth/me" ? { body: ME(["backup.create", "reports.view"]) } : undefined));
    providers(<CommandPalette open onOpenChange={() => {}} />);
    expect(await screen.findByRole("option", { name: /Create backup/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Generate report/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Account security/ })).toBeInTheDocument();
  });

  it("hides permissioned actions without the permissions but keeps account security", async () => {
    stub((path) => (path === "/auth/me" ? { body: ME([]) } : undefined));
    providers(<CommandPalette open onOpenChange={() => {}} />);
    expect(await screen.findByRole("option", { name: /Account security/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Create backup/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Generate report/ })).not.toBeInTheDocument();
  });

  it("navigates to the backup wizard", async () => {
    stub((path) => (path === "/auth/me" ? { body: ME(["backup.create"]) } : undefined));
    providers(<Routes>
      <Route path="/" element={<CommandPalette open onOpenChange={() => {}} />} />
      <Route path="/platform/backups/new" element={<p>wizard here</p>} />
    </Routes>);
    await userEvent.click(await screen.findByRole("option", { name: /Create backup/ }));
    expect(await screen.findByText("wizard here")).toBeInTheDocument();
  });
});

// ---- 11. performance metric catalog --------------------------------------------------------------
const CAT = (key: string, label: string, extra: Partial<CatalogMetric> = {}): CatalogMetric => ({
  key, name: key, type: "gauge", unit: "count", interval: "fast", labels: ["server_id", "engine", "data_source"], engines: "all", label, description: "", ...extra,
});
describe("performance compare metrics", () => {
  const catalog = [
    CAT("connections.total", "Connections"), CAT("cache.hit_ratio", "Cache hit ratio", { unit: "%" }),
    CAT("wait.active_sessions", "Active sessions by wait", { labels: ["server_id", "engine", "data_source", "wait_type"] }),
    CAT("xact_commit", "Commits (raw)", { type: "counter" }), CAT("locks.waiting", "Waiting locks"),
  ];
  it("keeps one-line-per-server metrics only", () => {
    expect(comparableMetrics(catalog).map((m) => m.key)).toEqual(["cache.hit_ratio", "connections.total", "locks.waiting"]);
  });

  it("loads the metric list from /metrics/catalog with connections.total selected", async () => {
    const calls = stub((path) => {
      if (path === "/auth/me") return { body: ME(["performance.view"]) };
      if (path === "/metrics/catalog") return { body: catalog };
      if (path === "/servers") return LIST([SERVER(["performance.view"])]);
      if (path === "/metrics/range") return { body: { series: [] } };
      return undefined;
    });
    providers(<AppRoutes />, "/monitoring/performance");
    const select = await screen.findByLabelText("Metric");
    await waitFor(() => expect(within(select).getByRole("option", { name: "Waiting locks" })).toBeInTheDocument());
    expect(select).toHaveValue("connections.total");
    expect(within(select).queryByRole("option", { name: "Active sessions by wait" })).not.toBeInTheDocument();
    await userEvent.selectOptions(select, "cache.hit_ratio");
    await waitFor(() => expect(calls.some((c) => c.path === "/metrics/range" && c.query.get("metric") === "cache.hit_ratio")).toBe(true));
  });
});

// ---- 12. bulk onboarding engine icon -------------------------------------------------------------
describe("bulk onboarding engine icon", () => {
  it("shows the engine icon once a known engine is entered", async () => {
    stub((path) => (path === "/auth/me" ? { body: ME(["server.register"]) } : undefined));
    providers(<AppRoutes />, "/fleet/onboarding/bulk");
    const input = await screen.findByLabelText("Row 1 engine");
    expect(screen.queryByTestId("row-1-engine-icon")).not.toBeInTheDocument();
    await userEvent.type(input, "nosuchdb");
    expect(screen.queryByTestId("row-1-engine-icon")).not.toBeInTheDocument();
    await userEvent.clear(input);
    await userEvent.type(input, "postgresql");
    expect(screen.getByTestId("row-1-engine-icon")).toBeInTheDocument();
    // typing kept focus on the same input
    expect(screen.getByLabelText("Row 1 engine")).toHaveFocus();
  });
});

// ---- 13. all servers across pages ----------------------------------------------------------------
describe("fetchAllServers", () => {
  it("pages through GET /servers until every item is loaded", async () => {
    const total = 1203;
    const calls = stub((path, _m, url) => {
      if (path !== "/servers") return undefined;
      const page = Number(url.searchParams.get("page"));
      const size = Number(url.searchParams.get("page_size"));
      const start = (page - 1) * size;
      const items = Array.from({ length: Math.max(0, Math.min(size, total - start)) }, (_, i) => ({ id: `s${start + i}` }));
      return { body: { items, total, page, page_size: size } };
    });
    const res = await fetchAllServers({ sort: "name", source: "all" });
    expect(res.items).toHaveLength(total);
    expect(res.total).toBe(total);
    expect(calls.map((c) => c.query.get("page"))).toEqual(["1", "2", "3"]);
    expect(calls.every((c) => c.query.get("page_size") === "500" && c.query.get("sort") === "name")).toBe(true);
  });

  it("stops on an empty page even if total is larger", async () => {
    const calls = stub((path) => (path === "/servers" ? { body: { items: [], total: 10, page: 1, page_size: 500 } } : undefined));
    const res = await fetchAllServers({});
    expect(res.items).toHaveLength(0);
    expect(calls).toHaveLength(1);
  });
});
