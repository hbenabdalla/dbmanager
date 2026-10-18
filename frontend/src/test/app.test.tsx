import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AppRoutes } from "@/App";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthProvider } from "@/lib/auth";
import { ThemeProvider } from "@/lib/theme";
import { mockFetch } from "./utils";

const ME = (perms: string[]) => ({
  id: "u1", username: "monitor", display_name: "Mona", email: null, auth_source: "local", must_change_password: false,
  permissions: perms, platform_permissions: [], grants: [{ role: "Monitoring User", scope_type: "environment", scope_id: "e1", scope_label: "Production" }],
});
const SUMMARY = {
  kpis: { servers: 1, healthy: 1, warning: 0, critical: 0, unavailable: 0, databases: 2, active_connections: 3, critical_findings: 0, open_recommendations: 0, total_storage_bytes: 1024 },
  health: { healthy: 1 }, engines: [{ engine: "postgresql", label: "PostgreSQL", count: 1 }],
  environments: [{ name: "Production", tier: "production", servers: 1, health: { healthy: 1 } }],
  fleet: [{ id: "s1", name: "pg-prod-01", engine: "postgresql", health: "healthy", environment: "Production", reasons: [], is_demo: false }],
  attention: [], recommendations_by_severity: {}, findings_by_severity: {}, top_recommendations: [], trends: {}, capacity: [], demo: false,
};

function renderApp(route: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider><QueryClientProvider client={qc}><MemoryRouter initialEntries={[route]}><AuthProvider><TooltipProvider><AppRoutes /></TooltipProvider></AuthProvider></MemoryRouter></QueryClientProvider></ThemeProvider>,
  );
}

describe("application", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("redirects to sign-in and signs in", async () => {
    let signedIn = false;
    mockFetch((url, init) => {
      if (url.pathname === "/api/v1/auth/me") return signedIn ? { body: ME(["server.view"]) } : { status: 401, body: { error: { code: "not_authenticated", message: "no" } } };
      if (url.pathname === "/api/v1/auth/providers") return { body: [] };
      if (url.pathname === "/api/v1/auth/login") {
        const b = JSON.parse(String(init.body));
        if (b.password !== "right-password") return { status: 401, body: { error: { code: "not_authenticated", message: "Invalid username or password" } } };
        signedIn = true;
        return { body: ME(["server.view"]) };
      }
      if (url.pathname === "/api/v1/dashboard/summary") return { body: SUMMARY };
      if (url.pathname === "/api/v1/settings") return { body: { editable: {}, readonly: {} } };
      return undefined;
    });
    renderApp("/");
    await screen.findByRole("heading", { name: "Sign in" });
    await userEvent.type(screen.getByLabelText("Username"), "monitor");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid username or password");
    await userEvent.clear(screen.getByLabelText("Password"));
    await userEvent.type(screen.getByLabelText("Password"), "right-password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Fleet overview" })).toBeInTheDocument();
  });

  it("only shows navigation the role allows", async () => {
    mockFetch((url) => {
      if (url.pathname === "/api/v1/auth/me") return { body: ME(["server.view", "performance.view"]) };
      if (url.pathname === "/api/v1/dashboard/summary") return { body: SUMMARY };
      if (url.pathname === "/api/v1/settings") return { body: { editable: {}, readonly: {} } };
      return undefined;
    });
    renderApp("/");
    await screen.findByRole("heading", { name: "Fleet overview" });
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(nav).toHaveTextContent("Fleet");
    expect(nav).toHaveTextContent("Monitoring");
    expect(nav).not.toHaveTextContent("Platform");
    expect(nav).not.toHaveTextContent("Audit logs");
    expect(nav).not.toHaveTextContent("Queries");
    await waitFor(() => expect(screen.getByLabelText("pg-prod-01: Healthy")).toBeInTheDocument());
  });
});
