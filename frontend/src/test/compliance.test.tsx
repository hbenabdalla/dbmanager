import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AppRoutes } from "@/App";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthProvider } from "@/lib/auth";
import { ThemeProvider } from "@/lib/theme";

const ME = { id: "u1", username: "dba", display_name: null, email: null, auth_source: "local", must_change_password: false,
  permissions: ["server.view", "security.view", "performance.view"], platform_permissions: [], grants: [], mfa_enabled: false, mfa_enrollment_required: false };
const RULES = [
  { rule_id: "PG-SEC-001", engines: ["postgresql"], kind: "security", category: "authentication", severity: "high", title: "Trust authentication", description: "" },
  { rule_id: "GEN-SEC-001", engines: ["*"], kind: "security", category: "accounts", severity: "medium", title: "Dormant accounts", description: "" },
  { rule_id: "GEN-PERF-001", engines: ["*"], kind: "performance", category: "connections", severity: "medium", title: "Connection pressure", description: "" },
];

afterEach(() => vi.unstubAllGlobals());

it("shows failing servers per rule from the server-side summary", async () => {
  const kinds: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    const body = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } });
    if (url.pathname === "/api/v1/auth/me") return body(ME);
    if (url.pathname === "/api/v1/advisor/rules") return body(RULES);
    if (url.pathname === "/api/v1/advisor/rule-summary") {
      kinds.push(url.searchParams.get("kind") ?? "");
      return body(url.searchParams.get("kind") === "security" ? { "PG-SEC-001": { findings: 812, servers: 640 } } : { "GEN-PERF-001": { findings: 3, servers: 2 } });
    }
    if (url.pathname === "/api/v1/branding/engine-logos") return body({ engines: [] });
    return new Response(JSON.stringify({ error: { code: "not_found", message: "not mocked" } }), { status: 404 });
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<ThemeProvider><QueryClientProvider client={qc}><MemoryRouter initialEntries={["/security/compliance"]}><AuthProvider><TooltipProvider><AppRoutes /></TooltipProvider></AuthProvider></MemoryRouter></QueryClientProvider></ThemeProvider>);
  const row = (await screen.findByText("Trust authentication")).closest("tr")!;
  expect(within(row).getByText("640")).toBeInTheDocument(); // not capped by a page of 500 findings
  expect(within(screen.getByText("Dormant accounts").closest("tr")!).getByText("pass")).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText("Rule set"), "performance");
  expect(within((await screen.findByText("Connection pressure")).closest("tr")!).getByText("2")).toBeInTheDocument();
  expect(kinds).toEqual(["security", "performance"]);
});
