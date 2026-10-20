import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AppRoutes } from "@/App";
import { SourceFilter } from "@/components/fleet-source";
import { SimulatedBadge } from "@/components/status";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthProvider } from "@/lib/auth";
import { FleetSourceProvider, resolveSource } from "@/lib/fleet-source";
import { ThemeProvider } from "@/lib/theme";
import { mockFetch, renderWithProviders } from "./utils";

const SIM_PERMS = ["simulation.view", "simulation.generate", "simulation.delete"];
const ME = (perms: string[], platform: string[] = []) => ({
  id: "u1", username: "admin", display_name: "Admin", email: null, auth_source: "local", must_change_password: false,
  permissions: perms, platform_permissions: platform, grants: [],
});
const PROFILES = [
  { key: "small", label: "Small", description: "5 servers", servers: 5, environments: { Production: 2, UAT: 1, Development: 1, Test: 1 }, applications: 5, history_days: 1, query_history_hours: 12, include_cloud: false, estimated_metric_rows: 1000 },
  { key: "standard", label: "Standard", description: "18 servers", servers: 18, environments: { Production: 7, DR: 3, UAT: 3, Development: 3, Test: 2 }, applications: 8, history_days: 7, query_history_hours: 24, include_cloud: true, estimated_metric_rows: 9000 },
  { key: "large", label: "Large", description: "75 servers", servers: 75, environments: { Production: 28 }, applications: 16, history_days: 30, query_history_hours: 24, include_cloud: true, estimated_metric_rows: 99000 },
];
const STATUS = (present: boolean) => ({
  simulation_present: present, profile: present ? "standard" : null, seed: present ? 42 : null, generated_at: present ? "2026-10-15T09:00:00Z" : null,
  generated_by: present ? "admin" : null, legacy: false, servers: present ? 18 : 0, real_servers: 2, databases: present ? 65 : 0,
  metrics: present ? 446000 : 0, queries: present ? 2600 : 0, security_findings: present ? 54 : 0, recommendations: present ? 118 : 0,
  sites: present ? 4 : 0, clusters: present ? 3 : 0, applications: present ? 8 : 0, active_run: null, last_run: null, history: [],
});
const EMPTY_SUMMARY = {
  kpis: { servers: 0, healthy: 0, warning: 0, critical: 0, unavailable: 0, databases: 0, active_connections: 0, critical_findings: 0, open_recommendations: 0, total_storage_bytes: 0 },
  health: {}, engines: [], environments: [], fleet: [], attention: [], recommendations_by_severity: {}, findings_by_severity: {},
  top_recommendations: [], trends: {}, capacity: [], demo: false, source: "all", sources: { real: 0, simulated: 0 },
};

function renderApp(route: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider><QueryClientProvider client={qc}><MemoryRouter initialEntries={[route]}><AuthProvider><TooltipProvider><AppRoutes /></TooltipProvider></AuthProvider></MemoryRouter></QueryClientProvider></ThemeProvider>,
  );
}

function base(url: URL) {
  if (url.pathname === "/api/v1/settings") return { body: { editable: {}, readonly: {} } };
  if (url.pathname === "/api/v1/simulation/profiles") return { body: PROFILES };
  return undefined;
}

describe("simulation administration", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it("shows status and removes simulated data only after typing REMOVE", async () => {
    const fetch = mockFetch((url, init) => {
      if (url.pathname === "/api/v1/auth/me") return { body: ME(["server.view"], SIM_PERMS) };
      if (url.pathname === "/api/v1/fleet/sources") return { body: { real: 2, simulated: 18, total: 20, simulation_active: true } };
      if (url.pathname === "/api/v1/simulation/status") return { body: STATUS(true) };
      if (url.pathname === "/api/v1/simulation/cleanup-preview") return { body: { counts: { servers: 18, databases: 65, metrics: 446000, queries: 2600, security_findings: 54, recommendations: 118, real_servers: 2 }, groupings: { sites: { remove: ["DC-East"], keep: [] } }, legacy_demo_accounts: [] } };
      if (url.pathname === "/api/v1/simulation" && init.method === "DELETE") return { body: { message: "ok", servers_deleted: 18 } };
      return base(url);
    });
    renderApp("/platform/simulation");
    expect(await screen.findByTestId("simulation-state")).toHaveTextContent("Active");
    expect(screen.getByText("18 simulated servers alongside 2 real")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Remove simulated data/ }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Real database resources and platform configuration will not be affected.");
    expect(await within(dialog).findByText("446,000")).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: "Remove simulated data" });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Confirmation text"), "REMOVE");
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);
    await waitFor(() => expect(fetch.mock.calls.some(([u, i]) => String(u).startsWith("/api/v1/simulation?confirm=REMOVE") && i?.method === "DELETE")).toBe(true));
  });

  it("generates the selected profile", async () => {
    const fetch = mockFetch((url, init) => {
      if (url.pathname === "/api/v1/auth/me") return { body: ME(["server.view"], SIM_PERMS) };
      if (url.pathname === "/api/v1/fleet/sources") return { body: { real: 2, simulated: 0, total: 2, simulation_active: false } };
      if (url.pathname === "/api/v1/simulation/status") return { body: STATUS(false) };
      if (url.pathname === "/api/v1/simulation/generate" && init.method === "POST") return { status: 202, body: { run: { id: "r1", status: "pending" } } };
      return base(url);
    });
    renderApp("/platform/simulation");
    expect(await screen.findByTestId("simulation-state")).toHaveTextContent("Not generated");
    expect(screen.queryByRole("button", { name: /Remove simulated data/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Generate simulated data/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("radio", { name: /Small/ }));
    expect(dialog).toHaveTextContent("5 simulated database servers");
    await userEvent.type(within(dialog).getByLabelText("Seed (optional)"), "42");
    await userEvent.click(within(dialog).getByRole("button", { name: /Generate simulated data/ }));
    await waitFor(() => {
      const call = fetch.mock.calls.find(([u]) => String(u) === "/api/v1/simulation/generate");
      expect(call && JSON.parse(String(call[1]?.body))).toEqual({ profile: "small", seed: 42 });
    });
  });

  it("is only in the navigation for holders of simulation.view", async () => {
    mockFetch((url) => {
      if (url.pathname === "/api/v1/auth/me") return { body: ME(["server.view", "users.manage"], ["users.manage"]) };
      if (url.pathname === "/api/v1/fleet/sources") return { body: { real: 1, simulated: 0, total: 1, simulation_active: false } };
      if (url.pathname === "/api/v1/dashboard/summary") return { body: { ...EMPTY_SUMMARY, sources: { real: 1, simulated: 0 } } };
      return base(url);
    });
    renderApp("/");
    await screen.findByRole("heading", { name: "Fleet overview" });
    const nav = screen.getByRole("navigation", { name: "Main" });
    await userEvent.click(within(nav).getByRole("button", { name: /^Configuration/ }));
    expect(nav).toHaveTextContent("Users");
    expect(nav).not.toHaveTextContent("Simulation");
  });
});

describe("empty fleet", () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each([
    [SIM_PERMS, true],
    [[], false],
  ])("offers onboarding (demo option: %s)", async (platform, demo) => {
    mockFetch((url) => {
      if (url.pathname === "/api/v1/auth/me") return { body: ME(["server.view", "server.register"], platform as string[]) };
      if (url.pathname === "/api/v1/fleet/sources") return { body: { real: 0, simulated: 0, total: 0, simulation_active: false } };
      if (url.pathname === "/api/v1/dashboard/summary") return { body: EMPTY_SUMMARY };
      return base(url);
    });
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "No database servers registered" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Add database server/ })).toHaveAttribute("href", "/fleet/servers?register=1");
    if (demo) expect(screen.getByRole("link", { name: /Generate demo environment/ })).toHaveAttribute("href", "/platform/simulation?generate=1");
    else expect(screen.queryByText(/Generate demo environment/)).not.toBeInTheDocument();
    // no simulation indicator while nothing is simulated
    expect(screen.queryByText("Simulation data active")).not.toBeInTheDocument();
  });
});

describe("real / simulated distinction", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it("labels simulated resources", () => {
    renderWithProviders(<SimulatedBadge />);
    expect(screen.getByText("Simulated")).toBeInTheDocument();
  });

  it("resolves automatic mode to real servers when any exist", () => {
    expect(resolveSource("auto", { real: 3, simulated: 5, total: 8, simulation_active: true })).toBe("real");
    expect(resolveSource("auto", { real: 0, simulated: 5, total: 5, simulation_active: true })).toBe("all");
    expect(resolveSource("simulated", undefined)).toBe("simulated");
  });

  it("switches between real, simulated and all and remembers the choice", async () => {
    mockFetch((url) => (url.pathname === "/api/v1/fleet/sources" ? { body: { real: 12, simulated: 18, total: 30, simulation_active: true } } : undefined));
    renderWithProviders(<FleetSourceProvider><SourceFilter /></FleetSourceProvider>);
    const real = await screen.findByRole("radio", { name: /Real/ });
    expect(real).toHaveAttribute("aria-checked", "true");
    await userEvent.click(screen.getByRole("radio", { name: /Simulated/ }));
    expect(screen.getByRole("radio", { name: /Simulated/ })).toHaveAttribute("aria-checked", "true");
    expect(window.localStorage.getItem("dbm.fleet-source")).toBe("simulated");
  });

  it("hides the filter when there is no simulated data", async () => {
    const fn = mockFetch((url) => (url.pathname === "/api/v1/fleet/sources" ? { body: { real: 3, simulated: 0, total: 3, simulation_active: false } } : undefined));
    renderWithProviders(<FleetSourceProvider><SourceFilter /><span>loaded</span></FleetSourceProvider>);
    await waitFor(() => expect(fn).toHaveBeenCalled());
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(screen.queryByRole("radiogroup", { name: "Show servers" })).not.toBeInTheDocument();
  });

  it("shows the indicator and the dashboard filter when simulated servers exist", async () => {
    mockFetch((url) => {
      if (url.pathname === "/api/v1/auth/me") return { body: ME(["server.view", "performance.view"]) };
      if (url.pathname === "/api/v1/fleet/sources") return { body: { real: 12, simulated: 18, total: 30, simulation_active: true } };
      if (url.pathname === "/api/v1/dashboard/summary") {
        const src = url.searchParams.get("source");
        return { body: { ...EMPTY_SUMMARY, kpis: { ...EMPTY_SUMMARY.kpis, servers: 12 }, source: src === "auto" ? "real" : src, sources: { real: 12, simulated: 18 },
          fleet: [{ id: "s1", name: "PG-PROD-01", engine: "postgresql", health: "healthy", environment: "Production", reasons: [], is_demo: src === "simulated" }] } };
      }
      return base(url);
    });
    renderApp("/");
    expect(await screen.findByText("Simulation data active")).toBeInTheDocument();
    expect(await screen.findByText("18 simulated servers are not included")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("radio", { name: /Simulated/ })[0]);
    expect(await screen.findByLabelText("PG-PROD-01: Healthy (simulated)")).toBeInTheDocument();
  });
});

describe("simulation removal preview errors", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows an error with retry instead of an endless skeleton when the cleanup preview fails", async () => {
    let fail = true;
    mockFetch((url) => {
      if (url.pathname === "/api/v1/auth/me") return { body: ME(["server.view"], SIM_PERMS) };
      if (url.pathname === "/api/v1/fleet/sources") return { body: { real: 2, simulated: 18, total: 20, simulation_active: true } };
      if (url.pathname === "/api/v1/simulation/status") return { body: STATUS(true) };
      if (url.pathname === "/api/v1/simulation/cleanup-preview") {
        return fail
          ? { status: 500, body: { error: { code: "internal", message: "preview exploded" } } }
          : { body: { counts: { servers: 18, databases: 65, metrics: 446000, queries: 2600, security_findings: 54, recommendations: 118, real_servers: 2 }, groupings: {}, legacy_demo_accounts: [] } };
      }
      return base(url);
    });
    renderApp("/platform/simulation");
    await userEvent.click(await screen.findByRole("button", { name: /Remove simulated data/ }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("preview exploded")).toBeInTheDocument();
    fail = false;
    await userEvent.click(within(dialog).getByRole("button", { name: "Try again" }));
    expect(await within(dialog).findByText("446,000")).toBeInTheDocument();
  });
});
